"""内存仓储：CLI 默认 + 单测离线零依赖，无需数据库。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from ..models import Report, ResearchPlan, ResearchResult, Source, SubQuestion
from ..observability import Event
from ..orchestration import WorkflowRun
from .repository import LeaseLostError, RunDetail, RunSummary, TagCount


@dataclass
class _RunRecord:
    id: str
    query: str
    status: str = "pending"
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


class InMemoryRepository:
    """把一切存在进程内存里。语义与 SqlRepository 对齐，便于在测试中互换。"""

    def __init__(self) -> None:
        self._runs: dict[str, _RunRecord] = {}
        self._order: list[str] = []

    async def create_run(
        self,
        query: str,
        *,
        execution: WorkflowRun | None = None,
        lease_owner: str | None = None,
    ) -> str:
        run_id = str(uuid4())
        record = _RunRecord(id=run_id, query=query)
        if execution is not None:
            record.orchestration = execution.model_copy(deep=True)
            record.lease_owner = lease_owner
            record.lease_expires_at = (
                datetime.now(UTC) + timedelta(seconds=120)
                if lease_owner is not None
                else None
            )
        self._runs[run_id] = record
        self._order.append(run_id)
        return run_id

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

    async def set_status(
        self, run_id: str, status: str, *, lease_owner: str | None = None
    ) -> None:
        self._assert_lease(run_id, lease_owner)
        self._runs[run_id].status = status

    async def prepare_resume(self, run_id: str, *, lease_owner: str) -> None:
        self._assert_lease(run_id, lease_owner)
        rec = self._runs[run_id]
        rec.status = "running"
        rec.events = [
            event
            for event in rec.events
            if not (
                event.stage == "ORCHESTRATOR" and event.type in {"done", "error"}
            )
        ]

    async def save_plan(self, run_id: str, plan: ResearchPlan) -> None:
        rec = self._runs[run_id]
        rec.interpretation = plan.interpretation
        rec.sub_questions.extend(plan.sub_questions)

    async def add_sub_questions(
        self, run_id: str, sub_questions: list[SubQuestion], *, origin: str, round: int
    ) -> None:
        self._runs[run_id].sub_questions.extend(sub_questions)

    async def save_result(self, run_id: str, result: ResearchResult) -> None:
        self._runs[run_id].results.append(result)

    async def save_sources(self, run_id: str, sources: list[Source]) -> None:
        self._runs[run_id].sources.extend(sources)

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
        self._runs[run_id].events = list(events)

    async def save_orchestration(
        self,
        run_id: str,
        execution: WorkflowRun,
        *,
        lease_owner: str | None = None,
    ) -> None:
        self._assert_lease(run_id, lease_owner)
        self._runs[run_id].orchestration = execution.model_copy(deep=True)

    async def acquire_lease(self, run_id: str, owner: str, *, seconds: int = 120) -> bool:
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

    async def renew_lease(self, run_id: str, owner: str, *, seconds: int = 120) -> bool:
        rec = self._runs.get(run_id)
        if rec is None:
            return False
        now = datetime.now(UTC)
        if (
            rec.lease_owner != owner
            or rec.lease_expires_at is None
            or rec.lease_expires_at <= now
        ):
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

    async def finalize(
        self,
        run_id: str,
        *,
        elapsed: float,
        total_tokens: int,
        lease_owner: str | None = None,
    ) -> None:
        self._assert_lease(run_id, lease_owner)
        rec = self._runs[run_id]
        rec.elapsed = elapsed
        rec.total_tokens = total_tokens
        rec.status = "done"

    async def delete_run(self, run_id: str) -> bool:
        if run_id not in self._runs:
            return False
        del self._runs[run_id]
        self._order.remove(run_id)
        return True

    async def set_tags(self, run_id: str, tags: list[str]) -> None:
        cleaned = list(dict.fromkeys(t.strip() for t in tags if t.strip()))
        self._runs[run_id].tags = cleaned

    async def list_tags(self) -> list[TagCount]:
        counts: dict[str, int] = {}
        for rec in self._runs.values():
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
    ) -> list[RunSummary]:
        ids = list(reversed(self._order))
        recs = [self._runs[i] for i in ids]
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
            interpretation=rec.interpretation,
            sub_questions=list(rec.sub_questions),
            results=list(rec.results),
            report=rec.report,
            total_tokens=rec.total_tokens,
            elapsed=rec.elapsed,
            tags=list(rec.tags),
            orchestration=rec.orchestration.model_copy(deep=True) if rec.orchestration else None,
        )

    async def get_run_status(self, run_id: str) -> str | None:
        rec = self._runs.get(run_id)
        return rec.status if rec is not None else None

    async def get_events(self, run_id: str, *, after_seq: int = 0) -> list[Event]:
        rec = self._runs.get(run_id)
        if rec is None:
            return []
        return rec.events[after_seq:]
