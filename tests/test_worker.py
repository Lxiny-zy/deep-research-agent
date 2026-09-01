"""Worker 执行进程：领取循环、毒任务熔断、优雅退出与崩溃接管。

这里的断言针对的是 worker 相对 inline 模式**新增**的语义。执行本身的语义
（终态落库、取消、资源清理）由 tests/test_api.py 的 _execute 测试覆盖，两种
拓扑共用同一个 RunExecutor，不重复验证。
"""

from __future__ import annotations

import asyncio

import pytest

from deep_research.config import Settings
from deep_research.execution import ExecutionContext, RunExecutor
from deep_research.orchestration import OrchestrationRuntime
from deep_research.persistence.memory_repository import InMemoryRepository
from deep_research.worker import Worker


class _RecordingExecutor(RunExecutor):
    """记录每次执行的参数；可选地阻塞，用于验证并发与退出行为。"""

    def __init__(self, ctx: ExecutionContext, *, block: asyncio.Event | None = None) -> None:
        super().__init__(ctx)
        self.calls: list[dict] = []
        self.started = asyncio.Event()
        self._progress = asyncio.Event()
        self._block = block

    async def wait_for_calls(self, count: int) -> None:
        """等到至少 count 次执行被派发。用事件而不是轮询，避免和调度器抢时间。"""
        while len(self.calls) < count:
            self._progress.clear()
            await self._progress.wait()

    async def execute(self, run_id, query, settings, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append({"run_id": run_id, "query": query, **kwargs})
        self.started.set()
        self._progress.set()
        if self._block is not None:
            await self._block.wait()
        lease_owner = kwargs.get("lease_owner")
        await self.ctx.repo.set_status(run_id, "done", lease_owner=lease_owner)
        await self.ctx.repo.release_lease(run_id, lease_owner)


def _execution(query: str, *, checkpoint: dict | None = None):
    runtime = OrchestrationRuntime()
    execution = runtime.start("deep", {"query": query})
    if checkpoint is not None:
        runtime.save_checkpoint(checkpoint, {"name": "deep", "steps": []})
    return execution


def _execution_with_requested_workflow(query: str, workflow: str):
    runtime = OrchestrationRuntime()
    execution = runtime.start(workflow, {"query": query})
    runtime.save_checkpoint(
        {"query": query, "scratch": {"requested_workflow": workflow}},
        {"name": workflow, "steps": []},
    )
    return execution


async def _enqueue(repo: InMemoryRepository, query: str, **kwargs) -> str:
    run_id, _ = await repo.create_run_once(
        query,
        request_hash="",
        execution=_execution(query, **kwargs),
        claimable=True,
    )
    return run_id


def _worker(repo, executor, **settings_kwargs):
    settings = Settings(worker_poll_seconds=0.01, **settings_kwargs)
    return Worker(repo, executor, settings, name="worker-test")


async def _run_until(worker: Worker, predicate, *, deadline: float = 2.0) -> None:
    """跑领取循环直到条件满足，然后请求停止并等待收尾。

    这里是真的在轮询：断言的对象是仓储状态（run 被置 error、被领走），没有可
    await 的事件——worker 内部的状态变更不对测试暴露信号。
    """
    task = asyncio.create_task(worker.run_forever())
    try:
        async with asyncio.timeout(deadline):
            while not predicate():  # noqa: ASYNC110 - 轮询仓储状态，无事件可等
                await asyncio.sleep(0.01)
    finally:
        worker.request_stop()
        await asyncio.wait_for(task, timeout=deadline)


@pytest.mark.asyncio
async def test_worker_claims_and_executes_a_queued_run() -> None:
    repo = InMemoryRepository()
    run_id = await _enqueue(repo, "queued question")
    executor = _RecordingExecutor(ExecutionContext(repo=repo))
    worker = _worker(repo, executor)

    await _run_until(worker, lambda: bool(executor.calls))

    assert [call["run_id"] for call in executor.calls] == [run_id]
    call = executor.calls[0]
    assert call["query"] == "queued question"
    # 首次执行：带 initial_execution，不带 resume_execution。
    assert call["resume_execution"] is None
    assert call["initial_execution"] is not None
    assert call["lease_owner"] == "worker-test"
    assert await repo.get_run_status(run_id) == "done"


@pytest.mark.asyncio
async def test_worker_preserves_explicit_workflow_from_checkpoint() -> None:
    repo = InMemoryRepository()
    execution = _execution_with_requested_workflow("queued deep question", "quick")
    run_id, _ = await repo.create_run_once(
        "queued deep question",
        request_hash="",
        execution=execution,
        claimable=True,
    )
    executor = _RecordingExecutor(ExecutionContext(repo=repo))
    worker = _worker(repo, executor)

    await _run_until(worker, lambda: bool(executor.calls))

    assert executor.calls[0]["requested_workflow"] == "quick"


@pytest.mark.asyncio
async def test_worker_ignores_runs_that_were_never_enqueued() -> None:
    """inline 模式创建的 run 不属于任何 worker。"""
    repo = InMemoryRepository()
    await repo.create_run_once("inline run", request_hash="", execution=_execution("inline run"))
    executor = _RecordingExecutor(ExecutionContext(repo=repo))
    worker = _worker(repo, executor)

    task = asyncio.create_task(worker.run_forever())
    await asyncio.sleep(0.1)
    worker.request_stop()
    await asyncio.wait_for(task, timeout=2.0)

    assert executor.calls == []


@pytest.mark.asyncio
async def test_worker_resumes_a_run_abandoned_by_a_crashed_worker() -> None:
    """崩溃接管：租约过期后另一个 worker 从 checkpoint 续跑，而不是重头再来。"""
    repo = InMemoryRepository()
    run_id = await _enqueue(repo, "crashed", checkpoint={"query": "crashed", "scratch": {}})
    crashed = await repo.claim_next_run("dead-worker")
    assert crashed is not None
    assert await repo.renew_lease(run_id, "dead-worker", seconds=0)  # 租约到期，进程已死

    executor = _RecordingExecutor(ExecutionContext(repo=repo))
    worker = _worker(repo, executor)
    await _run_until(worker, lambda: bool(executor.calls))

    call = executor.calls[0]
    assert call["run_id"] == run_id
    # 接管：带 resume_execution，让 WorkflowEngine 跳过已完成节点。
    assert call["resume_execution"] is not None
    assert call["resume_execution"].checkpoint == {"query": "crashed", "scratch": {}}
    assert call["initial_execution"] is None


@pytest.mark.asyncio
async def test_worker_stops_retrying_a_poison_run() -> None:
    """反复失败的任务必须被熔断，而不是在 worker 之间无限传递。"""
    repo = InMemoryRepository()
    run_id = await _enqueue(repo, "poison", checkpoint={"query": "poison", "scratch": {}})
    # 模拟已经被领取并崩溃 3 次。
    for owner in ("w1", "w2", "w3"):
        claimed = await repo.claim_next_run(owner)
        assert claimed is not None
        assert await repo.renew_lease(run_id, owner, seconds=0)

    executor = _RecordingExecutor(ExecutionContext(repo=repo))
    worker = _worker(repo, executor, max_claim_attempts=3)
    await _run_until(worker, lambda: repo._runs[run_id].status == "error")

    assert executor.calls == [], "毒任务不应再被执行"
    events = await repo.get_events(run_id)
    assert any(event.data.get("reason") == "poison_run" for event in events)
    # 熔断后租约必须释放，否则这条记录会永远显示为被占用。
    assert repo._runs[run_id].lease_owner is None


@pytest.mark.asyncio
async def test_poison_status_is_written_when_audit_event_fails() -> None:
    """A transient event-write failure must not leave a poisoned run active."""
    repo = InMemoryRepository()
    run_id = await _enqueue(
        repo,
        "poison event failure",
        checkpoint={"query": "poison event failure"},
    )
    claimed = await repo.claim_next_run("worker-test")
    assert claimed is not None

    async def fail_append(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("event store unavailable")

    repo.append_events = fail_append  # type: ignore[method-assign]
    executor = _RecordingExecutor(ExecutionContext(repo=repo))
    worker = _worker(repo, executor)

    await worker._poison(claimed)

    assert await repo.get_run_status(run_id) == "error"
    assert repo._runs[run_id].lease_owner is None


@pytest.mark.asyncio
async def test_worker_respects_its_concurrency_limit() -> None:
    repo = InMemoryRepository()
    for i in range(4):
        await _enqueue(repo, f"q{i}")
    release = asyncio.Event()
    executor = _RecordingExecutor(ExecutionContext(repo=repo), block=release)
    worker = _worker(repo, executor, max_active_runs=2)

    task = asyncio.create_task(worker.run_forever())
    try:
        await asyncio.wait_for(executor.wait_for_calls(2), timeout=2.0)
        await asyncio.sleep(0.05)
        assert len(executor.calls) == 2, "并发上限之外的任务必须留在队列里"
        assert worker.capacity == 0
    finally:
        release.set()
        worker.request_stop()
        await asyncio.wait_for(task, timeout=2.0)


@pytest.mark.asyncio
async def test_graceful_stop_waits_for_in_flight_runs() -> None:
    """优雅退出停止领取，但绝不打断已经在跑的研究。"""
    repo = InMemoryRepository()
    run_id = await _enqueue(repo, "in flight")
    release = asyncio.Event()
    executor = _RecordingExecutor(ExecutionContext(repo=repo), block=release)
    worker = _worker(repo, executor)

    task = asyncio.create_task(worker.run_forever())
    await asyncio.wait_for(executor.started.wait(), timeout=2.0)
    worker.request_stop()
    await asyncio.sleep(0.05)
    assert not task.done(), "worker 必须等待在跑的任务，而不是立刻退出"

    release.set()
    await asyncio.wait_for(task, timeout=2.0)
    assert await repo.get_run_status(run_id) == "done"


@pytest.mark.asyncio
async def test_claim_failure_does_not_kill_the_loop() -> None:
    """仓储抖动只应让这一轮领取失败，不应终结 worker。"""
    repo = InMemoryRepository()
    run_id = await _enqueue(repo, "after failure")
    failures = {"count": 0}
    original = repo.claim_next_run

    async def flaky(owner, **kwargs):  # type: ignore[no-untyped-def]
        if failures["count"] < 2:
            failures["count"] += 1
            raise RuntimeError("database is unavailable")
        return await original(owner, **kwargs)

    repo.claim_next_run = flaky  # type: ignore[method-assign]
    executor = _RecordingExecutor(ExecutionContext(repo=repo))
    worker = _worker(repo, executor)

    await _run_until(worker, lambda: bool(executor.calls))

    assert failures["count"] == 2
    assert executor.calls[0]["run_id"] == run_id
