"""Reflector：评估证据是否充分，决定是否继续补洞。"""

from __future__ import annotations

from ..config import Settings
from ..llm import LLM
from ..models import Reflection, ResearchResult
from ..observability import Tracer

SYSTEM = (
    "你是研究质检员。评估现有发现是否足以全面、可靠地回答原始问题。"
    "若不足，指出缺口并提出最多 3 个新的子问题以补足；若已充分，明确标记 is_sufficient=true。"
)


class Reflector:
    def __init__(self, llm: LLM, tracer: Tracer, settings: Settings) -> None:
        self.llm = llm
        self.tracer = tracer
        self.settings = settings

    async def run(self, query: str, results: list[ResearchResult]) -> Reflection:
        self.tracer.emit("REFLECTOR", "start", "评估证据是否充分…")
        reflection = await self.llm.parse(
            SYSTEM, f"原始问题：{query}\n\n现有发现：\n{_digest(results)}", Reflection
        )
        if reflection.is_sufficient:
            self.tracer.emit("REFLECTOR", "info", "证据充分，进入综合")
        else:
            reflection.new_sub_questions = reflection.new_sub_questions[:3]
            self.tracer.emit(
                "REFLECTOR",
                "info",
                f"仍有缺口，新增 {len(reflection.new_sub_questions)} 个子问题",
                data={"gaps": reflection.gaps, "new_sub_questions": reflection.new_sub_questions},
            )
        return reflection


def _digest(results: list[ResearchResult], limit: int = 40) -> str:
    lines = [f"- ({r.sub_question}) {f.statement}" for r in results for f in r.findings]
    return "\n".join(lines[:limit]) or "（暂无发现）"
