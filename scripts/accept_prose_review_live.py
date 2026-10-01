"""Review saved real outputs with the authorized model; never repeat their research steps."""

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
    from deep_research.guardrails import report_eligible
    from deep_research.llm import LLM
    from deep_research.models import ResearchResult
    from deep_research.observability import Tracer
    from deep_research.workbench.prose_review import ProseReviewer, reviewer_for_report

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authorized-ssh", required=True)
    parser.add_argument("--paper-detail", type=Path, help="Trusted local run pickle")
    parser.add_argument("--data-detail", required=True, type=Path, help="Trusted local run pickle")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--only-data", action="store_true")
    parser.add_argument("--publish-reviewed-copy", action="store_true")
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    args.output.mkdir(parents=True, exist_ok=True)
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

    def save(name, value):
        text = json.dumps(value, ensure_ascii=False, indent=2)
        if profile["api_key"] in text:
            raise RuntimeError("Refusing to persist a credential")
        (args.output / name).write_text(text, encoding="utf-8")

    try:
        # These files were produced locally by the acceptance runner. Pickle
        # inputs from uploads, remote URLs or other users must never be loaded.
        data = pickle.loads(args.data_detail.read_bytes())
        supported = unsupported = None
        print(f"Prose review acceptance started: {profile['model']}", flush=True)
        if not args.only_data:
            if args.paper_detail is None:
                raise ValueError("--paper-detail required unless --only-data")
            paper = pickle.loads(args.paper_detail.read_bytes())
            finding = next(
                f for result in paper.results for f in result.findings if report_eligible(f)
            )
            results = [ResearchResult(sub_question="已验证原文", findings=[finding])]
            review = ProseReviewer.research(
                llm,
                results,
                {finding.source_url: 1},
                200000,
                query="根据原文核对结论，不能扩大适用范围",
            )
            body = "## 结论\n\n" + finding.statement + " [1]。"
            supported = await review.review(body)
            save(
                "supported.json", {"body": body, "review": supported, "tokens": tracer.total_tokens}
            )
            unsupported_body = (
                body + "\n\n这已经证明该方法在所有场景下必然优于其他方法，完全不会产生误差 [1]。"
            )
            unsupported = await review.review(unsupported_body)
            save(
                "unsupported.json",
                {"body": unsupported_body, "review": unsupported, "tokens": tracer.total_tokens},
            )
            print(
                f"Grounded={supported['status']}; overgeneralization={unsupported['status']}",
                flush=True,
            )
            assert supported["status"] == "pass" and unsupported["status"] == "fail"

        checker = reviewer_for_report(
            llm,
            data.query,
            data.results,
            data.report.citations,
            data.orchestration.checkpoint["scratch"],
            200000,
        )
        assert checker is not None
        data_review = await checker.review(data.report.markdown)
        save(
            "data-report.json",
            {"body": data.report.markdown, "review": data_review, "tokens": tracer.total_tokens},
        )
        save(
            "acceptance.json",
            {
                "model": profile["model"],
                "tokens": tracer.total_tokens,
                "scope": "real review only; reuse prior verified research and frozen statistics",
                "grounded": supported["status"] if supported else None,
                "overgeneralization": unsupported["status"] if unsupported else None,
                "data_report": data_review["status"],
            },
        )
        print(f"Data report={data_review['status']}; tokens={tracer.total_tokens}", flush=True)
        if args.publish_reviewed_copy:
            from deep_research.workbench.prose_review import PROSE_REVIEW_KEY, stored_review
            from scripts.accept_delivery_live import render_case

            assert data_review["status"] == "pass", "Unapproved output will not be published"
            scratch = data.orchestration.checkpoint["scratch"]
            previous = stored_review(scratch) or {}
            assert previous.get("mechanically_finalized"), (
                "Expected a previously finalized local result"
            )
            data_review["mechanically_finalized"] = True
            data_review["body_replaced"] = previous.get("body_replaced", False)
            scratch[PROSE_REVIEW_KEY] = data_review
            scratch["workbench"]["extras"][PROSE_REVIEW_KEY] = data_review
            frozen = pickle.dumps(data)
            if profile["api_key"].encode() in frozen:
                raise RuntimeError("Refusing to persist a credential")
            (args.output / "local-detail.pickle").write_bytes(frozen)
            registry = render_case(
                data,
                args.output / "delivery",
                None,
                {
                    "mode": "rechecked prior finalized content; no repeat writing or statistics",
                    "tokens": tracer.total_tokens,
                },
            )
            save("delivery.json", registry)
            print(
                f"Reviewed copy: {registry['status']}; files={len(registry['items'])}",
                flush=True,
            )
    finally:
        await llm.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print(f"Prose acceptance stopped: {type(exc).__name__}", file=sys.stderr)
        sys.exit(1)
