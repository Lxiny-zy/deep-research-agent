"""Check repaired diagram relations and editorial edits without repeating research."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import pickle
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.accept_delivery_live import remote_profile, render_case


async def main() -> None:
    from deep_research.llm import LLM
    from deep_research.observability import Tracer
    from deep_research.report.validation import validate_body
    from deep_research.workbench.figure_review import FIGURE_REVIEW_KEY, FIGURE_RULES, review_figure
    from deep_research.workbench.figures import ConceptFigure
    from deep_research.workbench.paper_abstract import abstract_section_support
    from deep_research.workbench.prose_review import (
        PROSE_REVIEW_KEY,
        reviewer_for_report,
        stored_review,
    )
    from deep_research.workbench.scholarly import uncited_sections_for
    from deep_research.workbench.support import SupportReviewer, evidence_records

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authorized-ssh", required=True)
    parser.add_argument("--detail", type=Path, required=True)
    parser.add_argument("--figure", type=Path, required=True)
    parser.add_argument("--markdown", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    logging.disable(logging.CRITICAL)
    data = pickle.loads(args.detail.read_bytes())
    profile = remote_profile(args.authorized_ssh)
    tracer = Tracer()
    llm = LLM.from_params(
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
    tracer.cache_scope = "figure-review:" + data.id
    scratch = data.orchestration.checkpoint["scratch"]

    def save(name, value):
        text = json.dumps(value, ensure_ascii=False, indent=2)
        if profile["api_key"] in text:
            raise RuntimeError("Credential cannot be persisted")
        (args.output / name).write_text(text, encoding="utf-8")

    async def heartbeat():
        while True:
            await asyncio.sleep(30)
            print(f"Still checking; tokens={tracer.total_tokens}", flush=True)

    beat = asyncio.create_task(heartbeat())
    try:
        evidence = evidence_records(
            data.results, {u: i for i, u in enumerate(data.report.citations, 1)}
        )
        checker = SupportReviewer(
            llm, evidence, llm.input_capacity_chars, context=data.query, system_rules=FIGURE_RULES
        )
        original = ConceptFigure.model_validate(scratch["workbench"]["extras"]["concept_figure"])
        print("Checking original diagram", flush=True)
        before = await review_figure(original, checker)
        save("before-figure.json", before)
        corrected = ConceptFigure.model_validate_json(args.figure.read_text(encoding="utf-8"))
        print("Checking corrected diagram", flush=True)
        after = await review_figure(corrected, checker)
        save("after-figure.json", after)
        if after["status"] != "pass":
            raise ValueError("Corrected figure did not pass evidence review")
        prose = reviewer_for_report(
            llm, data.query, data.results, data.report.citations, scratch, llm.input_capacity_chars
        )
        if prose is None or not prose.prime(data.report.markdown, stored_review(scratch)):
            raise ValueError("Stored prose review is not bound to this draft")
        markdown = args.markdown.read_text(encoding="utf-8")
        check = validate_body(
            markdown,
            data.results,
            {u: i for i, u in enumerate(data.report.citations, 1)},
            fallback=False,
            uncited_sections=uncited_sections_for(scratch),
            section_support=abstract_section_support(scratch),
        )
        save(
            "editorial-mechanical.json",
            {"issues": list(check.issues), "problems": list(check.problems)},
        )
        if check.issues:
            raise ValueError("Editorial revision failed mechanical checks")
        print("Checking edited prose", flush=True)
        reviewed = await prose.review(markdown)
        save("editorial-review.json", reviewed)
        if reviewed["status"] != "pass":
            raise ValueError("Editorial revision failed source support")
        reviewed.update(mechanically_finalized=True, body_replaced=False)
        data.report = data.report.model_copy(update={"markdown": markdown})
        scratch[PROSE_REVIEW_KEY] = reviewed
        extras = scratch["workbench"]["extras"]
        extras[PROSE_REVIEW_KEY] = reviewed
        extras["concept_figure"] = corrected.model_dump(mode="json")
        extras[FIGURE_REVIEW_KEY] = after
        frozen = pickle.dumps(data)
        if profile["api_key"].encode() in frozen:
            raise RuntimeError("Credential cannot be persisted")
        (args.output / "local-detail.pickle").write_bytes(frozen)
        record = render_case(
            data,
            args.output / "delivery",
            None,
            {
                "mode": "diagram and changed prose reviewed; original research not repeated",
                "new_tokens": tracer.total_tokens,
            },
        )
        save("acceptance.json", record)
        print(
            f"Final diagram/report: {record['status']}; files={len(record['items'])}; "
            f"tokens={tracer.total_tokens}",
            flush=True,
        )
    finally:
        beat.cancel()
        await asyncio.gather(beat, return_exceptions=True)
        await llm.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print(f"Diagram acceptance stopped: {type(exc).__name__}", file=sys.stderr)
        sys.exit(1)
