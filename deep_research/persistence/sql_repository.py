"""PostgreSQL / SQLite 仓储（async SQLAlchemy 2.0）。

每个写方法独立事务（async with session.begin()）；读方法用 selectinload 预取
关系，避免 async 下的惰性加载问题。ORM↔Pydantic 的转换内联于各方法。
"""

from __future__ import annotations

import hashlib
import logging
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
    ExtractionAudit,
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
from ..resume_window import renewed_checkpoint
from ..scheduling import (
    ActiveRun,
    IdentityCredit,
    ScheduleCandidate,
    ScheduleKind,
    SchedulerState,
    dispatch_info,
    eligible_candidates,
    identity_key,
    schedule_for_execution,
    select_next,
    validate_priority,
)
from . import orm
from .coordination import transaction_lock
from .repository import (
    EXECUTION_LEASE_SECONDS,
    RUN_ACTIVE_STATUSES,
    ClaimedRun,
    IdempotencyConflictError,
    LeaseLostError,
    RunDetail,
    RunQueueFullError,
    RunSummary,
    TagCount,
)

logger = logging.getLogger(__name__)

_SOURCE_CONTEXT_FIELDS = {
    "section_title",
    "section_start",
    "section_end",
    "document_content_hash",
    "document_part_index",
    "document_part_count",
}


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
        search_queries=sub_question.search_queries,
        origin=origin,
        round=round_,
    )


