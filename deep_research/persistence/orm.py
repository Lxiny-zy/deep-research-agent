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
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class CoordinationRow(Base):
    __tablename__ = "coordination"
    name: Mapped[str] = mapped_column(String(64), primary_key=True)


class ProviderStateRow(Base):
    __tablename__ = "provider_state"
    fingerprint: Mapped[str] = mapped_column(String(64), primary_key=True)
    retry_at: Mapped[float] = mapped_column(Float, default=0)
    window_start: Mapped[float] = mapped_column(Float, default=0)
    window_count: Mapped[int] = mapped_column(Integer, default=0)
    requests: Mapped[int] = mapped_column(Integer, default=0)
    limited: Mapped[int] = mapped_column(Integer, default=0)


class ProviderLeaseRow(Base):
    __tablename__ = "provider_lease"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    expires_at: Mapped[float] = mapped_column(Float)


class RequestWindowRow(Base):
    __tablename__ = "request_window"
    identity: Mapped[str] = mapped_column(String(64), primary_key=True)
    starts_at: Mapped[float] = mapped_column(Float)
    count: Mapped[int] = mapped_column(Integer, default=0)


class RuntimeConfigRow(Base):
    __tablename__ = "runtime_config_revision"
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    values: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WorkerHeartbeatRow(Base):
    __tablename__ = "worker_heartbeat"
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    active: Mapped[int] = mapped_column(Integer, default=0)


class ArtifactCleanupRow(Base):
    __tablename__ = "artifact_cleanup"
    # Independent of research_run so deletion and the cleanup request commit together.
    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    slug: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ResearchProjectRow(Base):
    __tablename__ = "research_project"
    __table_args__ = (UniqueConstraint("owner_id", "name", name="uq_research_project_owner_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    owner_id: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    corpora: Mapped[list[CorpusRow]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="CorpusRow.created_at"
    )
    sources: Mapped[list[LibrarySourceRow]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    runs: Mapped[list[ResearchRun]] = relationship(back_populates="project")


class CorpusRow(Base):
    __tablename__ = "corpus"
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_corpus_project_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("research_project.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    project: Mapped[ResearchProjectRow] = relationship(back_populates="corpora")
    sources: Mapped[list[LibrarySourceRow]] = relationship(
        back_populates="corpus", cascade="all, delete-orphan"
    )


class LibrarySourceRow(Base):
    __tablename__ = "library_source"
    __table_args__ = (
        UniqueConstraint("corpus_id", "content_hash", name="uq_library_source_snapshot"),
        Index("ix_library_source_project_status", "project_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("research_project.id", ondelete="CASCADE"), index=True
    )
    corpus_id: Mapped[str] = mapped_column(ForeignKey("corpus.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="included")
    origin_url: Mapped[str] = mapped_column(Text, default="")
    mime_type: Mapped[str] = mapped_column(String(100), default="text/plain")
    content_hash: Mapped[str] = mapped_column(String(64))
    char_count: Mapped[int] = mapped_column(Integer)
    source_metadata: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    project: Mapped[ResearchProjectRow] = relationship(back_populates="sources")
    corpus: Mapped[CorpusRow] = relationship(back_populates="sources")
    chunks: Mapped[list[SourceChunkRow]] = relationship(
        back_populates="source", cascade="all, delete-orphan", order_by="SourceChunkRow.ordinal"
    )


class SourceChunkRow(Base):
    __tablename__ = "source_chunk"
    __table_args__ = (UniqueConstraint("source_id", "ordinal", name="uq_source_chunk_ordinal"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    source_id: Mapped[str] = mapped_column(
        ForeignKey("library_source.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    locator: Mapped[str] = mapped_column(String(300))
    start_char: Mapped[int] = mapped_column(Integer, default=0)
    end_char: Mapped[int] = mapped_column(Integer, default=0)
    page_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    section: Mapped[str] = mapped_column(String(300), default="")

    source: Mapped[LibrarySourceRow] = relationship(back_populates="chunks")


class ResearchRun(Base):
    __tablename__ = "research_run"
    __table_args__ = (
        Index("uq_research_run_idempotency_key", "idempotency_key", unique=True),
        Index("ix_research_run_claimable", "status", "claimable_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    query: Mapped[str] = mapped_column(Text)
    owner_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("research_project.id", ondelete="SET NULL"), nullable=True, index=True
    )
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
    project: Mapped[ResearchProjectRow | None] = relationship(back_populates="runs")


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
    # 逐字命中在来源正文里的字符区间。与 source_content_hash 配对使用：哈希钉住
    # 「哪一份快照」，区间钉住「快照里的哪一段」，合起来让引用可被独立重新定位。
    # 历史行为 NULL，回放时退回只有 evidence_context 窗口的既有行为。
    quote_start: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    quote_end: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
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
    locator: Mapped[str] = mapped_column(String(300), default="")
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
    tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    tokens_estimated: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")

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
        # 每次 worker 领取任务、每次带租约围栏的写入都过滤这两列——是全系统最热的
        # 谓词。顺序上 lease_expires_at 在前：领取查询按它做范围比较筛掉活跃租约，
        # lease_owner 只做等值确认。
        Index("ix_workflow_run_lease", "lease_expires_at", "lease_owner"),
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
    __table_args__ = (
        Index(
            "uq_model_profile_default",
            "is_default",
            unique=True,
            sqlite_where=text("is_default = 1"),
            postgresql_where=text("is_default = 1"),
        ),
    )

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
    prompt_mode: Mapped[str] = mapped_column(
        String(16), default="replace", server_default="replace"
    )
    search_profile_ids: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    # 卡片图标：图标名（bot / route / search 等），前端映射为线性图标
    icon: Mapped[str] = mapped_column(String(16), default="bot")
    enabled: Mapped[bool] = mapped_column(Integer, default=1)
    # 绑定的模型档案；NULL=用全局默认档案兜底（按角色绑模型）
    model_profile_id: Mapped[str | None] = mapped_column(
        ForeignKey("model_profile.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    model_profile: Mapped[ModelProfileRow | None] = relationship(back_populates="agents")


class SearchProfileRow(Base):
    __tablename__ = "search_profile"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    provider: Mapped[str] = mapped_column(String(16))
    endpoint: Mapped[str] = mapped_column(String(500), default="")
    model: Mapped[str] = mapped_column(String(100), default="")
    key_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Integer, default=1)


class SearchKeyRow(Base):
    """搜索 API key 池：主备故障转移——按 priority 升序使用，配额/限流错误切下一个。"""

    __tablename__ = "search_key"
    __table_args__ = (Index("ix_search_key_provider_priority", "provider", "priority"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    provider: Mapped[str] = mapped_column(String(16), default="tavily", server_default="tavily")
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


class QaConversationRow(Base):
    """学术问答会话：一个用户的一串多轮问答。"""

    __tablename__ = "qa_conversation"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    owner_id: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    messages: Mapped[list[QaMessageRow]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="QaMessageRow.position",
    )


class QaMessageRow(Base):
    """一轮问答：问句、带引用的答句、引用来源与思考过程（检索 / 核验 / 复核的留痕）。"""

    __tablename__ = "qa_message"
    __table_args__ = (
        UniqueConstraint("conversation_id", "position", name="uq_qa_message_position"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("qa_conversation.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    query: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text, default="")
    citations: Mapped[list[str]] = mapped_column(JSON, default=list)
    evidence: Mapped[list[dict]] = mapped_column(JSON, default=list)
    thoughts: Mapped[list[dict]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default="done")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    conversation: Mapped[QaConversationRow] = relationship(back_populates="messages")
