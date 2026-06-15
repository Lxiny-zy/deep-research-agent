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

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    query: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending/running/done/error
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
    source_url: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=0.7)

    result: Mapped[ResearchResultRow] = relationship(back_populates="findings")


class SourceRow(Base):
    __tablename__ = "source"
    __table_args__ = (UniqueConstraint("run_id", "url", name="uq_source_run_url"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("research_run.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text, default="")

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
    stage: Mapped[str] = mapped_column(String(20))
    type: Mapped[str] = mapped_column(String(16))
    message: Mapped[str] = mapped_column(Text, default="")
    elapsed: Mapped[float] = mapped_column(Float, default=0.0)
    data: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    run: Mapped[ResearchRun] = relationship(back_populates="events")
