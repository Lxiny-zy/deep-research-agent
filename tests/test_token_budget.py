"""TokenBudget 纯值对象单测：charge / update / remaining / exhausted / 不限语义。

TokenBudget 与 Tracer 解耦——这里直接验证它作为策略值对象的行为，无需引擎。
"""

from __future__ import annotations

from deep_research.token_budget import TokenBudget


def test_unlimited_budget_never_exhausts() -> None:
    b = TokenBudget(max_tokens=None)
    b.charge(10_000_000)
    assert b.exhausted is False
    assert b.remaining is None


def test_charge_accumulates_and_exhausts() -> None:
    b = TokenBudget(max_tokens=100)
    b.charge(40)
    assert b.consumed == 40
    assert b.remaining == 60
    assert b.exhausted is False
    b.charge(70)
    assert b.consumed == 110
    assert b.remaining == 0  # 不为负
    assert b.exhausted is True


def test_update_syncs_monotonically_without_double_count() -> None:
    """update 从 Tracer 累计同步，取 max 不回退（避免与 charge 双重计数）。"""
    b = TokenBudget(max_tokens=100)
    b.update(30)
    assert b.consumed == 30
    b.update(20)  # 旧值更小：不回退
    assert b.consumed == 30
    b.update(120)
    assert b.consumed == 120
    assert b.exhausted is True


def test_charge_ignores_negative() -> None:
    b = TokenBudget(max_tokens=100)
    b.charge(-5)
    assert b.consumed == 0
