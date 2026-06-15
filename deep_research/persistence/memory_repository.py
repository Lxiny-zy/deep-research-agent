"""内存仓储：CLI 默认 + 单测离线零依赖，无需数据库。"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

from ..models import Report, ResearchPlan, ResearchResult, Source, SubQuestion
from ..observability import Event
from .repository import RunDetail, RunSummary


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


class InMemoryRepository:
    """把一切存在进程内存里。语义与 SqlRepository 对齐，便于在测试中互换。"""

    def __init__(self) -> None:
        self._runs: dict[str, _RunRecord] = {}
        self._order: list[str] = []

    async def create_run(self, query: str) -> str:
        run_id = str(uuid4())
        self._runs[run_id] = _RunRecord(id=run_id, query=query)
        self._order.append(run_id)
        return run_id

    async def set_status(self, run_id: str, status: str) -> None:
        self._runs[run_id].status = status

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

    async def save_events(self, run_id: str, events: list[Event]) -> None:
        # 覆盖式：按产生顺序存全部非 token 事件（seq 即下标）
        self._runs[run_id].events = list(events)

    async def finalize(self, run_id: str, *, elapsed: float, total_tokens: int) -> None:
        rec = self._runs[run_id]
        rec.elapsed = elapsed
        rec.total_tokens = total_tokens
        rec.status = "done"

    async def list_runs(self, *, limit: int = 50, offset: int = 0) -> list[RunSummary]:
        ids = list(reversed(self._order))[offset : offset + limit]
        return [
            RunSummary(
                id=rec.id,
                query=rec.query,
                status=rec.status,
                total_tokens=rec.total_tokens,
                elapsed=rec.elapsed,
            )
            for rec in (self._runs[i] for i in ids)
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
        )

    async def get_events(self, run_id: str, *, after_seq: int = 0) -> list[Event]:
        rec = self._runs.get(run_id)
        if rec is None:
            return []
        return rec.events[after_seq:]
