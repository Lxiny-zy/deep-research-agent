"""Recheck and locally repair a saved answer with explicit necessity/suggestion calibration."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def main() -> None:
    from deep_research.llm import LLM
    from deep_research.models import Finding, ResearchResult
    from deep_research.observability import Tracer
    from deep_research.report.validation import validate_body
    from deep_research.workbench.prose_edit import repair_paragraphs
    from deep_research.workbench.prose_review import ProseReviewer
    from deep_research.workbench.quality import coerce_policy
    from deep_research.workbench.support import SupportReviewer, SupportUnit, evidence_records
    from scripts.accept_delivery_live import remote_profile

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authorized-ssh", required=True)
    parser.add_argument("--answer", required=True, type=Path)
    parser.add_argument("--history", required=True, type=Path)
    parser.add_argument("--question", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    logging.disable(logging.CRITICAL)
    original_bytes = args.answer.read_bytes()
    original = json.loads(original_bytes)
    findings = [Finding.model_validate(f) for f in original["findings"]]
    mapping = {url: i for i, url in enumerate(original["citations"], 1)}
    results = [ResearchResult(sub_question=args.question, findings=findings)]
    context = args.history.read_text(encoding="utf-8") + "\n本轮问题：" + args.question
    profile, tracer = remote_profile(args.authorized_ssh), Tracer()
    calls = []

    def save(name: str, value: object) -> None:
        text = json.dumps(value, ensure_ascii=False, indent=2)
        if profile["api_key"] in text:
            raise RuntimeError("Refusing to persist a credential")
        (args.output / name).write_text(text, encoding="utf-8")

    class Capture(LLM):
        async def parse(self, system, user, schema, **kwargs):
            number, started = len(calls) + 1, time.monotonic()
            record = {"schema": schema.__name__, "input_chars": len(system) + len(user)}
            calls.append(record)
            result = await super().parse(system, user, schema, **kwargs)
            record.update(seconds=time.monotonic() - started, output=result.model_dump(mode="json"))
            save(f"call-{number}.json", record)
            return result

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
    tracer.cache_scope = "qa-modality-acceptance"

    async def heartbeat():
        while True:
            await asyncio.sleep(30)
            print(
                f"QA modality acceptance: calls={len(calls)}; tokens={tracer.total_tokens}",
                flush=True,
            )

    beat, started = asyncio.create_task(heartbeat()), time.monotonic()
    try:
        evidence = evidence_records(results, mapping)
        last = original["answer"].split("\n\n")[-1]
        calibration = [
            SupportUnit("original-necessity", last, kind="prose", citations=list(mapping.values())),
            SupportUnit(
                "optional-evaluation",
                "建议在新的噪声条件下评估泛化表现，并核查是否需要调整模型或训练流程。",
                kind="prose",
            ),
            SupportUnit(
                "reported-retraining",
                "真实重建实验用真实掩膜在 CAVE 与 KAIST 上联合重训 DAUHST-3stg，"
                "并向训练样本注入 11-bit 散粒噪声 [3]。",
                kind="prose",
                citations=[3],
            ),
        ]
        reviewer = SupportReviewer(llm, evidence, 200000, context=context)
        decisions = await reviewer.review(calibration)
        checks = {
            "unsupported_necessity_rejected": decisions[0].verdict in {"unsupported", "uncertain"},
            "optional_evaluation_allowed": decisions[1].verdict == "non_factual",
            "reported_retraining_supported": decisions[2].verdict == "supported",
        }
        save(
            "calibration.json", {"checks": checks, "decisions": [d.model_dump() for d in decisions]}
        )
        if not all(checks.values()):
            raise ValueError("Necessity calibration did not pass")
        print("Necessity/suggestion/source-operation calibration passed", flush=True)
        checker = ProseReviewer.research(llm, results, mapping, 200000, query=context)
        body = original["answer"]
        before_review = await checker.review(body)
        save("initial-review.json", before_review)
        review = before_review
        edits = 0
        for _ in range(coerce_policy(profile.get("quality")).max_revisions):
            if review["status"] == "pass" or not review["can_revise"]:
                break
            updated = await repair_paragraphs(llm, checker, body, review)
            if updated is None:
                break
            body, edits = updated, edits + 1
            review = await checker.review(body)
            save(f"revision-{edits}.json", {"answer": body, "review": review})
        mechanical = validate_body(body, results, mapping, fallback=False)
        save(
            "answer.json",
            {**original, "answer": body, "review": review, "mechanical_issues": mechanical.issues},
        )
        assert args.answer.read_bytes() == original_bytes
        save(
            "acceptance.json",
            {
                "model": profile["model"],
                "checks": checks,
                "initial_status": before_review["status"],
                "final_status": review["status"],
                "mechanical_issues": mechanical.issues,
                "local_edits": edits,
                "tokens": tracer.total_tokens,
                "calls": len(calls),
                "seconds": time.monotonic() - started,
                "research_calls": 0,
                "original_unchanged": True,
            },
        )
        print(
            f"Saved QA repair: {review['status']}; mechanical={mechanical.issues}; edits={edits}",
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
        print(f"QA modality acceptance stopped: {type(exc).__name__}", file=sys.stderr)
        sys.exit(1)
