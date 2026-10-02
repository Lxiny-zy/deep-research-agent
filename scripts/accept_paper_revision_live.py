"""Continue paper writing from trusted frozen evidence; no repeat paper extraction."""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import logging
import pickle
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.accept_delivery_live import remote_profile, render_case


async def restore_abstract_from_pdf(detail, path: Path) -> None:
    """Add reparsed translation material without invalidating paid evidence snapshots."""
    from deep_research.workbench.attachments import attachments_from_scratch, parse_attachment
    from deep_research.workbench.intake import PAPER_SOURCES_KEY
    from deep_research.workbench.paper_abstract import _candidates, abstract_span

    scratch = detail.orchestration.checkpoint["scratch"]
    raw = await asyncio.to_thread(path.read_bytes)
    content_id = hashlib.sha256(raw).hexdigest()[:24]
    original = next(
        (a for a in attachments_from_scratch(scratch) if a.id == content_id and a.kind == "pdf"),
        None,
    )
    if original is None or original.size != len(raw):
        raise ValueError("PDF bytes do not match this task's frozen attachment")
    parsed = await parse_attachment(raw, original.filename)
    if parsed.truncated:
        raise ValueError("Reparsed PDF is incomplete")
    sources = []
    for source, _parts in _candidates(parsed.sources()):
        if abstract_span(source) is not None:
            sources.append(
                source.model_copy(
                    update={
                        "url": f"https://workspace.invalid/attachments/{content_id}"
                        f"?abstract-reparse={len(sources) + 1}"
                    }
                ).model_dump(mode="json")
            )
    if not sources:
        raise ValueError("Reparsed PDF does not contain a complete abstract")
    scratch[PAPER_SOURCES_KEY] = [*scratch.get(PAPER_SOURCES_KEY, []), *sources]
    print(
        f"Recovered {len(sources)} complete abstract source(s) from matching PDF bytes", flush=True
    )


