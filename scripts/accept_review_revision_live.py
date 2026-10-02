"""Rewrite a closed review from retained evidence, using an immutable code snapshot."""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import logging
import pickle
import sys
import time
from contextlib import aclosing
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))


async def main() -> None:
    from deep_research.agents.base import Blackboard, RunContext
    from deep_research.agents.researcher import Researcher
    from deep_research.config import Settings
    from deep_research.guardrails import report_eligible
    from deep_research.llm import LLM
    from deep_research.models import ResearchResult, Source
    from deep_research.observability import Tracer
    from deep_research.persistence.repository import RunDetail
    from deep_research.prompting import load_global_rules
    from deep_research.tools.base import SearchTool
    from deep_research.workbench.attachments import attachments_from_scratch, parse_attachment
    from deep_research.workbench.contract import contract_from_scratch, provided_review
    from deep_research.workbench.reader import paper_sources
    from deep_research.workbench.writers import SurveyWriter
    from scripts.accept_delivery_live import remote_profile, render_case

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authorized-ssh", required=True)
    parser.add_argument("--detail", type=Path, required=True, help="Trusted local RunDetail pickle")
    parser.add_argument("--papers", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    snapshot = _ROOT / ".acceptance-snapshot.json"
    if not snapshot.is_file():
        raise ValueError("Run this script from a frozen git archive with its commit manifest")
    code = json.loads(snapshot.read_text(encoding="utf-8"))
    args.output.mkdir(parents=True, exist_ok=False)
    logging.disable(logging.CRITICAL)
    # Only load snapshots produced by our local acceptance runner, never uploaded pickles.
    detail = pickle.loads(args.detail.read_bytes())
    if not isinstance(detail, RunDetail) or detail.orchestration is None:
        raise ValueError("Expected trusted local task state")
    detail.results = [ResearchResult.model_validate(r.model_dump()) for r in detail.results]
    detail.sources = [Source.model_validate(s.model_dump()) for s in detail.sources]
    scratch = copy.deepcopy(detail.orchestration.checkpoint["scratch"])
    contract = contract_from_scratch(scratch)
    if not provided_review(contract):
        raise ValueError("Expected a supplied-literature review")
    assert contract is not None
    attachments = attachments_from_scratch(scratch)
    by_id = {item.id: item for item in attachments}
    confirmed = []
    for path in args.papers:
        raw = path.read_bytes()
        identifier = hashlib.sha256(raw).hexdigest()[:24]
        old = by_id.get(identifier)
        if old is None or old.size != len(raw):
            raise ValueError("PDF bytes do not match the saved task")
        parsed = await parse_attachment(raw, old.filename)
        if parsed.truncated or parsed.chunks != old.chunks:
            raise ValueError("Parsed text changed; evidence cannot be silently rebound")
        old.title, old.authors = parsed.title, parsed.authors
        confirmed.append({"id": identifier, "title": parsed.title, "authors": parsed.authors})
    if len(confirmed) != len(attachments) or len({item["id"] for item in confirmed}) != len(
        attachments
    ):
        raise ValueError("Every original input must be confirmed exactly once")
    scratch["attachments"] = [item.model_dump(mode="json") for item in attachments]
    detail.orchestration.checkpoint["scratch"] = scratch
    sources = {source.url: source for source in paper_sources(detail)}
    researcher = Researcher(settings=Settings())
    admitted = 0
    for result in detail.results:
        kept = []
        for finding in result.findings:
            if not report_eligible(finding):
                continue
            source = sources.get(finding.source_url)
            if (
                source is None
                or hashlib.sha256(source.content.encode()).hexdigest()
                != finding.verification.source_content_hash
            ):
                raise ValueError("Source snapshot no longer matches the paid finding")
            if not researcher.source_policy.evaluate(source).allowed:
                raise ValueError("Retained source no longer passes source policy")
            checked = researcher.evidence_verifier.verify(finding, source)
            if (
                not checked.accepted
                or checked.finding is None
                or checked.finding.verification.quantity_status == "unsupported"
            ):
                raise ValueError("A retained finding failed current deterministic verification")
            finding.verification.source_title = source.title
            finding.verification.source_reference = checked.finding.verification.source_reference
            kept.append(finding)
        result.findings = kept
        admitted += len(kept)
    detail.sources = list(sources.values())
    (args.output / "input-detail.pickle").write_bytes(pickle.dumps(detail))
    profile = remote_profile(args.authorized_ssh)
    settings = Settings(
        llm_api_key=profile["api_key"],
        llm_base_url=profile["base_url"],
        llm_model=profile["model"],
        request_timeout=max(180, profile.get("request_timeout", 120)),
        quality=contract.quality,
    )
    tracer = Tracer()

    def save(name, value):
        text = json.dumps(value, ensure_ascii=False, indent=2)
        if profile["api_key"] in text:
            raise RuntimeError("Refusing to persist credentials")
        (args.output / name).write_text(text, encoding="utf-8")

    class CaptureLLM(LLM):
        calls = 0

        async def _stream_once(self, system, user, *, temperature=0.4):
            self.calls += 1
            number, started, chunks, status = self.calls, time.monotonic(), [], "interrupted"
            try:
                async with aclosing(
                    super()._stream_once(system, user, temperature=temperature)
                ) as stream:
                    async for chunk in stream:
                        chunks.append(chunk)
                        yield chunk
                status = "complete"
            finally:
                save(
                    f"response-{number}.json",
                    {
                        "call": number,
                        "status": status,
                        "seconds": time.monotonic() - started,
                        "input_chars": len(system) + len(user),
                        "prompt_sha256": hashlib.sha256(
                            json.dumps([system, user], ensure_ascii=False).encode()
                        ).hexdigest(),
                        "output": "".join(chunks),
                    },
                )

    llm = CaptureLLM.from_params(
        tracer,
        api_key=profile["api_key"],
        base_url=profile["base_url"],
        model=profile["model"],
        timeout=settings.request_timeout,
        user_agent="deep-research-local-acceptance",
        temperature=profile.get("temperature", 0.3),
        parameter_mode=profile.get("parameter_mode", "temperature"),
        reasoning_effort=profile.get("reasoning_effort", "medium"),
        context_window_tokens=profile.get("context_window_tokens"),
        max_output_tokens=profile.get("max_output_tokens"),
    )

    class NoSearch(SearchTool):
        async def search(self, query, *, max_results=5):
            raise AssertionError("Recovery must reuse evidence without new retrieval")

    bb = Blackboard(query=detail.query, results=detail.results, scratch=scratch)
    ctx = RunContext(
        llm=llm,
        search_tool=NoSearch(),
        tracer=tracer,
        settings=settings,
        global_rules=load_global_rules(),
    )
    metadata = {
        "code": code,
        "mode": "rewrite_from_retained_evidence",
        "new_extractions": 0,
        "retained_findings": admitted,
        "inputs": confirmed,
        "model": profile["model"],
    }
    save("input.json", metadata)
    print(f"Reusing {admitted} findings; no repeat extraction", flush=True)
    tracer.subscribe(
        lambda e: (
            print(
                f"{e.elapsed:.0f}s {e.stage}: {e.message[:200]}".replace(
                    profile["api_key"], "[REDACTED]"
                ),
                flush=True,
            )
            if e.stage != "LLM" and e.type in {"start", "info", "done", "error"}
            else None
        )
    )

    async def heartbeat():
        while True:
            await asyncio.sleep(30)
            print(f"Still running; recorded tokens={tracer.total_tokens}", flush=True)

    beat = asyncio.create_task(heartbeat())
    try:
        await SurveyWriter().step(bb, ctx)
        detail.results, detail.report = bb.results, bb.report
        detail.orchestration.checkpoint.update(bb.model_dump(mode="json"))
        next_sequence = (
            max((event.seq for event in detail.events if event.seq is not None), default=-1) + 1
        )
        detail.events.extend(
            event.model_copy(
                update={"elapsed": event.elapsed + detail.elapsed, "seq": next_sequence + index}
            )
            for index, event in enumerate(tracer.events)
        )
        detail.total_tokens += tracer.total_tokens
        detail.elapsed += tracer.elapsed
        # The original run's metrics do not evaluate the recovered report.
        detail.metrics = None
        frozen = pickle.dumps(detail)
        if profile["api_key"].encode() in frozen:
            raise RuntimeError("Refusing to persist credentials")
        (args.output / "local-detail.pickle").write_bytes(frozen)
        metadata["new_tokens"] = tracer.total_tokens
        metadata["cumulative_tokens"] = detail.total_tokens
        save("model-run.json", metadata)
        registry = render_case(detail, args.output / "delivery", None, metadata)
        save("acceptance.json", registry)
        print(f"Recovery: {registry['status']}; files={len(registry['items'])}", flush=True)
    finally:
        beat.cancel()
        await asyncio.gather(beat, return_exceptions=True)
        await llm.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print(f"Review recovery stopped: {type(exc).__name__}", file=sys.stderr)
        sys.exit(1)
