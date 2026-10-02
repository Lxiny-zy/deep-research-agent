"""Recover saved failed candidates with exact quote references and labelled units."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import pickle
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.accept_delivery_live import remote_profile


async def main() -> None:
    from deep_research.agents.base import direct_system_prompt
    from deep_research.agents.researcher import SYSTEM, Researcher, source_context
    from deep_research.config import Settings
    from deep_research.guardrails import report_eligible
    from deep_research.llm import LLM
    from deep_research.models import ExtractedFindingList
    from deep_research.observability import Tracer
    from deep_research.prompting import PrefixPrompt
    from deep_research.workbench.extraction import check_extraction

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authorized-ssh", required=True)
    parser.add_argument("--detail", required=True, type=Path, help="Trusted local RunDetail pickle")
    parser.add_argument("--candidate-ids", required=True, nargs="+")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    logging.disable(logging.CRITICAL)
    detail = pickle.loads(args.detail.read_bytes())
    audits = [r.extraction_audit for r in detail.results if r.extraction_audit]
    if len(audits) != 1:
        raise ValueError("This runner expects a single saved extraction audit")
    old = audits[0]
    candidates = [c for c in old.candidates if c.id in args.candidate_ids]
    if {c.id for c in candidates} != set(args.candidate_ids):
        raise ValueError("Requested candidate IDs are absent")
    wanted = {c.original.source_url for c in candidates}
    sources = [s.model_copy(deep=True) for s in old.sources if s.url in wanted]
    initial = ExtractedFindingList(findings=[c.original for c in candidates])
    profile = remote_profile(args.authorized_ssh)

    def save(name, value):
        encoded = json.dumps(value, ensure_ascii=False, indent=2)
        if profile["api_key"] in encoded:
            raise RuntimeError("Credential cannot be persisted")
        (args.output / name).write_text(encoded, encoding="utf-8")

    calls = []

    class Capture(LLM):
        async def _stream_once(self, system, user, *, temperature=0.4):
            number = len(calls) + 1
            record = {
                "call": number,
                "input_chars": len(system) + len(user),
                "status": "interrupted",
            }
            calls.append(record)
            chunks = []
            try:
                async for chunk in super()._stream_once(system, user, temperature=temperature):
                    chunks.append(chunk)
                    yield chunk
                record["status"] = "complete"
            finally:
                save(f"response-{number}.json", {**record, "output": "".join(chunks)})

    tracer = Tracer()
    tracer.cache_scope = "quote-span-recovery:" + detail.id
    llm = Capture.from_params(
        tracer,
        api_key=profile["api_key"],
        base_url=profile["base_url"],
        model=profile["model"],
        timeout=180,
        user_agent="deep-research-local-acceptance",
        temperature=profile.get("temperature", 0.3),
        parameter_mode=profile.get("parameter_mode", "temperature"),
        reasoning_effort=profile.get("reasoning_effort", "medium"),
        context_window_tokens=profile.get("context_window_tokens"),
        max_output_tokens=profile.get("max_output_tokens"),
    )
    researcher = Researcher(
        llm=llm, tracer=tracer, settings=Settings(quality={"extraction_max_revisions": 1})
    )
    researcher.raise_extraction_errors = True
    save(
        "input.json",
        {
            "source_run": detail.id,
            "candidate_ids": [c.id for c in candidates],
            "initial": initial.model_dump(mode="json"),
            "sources": [s.model_dump(mode="json") for s in sources],
            "scope": "original candidates and unchanged frozen sources; no extraction call",
        },
    )

    async def heartbeat():
        while True:
            await asyncio.sleep(30)
            print(f"Quote recovery running; recorded tokens={tracer.total_tokens}", flush=True)

    beat = asyncio.create_task(heartbeat())
    try:
        print(f"Rechecking {len(candidates)} saved candidates with frozen source bytes", flush=True)
        result = await check_extraction(
            researcher,
            initial,
            sources,
            old.question,
            direct_system_prompt(SYSTEM),
            PrefixPrompt("给定来源：\n" + source_context(sources), "\n子问题：" + old.question),
        )
        save("result.json", result.model_dump(mode="json"))
        passed = len(result.findings) == len(candidates) and all(
            report_eligible(f) for f in result.findings
        )
        save(
            "acceptance.json",
            {
                "status": "pass" if passed else "fail",
                "tokens": tracer.total_tokens,
                "calls": len(calls),
                "admitted": sum(report_eligible(f) for f in result.findings),
                "scope": "targeted recovery; not a new whole-paper extraction test",
            },
        )
        print(
            f"Quote recovery {'passed' if passed else 'failed'}; tokens={tracer.total_tokens}",
            flush=True,
        )
        if not passed:
            raise ValueError("Some candidates remain unsupported")
    finally:
        beat.cancel()
        await asyncio.gather(beat, return_exceptions=True)
        save("usage.json", {"tokens": tracer.total_tokens, "calls": calls})
        await llm.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print(f"Quote recovery stopped: {type(exc).__name__}", file=sys.stderr)
        sys.exit(1)
