"""PostgreSQL / SQLite 仓储（async SQLAlchemy 2.0）。

每个写方法独立事务（async with session.begin()）；读方法用 selectinload 预取
关系，避免 async 下的惰性加载问题。ORM↔Pydantic 的转换内联于各方法。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, func, or_, select, update
from sqlalchemy import delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from ..models import Finding, Report, ResearchPlan, ResearchResult, Source, SubQuestion
from ..observability import Event
from ..orchestration import StepRun, WorkflowRun
from . import orm
from .repository import RunDetail, RunSummary, TagCount


class SqlRepository:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sessionmaker

    async def create_run(self, query: str) -> str:
        async with self._sm() as s, s.begin():
            run = orm.ResearchRun(query=query, status="pending")
            s.add(run)
            await s.flush()
            return run.id

    async def set_status(self, run_id: str, status: str) -> None:
        async with self._sm() as s, s.begin():
            run = await s.get(orm.ResearchRun, run_id)
            if run is not None:
                run.status = status

    async def save_plan(self, run_id: str, plan: ResearchPlan) -> None:
        async with self._sm() as s, s.begin():
            run = await s.get(orm.ResearchRun, run_id)
            if run is not None:
                run.interpretation = plan.interpretation
            for i, sq in enumerate(plan.sub_questions):
                s.add(
                    orm.SubQuestionRow(
                        run_id=run_id,
                        idx=i,
                        question=sq.question,
                        rationale=sq.rationale,
                        depends_on=sq.depends_on,
                        origin="plan",
                        round=0,
                    )
                )

    async def add_sub_questions(
        self, run_id: str, sub_questions: list[SubQuestion], *, origin: str, round: int
    ) -> None:
        async with self._sm() as s, s.begin():
            count = await s.scalar(
                select(func.count())
                .select_from(orm.SubQuestionRow)
                .where(orm.SubQuestionRow.run_id == run_id)
            )
            base = int(count or 0)
            for j, sq in enumerate(sub_questions):
                s.add(
                    orm.SubQuestionRow(
                        run_id=run_id,
                        idx=base + j,
                        question=sq.question,
                        rationale=sq.rationale,
                        depends_on=sq.depends_on,
                        origin=origin,
                        round=round,
                    )
                )

    async def save_result(self, run_id: str, result: ResearchResult) -> None:
        async with self._sm() as s, s.begin():
            row = orm.ResearchResultRow(run_id=run_id, sub_question=result.sub_question)
            s.add(row)
            await s.flush()
            for f in result.findings:
                s.add(
                    orm.FindingRow(
                        result_id=row.id,
                        statement=f.statement,
                        source_url=f.source_url,
                        confidence=f.confidence,
                    )
                )

    async def save_sources(self, run_id: str, sources: list[Source]) -> None:
        async with self._sm() as s, s.begin():
            for src in sources:
                s.add(
                    orm.SourceRow(run_id=run_id, title=src.title, url=src.url, content=src.content)
                )

    async def save_report(self, run_id: str, report: Report) -> None:
        async with self._sm() as s, s.begin():
            s.add(
                orm.ReportRow(run_id=run_id, markdown=report.markdown, citations=report.citations)
            )

    async def save_events(self, run_id: str, events: list[Event]) -> None:
        # 覆盖式写入（与 InMemoryRepository 对齐）：先清旧再写新，
        # 同一 run 第二次保存不会撞 (run_id, seq) 唯一约束
        async with self._sm() as s, s.begin():
            await s.execute(sa_delete(orm.EventRow).where(orm.EventRow.run_id == run_id))
            for i, ev in enumerate(events):
                s.add(
                    orm.EventRow(
                        run_id=run_id,
                        seq=i,
                        stage=ev.stage,
                        type=ev.type,
                        message=ev.message,
                        elapsed=ev.elapsed,
                        data=ev.data,
                    )
                )

    async def save_orchestration(self, run_id: str, execution: WorkflowRun) -> None:
        async with self._sm() as s, s.begin():
            row = await s.scalar(
                select(orm.WorkflowRunRow).where(orm.WorkflowRunRow.research_run_id == run_id)
            )
            if row is None:
                row = orm.WorkflowRunRow(id=execution.id, research_run_id=run_id)
                s.add(row)
            row.workflow_name = execution.workflow_name
            row.status = execution.status.value
            row.input = execution.input
            row.output = execution.output
            row.definition = execution.definition
            row.checkpoint = execution.checkpoint
            row.started_at = execution.started_at
            row.finished_at = execution.finished_at
            await s.execute(
                sa_delete(orm.StepRunRow).where(
                    orm.StepRunRow.workflow_run_id == execution.id
                )
            )
            for idx, step in enumerate(execution.steps):
                s.add(
                    orm.StepRunRow(
                        id=step.id,
                        workflow_run_id=execution.id,
                        idx=idx,
                        node_id=step.node_id,
                        label=step.label,
                        kind=step.kind,
                        agent=step.agent,
                        status=step.status.value,
                        attempt=step.attempt,
                        error=step.error,
                        started_at=step.started_at,
                        finished_at=step.finished_at,
                    )
                )

    async def acquire_lease(self, run_id: str, owner: str, *, seconds: int = 120) -> bool:
        now = datetime.now(UTC)
        expires = now + timedelta(seconds=seconds)
        async with self._sm() as s, s.begin():
            result = await s.execute(
                update(orm.WorkflowRunRow)
                .where(
                    orm.WorkflowRunRow.research_run_id == run_id,
                    or_(
                        orm.WorkflowRunRow.lease_owner.is_(None),
                        orm.WorkflowRunRow.lease_owner == owner,
                        orm.WorkflowRunRow.lease_expires_at < now,
                    ),
                )
                .values(lease_owner=owner, lease_expires_at=expires)
            )
            return bool(cast("CursorResult[Any]", result).rowcount)

    async def release_lease(self, run_id: str, owner: str) -> None:
        async with self._sm() as s, s.begin():
            await s.execute(
                update(orm.WorkflowRunRow)
                .where(
                    orm.WorkflowRunRow.research_run_id == run_id,
                    orm.WorkflowRunRow.lease_owner == owner,
                )
                .values(lease_owner=None, lease_expires_at=None)
            )

    async def finalize(self, run_id: str, *, elapsed: float, total_tokens: int) -> None:
        async with self._sm() as s, s.begin():
            run = await s.get(orm.ResearchRun, run_id)
            if run is not None:
                run.elapsed = elapsed
                run.total_tokens = total_tokens
                run.status = "done"
                run.finished_at = datetime.now(UTC)

    async def delete_run(self, run_id: str) -> bool:
        # 单条 DELETE：DB 级 ondelete=CASCADE 清子表（SQLite 已开 foreign_keys=ON）
        async with self._sm() as s, s.begin():
            result = await s.execute(sa_delete(orm.ResearchRun).where(orm.ResearchRun.id == run_id))
            return bool(cast("CursorResult[Any]", result).rowcount)

    async def set_tags(self, run_id: str, tags: list[str]) -> None:
        # 替换语义：先清旧标签再写新（去重 + 去空白）
        cleaned = list(dict.fromkeys(t.strip() for t in tags if t.strip()))
        async with self._sm() as s, s.begin():
            await s.execute(sa_delete(orm.RunTagRow).where(orm.RunTagRow.run_id == run_id))
            for tag in cleaned:
                s.add(orm.RunTagRow(run_id=run_id, tag=tag))

    async def list_tags(self) -> list[TagCount]:
        async with self._sm() as s:
            rows = (
                await s.execute(
                    select(orm.RunTagRow.tag, func.count())
                    .group_by(orm.RunTagRow.tag)
                    .order_by(func.count().desc(), orm.RunTagRow.tag)
                )
            ).all()
            return [TagCount(tag=tag, count=int(count)) for tag, count in rows]

    async def list_runs(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
        q: str | None = None,
        tag: str | None = None,
    ) -> list[RunSummary]:
        async with self._sm() as s:
            stmt = (
                select(orm.ResearchRun)
                .options(selectinload(orm.ResearchRun.tags))
                .order_by(orm.ResearchRun.created_at.desc())
            )
            if status:
                stmt = stmt.where(orm.ResearchRun.status == status)
            if q:
                stmt = stmt.where(orm.ResearchRun.query.ilike(f"%{q}%"))
            if tag:
                # join 标签表筛选；distinct 防一行多标签时重复
                stmt = stmt.join(orm.RunTagRow).where(orm.RunTagRow.tag == tag).distinct()
            rows = (await s.scalars(stmt.limit(limit).offset(offset))).all()
            return [
                RunSummary(
                    id=r.id,
                    query=r.query,
                    status=r.status,
                    created_at=r.created_at,
                    total_tokens=r.total_tokens,
                    elapsed=r.elapsed,
                    tags=[t.tag for t in r.tags],
                )
                for r in rows
            ]

    async def get_run(self, run_id: str) -> RunDetail | None:
        async with self._sm() as s:
            run = (
                await s.scalars(
                    select(orm.ResearchRun)
                    .where(orm.ResearchRun.id == run_id)
                    .options(
                        selectinload(orm.ResearchRun.sub_questions),
                        selectinload(orm.ResearchRun.results).selectinload(
                            orm.ResearchResultRow.findings
                        ),
                        selectinload(orm.ResearchRun.report),
                        selectinload(orm.ResearchRun.tags),
                        selectinload(orm.ResearchRun.orchestration).selectinload(
                            orm.WorkflowRunRow.steps
                        ),
                    )
                )
            ).first()
            if run is None:
                return None
            sub_questions = [
                SubQuestion(
                    question=sq.question,
                    rationale=sq.rationale,
                    depends_on=list(sq.depends_on or []),
                )
                for sq in run.sub_questions
            ]
            results = [
                ResearchResult(
                    sub_question=rr.sub_question,
                    findings=[
                        Finding(
                            statement=f.statement,
                            source_url=f.source_url,
                            confidence=f.confidence,
                        )
                        for f in rr.findings
                    ],
                )
                for rr in run.results
            ]
            report = (
                Report(
                    query=run.query,
                    markdown=run.report.markdown,
                    citations=list(run.report.citations or []),
                )
                if run.report is not None
                else None
            )
            orchestration = None
            if run.orchestration is not None:
                orchestration = WorkflowRun(
                    id=run.orchestration.id,
                    workflow_name=run.orchestration.workflow_name,
                    status=run.orchestration.status,
                    input=run.orchestration.input or {},
                    output=run.orchestration.output or {},
                    definition=run.orchestration.definition or {},
                    checkpoint=run.orchestration.checkpoint or {},
                    started_at=run.orchestration.started_at,
                    finished_at=run.orchestration.finished_at,
                    steps=[
                        StepRun(
                            id=step.id,
                            node_id=step.node_id,
                            label=step.label,
                            kind=step.kind,
                            agent=step.agent,
                            status=step.status,
                            attempt=step.attempt,
                            error=step.error,
                            started_at=step.started_at,
                            finished_at=step.finished_at,
                        )
                        for step in run.orchestration.steps
                    ],
                )
            return RunDetail(
                id=run.id,
                query=run.query,
                status=run.status,
                interpretation=run.interpretation,
                sub_questions=sub_questions,
                results=results,
                report=report,
                total_tokens=run.total_tokens,
                elapsed=run.elapsed,
                created_at=run.created_at,
                tags=[t.tag for t in run.tags],
                orchestration=orchestration,
            )

    async def get_events(self, run_id: str, *, after_seq: int = 0) -> list[Event]:
        async with self._sm() as s:
            rows = (
                await s.scalars(
                    select(orm.EventRow)
                    .where(orm.EventRow.run_id == run_id, orm.EventRow.seq >= after_seq)
                    .order_by(orm.EventRow.seq)
                )
            ).all()
            return [
                Event(
                    stage=r.stage,
                    type=r.type,
                    message=r.message,
                    elapsed=r.elapsed,
                    data=r.data,
                )
                for r in rows
            ]