def _research_result_row(run_id: str, result: ResearchResult) -> orm.ResearchResultRow:
    row = orm.ResearchResultRow(
        run_id=run_id,
        sub_question=result.sub_question,
        extraction_audit=result.extraction_audit.model_dump(mode="json")
        if result.extraction_audit
        else None,
    )
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
            quote_start=finding.verification.quote_start,
            quote_end=finding.verification.quote_end,
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
        owner_id: str | None = None,
        project_id: str | None = None,
        max_inflight: int | None = None,
        schedule_priority: int = 1,
    ) -> tuple[str, bool]:
        """Insert a run and its initial workflow atomically.

        The unique idempotency index is the cross-process arbiter.  A failed
        insert is followed by a read of the winning row so retries return the
        original run rather than launching a second worker.
        """
        validate_priority(schedule_priority)
        try:
            async with self._sm() as s, s.begin():
                await transaction_lock(s, "run-admission")
                if idempotency_key:
                    existing = await s.scalar(
                        select(orm.ResearchRun).where(
                            orm.ResearchRun.idempotency_key == idempotency_key
                        )
                    )
                    if existing is not None:
                        if existing.request_hash != request_hash:
                            raise IdempotencyConflictError("Idempotency-Key 已用于不同的请求")
                        return existing.id, False
                schedule = schedule_for_execution(execution)
                await self._check_capacity(s, max_inflight)
                run = orm.ResearchRun(
                    query=query,
                    owner_id=owner_id,
                    project_id=project_id,
                    status="pending",
                    idempotency_key=idempotency_key,
                    request_hash=request_hash or None,
                    claimable_at=datetime.now(UTC) if claimable else None,
                    schedule_cost=schedule.cost,
                    schedule_class=schedule.kind,
                    schedule_priority=schedule_priority,
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

    async def _check_capacity(self, session: AsyncSession, maximum: int | None) -> None:
        if maximum is not None:
            count = await session.scalar(
                select(func.count())
                .select_from(orm.ResearchRun)
                .where(orm.ResearchRun.status.in_(RUN_ACTIVE_STATUSES))
            )
            if int(count or 0) >= maximum:
                raise RunQueueFullError("研究队列已满，请稍后重试")

    async def find_run_once(self, idempotency_key: str, request_hash: str) -> str | None:
        async with self._sm() as s:
            row = await s.scalar(
                select(orm.ResearchRun).where(orm.ResearchRun.idempotency_key == idempotency_key)
            )
            if row is None:
                return None
            if row.request_hash != request_hash:
                raise IdempotencyConflictError("Idempotency-Key 已用于不同的请求")
            return row.id

    async def get_run_owner(self, run_id: str) -> str | None:
        async with self._sm() as s:
            return await s.scalar(
                select(orm.ResearchRun.owner_id).where(orm.ResearchRun.id == run_id)
            )

    async def heartbeat_worker(self, name: str, active: int) -> None:
        async with self._sm() as s, s.begin():
            row = await s.get(orm.WorkerHeartbeatRow, name)
            if row is None:
                row = orm.WorkerHeartbeatRow(name=name)
                s.add(row)
            row.seen_at, row.active = datetime.now(UTC), active

    async def remove_worker(self, name: str) -> None:
        async with self._sm() as s, s.begin():
            await s.execute(
                sa_delete(orm.WorkerHeartbeatRow).where(orm.WorkerHeartbeatRow.name == name)
            )

    async def service_status(self, *, heartbeat_seconds: int = 30) -> dict[str, int | float]:
        async with self._sm() as s:
            now = datetime.now(UTC)
            workers = list(
                await s.scalars(
                    select(orm.WorkerHeartbeatRow).where(
                        orm.WorkerHeartbeatRow.seen_at > now - timedelta(seconds=heartbeat_seconds)
                    )
                )
            )
            queued, oldest = (
                await s.execute(
                    select(func.count(), func.min(orm.ResearchRun.claimable_at)).where(
                        orm.ResearchRun.status == "pending",
                        orm.ResearchRun.claimable_at.is_not(None),
                    )
                )
            ).one()
            counts = {
                status: int(count)
                for status, count in await s.execute(
                    select(orm.ResearchRun.status, func.count()).group_by(orm.ResearchRun.status)
                )
            }
            tokens = await s.scalar(select(func.sum(orm.ResearchRun.total_tokens)))
            provider_requests, provider_limited = (
                await s.execute(
                    select(
                        func.sum(orm.ProviderStateRow.requests),
                        func.sum(orm.ProviderStateRow.limited),
                    )
                )
            ).one()
            cleanup_pending = await s.scalar(
                select(func.count()).select_from(orm.ArtifactCleanupRow)
            )
            return {
                "workers": len(workers),
                "active": sum(w.active for w in workers),
                "queued": queued,
                "oldest_queued_seconds": max(0, (now - oldest.replace(tzinfo=UTC)).total_seconds())
                if oldest
                else 0,
                "done": counts.get("done", 0),
                "error": counts.get("error", 0),
                "total_tokens": int(tokens or 0),
                "provider_requests": int(provider_requests or 0),
                "provider_limited": int(provider_limited or 0),
                "artifact_cleanup_pending": int(cleanup_pending or 0),
            }

    async def pending_artifact_cleanup(self) -> list[tuple[str, str]]:
        async with self._sm() as s:
            return [
                (row.run_id, row.slug)
                for row in await s.scalars(
                    select(orm.ArtifactCleanupRow)
                    .order_by(orm.ArtifactCleanupRow.created_at)
                    .limit(100)
                )
            ]

    async def artifact_slug_in_use(self, slug: str) -> bool:
        async with self._sm() as s:
            return bool(
                await s.scalar(
                    select(func.count())
                    .select_from(orm.WorkflowRunRow)
                    .where(
                        orm.WorkflowRunRow.checkpoint["scratch"]["_artifact_slug"].as_string()
                        == slug
                    )
                )
            )

    async def finish_artifact_cleanup(self, run_id: str) -> None:
        async with self._sm() as s, s.begin():
            await s.execute(
                sa_delete(orm.ArtifactCleanupRow).where(orm.ArtifactCleanupRow.run_id == run_id)
            )

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
                .values(status="cancelling", cancel_requested_at=datetime.now(UTC))
            )
            if not cast("CursorResult[Any]", result).rowcount:
                return await s.scalar(
                    select(orm.ResearchRun.status).where(orm.ResearchRun.id == run_id)
                )
            return "cancelling"

    async def prepare_resume(
        self, run_id: str, *, lease_owner: str, restart_seconds: int | None = None
    ) -> int:
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
            if restart_seconds is not None:
                workflow.checkpoint = renewed_checkpoint(workflow.checkpoint, restart_seconds)
            return workflow.attempt

    async def save_plan(
        self, run_id: str, plan: ResearchPlan, *, lease_owner: str | None = None
    ) -> None:
        async with self._sm() as s, s.begin():
            await self._owned_workflow_row(s, run_id, lease_owner)
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
                        search_queries=sq.search_queries,
                        origin="plan",
                        round=0,
                    )
                )

    async def add_sub_questions(
        self,
        run_id: str,
        sub_questions: list[SubQuestion],
        *,
        origin: str,
        round: int,
        lease_owner: str | None = None,
    ) -> None:
        async with self._sm() as s, s.begin():
            # The fenced no-op UPDATE also serialises concurrent writers on this
            # run, so the max(idx) read below cannot race another append.
            await self._owned_workflow_row(s, run_id, lease_owner)
            # max(idx)+1 rather than count(): a gap left by a deleted row would
            # otherwise make count() hand out an idx that is already taken.
            highest = await s.scalar(
                select(func.max(orm.SubQuestionRow.idx)).where(orm.SubQuestionRow.run_id == run_id)
            )
            base = 0 if highest is None else int(highest) + 1
            for j, sq in enumerate(sub_questions):
                s.add(
                    orm.SubQuestionRow(
                        run_id=run_id,
                        idx=base + j,
                        question=sq.question,
                        rationale=sq.rationale,
                        depends_on=sq.depends_on,
                        search_queries=sq.search_queries,
                        origin=origin,
                        round=round,
                    )
                )

    async def save_result(
        self, run_id: str, result: ResearchResult, *, lease_owner: str | None = None
    ) -> None:
        async with self._sm() as s, s.begin():
            await self._owned_workflow_row(s, run_id, lease_owner)
            row = orm.ResearchResultRow(
                run_id=run_id,
                sub_question=result.sub_question,
                extraction_audit=result.extraction_audit.model_dump(mode="json")
                if result.extraction_audit
                else None,
            )
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
                        quote_start=f.verification.quote_start,
                        quote_end=f.verification.quote_end,
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
                source_context = source.model_dump(mode="json", include=_SOURCE_CONTEXT_FIELDS)
                values.append(
                    {
                        "run_id": run_id,
                        "title": source.title,
                        "url": source.url,
                        "content": source.content,
                        "content_hash": content_hash,
                        "locator": source.locator,
                        "document_authors": source.document_authors,
                        "source_context": source_context if any(source_context.values()) else None,
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
                    "locator": statement.excluded.locator,
                    "document_authors": statement.excluded.document_authors,
                    "source_context": func.coalesce(
                        statement.excluded.source_context, orm.SourceRow.source_context
                    ),
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
            durable = events
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
                        tokens=ev.tokens,
                        tokens_estimated=ev.tokens_estimated,
                    )
                )

    async def append_events(
        self, run_id: str, events: list[Event], *, lease_owner: str | None = None
    ) -> list[Event]:
        """Append a checkpoint's new events with monotonically increasing ids."""
        durable = events
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
                        tokens=event.tokens,
                        tokens_estimated=event.tokens_estimated,
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

    async def acquire_lease(
        self, run_id: str, owner: str, *, seconds: int = EXECUTION_LEASE_SECONDS
    ) -> bool:
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

    async def renew_lease(
        self, run_id: str, owner: str, *, seconds: int = EXECUTION_LEASE_SECONDS
    ) -> bool:
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
            await transaction_lock(s, "run-admission")
            now = datetime.now(UTC)
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
                .values(lease_owner=orm.WorkflowRunRow.lease_owner)
                .execution_options(synchronize_session=False)
            )
            if not cast("CursorResult[Any]", result).rowcount:
                return False
            checkpoint = await s.scalar(
                select(orm.WorkflowRunRow.checkpoint).where(
                    orm.WorkflowRunRow.research_run_id == run_id
                )
            )
            if not isinstance(checkpoint, dict) or not checkpoint:
                return False
            result = await s.execute(
                update(orm.ResearchRun)
                .where(
                    orm.ResearchRun.id == run_id,
                    orm.ResearchRun.status.in_(("pending", "running")),
                    orm.ResearchRun.claimable_at.is_(None),
                )
                .values(claimable_at=now)
                .execution_options(synchronize_session=False)
            )
            return bool(cast("CursorResult[Any]", result).rowcount)

    async def defer_run(
        self, run_id: str, execution: WorkflowRun, *, lease_owner: str, not_before: float
    ) -> bool:
        async with self._sm() as s, s.begin():
            workflow = await self._owned_workflow_row(s, run_id, lease_owner)
            result = await s.execute(
                update(orm.ResearchRun)
                .where(
                    orm.ResearchRun.id == run_id,
                    orm.ResearchRun.status.in_(("pending", "running", "error")),
                )
                .values(status="running", claimable_at=datetime.fromtimestamp(not_before, UTC))
            )
            if not cast("CursorResult[Any]", result).rowcount:
                return False
            assert workflow is not None
            workflow.checkpoint = execution.checkpoint
            return True

    async def requeue_failed_run(
        self, run_id: str, *, max_inflight: int | None = None, restart_seconds: int | None = None
    ) -> bool:
        now = datetime.now(UTC)
        owner = f"resume-{uuid4().hex}"
        async with self._sm() as s, s.begin():
            await transaction_lock(s, "run-admission")
            await self._check_capacity(s, max_inflight)
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
                .values(
                    lease_owner=owner,
                    lease_expires_at=now + timedelta(seconds=EXECUTION_LEASE_SECONDS),
                )
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
            if restart_seconds is not None:
                workflow.checkpoint = renewed_checkpoint(workflow.checkpoint, restart_seconds)
            workflow.lease_owner = None
            workflow.lease_expires_at = None
            return True

    async def claim_next_run(
        self,
        owner: str,
        *,
        lease_seconds: int = EXECUTION_LEASE_SECONDS,
        max_active_runs: int | None = None,
    ) -> ClaimedRun | None:
        """Choose and charge one identity atomically with its execution lease."""
        async with self._sm() as s, s.begin():
            await transaction_lock(s, "run-admission")
            now = datetime.now(UTC)
            active_rows = (
                await s.execute(
                    select(orm.ResearchRun.owner_id, orm.ResearchRun.schedule_class)
                    .join(
                        orm.WorkflowRunRow,
                        orm.WorkflowRunRow.research_run_id == orm.ResearchRun.id,
                    )
                    .where(
                        orm.ResearchRun.status.in_(RUN_ACTIVE_STATUSES),
                        orm.WorkflowRunRow.lease_owner.is_not(None),
                        orm.WorkflowRunRow.lease_expires_at > now,
                    )
                )
            ).all()
            active = [
                ActiveRun(identity_key(row.owner_id), cast(ScheduleKind, row.schedule_class))
                for row in active_rows
            ]
            if max_active_runs is not None and len(active) >= max_active_runs:
                return None
            lease_free = or_(
                orm.WorkflowRunRow.lease_owner.is_(None),
                orm.WorkflowRunRow.lease_expires_at.is_(None),
                orm.WorkflowRunRow.lease_expires_at <= now,
            )
            # Do not truncate runs before grouping by identity: a large queue
            # owned by one caller must not hide everybody else's eligible head.
            rows = (
                await s.execute(
                    select(orm.ResearchRun, orm.WorkflowRunRow)
                    .join(
                        orm.WorkflowRunRow,
                        orm.WorkflowRunRow.research_run_id == orm.ResearchRun.id,
                    )
                    .where(
                        orm.ResearchRun.status.in_(("pending", "running")),
                        orm.ResearchRun.claimable_at <= now,
                        lease_free,
                    )
                )
            ).all()

            def stamp(value: datetime) -> float:
                return value.replace(tzinfo=UTC).timestamp()

            candidates = []
            for run, workflow in rows:
                ready_at = stamp(run.claimable_at)
                if workflow.lease_owner is not None and workflow.lease_expires_at is not None:
                    ready_at = max(ready_at, stamp(workflow.lease_expires_at))
                candidates.append(
                    ScheduleCandidate(
                        run.id,
                        identity_key(run.owner_id),
                        run.schedule_cost,
                        cast(ScheduleKind, run.schedule_class),
                        run.schedule_priority,
                        ready_at,
                    )
                )
            candidates = eligible_candidates(candidates, active, max_active_runs)
            if not candidates:
                return None
            state_row = await s.get(orm.SchedulerStateRow, "runs")
            state = (
                SchedulerState(state_row.cursor, state_row.round_no)
                if state_row is not None
                else SchedulerState()
            )
            credit_rows = {
                row.identity_key: row
                for row in (
                    await s.scalars(
                        select(orm.SchedulerIdentityRow).where(
                            orm.SchedulerIdentityRow.identity_key.in_(
                                {candidate.identity for candidate in candidates}
                            )
                        )
                    )
                ).all()
            }
            credits = {
                key: IdentityCredit(row.deficit, row.last_round) for key, row in credit_rows.items()
            }
            selection = select_next(candidates, credits, state, now=now.timestamp())
            assert selection is not None
            selected = selection.candidate
            # Non-scheduler writers (cancellation, explicit lease recovery) may
            # have changed a candidate since the read. Fence W then R, matching
            # the repository's existing workflow-before-root lock order.
            result = await s.execute(
                update(orm.WorkflowRunRow)
                .where(orm.WorkflowRunRow.research_run_id == selected.run_id, lease_free)
                .values(lease_owner=owner, lease_expires_at=now + timedelta(seconds=lease_seconds))
                .execution_options(synchronize_session=False)
            )
            if not cast("CursorResult[Any]", result).rowcount:
                await s.rollback()
                return None
            result = await s.execute(
                update(orm.ResearchRun)
                .where(
                    orm.ResearchRun.id == selected.run_id,
                    orm.ResearchRun.status.in_(("pending", "running")),
                    orm.ResearchRun.claimable_at <= now,
                )
                .values(status=orm.ResearchRun.status)
                .execution_options(synchronize_session=False)
            )
            if not cast("CursorResult[Any]", result).rowcount:
                await s.rollback()
                return None
            run = await s.scalar(
                select(orm.ResearchRun)
                .where(orm.ResearchRun.id == selected.run_id)
                .execution_options(populate_existing=True)
            )
            workflow = await s.scalar(
                select(orm.WorkflowRunRow)
                .where(orm.WorkflowRunRow.research_run_id == selected.run_id)
                .options(selectinload(orm.WorkflowRunRow.steps))
                .execution_options(populate_existing=True)
            )
            assert run is not None and workflow is not None
            resumed = bool(workflow.checkpoint) and run.status == "running"
            run.status = "running"
            run.claim_attempts = (run.claim_attempts or 0) + 1
            if resumed:
                workflow.attempt = max(1, workflow.attempt or 1) + 1
            if state_row is None:
                state_row = orm.SchedulerStateRow(name="runs")
                s.add(state_row)
            state_row.cursor = selection.state.cursor
            state_row.round_no = selection.state.round_no
            for identity, credit in selection.credits.items():
                row = credit_rows.get(identity)
                if row is None:
                    row = orm.SchedulerIdentityRow(identity_key=identity)
                    s.add(row)
                row.deficit, row.last_round = credit.deficit, credit.last_round
            await s.flush()
            logger.info(
                "claimed run %s identity=%s class=%s cost=%s priority=%s wait_seconds=%.3f",
                run.id,
                hashlib.sha256(selected.identity.encode()).hexdigest()[:12],
                selected.kind,
                selected.cost,
                selected.priority,
                max(0.0, now.timestamp() - selected.ready_at),
            )
            return ClaimedRun(
                run.id,
                run.query,
                owner,
                _workflow_run(workflow),
                workflow.attempt,
                run.claim_attempts,
                resumed,
                dispatch=dispatch_info(selected, now=now.timestamp()),
            )

    async def _claim_with_limit(self, owner: str, seconds: int, maximum: int) -> ClaimedRun | None:
        """Compatibility entry point; all claims use the same transaction."""
        return await self.claim_next_run(owner, lease_seconds=seconds, max_active_runs=maximum)

    async def finalize(
        self,
        run_id: str,
        *,
        elapsed: float,
        total_tokens: int,
        lease_owner: str | None = None,
        completion: dict[str, Any] | None = None,
    ) -> None:
        async with self._sm() as s, s.begin():
            workflow = await self._owned_workflow_row(s, run_id, lease_owner)
            if lease_owner is None:
                await s.execute(
                    update(orm.WorkflowRunRow)
                    .where(orm.WorkflowRunRow.research_run_id == run_id)
                    .values(checkpoint=orm.WorkflowRunRow.checkpoint)
                )
            await s.execute(
                update(orm.ResearchRun)
                .where(orm.ResearchRun.id == run_id)
                .values(status=orm.ResearchRun.status)
            )
            if completion is not None and workflow is None:
                workflow = await s.scalar(
                    select(orm.WorkflowRunRow).where(orm.WorkflowRunRow.research_run_id == run_id)
                )
            run = await s.get(orm.ResearchRun, run_id)
            if run is not None:
                run.elapsed = elapsed
                run.total_tokens = total_tokens
                if run.status in {"pending", "running"}:
                    if completion is not None:
                        if workflow is None or completion.get("status") not in {
                            "done",
                            "needs_review",
                        }:
                            raise ValueError("invalid task completion record")
                        checkpoint = dict(workflow.checkpoint)
                        checkpoint["scratch"] = {
                            **checkpoint.get("scratch", {}),
                            "_completion": completion,
                        }
                        workflow.checkpoint = checkpoint
                    run.status = completion["status"] if completion else "done"
                run.finished_at = datetime.now(UTC)

    async def update_completion(
        self, run_id: str, completion: dict[str, Any], *, expected_version: str
    ) -> bool:
        async with self._sm() as s, s.begin():
            # Same workflow -> root lock order as leased finalization/resume.
            await s.execute(
                update(orm.WorkflowRunRow)
                .where(orm.WorkflowRunRow.research_run_id == run_id)
                .values(checkpoint=orm.WorkflowRunRow.checkpoint)
            )
            await s.execute(
                update(orm.ResearchRun)
                .where(orm.ResearchRun.id == run_id)
                .values(status=orm.ResearchRun.status)
            )
            run = await s.get(orm.ResearchRun, run_id)
            workflow = await s.scalar(
                select(orm.WorkflowRunRow).where(orm.WorkflowRunRow.research_run_id == run_id)
            )
            if run is None or run.status not in {"done", "needs_review"} or workflow is None:
                return False
            scratch = workflow.checkpoint.get("scratch", {})
            previous = scratch.get("_completion", {})
            if previous.get("content_version") != expected_version or previous.get(
                "input_version"
            ) != completion.get("input_version"):
                return False
            if completion.get("status") not in {"done", "needs_review"}:
                raise ValueError("invalid task completion status")
            workflow.checkpoint = {
                **workflow.checkpoint,
                "scratch": {**scratch, "_completion": completion},
            }
            run.status = completion["status"]
            return True

    async def delete_run(self, run_id: str) -> bool:
        # 单条 DELETE：DB 级 ondelete=CASCADE 清子表（SQLite 已开 foreign_keys=ON）
        async with self._sm() as s, s.begin():
            workflow = await s.scalar(
                select(orm.WorkflowRunRow).where(orm.WorkflowRunRow.research_run_id == run_id)
            )
            scratch = workflow.checkpoint.get("scratch", {}) if workflow else {}
            slug = scratch.get("_artifact_slug") if isinstance(scratch, dict) else None
            if isinstance(slug, str) and slug:
                target = f"runs/{run_id}" if scratch.get("_artifact_run_scoped") else slug
            else:
                target = f"runs/{run_id}"
            result = await s.execute(sa_delete(orm.ResearchRun).where(orm.ResearchRun.id == run_id))
            deleted = bool(cast("CursorResult[Any]", result).rowcount)
            if deleted:
                s.add(orm.ArtifactCleanupRow(run_id=run_id, slug=target))
            return deleted

    async def set_tags(self, run_id: str, tags: list[str]) -> None:
        # 替换语义：先清旧标签再写新（去重 + 去空白）
        cleaned = list(dict.fromkeys(t.strip() for t in tags if t.strip()))
        async with self._sm() as s, s.begin():
            await s.execute(sa_delete(orm.RunTagRow).where(orm.RunTagRow.run_id == run_id))
            for tag in cleaned:
                s.add(orm.RunTagRow(run_id=run_id, tag=tag))

    async def list_tags(self, *, owner_id: str | None = None) -> list[TagCount]:
        async with self._sm() as s:
            stmt = select(orm.RunTagRow.tag, func.count()).join(
                orm.ResearchRun, orm.ResearchRun.id == orm.RunTagRow.run_id
            )
            if owner_id is not None:
                stmt = stmt.where(orm.ResearchRun.owner_id == owner_id)
            rows = (
                await s.execute(
                    stmt.group_by(orm.RunTagRow.tag).order_by(
                        func.count().desc(), orm.RunTagRow.tag
                    )
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
        owner_id: str | None = None,
    ) -> list[RunSummary]:
        async with self._sm() as s:
            stmt = (
                select(orm.ResearchRun)
                .options(selectinload(orm.ResearchRun.tags))
                .order_by(orm.ResearchRun.created_at.desc())
            )
            if status:
                stmt = stmt.where(orm.ResearchRun.status == status)
            if owner_id is not None:
                stmt = stmt.where(orm.ResearchRun.owner_id == owner_id)
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
                    owner_id=r.owner_id,
                    project_id=r.project_id,
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
                    search_queries=list(sq.search_queries or []),
                )
                for sq in run.sub_questions
            ]
            results = [
                ResearchResult(
                    sub_question=rr.sub_question,
                    extraction_audit=ExtractionAudit.model_validate(rr.extraction_audit)
                    if rr.extraction_audit
                    else None,
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
                                quote_start=f.quote_start,
                                quote_end=f.quote_end,
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
                owner_id=run.owner_id,
                project_id=run.project_id,
                query=run.query,
                status=run.status,
                interpretation=run.interpretation,
                cancel_requested_at=(
                    run.cancel_requested_at.replace(tzinfo=UTC)
                    if (
                        run.cancel_requested_at is not None
                        and run.cancel_requested_at.tzinfo is None
                    )
                    else run.cancel_requested_at
                ),
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
                        locator=source.locator,
                        document_authors=source.document_authors or [],
                        **{
                            key: value
                            for key, value in (source.source_context or {}).items()
                            if key in _SOURCE_CONTEXT_FIELDS
                        },
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
                    tokens=r.tokens,
                    tokens_estimated=r.tokens_estimated,
                )
                for r in rows
            ]

    async def healthcheck(self) -> bool:
        async with self._sm() as s:
            return (await s.scalar(select(1))) == 1
