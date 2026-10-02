"""Check observed statistical-scope failures against a trusted frozen local run."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import pickle
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def main() -> None:
    from deep_research.config import Settings
    from deep_research.llm import LLM
    from deep_research.observability import Tracer
    from deep_research.workbench.analysis_review import count_scope_issues
    from deep_research.workbench.prose_review import reviewer_for_report
    from deep_research.workbench.support import SupportUnit
    from scripts.accept_delivery_live import remote_profile

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authorized-ssh", required=True)
    parser.add_argument("--detail", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    raw = args.detail.read_bytes()
    detail = pickle.loads(raw)  # Only locally created acceptance snapshots.
    scratch = detail.orchestration.checkpoint["scratch"]
    original = next(
        p for p in detail.report.markdown.split("\n\n") if "其余变量及其分组均为 n=150" in p
    )
    fixed = original.replace(
        "其余变量及其分组均为 n=150",
        "其余变量的有效总样本量均为 n=150，各自的每个 species 组 n=50",
    )
    pair = next(row for row in scratch["analysis"]["correlations"] if row["n"] == 149)
    cases = [
        ("original_group_error", original, "unsupported"),
        ("corrected_group_scope", fixed, "supported"),
        (
            "wrong_pair_scope",
            f"{pair['a']} 与 {pair['b']} 的 Pearson 相关有效样本量为 n=150。",
            "unsupported",
        ),
        (
            "correct_pair_scope",
            f"{pair['a']} 与 {pair['b']} 的 Pearson 相关有效样本量为 n={pair['n']}。",
            "supported",
        ),
    ]
    args.output.mkdir(exist_ok=False, parents=True)
    profile = remote_profile(args.authorized_ssh)
    tracer = Tracer()
    llm = LLM.from_params(
        tracer,
        api_key=profile["api_key"],
        base_url=profile["base_url"],
        model=profile["model"],
        timeout=Settings().request_timeout,
        user_agent="deep-research-local-acceptance",
        temperature=profile.get("temperature", 0.3),
        parameter_mode=profile.get("parameter_mode", "temperature"),
        reasoning_effort=profile.get("reasoning_effort", "medium"),
        context_window_tokens=profile.get("context_window_tokens"),
        max_output_tokens=profile.get("max_output_tokens"),
    )
    print("Reviewing original and corrected statistical scopes; no report rewriting", flush=True)
    try:
        checker = reviewer_for_report(llm, detail.query, [], [], scratch, 200000)
        assert checker is not None
        decisions = await checker.reviewer.review(
            [SupportUnit(key, text, citations=[0]) for key, text, _ in cases]
        )
        by_id = {d.unit_id: d for d in decisions}
        record = {
            "source_sha256": hashlib.sha256(raw).hexdigest(),
            "model": profile["model"],
            "tokens": tracer.total_tokens,
            "elapsed": tracer.elapsed,
            "cases": [
                {
                    "id": key,
                    "text": text,
                    "expected": expected,
                    "decision": by_id[key].model_dump(mode="json"),
                    "count_scope_issues": count_scope_issues(text, scratch["analysis"]),
                }
                for key, text, expected in cases
            ],
        }
        record["status"] = (
            "pass" if all(by_id[key].verdict == expected for key, _, expected in cases) else "fail"
        )
        serialized = json.dumps(record, ensure_ascii=False, indent=2)
        if profile["api_key"] in serialized:
            raise RuntimeError("Refusing to persist a credential")
        (args.output / "acceptance.json").write_text(serialized, encoding="utf-8")
        print(f"Scope acceptance: {record['status']}; tokens={tracer.total_tokens}", flush=True)
    finally:
        await llm.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print(f"Scope acceptance stopped: {type(exc).__name__}", file=sys.stderr)
        sys.exit(1)
