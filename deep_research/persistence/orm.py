"""SQLAlchemy 2.0 ORM 模型：把一次研究运行的全过程持久化。

表对应 deep_research.models 的 Pydantic 模型 + observability.Event：
  research_run ──┬── sub_question
                 ├── research_result ── finding
                 ├── source
                 ├── report (1:1)
                 └── event（按 seq 单调排序，支持回放）

主键统一用 str(uuid4())（String(36)），JSON 列存依赖/引用列表，
跨 SQLite（测试）与 PostgreSQL（生产）通用。
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class ResearchRun(Base):
    __tablename__ = "research_run"
    __table_args__ = (
        Index("uq_research_run_idempotency_key", "idempotency_key", unique=True),
        Index("ix_research_run_claimable", "status", "claimable_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    query: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending/running/done/error
    # 队列语义（``execution_mode=worker``）：
    #   status=pending 且 claimable_at 非空  → 待领取，**不是孤儿**
    #   status=running 且租约过期            → 执行者崩溃，走恢复路径续跑
    #   status=pending 且 claimable_at 为空  → inline 模式的运行中任务，或首个
    #                                          checkpoint 前崩溃，由恢复扫描判定
    # 没有这一列就无法区分「从未开始」与「刚崩溃」，前者会被恢复扫描误判为 error。
    claimable_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    # 被 worker 领取的累计次数。超过 max_claim_attempts 判定为毒任务并置 error，
    # 避免必然崩溃的任务在 worker 之间无限传递。
    claim_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)

    request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    interpretation: Mapped[str] = mapped_column(Text, default="")
    elapsed: Mapped[float] = mapped_column(Float, default=0.0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    sub_questions: Mapped[list[SubQuestionRow]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="SubQuestionRow.idx"
    )
    results: Mapped[list[ResearchResultRow]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    sources: Mapped[list[SourceRow]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    events: Mapped[list[EventRow]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="EventRow.seq"
    )
    report: Mapped[ReportRow | None] = relationship(
        back_populates="run", cascade="all, delete-orphan", uselist=False
    )
    tags: Mapped[list[RunTagRow]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="RunTagRow.tag"
    )
    orchestration: Mapped[WorkflowRunRow | None] = relationship(
        back_populates="research_run", cascade="all, delete-orphan", uselist=False
    )


class SubQuestionRow(Base):
    __tablename__ = "sub_question"
    __table_args__ = (UniqueConstraint("run_id", "idx", name="uq_subquestion_run_idx"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("research_run.id", ondelete="CASCADE"), index=True
    )
    idx: Mapped[int] = mapped_column(Integer)
    question: Mapped[str] = mapped_column(Text)
    rationale: Mapped[str] = mapped_column(Text, default="")
    depends_on: Mapped[list[int]] = mapped_column(JSON, default=list)
    origin: Mapped[str] = mapped_column(String(16), default="plan")  # plan / reflection
    round: Mapped[int] = mapped_column(Integer, default=0)

    run: Mapped[ResearchRun] = relationship(back_populates="sub_questions")


class ResearchResultRow(Base):
    __tablename__ = "research_result"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("research_run.id", ondelete="CASCADE"), index=True
    )
    sub_question: Mapped[str] = mapped_column(Text)

    run: Mapped[ResearchRun] = relationship(back_populates="results")
    findings: Mapped[list[FindingRow]] = relationship(
        back_populates="result", cascade="all, delete-orphan"
    )


class FindingRow(Base):
    __tablename__ = "finding"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    result_id: Mapped[str] = mapped_column(
        ForeignKey("research_result.id", ondelete="CASCADE"), index=True
    )
    statement: Mapped[str] = mapped_column(Text)
    # 对照表的"行"：该论断描述的对象（方法名 / 方案名 / 数据集名）。
    entity: Mapped[str] = mapped_column(Text, default="")
    source_url: Mapped[str] = mapped_column(Text)
    evidence_quote: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.7)
    verification_status: Mapped[str] = mapped_column(String(16), default="unverified")
    verification_method: Mapped[str] = mapped_column(String(32), default="none")
    source_content_hash: Mapped[str] = mapped_column(String(64), default="")
    source_title: Mapped[str] = mapped_column(Text, default="")
    source_reference: Mapped[str] = mapped_column(Text, default="")
    # 发布方身份（DOI / work_id / 标题 / 作者 / 域名），供交叉印证判"是否同源"。
    # 存 JSON：整体来自一次验证、整体被消费，没有按单字段查询的需求。
    source_identity: Mapped[dict[str, object] | None] = mapped_column(
        JSON, nullable=True, default=None
    )
    quantity_status: Mapped[str] = mapped_column(String(16), default="not_applicable")
    quantity_reason: Mapped[str] = mapped_column(Text, default="")
    # 数值与实验条件整体来自单次抽取、整体被消费，没有按单字段查询的需求，
    # 拆成十几列只会让每加一个条件字段都要一次迁移。定性论断为 NULL。
    quantity: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True, default=None)
    conditions: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True, default=None)
    evidence_context: Mapped[str] = mapped_column(Text, default="")
    verification_reason: Mapped[str] = mapped_column(Text, default="")
    semantic_status: Mapped[str] = mapped_column(String(16), default="not_checked")
    semantic_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    semantic_reason: Mapped[str] = mapped_column(Text, default="")
    claim_id: Mapped[str] = mapped_column(String(32), default="")
    consistency_status: Mapped[str] = mapped_column(String(16), default="not_checked")
    contradicts_claim_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    contradiction_reason: Mapped[str] = mapped_column(Text, default="")
    corroboration_status: Mapped[str] = mapped_column(String(20), default="not_checked")
    independent_source_count: Mapped[int] = mapped_column(Integer, default=0)
    corroborates_claim_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    corroboration_reason: Mapped[str] = mapped_column(Text, default="")

    result: Mapped[ResearchResultRow] = relationship(back_populates="findings")


class SourceRow(Base):
    __tablename__ = "source"
    __table_args__ = (
        UniqueConstraint("run_id", "url", "content_hash", name="uq_source_run_snapshot"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("research_run.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    # 学术元数据（DOI / 作者 / 机构 / 期刊 / 撤稿标记…）。存成 JSON 而不是拆成十几列：
    # 它整体来自单个检索后端的一次响应、整体被消费，没有任何按单字段查询的需求，
    # 而拆列会让每加一个字段都要一次迁移。非学术来源为 NULL。
    scholarly: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True, default=None)

    run: Mapped[ResearchRun] = relationship(back_populates="sources")


class ReportRow(Base):
    __tablename__ = "report"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("research_run.id", ondelete="CASCADE"), unique=True, index=True
    )
    markdown: Mapped[str] = mapped_column(Text)
    citations: Mapped[list[str]] = mapped_column(JSON, default=list)

    run: Mapped[ResearchRun] = relationship(back_populates="report")


class EventRow(Base):
    __tablename__ = "event"
    __table_args__ = (UniqueConstraint("run_id", "seq", name="uq_event_run_seq"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("research_run.id", ondelete="CASCADE"), index=True
    )
    seq: Mapped[int] = mapped_column(Integer)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    stage: Mapped[str] = mapped_column(String(20))
    type: Mapped[str] = mapped_column(String(16))
    message: Mapped[str] = mapped_column(Text, default="")
    elapsed: Mapped[float] = mapped_column(Float, default=0.0)
    data: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    run: Mapped[ResearchRun] = relationship(back_populates="events")


class RunTagRow(Base):
    __tablename__ = "run_tag"
    __table_args__ = (UniqueConstraint("run_id", "tag", name="uq_run_tag"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("research_run.id", ondelete="CASCADE"), index=True
    )
    tag: Mapped[str] = mapped_column(String(64))

    run: Mapped[ResearchRun] = relationship(back_populates="tags")


class WorkflowRunRow(Base):
    __tablename__ = "workflow_run"
    __table_args__ = (
        UniqueConstraint("research_run_id"),
        Index("ix_workflow_run_research_run_id", "research_run_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    research_run_id: Mapped[str] = mapped_column(ForeignKey("research_run.id", ondelete="CASCADE"))
    workflow_name: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20))
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    input: Mapped[dict] = mapped_column(JSON, default=dict)
    output: Mapped[dict] = mapped_column(JSON, default=dict)
    definition: Mapped[dict] = mapped_column(JSON, default=dict)
    checkpoint: Mapped[dict] = mapped_column(JSON, default=dict)
    lease_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    research_run: Mapped[ResearchRun] = relationship(back_populates="orchestration")
    steps: Mapped[list[StepRunRow]] = relationship(
        back_populates="workflow_run", cascade="all, delete-orphan", order_by="StepRunRow.idx"
    )


class StepRunRow(Base):
    __tablename__ = "step_run"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_run_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_run.id", ondelete="CASCADE"), index=True
    )
    idx: Mapped[int] = mapped_column(Integer)
    node_id: Mapped[str] = mapped_column(String(100))
    label: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(32))
    agent: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(20))
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    workflow_run: Mapped[WorkflowRunRow] = relationship(back_populates="steps")


# ──────────────────────────────────────────────────────────────────────────
# 角色广场 catalog：模型档案 / 角色卡片 / 搜索 key 池（独立于单次 run，全局配置）
# ──────────────────────────────────────────────────────────────────────────


class ModelProfileRow(Base):
    """一个可复用的 LLM 模型档案：不同任务可绑定不同档案（不同 base_url/key/model）。"""

    __tablename__ = "model_profile"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(64), unique=True)  # 展示名，唯一
    base_url: Mapped[str | None] = mapped_column(Text, nullable=True)  # None=官方默认端点
    api_key: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(100), default="gpt-4o-mini")
    temperature: Mapped[float] = mapped_column(Float, default=0.3)
    parameter_mode: Mapped[str] = mapped_column(String(16), default="temperature")
    reasoning_effort: Mapped[str] = mapped_column(String(16), default="medium")
    is_default: Mapped[bool] = mapped_column(Integer, default=0)  # 1=全局兜底档案
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    agents: Mapped[list[AgentCardRow]] = relationship(back_populates="model_profile")


class AgentCardRow(Base):
    """角色卡片：数据驱动的角色定义。behavior 选一种内置行为模板，prompt 与模型可自定。"""

    __tablename__ = "agent_card"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(64), unique=True)  # 工作流按此名引用，唯一
    display_name: Mapped[str] = mapped_column(String(100), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    # 行为模板：plan / research / reflect / synthesize / critique
    behavior: Mapped[str] = mapped_column(String(20))
    system_prompt: Mapped[str] = mapped_column(Text, default="")  # 空=用该行为的内置默认
    icon: Mapped[str] = mapped_column(String(16), default="🧩")  # 卡片图标（emoji）
    enabled: Mapped[bool] = mapped_column(Integer, default=1)
    # 绑定的模型档案；NULL=用全局默认档案兜底（按角色绑模型）
    model_profile_id: Mapped[str | None] = mapped_column(
        ForeignKey("model_profile.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    model_profile: Mapped[ModelProfileRow | None] = relationship(back_populates="agents")


class SearchKeyRow(Base):
    """搜索 API key 池：主备故障转移——按 priority 升序使用，配额/限流错误切下一个。"""

    __tablename__ = "search_key"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    label: Mapped[str] = mapped_column(String(64), default="")  # 备注名（如 "主账号"）
    api_key: Mapped[str] = mapped_column(Text)
    priority: Mapped[int] = mapped_column(Integer, default=0)  # 越小越先用
    enabled: Mapped[bool] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WorkflowDefRow(Base):
    """自定义工作流定义：用户在构建器里拼出的有序流程，存为可复用的命名实体。

    steps 存 Step[] 的 JSON 序列（kind/agent/reflector/researcher/max_rounds…）；运行时
    编排器按 name 取出、Step.model_validate 还原后交引擎执行。独立于单次 run，属全局配置。
    """

    __tablename__ = "workflow_def"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(64), unique=True)  # 运行按此名引用，唯一
    display_name: Mapped[str] = mapped_column(String(100), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    steps: Mapped[list[dict]] = mapped_column(JSON, default=list)
    nodes: Mapped[list[dict]] = mapped_column(JSON, default=list)
    edges: Mapped[list[dict]] = mapped_column(JSON, default=list)
    viewport: Mapped[dict] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    enabled: Mapped[bool] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
