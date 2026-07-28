"""各 Agent 的结构化数据模型（Pydantic v2）。

把每个 Agent 的输入/输出都建模成 schema，是本项目「可靠性」的核心：
LLM 被强制产出符合 schema 的 JSON，下游可直接消费而无需脆弱的字符串解析。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SubQuestion(BaseModel):
    question: str = Field(..., description="一个可独立检索的子问题")
    rationale: str = Field("", description="为什么需要研究它")
    depends_on: list[int] = Field(
        default_factory=list,
        description="依赖的前驱子问题序号（本计划内 0 起始下标）；为空表示无依赖、可立即并行检索",
    )


class ResearchPlan(BaseModel):
    interpretation: str = Field(..., description="对原始问题的理解与边界界定")
    sub_questions: list[SubQuestion] = Field(default_factory=list)


class Source(BaseModel):
    title: str = ""
    url: str
    content: str = ""


class EvidenceVerification(BaseModel):
    """Program-produced evidence status for one finding.

    The LLM may propose an evidence quote, but only the deterministic verifier may
    promote the status to ``verified``.
    """

    status: Literal["unverified", "verified"] = "unverified"
    method: Literal["none", "normalized_quote"] = "none"
    source_content_hash: str = ""
    source_title: str = ""
    evidence_context: str = Field(
        "",
        max_length=1200,
        description="程序从检索快照中截取的证据上下文，不由模型生成",
    )
    reason: str = ""
    semantic_status: Literal["not_checked", "supported", "unsupported", "uncertain"] = "not_checked"
    semantic_confidence: float = Field(0.0, ge=0.0, le=1.0)
    semantic_reason: str = ""
    claim_id: str = ""
    consistency_status: Literal["not_checked", "clear", "conflicted"] = "not_checked"
    contradicts_claim_ids: list[str] = Field(default_factory=list)
    contradiction_reason: str = ""
    corroboration_status: Literal["not_checked", "single_source", "corroborated", "disputed"] = (
        "not_checked"
    )
    independent_source_count: int = Field(0, ge=0)
    corroborates_claim_ids: list[str] = Field(default_factory=list)
    corroboration_reason: str = ""


class Finding(BaseModel):
    statement: str = Field(..., description="一条具体、自洽的事实/发现")
    source_url: str = Field(..., description="该发现的出处 URL（必须来自给定来源）")
    evidence_quote: str = Field(
        "",
        max_length=500,
        description="支持该发现的来源原文短句；必须逐字来自 source_url 对应内容",
    )
    confidence: float = Field(0.7, ge=0.0, le=1.0, description="置信度 0~1")
    verification: EvidenceVerification = Field(default_factory=EvidenceVerification)


class FindingList(BaseModel):
    findings: list[Finding] = Field(default_factory=list)


class ResearchResult(BaseModel):
    sub_question: str
    findings: list[Finding] = Field(default_factory=list)


class Reflection(BaseModel):
    is_sufficient: bool = Field(..., description="现有证据是否足以回答原问题")
    gaps: list[str] = Field(default_factory=list, description="仍缺失的信息点")
    new_sub_questions: list[str] = Field(default_factory=list, description="为补洞而新增的子问题")


class Report(BaseModel):
    query: str
    markdown: str
    citations: list[str] = Field(default_factory=list, description="按 [n] 顺序排列的来源 URL")
