"""内存仓储：CLI 默认 + 单测离线零依赖，无需数据库。"""

from __future__ import annotations

import hashlib
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from ..models import Report, ResearchPlan, ResearchResult, Source, SubQuestion
from ..observability import Event
from ..orchestration import WorkflowRun
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


@dataclass
class _RunRecord:
    id: str
    query: str
    owner_id: str | None = None
    project_id: str | None = None
    status: str = "pending"
    cancel_requested_at: datetime | None = None
    interpretation: str = ""
    sub_questions: list[SubQuestion] = field(default_factory=list)
    results: list[ResearchResult] = field(default_factory=list)
    sources: list[Source] = field(default_factory=list)
    report: Report | None = None
    events: list[Event] = field(default_factory=list)
    total_tokens: int = 0
    elapsed: float = 0.0
    tags: list[str] = field(default_factory=list)
    orchestration: WorkflowRun | None = None
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    idempotency_key: str | None = None
    request_hash: str | None = None
    attempt: int = 1
    claimable_at: datetime | None = None
    claim_attempts: int = 0
    schedule_cost: int = 4
    schedule_class: ScheduleKind = "heavy"
    schedule_priority: int = 1


class InMemoryRepository:
    """把一切存在进程内存里。语义与 SqlRepository 对齐，便于在测试中互换。"""

    def __init__(self) -> None:
        self._runs: dict[str, _RunRecord] = {}
        self._order: list[str] = []
        self._idempotency: dict[str, tuple[str, str]] = {}
        self._workers: dict[str, tuple[datetime, int]] = {}
        self._artifact_cleanup: dict[str, str] = {}
        self._scheduler_state = SchedulerState()
        self._scheduler_credits: dict[str, IdentityCredit] = {}

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
        validate_priority(schedule_priority)
        if idempotency_key:
            existing = self._idempotency.get(idempotency_key)
            if existing is not None:
                existing_id, existing_hash = existing
                if existing_hash != request_hash:
                    raise IdempotencyConflictError(
                        "idempotency key was already used for a different request"
                    )
                return existing_id, False
        self._check_capacity(max_inflight)
        schedule = schedule_for_execution(execution)
        run_id = str(uuid4())
        record = _RunRecord(
            id=run_id,
            query=query,
            owner_id=owner_id,
            project_id=project_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            attempt=execution.attempt if execution is not None else 1,
            claimable_at=datetime.now(UTC) if claimable else None,
            schedule_cost=schedule.cost,
            schedule_class=schedule.kind,
            schedule_priority=schedule_priority,
        )
        if execution is not None:
            record.orchestration = execution.model_copy(deep=True)
            record.lease_owner = lease_owner
            record.lease_expires_at = (
                datetime.now(UTC) + timedelta(seconds=EXECUTION_LEASE_SECONDS)
                if lease_owner is not None
                else None
            )
        self._runs[run_id] = record
        self._order.append(run_id)
        if idempotency_key:
            self._idempotency[idempotency_key] = (run_id, request_hash)
        return run_id, True

    def _check_capacity(self, maximum: int | None) -> None:
        if (
            maximum is not None
            and sum(r.status in RUN_ACTIVE_STATUSES for r in self._runs.values()) >= maximum
        ):
            raise RunQueueFullError("研究队列已满，请稍后重试")

    async def find_run_once(self, idempotency_key: str, request_hash: str) -> str | None:
        existing = self._idempotency.get(idempotency_key)
        if existing is None:
            return None
        if existing[1] != request_hash:
            raise IdempotencyConflictError("Idempotency-Key 已用于不同的请求")
        return existing[0]

    async def get_run_owner(self, run_id: str) -> str | None:
        record = self._runs.get(run_id)
        return record.owner_id if record else None

    async def heartbeat_worker(self, name: str, active: int) -> None:
        self._workers[name] = (datetime.now(UTC), active)

    async def remove_worker(self, name: str) -> None:
        self._workers.pop(name, None)

    async def service_status(self, *, heartbeat_seconds: int = 30) -> dict[str, int | float]:
        now = datetime.now(UTC)
        workers = [
            active
            for seen, active in self._workers.values()
            if (now - seen).total_seconds() < heartbeat_seconds
        ]
        queued = [
            r for r in self._runs.values() if r.status == "pending" and r.claimable_at is not None
        ]
        return {
            "workers": len(workers),
            "active": sum(workers),
            "queued": len(queued),
            "oldest_queued_seconds": max(
                ((now - r.claimable_at).total_seconds() for r in queued if r.claimable_at),
                default=0,
            ),
            "done": sum(r.status == "done" for r in self._runs.values()),
            "error": sum(r.status == "error" for r in self._runs.values()),
            "total_tokens": sum(r.total_tokens for r in self._runs.values()),
        }

    async def pending_artifact_cleanup(self) -> list[tuple[str, str]]:
        return list(self._artifact_cleanup.items())[:100]

    async def finish_artifact_cleanup(self, run_id: str) -> None:
        self._artifact_cleanup.pop(run_id, None)

    async def artifact_slug_in_use(self, slug: str) -> bool:
        return any(
            record.orchestration
            and record.orchestration.checkpoint.get("scratch", {}).get("_artifact_slug") == slug
            for record in self._runs.values()
        )

    def _assert_lease(self, run_id: str, owner: str | None) -> None:
        if owner is None:
            return
        rec = self._runs.get(run_id)
        if (
            rec is None
            or rec.lease_owner != owner
            or rec.lease_expires_at is None
            or rec.lease_expires_at <= datetime.now(UTC)
        ):
            raise LeaseLostError(f"run {run_id} lease is no longer owned by this worker")

    async def set_status(self, run_id: str, status: str, *, lease_owner: str | None = None) -> None:
        self._assert_lease(run_id, lease_owner)
        self._runs[run_id].status = status

    async def prepare_resume(
        self, run_id: str, *, lease_owner: str, restart_seconds: int | None = None
    ) -> int:
        self._assert_lease(run_id, lease_owner)
        rec = self._runs[run_id]
        renewed = (
            renewed_checkpoint(rec.orchestration.checkpoint, restart_seconds)
            if rec.orchestration is not None and restart_seconds is not None
            else None
        )
        rec.status = "running"
        rec.attempt += 1
        if rec.orchestration is not None:
            rec.orchestration.attempt = rec.attempt
            if renewed is not None:
                rec.orchestration.checkpoint = renewed
        return rec.attempt

    async def request_cancel(self, run_id: str) -> str | None:
        rec = self._runs.get(run_id)
        if rec is None:
            return None
        if rec.status in {"pending", "running"}:
            rec.status = "cancelling"
            rec.cancel_requested_at = datetime.now(UTC)
        return rec.status

    async def save_plan(
        self, run_id: str, plan: ResearchPlan, *, lease_owner: str | None = None
    ) -> None:
        self._assert_lease(run_id, lease_owner)
        rec = self._runs[run_id]
        rec.interpretation = plan.interpretation
        rec.sub_questions.extend(plan.sub_questions)

    async def add_sub_questions(
        self,
        run_id: str,
        sub_questions: list[SubQuestion],
        *,
        origin: str,
        round: int,
        lease_owner: str | None = None,
    ) -> None:
        self._assert_lease(run_id, lease_owner)
        self._runs[run_id].sub_questions.extend(sub_questions)

    async def save_result(
        self, run_id: str, result: ResearchResult, *, lease_owner: str | None = None
    ) -> None:
        self._assert_lease(run_id, lease_owner)
        self._runs[run_id].results.append(result)

    async def save_sources(
        self, run_id: str, sources: list[Source], *, lease_owner: str | None = None
    ) -> None:
        self._assert_lease(run_id, lease_owner)
        by_snapshot = {
            (source.url, source.content_hash): source for source in self._runs[run_id].sources
        }
        for source in sources:
            content_hash = hashlib.sha256(source.content.encode("utf-8")).hexdigest()
            snapshot = source.model_copy(update={"content_hash": content_hash})
            by_snapshot[(snapshot.url, snapshot.content_hash)] = snapshot
        self._runs[run_id].sources = list(by_snapshot.values())

    async def save_report(self, run_id: str, report: Report) -> None:
        self._runs[run_id].report = report

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
        self._assert_lease(run_id, lease_owner)
        rec = self._runs[run_id]
        rec.interpretation = plan.interpretation if plan is not None else ""
        rec.sub_questions = list(plan.sub_questions) if plan is not None else []
        for _, sub_questions in reflection_rounds:
            rec.sub_questions.extend(sub_questions)
        rec.results = list(results)
        rec.report = report

    async def save_events(
        self, run_id: str, events: list[Event], *, lease_owner: str | None = None
    ) -> None:
        self._assert_lease(run_id, lease_owner)
        # 覆盖式：按产生顺序存全部非 token 事件（seq 即下标）
        rec = self._runs[run_id]
        durable = events
        rec.events = [
            event.model_copy(update={"seq": i, "attempt": rec.attempt})
            for i, event in enumerate(durable)
        ]

    async def append_events(
        self, run_id: str, events: list[Event], *, lease_owner: str | None = None
    ) -> list[Event]:
        self._assert_lease(run_id, lease_owner)
        rec = self._runs[run_id]
        durable = events
        appended = [
            event.model_copy(update={"seq": len(rec.events) + i, "attempt": rec.attempt})
            for i, event in enumerate(durable)
        ]
        rec.events.extend(appended)
        return appended

    async def save_orchestration(
        self,
        run_id: str,
        execution: WorkflowRun,
        *,
        lease_owner: str | None = None,
    ) -> None:
        self._assert_lease(run_id, lease_owner)
        self._runs[run_id].orchestration = execution.model_copy(deep=True)
        self._runs[run_id].attempt = execution.attempt

    async def acquire_lease(
        self, run_id: str, owner: str, *, seconds: int = EXECUTION_LEASE_SECONDS
    ) -> bool:
        rec = self._runs.get(run_id)
        if rec is None:
            return False
        now = datetime.now(UTC)
        lease_active = rec.lease_expires_at is not None and rec.lease_expires_at > now
        if rec.lease_owner is not None and lease_active:
            return False
        rec.lease_owner = owner
        rec.lease_expires_at = now + timedelta(seconds=seconds)
        return True

    async def renew_lease(
        self, run_id: str, owner: str, *, seconds: int = EXECUTION_LEASE_SECONDS
    ) -> bool:
        rec = self._runs.get(run_id)
        if rec is None:
            return False
        now = datetime.now(UTC)
        if rec.lease_owner != owner or rec.lease_expires_at is None or rec.lease_expires_at <= now:
            return False
        rec.lease_expires_at = now + timedelta(seconds=seconds)
        return True

    async def release_lease(self, run_id: str, owner: str) -> None:
        rec = self._runs.get(run_id)
        if rec is None:
            return
        if rec.lease_owner == owner:
            rec.lease_owner = None
            rec.lease_expires_at = None

    async def enqueue_run(self, run_id: str) -> bool:
        rec = self._runs.get(run_id)
        if (
            rec is None
            or rec.status not in {"pending", "running"}
            or rec.orchestration is None
            or not rec.orchestration.checkpoint
            or rec.claimable_at is not None
        ):
            return False
        now = datetime.now(UTC)
        if (
            rec.lease_owner is not None
            and rec.lease_expires_at is not None
            and rec.lease_expires_at > now
        ):
            return False
        rec.claimable_at = now
        return True

    async def defer_run(
        self, run_id: str, execution: WorkflowRun, *, lease_owner: str, not_before: float
    ) -> bool:
        self._assert_lease(run_id, lease_owner)
        rec = self._runs[run_id]
        if rec.status not in {"pending", "running", "error"}:
            return False
        rec.orchestration = execution.model_copy(deep=True)
        rec.status = "running"
        rec.claimable_at = datetime.fromtimestamp(not_before, UTC)
        return True

    async def requeue_failed_run(
        self, run_id: str, *, max_inflight: int | None = None, restart_seconds: int | None = None
    ) -> bool:
        rec = self._runs.get(run_id)
        if rec is None or rec.status != "error" or rec.orchestration is None:
            return False
        if not rec.orchestration.checkpoint:
            return False
        now = datetime.now(UTC)
        lease_active = rec.lease_expires_at is not None and rec.lease_expires_at > now
        if rec.lease_owner is not None and lease_active:
            return False
        self._check_capacity(max_inflight)
        if restart_seconds is not None:
            rec.orchestration.checkpoint = renewed_checkpoint(
                rec.orchestration.checkpoint, restart_seconds
            )
        rec.status = "running"
        rec.claimable_at = now
        rec.lease_owner = None
        rec.lease_expires_at = None
        return True

    async def claim_next_run(
        self,
        owner: str,
        *,
        lease_seconds: int = EXECUTION_LEASE_SECONDS,
        max_active_runs: int | None = None,
    ) -> ClaimedRun | None:
        """Reference implementation of the claim protocol.

        This method contains no await: fairness credits, capacity checks and
        the lease commit form one atomic operation in the in-memory backend.
        The selection rules are shared with the SQL transaction path.
        """
        now = datetime.now(UTC)
        active = [
            ActiveRun(identity_key(rec.owner_id), rec.schedule_class)
            for rec in self._runs.values()
            if rec.status in RUN_ACTIVE_STATUSES
            and rec.lease_owner is not None
            and rec.lease_expires_at is not None
            and rec.lease_expires_at > now
        ]
        candidates = []
        for run_id in self._order:
            rec = self._runs.get(run_id)
            if rec is None or rec.orchestration is None:
                continue
            if rec.status not in ("pending", "running"):
                continue
            if rec.claimable_at is None or rec.claimable_at > now:
                continue
            if rec.lease_owner is not None and (
                rec.lease_expires_at is not None and rec.lease_expires_at > now
            ):
                continue
            ready_at = rec.claimable_at
            if rec.lease_owner is not None and rec.lease_expires_at is not None:
                # Time spent executing or awaiting lease expiry is not queue aging.
                ready_at = max(ready_at, rec.lease_expires_at)
            candidates.append(
                ScheduleCandidate(
                    run_id=run_id,
                    identity=identity_key(rec.owner_id),
                    cost=rec.schedule_cost,
                    kind=rec.schedule_class,
                    priority=rec.schedule_priority,
                    ready_at=ready_at.timestamp(),
                )
            )
        selection = select_next(
            eligible_candidates(candidates, active, max_active_runs),
            self._scheduler_credits,
            self._scheduler_state,
            now=now.timestamp(),
        )
        if selection is None:
            return None
        rec = self._runs[selection.candidate.run_id]
        assert rec.orchestration is not None
        resumed = bool(rec.orchestration.checkpoint) and rec.status == "running"
        rec.lease_owner = owner
        rec.lease_expires_at = now + timedelta(seconds=lease_seconds)
        rec.claim_attempts += 1
        rec.status = "running"
        if resumed:
            rec.attempt = rec.orchestration.attempt = max(1, rec.orchestration.attempt) + 1
        self._scheduler_credits = selection.credits
        self._scheduler_state = selection.state
        return ClaimedRun(
            run_id=rec.id,
            query=rec.query,
            lease_owner=owner,
            execution=rec.orchestration.model_copy(deep=True),
            attempt=rec.orchestration.attempt,
            claim_attempts=rec.claim_attempts,
            resumed=resumed,
            dispatch=dispatch_info(selection.candidate, now=now.timestamp()),
        )

    async def finalize(
        self,
        run_id: str,
        *,
        elapsed: float,
        total_tokens: int,
        lease_owner: str | None = None,
        completion: dict[str, Any] | None = None,
    ) -> None:
        self._assert_lease(run_id, lease_owner)
        rec = self._runs[run_id]
        if completion is not None and rec.status in {"pending", "running"}:
            if rec.orchestration is None or completion.get("status") not in {
                "done",
                "needs_review",
            }:
                raise ValueError("invalid task completion record")
        rec.elapsed = elapsed
        rec.total_tokens = total_tokens
        if rec.status in {"pending", "running"}:
            if completion is not None:
                if rec.orchestration is None or completion.get("status") not in {
                    "done",
                    "needs_review",
                }:
                    raise ValueError("invalid task completion record")
                execution = rec.orchestration.model_copy(deep=True)
                execution.checkpoint.setdefault("scratch", {})["_completion"] = deepcopy(completion)
                rec.orchestration = execution
            rec.status = completion["status"] if completion else "done"

    async def update_completion(
        self, run_id: str, completion: dict[str, Any], *, expected_version: str
    ) -> bool:
        rec = self._runs.get(run_id)
        if rec is None or rec.status not in {"done", "needs_review"} or rec.orchestration is None:
            return False
        scratch = rec.orchestration.checkpoint.get("scratch", {})
        previous = scratch.get("_completion", {})
        if previous.get("content_version") != expected_version or previous.get(
            "input_version"
        ) != completion.get("input_version"):
            return False
        if completion.get("status") not in {"done", "needs_review"}:
            raise ValueError("invalid task completion status")
        updated = rec.orchestration.model_copy(deep=True)
        updated.checkpoint["scratch"]["_completion"] = deepcopy(completion)
        rec.orchestration = updated
        rec.status = completion["status"]
        return True

    async def delete_run(self, run_id: str) -> bool:
        if run_id not in self._runs:
            return False
        record = self._runs[run_id]
        scratch = record.orchestration.checkpoint.get("scratch", {}) if record.orchestration else {}
        slug = scratch.get("_artifact_slug") if isinstance(scratch, dict) else None
        if isinstance(slug, str) and slug:
            self._artifact_cleanup[run_id] = (
                f"runs/{run_id}" if scratch.get("_artifact_run_scoped") else slug
            )
        else:
            self._artifact_cleanup[run_id] = f"runs/{run_id}"
        del self._runs[run_id]
        self._order.remove(run_id)
        for key, (stored_id, _hash) in list(self._idempotency.items()):
            if stored_id == run_id:
                del self._idempotency[key]
        for service in getattr(self, "_render_services", {}).values():
            await service.queue.remove_run(run_id)
            service._notify()
        return True

    async def set_tags(self, run_id: str, tags: list[str]) -> None:
        cleaned = list(dict.fromkeys(t.strip() for t in tags if t.strip()))
        self._runs[run_id].tags = cleaned

    async def list_tags(self, *, owner_id: str | None = None) -> list[TagCount]:
        counts: dict[str, int] = {}
        for rec in self._runs.values():
            if owner_id is not None and rec.owner_id != owner_id:
                continue
            for tag in rec.tags:
                counts[tag] = counts.get(tag, 0) + 1
        # 计数降序、同计数按标签名升序，与 SQL 实现对齐
        ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        return [TagCount(tag=t, count=c) for t, c in ordered]

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
        ids = list(reversed(self._order))
        recs = [self._runs[i] for i in ids]
        if owner_id is not None:
            recs = [r for r in recs if r.owner_id == owner_id]
        if status:
            recs = [r for r in recs if r.status == status]
        if q:
            ql = q.lower()
            recs = [r for r in recs if ql in r.query.lower()]
        if tag:
            recs = [r for r in recs if tag in r.tags]
        recs = recs[offset : offset + limit]
        return [
            RunSummary(
                id=rec.id,
                query=rec.query,
                status=rec.status,
                owner_id=rec.owner_id,
                project_id=rec.project_id,
                total_tokens=rec.total_tokens,
                elapsed=rec.elapsed,
                tags=list(rec.tags),
            )
            for rec in recs
        ]

    async def get_run(self, run_id: str) -> RunDetail | None:
        rec = self._runs.get(run_id)
        if rec is None:
            return None
        return RunDetail(
            id=rec.id,
            query=rec.query,
            status=rec.status,
            owner_id=rec.owner_id,
            cancel_requested_at=rec.cancel_requested_at,
            project_id=rec.project_id,
            interpretation=rec.interpretation,
            sub_questions=list(rec.sub_questions),
            results=list(rec.results),
            report=rec.report,
            total_tokens=rec.total_tokens,
            elapsed=rec.elapsed,
            tags=list(rec.tags),
            orchestration=rec.orchestration.model_copy(deep=True) if rec.orchestration else None,
            sources=list(rec.sources),
            events=list(rec.events),
        )

    async def get_run_status(self, run_id: str) -> str | None:
        rec = self._runs.get(run_id)
        return rec.status if rec is not None else None

    async def get_run_attempt(self, run_id: str) -> int | None:
        rec = self._runs.get(run_id)
        return rec.attempt if rec is not None else None

    async def get_events(
        self, run_id: str, *, after_seq: int = 0, limit: int | None = None
    ) -> list[Event]:
        rec = self._runs.get(run_id)
        if rec is None:
            return []
        events = [event for event in rec.events if event.seq is not None and event.seq >= after_seq]
        return events if limit is None else events[:limit]

    async def healthcheck(self) -> bool:
        return True
