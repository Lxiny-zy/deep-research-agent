"""批量评估：对用例集逐条研究 + 打分，输出汇总表。

运行：python -m eval.run_eval
"""

from __future__ import annotations

import asyncio

from deep_research.config import Settings
from deep_research.orchestrator import DeepResearchAgent

from .dataset import CASES, EvalCase
from .judge import EvalScore, Judge


async def _run_case(settings: Settings, judge: Judge, case: EvalCase) -> tuple[str, EvalScore]:
    agent = DeepResearchAgent(settings)
    report = await agent.run(case.query)
    score = await judge.score(case.query, report.markdown, case.notes)
    return case.id, score


async def _amain() -> None:
    settings = Settings()
    settings.validate()
    judge = Judge(settings)

    rows: list[tuple[str, EvalScore]] = []
    for case in CASES:
        print(f"▶ 评估用例：{case.id} …")
        cid, score = await _run_case(settings, judge, case)
        rows.append((cid, score))
        print(
            f"  覆盖 {score.coverage} ｜ 可靠 {score.groundedness} ｜ "
            f"深度 {score.depth} ｜ 可读 {score.coherence} ｜ 均分 {score.average}"
        )

    print("\n=== 汇总 ===")
    print(f"{'用例':<22}{'覆盖':>5}{'可靠':>5}{'深度':>5}{'可读':>5}{'均分':>8}")
    for cid, s in rows:
        print(
            f"{cid:<22}{s.coverage:>5}{s.groundedness:>5}{s.depth:>5}{s.coherence:>5}{s.average:>8}"
        )
    overall = round(sum(s.average for _, s in rows) / len(rows), 2) if rows else 0.0
    print(f"\n总体均分：{overall}/5")


if __name__ == "__main__":
    asyncio.run(_amain())
