"""各 Agent 的结构化数据模型（Pydantic v2）。

把每个 Agent 的输入/输出都建模成 schema，是本项目「可靠性」的核心：
LLM 被强制产出符合 schema 的 JSON，下游可直接消费而无需脆弱的字符串解析。
"""

from __future__ import annotations

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


class Finding(BaseModel):
    statement: str = Field(..., description="一条具体、自洽的事实/发现")
    source_url: str = Field(..., description="该发现的出处 URL（必须来自给定来源）")
    confidence: float = Field(0.7, ge=0.0, le=1.0, description="置信度 0~1")


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
