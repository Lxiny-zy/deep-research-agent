"""PostgreSQL / SQLite 仓储（async SQLAlchemy 2.0）。

每个写方法独立事务（async with session.begin()）；读方法用 selectinload 预取
关系，避免 async 下的惰性加载问题。ORM↔Pydantic 的转换内联于各方法。
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import CursorResult, func, or_, select, update
from sqlalchemy import delete as sa_delete
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from ..models import (
    EvidenceVerification,
    ExperimentConditions,
    Finding,
    Quantity,
    Report,
    ResearchPlan,
    ResearchResult,
    ScholarlyMetadata,
    Source,
    SourceIdentity,
    SubQuestion,
)
from ..observability import Event
from ..orchestration import StepRun, WorkflowRun
from . import orm
from .repository import (
    RUN_ACTIVE_STATUSES,
    ClaimedRun,
    IdempotencyConflictError,
    LeaseLostError,
    RunDetail,
    RunSummary,
    TagCount,
)

# 一次 claim 调用最多尝试的候选数。并发 worker 抢同一条时会失败重试，
# 但不能无界重试——超过上限就返回 None，由 worker 的轮询间隔自然退避。
_CLAIM_CANDIDATE_LIMIT = 8


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
            entity=finding.entity,
            source_url=finding.source_url,
            evidence_quote=finding.evidence_quote,
            confidence=finding.confidence,
            verification_status=finding.verification.status,
            verification_method=finding.verification.method,
            source_content_hash=finding.verification.source_content_hash,
            source_title=finding.verification.source_title,
            source_reference=finding.verification.source_reference,
            source_identity=(
                finding.verification.source_identity.model_dump(mode="json")
                if finding.verification.source_identity
                else None
            ),
            quantity_status=finding.verification.quantity_status,
            quantity_reason=finding.verification.quantity_reason,
            quantity=(finding.quantity.model_dump(mode="json") if finding.quantity else None),
            conditions=(finding.conditions.model_dump(mode="json") if finding.conditions else None),
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


def _workflow_run(row: orm.WorkflowRunRow) -> WorkflowRun:
    """ORM → Pydantic，供 run 详情读取与 worker 领取共用。"""
    return WorkflowRun(
        id=row.id,
        workflow_name=row.workflow_name,
        status=row.status,
        attempt=row.attempt,
        input=row.input or {},
        output=row.output or {},
        definition=row.definition or {},
        checkpoint=row.checkpoint or {},
        started_at=row.started_at,
        finished_at=row.finished_at,
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
            for step in row.steps
        ],
    )


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
        run_id, _ = await self.create_run_once(
            query,
            request_hash="",
            execution=execution,
            lease_owner=lease_owner,
        )
        return run_id

    async def create_run_once(
        self,
        query: str,
        *,
        request_hash: str,
        idempotency_key: str | None = None,
        execution: WorkflowRun | None = None,
        lease_owner: str | None = None,
        claimable: bool = False,
    ) -> tuple[str, bool]:
        """Insert a run and its initial workflow atomically.

        The unique idempotency index is the cross-process arbiter.  A failed
        insert is followed by a read of the winning row so retries return the
        original run rather than launching a second worker.
        """
        try:
            async with self._sm() as s, s.begin():
                run = orm.ResearchRun(
                    query=query,
                    status="pending",
                    idempotency_key=idempotency_key,
                    request_hash=request_hash or None,
                    claimable_at=datetime.now(UTC) if claimable else None,
                )
                s.add(run)
                await s.flush()
                if execution is not None:
                    s.add(
                        orm.WorkflowRunRow(
                            id=execution.id,
                            research_run_id=run.id,
                            workflow_name=execution.workflow_name,
                            status=execution.status.value,
                            attempt=execution.attempt,
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
                return run.id, True
        except IntegrityError as exc:
            if not idempotency_key:
                raise
            async with self._sm() as s:
                row = await s.scalar(
                    select(orm.ResearchRun).where(
                        orm.ResearchRun.idempotency_key == idempotency_key
                    )
                )
                if row is None:
                    raise
                if row.request_hash != request_hash:
                    raise IdempotencyConflictError(
                        "idempotency key was already used for a different request"
                    ) from exc
                return row.id, False

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

    async def request_cancel(self, run_id: str) -> str | None:
        async with self._sm() as s, s.begin():
            result = await s.execute(
                update(orm.ResearchRun)
                .where(
                    orm.ResearchRun.id == run_id,
                    orm.ResearchRun.status.in_(("pending", "running")),
                )
                .values(status="cancelling")
            )
            if not cast("CursorResult[Any]", result).rowcount:
                return await s.scalar(
                    select(orm.ResearchRun.status).where(orm.ResearchRun.id == run_id)
                )
            return "cancelling"

    async def prepare_resume(self, run_id: str, *, lease_owner: str) -> int:
        async with self._sm() as s, s.begin():
            workflow = await self._owned_workflow_row(s, run_id, lease_owner)
            run = await s.get(orm.ResearchRun, run_id)
            if run is not None:
                run.status = "running"
            if workflow is None:
                workflow = await s.scalar(
                    select(orm.WorkflowRunRow)
                    .where(orm.WorkflowRunRow.research_run_id == run_id)
                    .with_for_update()
                )
            if workflow is None:
                raise ValueError(f"run {run_id} has no workflow row")
            workflow.attempt = max(1, workflow.attempt or 1) + 1
            return workflow.attempt

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
                        entity=f.entity,
                        source_url=f.source_url,
                        evidence_quote=f.evidence_quote,
                        confidence=f.confidence,
                        verification_status=f.verification.status,
                        verification_method=f.verification.method,
                        source_content_hash=f.verification.source_content_hash,
                        source_title=f.verification.source_title,
                        source_reference=f.verification.source_reference,
                        source_identity=(
                            f.verification.source_identity.model_dump(mode="json")
                            if f.verification.source_identity
                            else None
                        ),
                        quantity_status=f.verification.quantity_status,
                        quantity_reason=f.verification.quantity_reason,
                        quantity=(f.quantity.model_dump(mode="json") if f.quantity else None),
                        conditions=(f.conditions.model_dump(mode="json") if f.conditions else None),
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
                        "scholarly": (
                            source.scholarly.model_dump(mode="json")
                            if source.scholarly is not None
                            else None
                        ),
                    }
                )
            if not values:
                return
            dialect = s.bind.dialect.name if s.bind is not None else ""
            insert = postgresql_insert if dialect == "postgresql" else sqlite_insert
            statement = insert(orm.SourceRow).values(values)
            statement = statement.on_conflict_do_update(
                index_elements=["run_id", "url", "content_hash"],
                set_={
                    "title": statement.excluded.title,
                    "content": statement.excluded.content,
                    "scholarly": statement.excluded.scholarly,
                },
            )
            await s.execute(statement)

    async def save_report(self, run_id: str, report: Report) -> None:
        async with self._sm() as s, s.begin():
            # ``save_report`` is an overwrite operation and may be called by
            # concurrent retry/resume paths.  A read-then-insert sequence has
            # a race: both transactions can observe no row, then one loses on
            # the unique ``report.run_id`` constraint.  Use the database's
            # atomic conflict arbiter instead.
            dialect = s.bind.dialect.name if s.bind is not None else ""
            insert = postgresql_insert if dialect == "postgresql" else sqlite_insert
            statement = insert(orm.ReportRow).values(
                run_id=run_id,
                markdown=report.markdown,
                citations=report.citations,
            )
            statement = statement.on_conflict_do_update(
                index_elements=["run_id"],
                set_={
                    "markdown": statement.excluded.markdown,
                    "citations": statement.excluded.citations,
                },
            )
            await s.execute(statement)

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
            workflow = await s.scalar(
                select(orm.WorkflowRunRow).where(orm.WorkflowRunRow.research_run_id == run_id)
            )
            attempt = workflow.attempt if workflow is not None else 1
            durable = [event for event in events if event.type != "token"]
            for i, ev in enumerate(durable):
                s.add(
                    orm.EventRow(
                        run_id=run_id,
                        seq=i,
                        attempt=attempt,
                        stage=ev.stage,
                        type=ev.type,
                        message=ev.message,
                        elapsed=ev.elapsed,
                        data=ev.data,
                    )
                )

    async def append_events(
        self, run_id: str, events: list[Event], *, lease_owner: str | None = None
    ) -> list[Event]:
        """Append a checkpoint's new events with monotonically increasing ids."""
        durable = [event for event in events if event.type != "token"]
        if not durable:
            return []
        async with self._sm() as s, s.begin():
            await self._owned_workflow_row(s, run_id, lease_owner)
            # Lock the root row before reading MAX(seq), which serializes
            # appenders on PostgreSQL and forces SQLite into its writer path.
            await s.execute(
                update(orm.ResearchRun)
                .where(orm.ResearchRun.id == run_id)
                .values(status=orm.ResearchRun.status)
            )
            workflow = await s.scalar(
                select(orm.WorkflowRunRow).where(orm.WorkflowRunRow.research_run_id == run_id)
            )
            attempt = workflow.attempt if workflow is not None else 1
            last_seq = await s.scalar(
                select(func.max(orm.EventRow.seq)).where(orm.EventRow.run_id == run_id)
            )
            next_seq = int(last_seq) + 1 if last_seq is not None else 0
            appended: list[Event] = []
            for offset, event in enumerate(durable):
                stored = event.model_copy(update={"seq": next_seq + offset, "attempt": attempt})
                appended.append(stored)
                s.add(
                    orm.EventRow(
                        run_id=run_id,
                        seq=next_seq + offset,
                        attempt=attempt,
                        stage=event.stage,
                        type=event.type,
                        message=event.message,
                        elapsed=event.elapsed,
                        data=event.data,
                    )
                )
            return appended

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
            row.attempt = execution.attempt
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

    async def enqueue_run(self, run_id: str) -> bool:
        async with self._sm() as s, s.begin():
            result = await s.execute(
                update(orm.ResearchRun)
                .where(
                    orm.ResearchRun.id == run_id,
                    orm.ResearchRun.status.in_(tuple(RUN_ACTIVE_STATUSES)),
                    orm.ResearchRun.claimable_at.is_(None),
                )
                .values(claimable_at=datetime.now(UTC))
            )
            return bool(cast("CursorResult[Any]", result).rowcount)

    async def requeue_failed_run(self, run_id: str) -> bool:
        now = datetime.now(UTC)
        owner = f"resume-{uuid4().hex}"
        async with self._sm() as s, s.begin():
            fenced = await s.execute(
                update(orm.WorkflowRunRow)
                .where(
                    orm.WorkflowRunRow.research_run_id == run_id,
                    or_(
                        orm.WorkflowRunRow.lease_owner.is_(None),
                        orm.WorkflowRunRow.lease_expires_at.is_(None),
                        orm.WorkflowRunRow.lease_expires_at <= now,
                    ),
                )
                .values(lease_owner=owner, lease_expires_at=now + timedelta(seconds=120))
            )
            if not cast("CursorResult[Any]", fenced).rowcount:
                return False
            workflow = await s.scalar(
                select(orm.WorkflowRunRow).where(
                    orm.WorkflowRunRow.research_run_id == run_id,
                    orm.WorkflowRunRow.lease_owner == owner,
                )
            )
            if workflow is None:
                return False
            if not workflow.checkpoint:
                workflow.lease_owner = None
                workflow.lease_expires_at = None
                return False
            reopened = await s.execute(
                update(orm.ResearchRun)
                .where(
                    orm.ResearchRun.id == run_id,
                    orm.ResearchRun.status == "error",
                )
                .values(status="running", claimable_at=now, finished_at=None)
            )
            if not cast("CursorResult[Any]", reopened).rowcount:
                workflow.lease_owner = None
                workflow.lease_expires_at = None
                return False
            workflow.lease_owner = None
            workflow.lease_expires_at = None
            return True

    async def claim_next_run(self, owner: str, *, lease_seconds: int = 120) -> ClaimedRun | None:
        """Claim the oldest queued or abandoned run whose lease is free.

        Candidate selection and the actual claim are deliberately separate.
        ``SKIP LOCKED`` only reduces contention between concurrent workers; the
        conditional lease UPDATE is what actually guarantees single ownership,
        and it is the same arbiter crash recovery already relies on.  A worker
        that loses the race simply moves on to the next candidate.
        """
        for _ in range(_CLAIM_CANDIDATE_LIMIT):
            candidate = await self._next_claim_candidate()
            if candidate is None:
                return None
            if not await self.acquire_lease(candidate, owner, seconds=lease_seconds):
                continue
            claimed = await self._finish_claim(candidate, owner)
            if claimed is not None:
                return claimed
            # The row changed between selection and fencing (finished,
            # cancelled, or dequeued).  Give the lease back and look further.
            await self.release_lease(candidate, owner)
        return None

    async def _next_claim_candidate(self) -> str | None:
        now = datetime.now(UTC)
        async with self._sm() as s, s.begin():
            stmt = (
                select(orm.ResearchRun.id)
                .join(
                    orm.WorkflowRunRow,
                    orm.WorkflowRunRow.research_run_id == orm.ResearchRun.id,
                )
                .where(
                    orm.ResearchRun.status.in_(("pending", "running")),
                    orm.ResearchRun.claimable_at.is_not(None),
                    orm.ResearchRun.claimable_at <= now,
                    or_(
                        orm.WorkflowRunRow.lease_owner.is_(None),
                        orm.WorkflowRunRow.lease_expires_at.is_(None),
                        orm.WorkflowRunRow.lease_expires_at <= now,
                    ),
                )
                .order_by(orm.ResearchRun.claimable_at)
                .limit(1)
            )
            if s.get_bind().dialect.name == "postgresql":
                # SQLite ignores row locking; there the conditional lease UPDATE
                # below remains the only arbiter, which is sufficient for the
                # single-node development target.
                stmt = stmt.with_for_update(skip_locked=True, of=orm.ResearchRun)
            return await s.scalar(stmt)

    async def _finish_claim(self, run_id: str, owner: str) -> ClaimedRun | None:
        """Re-read behind the lease, then mark the run as owned by this worker.

        Reloading after fencing is mandatory: the candidate query ran without
        the lease, so the row may have completed or been cancelled in between.
        """
        async with self._sm() as s, s.begin():
            row = await s.get(orm.ResearchRun, run_id)
            if row is None or row.status not in ("pending", "running"):
                return None
            if row.claimable_at is None:
                return None
            workflow = await s.scalar(
                select(orm.WorkflowRunRow)
                .where(orm.WorkflowRunRow.research_run_id == run_id)
                .options(selectinload(orm.WorkflowRunRow.steps))
            )
            if workflow is None or workflow.lease_owner != owner:
                return None
            resumed = bool(workflow.checkpoint) and row.status == "running"
            row.claim_attempts = (row.claim_attempts or 0) + 1
            row.status = "running"
            if resumed:
                # Resuming re-emits the run's event stream, so the attempt
                # counter must advance for SSE to distinguish the new pass.
                # ``attempt`` lives only on the workflow row (see prepare_resume).
                workflow.attempt = max(1, workflow.attempt or 1) + 1
            execution = _workflow_run(workflow)
            return ClaimedRun(
                run_id=run_id,
                query=row.query,
                lease_owner=owner,
                execution=execution,
                attempt=workflow.attempt,
                claim_attempts=row.claim_attempts,
                resumed=resumed,
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
                if run.status != "cancelling":
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
                            entity=f.entity or "",
                            source_url=f.source_url,
                            evidence_quote=f.evidence_quote,
                            confidence=f.confidence,
                            quantity=(
                                Quantity.model_validate(f.quantity)
                                if isinstance(f.quantity, dict)
                                else None
                            ),
                            conditions=(
                                ExperimentConditions.model_validate(f.conditions)
                                if isinstance(f.conditions, dict)
                                else None
                            ),
                            verification=EvidenceVerification(
                                status=f.verification_status,
                                method=f.verification_method,
                                source_content_hash=f.source_content_hash,
                                source_title=f.source_title,
                                source_reference=f.source_reference or "",
                                source_identity=(
                                    SourceIdentity.model_validate(f.source_identity)
                                    if isinstance(f.source_identity, dict)
                                    else None
                                ),
                                quantity_status=f.quantity_status or "not_applicable",
                                quantity_reason=f.quantity_reason or "",
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
                orchestration = _workflow_run(run.orchestration)
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
                        scholarly=(
                            ScholarlyMetadata.model_validate(source.scholarly)
                            if isinstance(source.scholarly, dict)
                            else None
                        ),
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

    async def get_run_attempt(self, run_id: str) -> int | None:
        async with self._sm() as s:
            return await s.scalar(
                select(orm.WorkflowRunRow.attempt).where(
                    orm.WorkflowRunRow.research_run_id == run_id
                )
            )

    async def get_events(
        self, run_id: str, *, after_seq: int = 0, limit: int | None = None
    ) -> list[Event]:
        async with self._sm() as s:
            stmt = (
                select(orm.EventRow)
                .where(orm.EventRow.run_id == run_id, orm.EventRow.seq >= after_seq)
                .order_by(orm.EventRow.seq)
            )
            if limit is not None:
                stmt = stmt.limit(limit)
            rows = (await s.scalars(stmt)).all()
            return [
                Event(
                    seq=r.seq,
                    attempt=r.attempt,
                    stage=r.stage,
                    type=r.type,
                    message=r.message,
                    elapsed=r.elapsed,
                    data=r.data,
                )
                for r in rows
            ]

    async def healthcheck(self) -> bool:
        async with self._sm() as s:
            return (await s.scalar(select(1))) == 1
