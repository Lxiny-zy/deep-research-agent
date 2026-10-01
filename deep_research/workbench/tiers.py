"""研究档位：轻量 / 标准 / 深度，把「花多少力气」变成一个用户可选的旋钮。

档位控制子问题数、反思轮数和每次检索结果数，用户显式参数优先。
token 仅作实际消耗统计，不限制研究或交付。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Tier = Literal["light", "standard", "deep"]


@dataclass(frozen=True)
class TierSpec:
    key: Tier
    title: str
    description: str
    max_sub_questions: int
    max_rounds: int
    results_per_search: int

    def to_public(self) -> dict[str, object]:
        return {
            "key": self.key,
            "title": self.title,
            "description": self.description,
            "max_sub_questions": self.max_sub_questions,
            "max_rounds": self.max_rounds,
            "results_per_search": self.results_per_search,
        }


TIERS: dict[str, TierSpec] = {
    spec.key: spec
    for spec in (
        TierSpec("light", "轻量", "少量子问题、不补洞，适合快速了解", 3, 0, 4),
        TierSpec("standard", "标准", "常规深度，一轮补洞", 5, 1, 5),
        TierSpec("deep", "深度", "更多子问题与两轮补洞，适合系统调研", 8, 2, 8),
    )
}


def tier_overrides(
    tier: str | None,
    *,
    explicit: dict[str, object],
    ceilings: dict[str, int | None],
) -> dict[str, object]:
    """返回档位产生的参数覆盖；用户显式参数不被覆盖，部署上限不被突破。"""
    spec = TIERS.get(tier or "")
    if spec is None:
        return {}
    wanted = {
        "max_sub_questions": spec.max_sub_questions,
        "max_rounds": spec.max_rounds,
        "results_per_search": spec.results_per_search,
    }
    out: dict[str, object] = {}
    for field, value in wanted.items():
        if explicit.get(field) is not None:
            continue
        ceiling = ceilings.get(field)
        out[field] = min(value, ceiling) if isinstance(ceiling, int) and ceiling > 0 else value
    return out


def public_tiers() -> list[dict[str, object]]:
    return [spec.to_public() for spec in TIERS.values()]


__all__ = ["TIERS", "Tier", "TierSpec", "public_tiers", "tier_overrides"]
