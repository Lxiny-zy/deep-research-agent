"""核验输出被截断时对半拆批重试，不把整批记为不可修订的失败。"""

import json

from deep_research.llm import ModelOutputTruncated
from deep_research.workbench.prose_review import can_revise
from deep_research.workbench.support import (
    SupportDecision,
    SupportDecisions,
    SupportReviewer,
    SupportUnit,
)


class SplitOnlyVerifier:
    """模拟第六轮线上同行评审：超过 2 段一批时输出超过渠道上限。"""

    def __init__(self, limit: int | None = 2) -> None:
        self.limit = limit
        self.batches: list[int] = []

    async def parse(self, system, user, schema, **kwargs):
        units = json.loads(user)["units"]
        self.batches.append(len(units))
        if self.limit is None or len(units) > self.limit:
            raise ModelOutputTruncated(100)
        return SupportDecisions(
            decisions=[
                SupportDecision(unit_id=unit["id"], verdict="non_factual", reason="标题")
                for unit in units
            ]
        )


def _units(count: int) -> list[SupportUnit]:
    return [
        SupportUnit(id=f"u{index}", text=f"小节 {index}", kind="summary")
        for index in range(count)
    ]


async def test_truncated_batches_are_split_until_the_output_fits():
    llm = SplitOnlyVerifier(limit=2)
    reviewer = SupportReviewer(llm, [], 200000)
    output = await reviewer._judge(_units(8))
    assert all(decision.verdict == "non_factual" for decision in output.values())
    assert sorted(output) == [f"u{index}" for index in range(8)]
    assert llm.batches == [8, 4, 2, 2, 4, 2, 2]
    assert can_revise(list(output.values()))


async def test_splitting_stops_after_three_levels_and_keeps_the_failure_visible():
    llm = SplitOnlyVerifier(limit=None)
    reviewer = SupportReviewer(llm, [], 200000)
    output = await reviewer._judge(_units(16))
    # 16 → 8 → 4 → 2：三层后仍截断，如实记为核验失败
    assert len(llm.batches) == 1 + 2 + 4 + 8
    assert all(decision.reason.startswith("核验调用失败") for decision in output.values())
    assert not can_revise(list(output.values()))


async def test_per_unit_checks_run_concurrently_and_keep_their_results(monkeypatch):
    import asyncio

    reviewer = SupportReviewer(SplitOnlyVerifier(), [], 200000)
    active = peak = 0
    calls: list[str] = []

    async def slow_check(unit, key, checker=None):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        calls.append(unit.id)
        return None

    async def no_formula(unit, key):
        return None

    monkeypatch.setattr(reviewer, "_check_fulltext", slow_check)
    monkeypatch.setattr(reviewer, "_check_formula", no_formula)
    await reviewer._prefetch_checks(_units(8), set(), None)
    assert sorted(calls) == [f"u{index}" for index in range(8)]
    assert 1 < peak <= 4


async def test_prefetch_failures_keep_their_original_type(monkeypatch):
    from deep_research.persistence.repository import LeaseLostError

    reviewer = SupportReviewer(SplitOnlyVerifier(), [], 200000)

    async def lost(unit, key, checker=None):
        raise LeaseLostError("lease lost")

    monkeypatch.setattr(reviewer, "_check_fulltext", lost)
    import pytest

    with pytest.raises(LeaseLostError):
        await reviewer._prefetch_checks(_units(3), set(), None)
