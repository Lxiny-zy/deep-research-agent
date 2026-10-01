"""Local semantic-review calibration with labelled synthetic evidence and a real model."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.accept_delivery_live import remote_profile


async def main() -> None:
    from deep_research.llm import LLM
    from deep_research.observability import Tracer
    from deep_research.workbench.support import SupportReviewer, SupportUnit

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authorized-ssh", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
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
    evidence = [
        {
            "id": "correlation",
            "citation": 1,
            "source": "synthetic:correlation",
            "statement": "变量 A 与 B 相关，不能得出因果关系。",
            "quote": "观察到变量 A 与 B 相关；这项观察研究没有证明 A 导致 B。",
        },
        {
            "id": "adoption",
            "citation": 2,
            "source": "synthetic:adoption",
            "statement": "作者采用已有滤波器，没有提出新滤波算法。",
            "quote": "我们使用已有的滤波器进行预处理；本工作未提出新的滤波算法。",
        },
    ]
    units = [
        SupportUnit(
            "supported-correlation", "A 与 B 存在相关关系，不能由此确认因果关系。", citations=[1]
        ),
        SupportUnit("invented-causality", "A 导致 B，因此干预 A 一定能改变 B。", citations=[1]),
        SupportUnit("supported-adoption", "作者使用既有滤波器完成预处理。", citations=[2]),
        SupportUnit("invented-novelty", "本文首次发明了该滤波算法。", citations=[2]),
        SupportUnit("disguised-claim", "相关性足以证明因果关系", kind="concept", citations=[1]),
        SupportUnit("concept", "相关分析", context="统计方法包含相关分析", kind="concept"),
        SupportUnit("open-question", "是否存在因果机制尚待研究？", kind="question"),
    ]
    expected = [
        "supported",
        "unsupported",
        "supported",
        "unsupported",
        "unsupported",
        "non_factual",
        "non_factual",
    ]
    try:
        print(f"Real-model semantic calibration started: {profile['model']}", flush=True)
        decisions = await SupportReviewer(llm, evidence, 200000).review(units)
        record = {
            "model": profile["model"],
            "scope": "labelled synthetic calibration; not research accuracy",
            "tokens": tracer.total_tokens,
            "cases": [
                {
                    "id": d.unit_id,
                    "expected": target,
                    "decision": d.model_dump(mode="json"),
                    "passed": d.verdict == target,
                }
                for d, target in zip(decisions, expected, strict=True)
            ],
        }
        output = json.dumps(record, ensure_ascii=False, indent=2)
        if profile["api_key"] in output:
            raise RuntimeError("Refusing to persist a credential")
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "acceptance.json").write_text(output, encoding="utf-8")
        passed = sum(case["passed"] for case in record["cases"])
        print(f"Calibration: {passed}/{len(units)}; tokens={tracer.total_tokens}", flush=True)
        assert passed == len(units), "Review calibration did not meet expected decisions"
    finally:
        await llm.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print(f"Calibration stopped: {type(exc).__name__}", file=sys.stderr)
        sys.exit(1)
