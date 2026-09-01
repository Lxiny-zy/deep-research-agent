"""持久化仓储接口（Protocol）+ 读取用 DTO。

接口同时服务两条路径：
  - 写入（orchestrator）：create_run / save_plan / add_sub_questions / save_result /
    save_sources / save_report / save_events / finalize / set_status
  - 读取（API）：list_runs / get_run / get_events

两个实现：
  - InMemoryRepository：纯内存，离线单测零依赖（亦为本协议的参考实现）
  - SqlRepository      ：async SQLAlchemy（PostgreSQL / SQLite），API 默认使用
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from ..intent.types import IntentDecision
from ..models import (
    QualityMetrics,
    Report,
    ResearchPlan,
    ResearchResult,
    RunManifest,
    Source,
    SubQuestion,
)
from ..observability import Event
from ..orchestration import WorkflowRun


class LeaseLostError(RuntimeError):
    """Raised when a worker tries to persist after losing its execution lease."""


class IdempotencyConflictError(RuntimeError):
    """The same idempotency key was reused with a different request payload."""


RUN_ACTIVE_STATUSES = frozenset({"pending", "running", "cancelling"})
RUN_TERMINAL_STATUSES = frozenset({"cancelled", "done", "error"})


@dataclass
class RunSummary:
    """历史列表行（轻量）。"""

    id: str
    query: str
    status: str
    created_at: datetime | None = None
    total_tokens: int = 0
    elapsed: float = 0.0
    tags: list[str] = field(default_factory=list)


@dataclass
class RunDetail:
    """单次研究详情（含计划、结果、报告）。"""

    id: str
    query: str
    status: str
    interpretation: str = ""
    sub_questions: list[SubQuestion] = field(default_factory=list)
    results: list[ResearchResult] = field(default_factory=list)
    report: Report | None = None
    total_tokens: int = 0
    elapsed: float = 0.0
    created_at: datetime | None = None
    tags: list[str] = field(default_factory=list)
    orchestration: WorkflowRun | None = None
    sources: list[Source] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    manifest: RunManifest | None = None
    metrics: QualityMetrics | None = None
    # 本次运行的意图判定（从 checkpoint scratch 还原）；未跑意图门禁时为 None。
    intent: IntentDecision | None = None


@dataclass
class TagCount:
    """标签 + 引用计数（历史筛选侧栏用）。"""

    tag: str
    count: int


@dataclass
class ClaimedRun:
    """一次成功领取的结果（``execution_mode=worker``）。

    领取成功意味着调用方已持有该 run 的执行租约，并且 run 状态已置 ``running``。
    调用方负责在执行结束后释放租约；崩溃时租约自然过期，交给下一个 worker。
    """

    run_id: str
    query: str
    lease_owner: str
    execution: WorkflowRun
    attempt: int
    # 该 run 累计被领取的次数（含本次）。worker 用它做毒任务熔断。
    claim_attempts: int
    # True 表示这是对一个已有 checkpoint 的接管（断点续跑），
    # False 表示首次执行。二者的事件重放语义不同。
    resumed: bool


class ResearchRepository(Protocol):
    """研究运行的持久化仓储。两个实现结构化满足本协议（无需显式继承）。"""

    async def create_run(
        self,
        query: str,
        *,
        execution: WorkflowRun | None = None,
        lease_owner: str | None = None,
    ) -> str: ...

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
        """Create a run once; return ``(run_id, created)``.

        ``claimable`` marks the run as queued for an external worker.  It is
        the only durable signal that separates "enqueued, never started" from
        "crashed before the first checkpoint", which otherwise look identical.
        """
        ...

    async def enqueue_run(self, run_id: str) -> bool:
        """Mark an existing active run as claimable; return ``False`` if absent.

        Used by ``resume`` in worker mode: the API hands the run back to the
        queue instead of executing it in the request process.
        """
        ...

    async def requeue_failed_run(self, run_id: str) -> bool:
        """Atomically requeue a failed checkpointed run for worker recovery.

        Implementations must reject runs without a checkpoint and runs whose
        workflow lease is still active.  Success changes the run back to
        ``running`` and marks it claimable without broadening the global set of
        active statuses.
        """
        ...

    async def claim_next_run(self, owner: str, *, lease_seconds: int = 120) -> ClaimedRun | None:
        """Atomically claim one queued or abandoned run, or return ``None``.

        The workflow lease is the cross-process arbiter, exactly as it is for
        crash recovery; candidate selection is only an optimization.  Runs whose
        lease is still live are never returned.
        """
        ...

    async def request_cancel(self, run_id: str) -> str | None:
        """Atomically move an active run to ``cancelling`` and return status."""
        ...

    async def append_events(
        self, run_id: str, events: list[Event], *, lease_owner: str | None = None
    ) -> list[Event]: ...

    async def set_status(
        self, run_id: str, status: str, *, lease_owner: str | None = None
    ) -> None: ...

    async def prepare_resume(self, run_id: str, *, lease_owner: str) -> int:
        """Atomically mark a new attempt active and return its number."""
        ...

    async def save_plan(self, run_id: str, plan: ResearchPlan) -> None: ...

    async def add_sub_questions(
        self, run_id: str, sub_questions: list[SubQuestion], *, origin: str, round: int
    ) -> None: ...

    async def save_result(self, run_id: str, result: ResearchResult) -> None: ...

    async def save_sources(
        self, run_id: str, sources: list[Source], *, lease_owner: str | None = None
    ) -> None: ...

    async def save_report(self, run_id: str, report: Report) -> None: ...

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
        """Atomically replace all derived research artifacts for a run."""
        ...

    async def save_events(
        self, run_id: str, events: list[Event], *, lease_owner: str | None = None
    ) -> None: ...

    async def save_orchestration(
        self,
        run_id: str,
        execution: WorkflowRun,
        *,
        lease_owner: str | None = None,
    ) -> None: ...

    async def acquire_lease(self, run_id: str, owner: str, *, seconds: int = 120) -> bool: ...

    async def renew_lease(self, run_id: str, owner: str, *, seconds: int = 120) -> bool: ...

    async def release_lease(self, run_id: str, owner: str) -> None: ...

    async def finalize(
        self,
        run_id: str,
        *,
        elapsed: float,
        total_tokens: int,
        lease_owner: str | None = None,
    ) -> None: ...

    async def delete_run(self, run_id: str) -> bool: ...

    async def set_tags(self, run_id: str, tags: list[str]) -> None: ...

    async def list_tags(self) -> list[TagCount]: ...

    async def list_runs(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
        q: str | None = None,
        tag: str | None = None,
    ) -> list[RunSummary]: ...

    async def get_run(self, run_id: str) -> RunDetail | None: ...

    async def get_run_status(self, run_id: str) -> str | None: ...

    async def get_run_attempt(self, run_id: str) -> int | None: ...

    async def get_events(
        self, run_id: str, *, after_seq: int = 0, limit: int | None = None
    ) -> list[Event]: ...

    async def healthcheck(self) -> bool: ...