async def main() -> None:
    from deep_research.agents.base import Blackboard, RunContext
    from deep_research.agents.researcher import Researcher
    from deep_research.config import Settings
    from deep_research.llm import LLM
    from deep_research.models import ResearchResult
    from deep_research.observability import Tracer
    from deep_research.persistence.repository import RunDetail
    from deep_research.prompting import load_global_rules
    from deep_research.workbench.intake import _FixedSources
    from deep_research.workbench.paper_evidence import current_findings, merge_findings
    from deep_research.workbench.reader import paper_sources
    from deep_research.workbench.writers import PaperReader

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authorized-ssh", required=True)
    parser.add_argument("--detail", type=Path, required=True, help="Trusted local RunDetail pickle")
    parser.add_argument(
        "--supplement", type=Path, help="Trusted prior run; only same-source evidence is admitted"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--abstract-pdf",
        type=Path,
        help="Recover abstract from the same PDF after a parser fix; retain old evidence sources",
    )
    parser.add_argument(
        "--patch-final",
        action="store_true",
        help="Repair only rejected paragraphs in the saved final report",
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    logging.disable(logging.CRITICAL)
    detail = pickle.loads(args.detail.read_bytes())
    if not isinstance(detail, RunDetail) or detail.orchestration is None:
        raise ValueError("A trusted completed local run snapshot is required")
    if args.abstract_pdf:
        if args.patch_final:
            raise ValueError("Changing abstract inputs requires a fresh report review")
        await restore_abstract_from_pdf(detail, args.abstract_pdf)
    profile = remote_profile(args.authorized_ssh)
    settings = Settings(
        llm_api_key=profile["api_key"],
        llm_base_url=profile["base_url"],
        llm_model=profile["model"],
        quality=profile.get("quality", {}),
        request_timeout=180,
    )
    tracer = Tracer()
    tracer.cache_scope = "paper-revision:" + detail.id
    calls = []

    def save(name, value):
        text = json.dumps(value, ensure_ascii=False, indent=2)
        if profile["api_key"] in text:
            raise RuntimeError("Credential cannot be persisted")
        (args.output / name).write_text(text, encoding="utf-8")

    class CapturingLLM(LLM):
        async def _complete_once(self, system, user, temperature):
            text = await super()._complete_once(system, user, temperature)
            calls.append(
                {"kind": "structured", "input_chars": len(system) + len(user), "output": text}
            )
            save(f"response-{len(calls)}.json", calls[-1])
            return text

        async def stream(self, system, user, **kwargs):
            chunks = []
            async for chunk in super().stream(system, user, **kwargs):
                chunks.append(chunk)
                yield chunk
            calls.append(
                {
                    "kind": "writer",
                    "input_chars": len(system) + len(user),
                    "output": "".join(chunks),
                }
            )
            save(f"response-{len(calls)}.json", calls[-1])

    llm = CapturingLLM.from_params(
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
    researcher = Researcher()
    researcher.settings = settings
    candidates = [f for result in detail.results for f in result.findings]
    if args.supplement:
        prior = pickle.loads(args.supplement.read_bytes())
        if not isinstance(prior, RunDetail):
            raise ValueError("Expected trusted supplementary RunDetail")
        candidates = merge_findings(
            candidates, [f for result in prior.results for f in result.findings]
        )
    admitted = await current_findings(candidates, paper_sources(detail), researcher)
    bb = Blackboard(
        query=detail.query,
        results=[ResearchResult(sub_question="同一论文已核验材料复用", findings=admitted)],
        scratch=copy.deepcopy(detail.orchestration.checkpoint["scratch"]),
    )
    if args.patch_final:
        if args.supplement:
            raise ValueError("A bound paragraph repair cannot change its source evidence")
        if [f.model_dump() for f in admitted] != [
            f.model_dump() for r in detail.results for f in r.findings
        ]:
            raise ValueError("Saved evidence no longer matches the current paper")
        bb.results = copy.deepcopy(detail.results)
    print(f"Reusing {len(admitted)} verified findings; no extraction call", flush=True)
    ctx = RunContext(
        llm=llm,
        search_tool=_FixedSources([]),
        tracer=tracer,
        settings=settings,
        global_rules=load_global_rules(),
    )

    def progress(event):
        if event.stage != "LLM" and event.type in {"start", "done", "info", "error"}:
            print(
                f"{event.elapsed:.0f}s {event.message[:200]}".replace(
                    profile["api_key"], "[REDACTED]"
                ),
                flush=True,
            )

    tracer.subscribe(progress)

    async def heartbeat():
        while True:
            await asyncio.sleep(30)
            print(f"Still running; recorded model tokens={tracer.total_tokens}", flush=True)

    beat = asyncio.create_task(heartbeat())
    try:
        if args.patch_final:
            from deep_research.report.validation import validate_body
            from deep_research.workbench.paper_abstract import abstract_section_support
            from deep_research.workbench.prose_edit import repair_paragraphs
            from deep_research.workbench.prose_review import (
                PROSE_REVIEW_KEY,
                reviewer_for_report,
                stored_review,
            )
            from deep_research.workbench.scholarly import uncited_sections_for

            old = stored_review(bb.scratch)
            checker = reviewer_for_report(
                llm,
                bb.query,
                bb.results,
                detail.report.citations,
                bb.scratch,
                llm.input_capacity_chars,
            )
            if checker is None or not checker.prime(detail.report.markdown, old):
                raise ValueError("Saved final review does not match the report")
            patched = await repair_paragraphs(llm, checker, detail.report.markdown, old)
            if patched is None:
                raise ValueError("This result requires a structural rewrite, not paragraph edits")
            check = validate_body(
                patched,
                bb.results,
                {u: i for i, u in enumerate(detail.report.citations, 1)},
                fallback=False,
                uncited_sections=uncited_sections_for(bb.scratch),
                section_support=abstract_section_support(bb.scratch),
            )
            save(
                "patch-check.json", {"issues": list(check.issues), "problems": list(check.problems)}
            )
            if check.issues:
                raise ValueError("Patched text did not pass mechanical validation")
            audit = await checker.review(patched)
            audit.update(mechanically_finalized=True, body_replaced=False)
            bb.report = detail.report.model_copy(update={"markdown": patched})
            bb.scratch[PROSE_REVIEW_KEY] = audit
            extras = bb.scratch["workbench"]["extras"]
            extras[PROSE_REVIEW_KEY] = audit
            previous = extras.get("revision", {})
            attempt = previous.get("attempts", 0) + 1
            extras["revision"] = {
                **previous,
                "attempts": attempt,
                "chosen": attempt,
                "remaining": list(audit["issues"]),
                "method": "paragraph_repair",
                "history": [
                    *previous.get("history", []),
                    {"attempt": attempt, "hard": len(audit["issues"])},
                ],
            }
        else:
            await PaperReader().step(bb, ctx)
        detail.report, detail.results = bb.report, bb.results
        detail.orchestration.checkpoint["scratch"] = bb.scratch
        detail.events = [*detail.events, *tracer.events]
        frozen = pickle.dumps(detail)
        if profile["api_key"].encode() in frozen:
            raise RuntimeError("Credential cannot be persisted")
        (args.output / "local-detail.pickle").write_bytes(frozen)
        record = render_case(
            detail,
            args.output / "delivery",
            None,
            {
                "mode": "writer and support review only; prior same-paper evidence reused",
                "new_tokens": tracer.total_tokens,
                "findings": len(admitted),
            },
        )
        save("acceptance.json", record)
        print(
            f"Revision finished: {record['status']}; tokens={tracer.total_tokens}; "
            f"files={len(record['items'])}",
            flush=True,
        )
    finally:
        beat.cancel()
        await asyncio.gather(beat, return_exceptions=True)
        save(
            "events.json",
            [
                e.model_dump(mode="json")
                for e in tracer.events
                if not (e.data or {}).get("reasoning_delta")
            ],
        )
        await llm.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print(f"Paper revision stopped: {type(exc).__name__}", file=sys.stderr)
        sys.exit(1)
