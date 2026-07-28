"""PostgreSQL / SQLite 仓储（async SQLAlchemy 2.0）。

每个写方法独立事务（async with session.begin()）；读方法用 selectinload 预取
关系，避免 async 下的惰性加载问题。ORM↔Pydantic 的转换内联于各方法。
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, func, or_, select, update
from sqlalchemy import delete as sa_delete
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from ..models import (
    EvidenceVerification,
    Finding,
    Report,
    ResearchPlan,
    ResearchResult,
    Source,
    SubQuestion,
)
from ..observability import Event
from ..orchestration import StepRun, WorkflowRun
from . import orm
from .repository import LeaseLostError, RunDetail, RunSummary, TagCount


def _sub_question_row(
    run_id: str,
    index: int,
    sub_question: SubQuestion,
    *,
    origin: str,
    round_: int,
) -> orm.SubQuestionRow:
    return orm.SubQuestionRow(
        run_id=run_id,
        idx=index,
        question=sub_question.question,
        rationale=sub_question.rationale,
        depends_on=sub_question.depends_on,
        origin=origin,
        round=round_,
    )


def _research_result_row(run_id: str, result: ResearchResult) -> orm.ResearchResultRow:
    row = orm.ResearchResultRow(run_id=run_id, sub_question=result.sub_question)
    row.findings = [
        orm.FindingRow(
            statement=finding.statement,
            source_url=finding.source_url,
            evidence_quote=finding.evidence_quote,
            confidence=finding.confidence,
            verification_status=finding.verification.status,
            verification_method=finding.verification.method,
            source_content_hash=finding.verification.source_content_hash,
            source_title=finding.verification.source_title,
            evidence_context=finding.verification.evidence_context,
            verification_reason=finding.verification.reason,
            semantic_status=finding.verification.semantic_status,
            semantic_confidence=finding.verification.semantic_confidence,
            semantic_reason=finding.verification.semantic_reason,
            claim_id=finding.verification.claim_id,
            consistency_status=finding.verification.consistency_status,
            contradicts_claim_ids=finding.verification.contradicts_claim_ids,
            contradiction_reason=finding.verification.contradiction_reason,
            corroboration_status=finding.verification.corroboration_status,
            independent_source_count=finding.verification.independent_source_count,
            corroborates_claim_ids=finding.verification.corroborates_claim_ids,
            corroboration_reason=finding.verification.corroboration_reason,
        )
        for finding in result.findings
    ]
    return row


class SqlRepository:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sessionmaker

    async def create_run(
        self,
        query: str,
        *,
        execution: WorkflowRun | None = None,
        lease_owner: str | None = None,
    ) -> str:
        async with self._sm() as s, s.begin():
            run = orm.ResearchRun(query=query, status="pending")
            s.add(run)
            await s.flush()
            if execution is not None:
                s.add(
                    orm.WorkflowRunRow(
                        id=execution.id,
                        research_run_id=run.id,
                        workflow_name=execution.workflow_name,
                        status=execution.status.value,
                        input=execution.input,
                        output=execution.output,
                        definition=execution.definition,
                        checkpoint=execution.checkpoint,
                        lease_owner=lease_owner,
                        lease_expires_at=(
                            datetime.now(UTC) + timedelta(seconds=120)
                            if lease_owner is not None
                            else None
                        ),
                        started_at=execution.started_at,
                        finished_at=execution.finished_at,
                    )
                )
            return run.id

    async def _owned_workflow_row(
        self, s: AsyncSession, run_id: str, owner: str | None
    ) -> orm.WorkflowRunRow | None:
        """Lock and validate a worker lease before a fenced write.

        The no-op UPDATE is intentional: PostgreSQL locks the matched row and
        SQLite acquires its write lock before the protected mutation begins.
        """
        if owner is None:
            return None
        result = await s.execute(
            update(orm.WorkflowRunRow)
            .where(
                orm.WorkflowRunRow.research_run_id == run_id,
                orm.WorkflowRunRow.lease_owner == owner,
                orm.WorkflowRunRow.lease_expires_at > datetime.now(UTC),
            )
            .values(lease_owner=owner)
        )
        if not cast("CursorResult[Any]", result).rowcount:
            raise LeaseLostError(f"run {run_id} lease is no longer owned by this worker")
        return await s.scalar(
            select(orm.WorkflowRunRow).where(orm.WorkflowRunRow.research_run_id == run_id)
        )

    async def set_status(self, run_id: str, status: str, *, lease_owner: str | None = None) -> None:
        async with self._sm() as s, s.begin():
            await self._owned_workflow_row(s, run_id, lease_owner)
            run = await s.get(orm.ResearchRun, run_id)
            if run is not None:
                run.status = status

    async def prepare_resume(self, run_id: str, *, lease_owner: str) -> None:
        async with self._sm() as s, s.begin():
            await self._owned_workflow_row(s, run_id, lease_owner)
            run = await s.get(orm.ResearchRun, run_id)
            if run is not None:
                run.status = "running"
            await s.execute(
                sa_delete(orm.EventRow).where(
                    orm.EventRow.run_id == run_id,
                    orm.EventRow.stage == "ORCHESTRATOR",
                    orm.EventRow.type.in_(("done", "error")),
                )
            )

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
                        evidence_quote=f.evidence_quote,
                        confidence=f.confidence,
                        verification_status=f.verification.status,
                        verification_method=f.verification.method,
                        source_content_hash=f.verification.source_content_hash,
                        source_title=f.verification.source_title,
                        evidence_context=f.verification.evidence_context,
                        verification_reason=f.verification.reason,
                        semantic_status=f.verification.semantic_status,
                        semantic_confidence=f.verification.semantic_confidence,
                        semantic_reason=f.verification.semantic_reason,
                        claim_id=f.verification.claim_id,
                        consistency_status=f.verification.consistency_status,
                        contradicts_claim_ids=f.verification.contradicts_claim_ids,
                        contradiction_reason=f.verification.contradiction_reason,
                        corroboration_status=f.verification.corroboration_status,
                        independent_source_count=f.verification.independent_source_count,
                        corroborates_claim_ids=f.verification.corroborates_claim_ids,
                        corroboration_reason=f.verification.corroboration_reason,
                    )
                )

    async def save_sources(
        self, run_id: str, sources: list[Source], *, lease_owner: str | None = None
    ) -> None:
        async with self._sm() as s, s.begin():
            await self._owned_workflow_row(s, run_id, lease_owner)
            values = []
            seen: set[tuple[str, str]] = set()
            for source in sources:
                content_hash = hashlib.sha256(source.content.encode("utf-8")).hexdigest()
                key = (source.url, content_hash)
                if key in seen:
                    continue
                seen.add(key)
                values.append(
                    {
                        "run_id": run_id,
                        "title": source.title,
                        "url": source.url,
                        "content": source.content,
                        "content_hash": content_hash,
                    }
                )
            if not values:
                return
            dialect = s.bind.dialect.name if s.bind is not None else ""
            insert = postgresql_insert if dialect == "postgresql" else sqlite_insert
            statement = insert(orm.SourceRow).values(values)
            statement = statement.on_conflict_do_update(
                index_elements=["run_id", "url", "content_hash"],
                set_={"title": statement.excluded.title, "content": statement.excluded.content},
            )
            await s.execute(statement)

    async def save_report(self, run_id: str, report: Report) -> None:
        async with self._sm() as s, s.begin():
            s.add(
                orm.ReportRow(run_id=run_id, markdown=report.markdown, citations=report.citations)
            )

    async def replace_artifacts(
        self,
        run_id: str,
        *,
        plan: ResearchPlan | None,
        reflection_rounds: list[tuple[int, list[SubQuestion]]],
        results: list[ResearchResult],
        report: Report,
        lease_owner: str | None = None,
    ) -> None:
        """Replace plan/results/report in one transaction for resumable writes."""
        async with self._sm() as s, s.begin():
            await self._owned_workflow_row(s, run_id, lease_owner)
            run = await s.get(orm.ResearchRun, run_id)
            if run is None:
                return
            run.interpretation = plan.interpretation if plan is not None else ""
            await s.execute(
                sa_delete(orm.SubQuestionRow).where(orm.SubQuestionRow.run_id == run_id)
            )
            await s.execute(
                sa_delete(orm.ResearchResultRow).where(orm.ResearchResultRow.run_id == run_id)
            )
            await s.execute(sa_delete(orm.ReportRow).where(orm.ReportRow.run_id == run_id))

            index = 0
            if plan is not None:
                for sub_question in plan.sub_questions:
                    s.add(
                        _sub_question_row(
                            run_id,
                            index,
                            sub_question,
                            origin="plan",
                            round_=0,
                        )
                    )
                    index += 1
            for round_, sub_questions in reflection_rounds:
                for sub_question in sub_questions:
                    s.add(
                        _sub_question_row(
                            run_id,
                            index,
                            sub_question,
                            origin="reflection",
                            round_=round_,
                        )
                    )
                    index += 1
            for result in results:
                s.add(_research_result_row(run_id, result))
            s.add(
                orm.ReportRow(run_id=run_id, markdown=report.markdown, citations=report.citations)
            )

    async def save_events(
        self, run_id: str, events: list[Event], *, lease_owner: str | None = None
    ) -> None:
        # 覆盖式写入（与 InMemoryRepository 对齐）：先清旧再写新，
        # 同一 run 第二次保存不会撞 (run_id, seq) 唯一约束
        async with self._sm() as s, s.begin():
            await self._owned_workflow_row(s, run_id, lease_owner)
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

    async def save_orchestration(
        self,
        run_id: str,
        execution: WorkflowRun,
        *,
        lease_owner: str | None = None,
    ) -> None:
        async with self._sm() as s, s.begin():
            row = await self._owned_workflow_row(s, run_id, lease_owner)
            if row is None:
                row = await s.scalar(
                    select(orm.WorkflowRunRow)
                    .where(orm.WorkflowRunRow.research_run_id == run_id)
                    .with_for_update()
                )
            if row is None:
                row = orm.WorkflowRunRow(id=execution.id, research_run_id=run_id)
                s.add(row)
            workflow_run_id = row.id
            row.workflow_name = execution.workflow_name
            row.status = execution.status.value
            row.input = execution.input
            row.output = execution.output
            row.definition = execution.definition
            row.checkpoint = execution.checkpoint
            row.started_at = execution.started_at
            row.finished_at = execution.finished_at
            await s.execute(
                sa_delete(orm.StepRunRow).where(orm.StepRunRow.workflow_run_id == workflow_run_id)
            )
            for idx, step in enumerate(execution.steps):
                s.add(
                    orm.StepRunRow(
                        id=step.id,
                        workflow_run_id=workflow_run_id,
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
                        orm.WorkflowRunRow.lease_expires_at.is_(None),
                        orm.WorkflowRunRow.lease_expires_at <= now,
                    ),
                )
                .values(lease_owner=owner, lease_expires_at=expires)
            )
            return bool(cast("CursorResult[Any]", result).rowcount)

    async def renew_lease(self, run_id: str, owner: str, *, seconds: int = 120) -> bool:
        now = datetime.now(UTC)
        expires = now + timedelta(seconds=seconds)
        async with self._sm() as s, s.begin():
            result = await s.execute(
                update(orm.WorkflowRunRow)
                .where(
                    orm.WorkflowRunRow.research_run_id == run_id,
                    orm.WorkflowRunRow.lease_owner == owner,
                    orm.WorkflowRunRow.lease_expires_at > now,
                )
                .values(lease_expires_at=expires)
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

    async def finalize(
        self,
        run_id: str,
        *,
        elapsed: float,
        total_tokens: int,
        lease_owner: str | None = None,
    ) -> None:
        async with self._sm() as s, s.begin():
            await self._owned_workflow_row(s, run_id, lease_owner)
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
                        selectinload(orm.ResearchRun.sources),
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
                            evidence_quote=f.evidence_quote,
                            confidence=f.confidence,
                            verification=EvidenceVerification(
                                status=f.verification_status,
                                method=f.verification_method,
                                source_content_hash=f.source_content_hash,
                                source_title=f.source_title,
                                evidence_context=f.evidence_context,
                                reason=f.verification_reason,
                                semantic_status=f.semantic_status,
                                semantic_confidence=f.semantic_confidence,
                                semantic_reason=f.semantic_reason,
                                claim_id=f.claim_id,
                                consistency_status=f.consistency_status,
                                contradicts_claim_ids=list(f.contradicts_claim_ids or []),
                                contradiction_reason=f.contradiction_reason,
                                corroboration_status=f.corroboration_status,
                                independent_source_count=f.independent_source_count,
                                corroborates_claim_ids=list(f.corroborates_claim_ids or []),
                                corroboration_reason=f.corroboration_reason,
                            ),
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
                sources=[
                    Source(
                        title=source.title,
                        url=source.url,
                        content=source.content,
                        content_hash=source.content_hash,
                    )
                    for source in run.sources
                ],
                orchestration=orchestration,
            )

    async def get_run_status(self, run_id: str) -> str | None:
        async with self._sm() as s:
            return await s.scalar(
                select(orm.ResearchRun.status).where(orm.ResearchRun.id == run_id)
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
