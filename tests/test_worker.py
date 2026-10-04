"""Worker 执行进程：领取循环、毒任务熔断、优雅退出与崩溃接管。

这里的断言针对的是 worker 相对 inline 模式**新增**的语义。执行本身的语义
（终态落库、取消、资源清理）由 tests/test_api.py 的 _execute 测试覆盖，两种
拓扑共用同一个 RunExecutor，不重复验证。
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import textwrap
from pathlib import Path

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


def _execution(query: str, *, checkpoint: dict | None = None, workflow: str = "deep"):
    runtime = OrchestrationRuntime()
    execution = runtime.start(workflow, {"query": query})
    if checkpoint is not None:
        runtime.save_checkpoint(checkpoint, {"name": workflow, "steps": []})
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
    assert call["lease_owner"].startswith("claim-")
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
        # This exercises the total cap, independent of the heavy-work reserve.
        await _enqueue(repo, f"q{i}", workflow="quick")
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
    """优雅退出停止领取，在宽限期内允许已经在跑的研究完成。"""
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
async def test_shutdown_deadline_preserves_checkpoint_for_the_next_worker(caplog) -> None:
    repo = InMemoryRepository()
    checkpoint = {"query": "recover after shutdown", "scratch": {"completed": ["search"]}}
    run_id = await _enqueue(repo, "recover after shutdown", checkpoint=checkpoint)
    executor = _RecordingExecutor(ExecutionContext(repo=repo), block=asyncio.Event())
    worker = _worker(repo, executor, max_active_runs=1, worker_shutdown_grace_seconds=0.02)
    task = asyncio.create_task(worker.run_forever())
    await asyncio.wait_for(executor.started.wait(), timeout=2)
    queued_id = await _enqueue(repo, "leave queued")
    worker.request_stop()
    await asyncio.wait_for(task, timeout=2)

    assert not worker.requires_hard_exit
    assert len(executor.calls) == 1
    assert repo._runs[queued_id].lease_owner is None
    original_owner = repo._runs[run_id].lease_owner
    assert original_owner and original_owner.startswith("claim-")
    assert await repo.get_run_status(run_id) not in {"done", "error", "cancelled"}
    assert run_id in caplog.text
    assert "shutdown_deadline" in caplog.text
    assert worker.name not in repo._workers

    # Only lease expiry permits takeover, and the checkpoint survives shutdown.
    assert not await repo.acquire_lease(run_id, "premature-replacement")
    assert await repo.renew_lease(run_id, original_owner, seconds=0)
    replacement = _RecordingExecutor(ExecutionContext(repo=repo))
    successor = _worker(repo, replacement, max_active_runs=1)
    claimed_ids = set()
    for _ in range(2):
        claimed = await repo.claim_next_run("replacement-worker", max_active_runs=1)
        assert claimed is not None and claimed.run_id not in claimed_ids
        claimed_ids.add(claimed.run_id)
        # Fair scheduling may choose the fresh task or the interrupted task first.
        assert claimed.resumed is (claimed.run_id == run_id)
        if claimed.resumed:
            assert claimed.execution.checkpoint == checkpoint
        assert await repo.claim_next_run("competing-worker", max_active_runs=1) is None
        await successor._execute_claimed(claimed)
    assert claimed_ids == {run_id, queued_id}
    assert await repo.claim_next_run("replacement-worker", max_active_runs=1) is None
    statuses = await asyncio.gather(*(repo.get_run_status(item) for item in claimed_ids))
    assert statuses == ["done", "done"]
    assert len(replacement.calls) == len({call["run_id"] for call in replacement.calls}) == 2
    resumed_call = next(call for call in replacement.calls if call["run_id"] == run_id)
    assert resumed_call["resume_execution"].checkpoint == checkpoint


@pytest.mark.asyncio
async def test_draining_worker_keeps_heartbeat_until_tasks_finish(monkeypatch) -> None:
    from deep_research import worker as worker_module

    monkeypatch.setattr(worker_module, "_HEARTBEAT_SECONDS", 0.01)
    repo = InMemoryRepository()
    await _enqueue(repo, "finish while draining")
    release = asyncio.Event()
    executor = _RecordingExecutor(ExecutionContext(repo=repo), block=release)
    worker = _worker(repo, executor)
    original_heartbeat = repo.heartbeat_worker
    drain_heartbeats: list[int] = []
    observed = asyncio.Event()

    async def record_heartbeat(name: str, active: int) -> None:
        await original_heartbeat(name, active)
        if worker._stopping.is_set():
            drain_heartbeats.append(active)
            if len(drain_heartbeats) >= 2:
                observed.set()

    monkeypatch.setattr(repo, "heartbeat_worker", record_heartbeat)
    task = asyncio.create_task(worker.run_forever())
    try:
        await asyncio.wait_for(executor.started.wait(), timeout=2)
        worker.request_stop()
        await asyncio.wait_for(observed.wait(), timeout=2)
        assert drain_heartbeats[:2] == [1, 1]
        assert worker.name in repo._workers
        assert not task.done()
    finally:
        release.set()
        await asyncio.wait_for(task, timeout=2)
    assert worker.name not in repo._workers
    count = len(drain_heartbeats)
    await asyncio.sleep(0.03)
    assert len(drain_heartbeats) == count


@pytest.mark.asyncio
async def test_stop_during_heartbeat_does_not_claim_a_run(monkeypatch) -> None:
    repo = InMemoryRepository()
    run_id = await _enqueue(repo, "still queued")
    executor = _RecordingExecutor(ExecutionContext(repo=repo))
    worker = _worker(repo, executor)

    async def stop_on_heartbeat(name: str, active: int) -> None:
        worker.request_stop()

    monkeypatch.setattr(repo, "heartbeat_worker", stop_on_heartbeat)
    await worker._tick()
    await worker._tick()
    assert repo._runs[run_id].lease_owner is None
    assert not executor.calls


@pytest.mark.asyncio
async def test_stop_interrupts_a_stalled_admission(monkeypatch) -> None:
    repo = InMemoryRepository()
    executor = _RecordingExecutor(ExecutionContext(repo=repo))
    worker = _worker(repo, executor, worker_shutdown_grace_seconds=0.02)
    entered_claim = asyncio.Event()
    claim_cancelled = asyncio.Event()

    async def stall_claim(*args, **kwargs):
        entered_claim.set()
        try:
            await asyncio.Event().wait()
        finally:
            claim_cancelled.set()

    monkeypatch.setattr(repo, "claim_next_run", stall_claim)
    task = asyncio.create_task(worker.run_forever())
    await asyncio.wait_for(entered_claim.wait(), timeout=2)
    worker.request_stop()
    await asyncio.wait_for(task, timeout=2)
    assert claim_cancelled.is_set()
    assert not worker.requires_hard_exit
    assert worker.name not in repo._workers


@pytest.mark.asyncio
async def test_uncooperative_execution_requires_process_exit(monkeypatch, caplog) -> None:
    from deep_research import worker as worker_module

    monkeypatch.setattr(worker_module, "_CLEANUP_SECONDS", 0.02)
    repo = InMemoryRepository()
    run_id = await _enqueue(repo, "stuck provider cleanup")
    release = asyncio.Event()
    started = asyncio.Event()

    async def uncooperative(*args, **kwargs) -> None:
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()

    executor = _RecordingExecutor(ExecutionContext(repo=repo))
    monkeypatch.setattr(executor, "execute", uncooperative)
    worker = _worker(repo, executor, worker_shutdown_grace_seconds=0.01)
    task = asyncio.create_task(worker.run_forever())
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        worker.request_stop()
        await asyncio.wait_for(task, timeout=2)
        assert worker.requires_hard_exit
        assert "shutdown_cleanup_timeout" in caplog.text
        assert run_id in caplog.text
        assert repo._runs[run_id].lease_owner.startswith("claim-")
    finally:
        release.set()
        await asyncio.gather(*worker._running, return_exceptions=True)


@pytest.mark.asyncio
async def test_drain_heartbeat_failure_is_logged_and_retried(monkeypatch, caplog) -> None:
    from deep_research import worker as worker_module

    monkeypatch.setattr(worker_module, "_HEARTBEAT_SECONDS", 0.01)
    repo = InMemoryRepository()
    await _enqueue(repo, "heartbeat failure")
    release = asyncio.Event()
    executor = _RecordingExecutor(ExecutionContext(repo=repo), block=release)
    worker = _worker(repo, executor)
    original = repo.heartbeat_worker
    recovered = asyncio.Event()
    failed = False

    async def fail_once(name: str, active: int) -> None:
        nonlocal failed
        if worker._stopping.is_set():
            if not failed:
                failed = True
                raise RuntimeError("transient heartbeat outage")
            recovered.set()
        await original(name, active)

    monkeypatch.setattr(repo, "heartbeat_worker", fail_once)
    task = asyncio.create_task(worker.run_forever())
    try:
        await asyncio.wait_for(executor.started.wait(), timeout=2)
        worker.request_stop()
        await asyncio.wait_for(recovered.wait(), timeout=2)
        assert "transient heartbeat outage" in caplog.text
    finally:
        release.set()
        await asyncio.wait_for(task, timeout=2)
    assert worker.name not in repo._workers


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


# ---- 进程入口：--check 健康探针与启动/退出路径 ----


def _sqlite_url(tmp_path) -> str:  # type: ignore[no-untyped-def]
    return f"sqlite+aiosqlite:///{(tmp_path / 'worker.db').as_posix()}"


@pytest.mark.asyncio
async def test_worker_check_reports_fresh_and_missing_heartbeat(tmp_path, monkeypatch):
    from deep_research import worker as worker_module
    from deep_research.persistence.db import make_engine, make_sessionmaker, prepare_sqlite_schema
    from deep_research.persistence.sql_repository import SqlRepository

    url = _sqlite_url(tmp_path)
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("APP_ENV", "development")
    engine = make_engine(url)
    await prepare_sqlite_schema(engine, url)
    try:
        # 没有心跳记录：探针失败，容器编排据此重启 worker
        assert await worker_module.main_async(["--check", "--name", "w-probe"]) == 1
        await SqlRepository(make_sessionmaker(engine)).heartbeat_worker("w-probe", 0)
        assert await worker_module.main_async(["--check", "--name", "w-probe"]) == 0
        # 其他 worker 的心跳不能让本 worker 的探针通过
        assert await worker_module.main_async(["--check", "--name", "w-other"]) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_worker_main_builds_and_stops_cleanly(tmp_path, monkeypatch):
    from deep_research import worker as worker_module

    monkeypatch.setenv("DATABASE_URL", _sqlite_url(tmp_path))
    monkeypatch.setenv("APP_ENV", "development")
    seen: dict[str, str] = {}

    async def fake_run_forever(self) -> None:  # type: ignore[no-untyped-def]
        seen["name"] = self.name
        self.request_stop()

    def unexpected_hard_exit(code: int) -> None:
        pytest.fail(f"clean shutdown must not force process exit: {code}")

    monkeypatch.setattr(worker_module.Worker, "run_forever", fake_run_forever)
    monkeypatch.setattr(worker_module.os, "_exit", unexpected_hard_exit)
    assert await worker_module.main_async(["--name", "w-main"]) == 0
    assert seen["name"] == "w-main"


def test_worker_cli_exits_even_when_execution_ignores_cancellation() -> None:
    """Prove asyncio.run's final cancellation cannot hang the dedicated CLI."""
    script = textwrap.dedent(
        """
        import asyncio
        from deep_research import worker as module
        from deep_research.orchestration import OrchestrationRuntime
        from deep_research.persistence.memory_repository import InMemoryRepository

        module._CLEANUP_SECONDS = 0.02

        async def build(settings):
            repo = InMemoryRepository()
            await repo.create_run_once(
                "stuck provider", request_hash="", claimable=True,
                execution=OrchestrationRuntime().start("deep", {"query": "stuck provider"}),
            )

            class Executor:
                async def execute(self, *args, **kwargs):
                    worker.request_stop()
                    while True:
                        try:
                            await asyncio.Event().wait()
                        except asyncio.CancelledError:
                            pass

            class Engine:
                async def dispose(self):
                    raise AssertionError("uncooperative task must be ended at process boundary")

            worker = module.Worker(repo, Executor(), settings)
            return worker, Engine()

        module._build_worker = build
        module.main()
        raise AssertionError("hard exit must end the process")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        env={
            **os.environ,
            "APP_ENV": "development",
            "DR_WORKER_SHUTDOWN_GRACE_SECONDS": "0.02",
            "DR_WORKER_POLL_SECONDS": "0.01",
        },
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "shutdown_cleanup_timeout" in result.stderr
    assert "forcing process exit after bounded shutdown" in result.stderr
