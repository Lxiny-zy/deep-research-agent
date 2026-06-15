"""评估用例集：每条给出问题与考察要点（供 judge 参考）。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class EvalCase:
    id: str
    query: str
    notes: str = ""  # 理想答案应覆盖的要点


CASES: list[EvalCase] = [
    EvalCase(
        "agent-frameworks",
        "2026 年主流 AI Agent 框架有哪些？各自的设计取舍是什么？",
        "应覆盖 LangGraph / AutoGen / CrewAI / LlamaIndex 等，并比较编排模型、状态管理、生态。",
    ),
    EvalCase(
        "rag-vs-longctx",
        "在长上下文模型变强的 2026 年，RAG 还有必要吗？",
        "应讨论成本、时效性、可溯源、私有数据等权衡，并给出场景化结论。",
    ),
    EvalCase(
        "embodied-ai",
        "具身智能领域近一年有哪些值得关注的技术进展与代表团队？",
        "应覆盖 VLA 模型、数据采集、仿真到真实迁移、代表公司。",
    ),
]
