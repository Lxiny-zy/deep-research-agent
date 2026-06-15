"""LLM-as-judge：从覆盖度/可靠性/深度/可读性四维给报告打分。

这是本项目的「工程成熟度」差异点：不仅能产出报告，还能量化报告质量、做回归评估。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from deep_research.config import Settings
from deep_research.llm import LLM
from deep_research.observability import Tracer


class EvalScore(BaseModel):
    coverage: int = Field(..., ge=1, le=5, description="是否覆盖问题关键面")
    groundedness: int = Field(..., ge=1, le=5, description="论断是否有引用支撑、可溯源")
    depth: int = Field(..., ge=1, le=5, description="是否有深度分析而非罗列")
    coherence: int = Field(..., ge=1, le=5, description="结构与可读性")
    justification: str = Field("", description="简要评语")

    @property
    def average(self) -> float:
        return round((self.coverage + self.groundedness + self.depth + self.coherence) / 4, 2)


SYSTEM = (
    "你是严格的研究报告评审。针对【研究问题】与【报告】，从四个维度各打 1~5 分："
    "coverage(覆盖度)、groundedness(论断是否有引用支撑)、depth(深度)、coherence(结构与可读性)，"
    "并给出简短评语。务必严格——不要因为篇幅长就给高分；缺引用、空泛、跑题都要扣分。"
)


class Judge:
    def __init__(self, settings: Settings) -> None:
        # judge 用独立 Tracer，不污染被评估 agent 的统计
        self.llm = LLM(settings, Tracer())

    async def score(self, query: str, report_markdown: str, notes: str = "") -> EvalScore:
        user = (
            f"研究问题：{query}\n\n参考要点：{notes or '（无）'}\n\n待评报告：\n{report_markdown}"
        )
        return await self.llm.parse(SYSTEM, user, EvalScore, temperature=0.0)
