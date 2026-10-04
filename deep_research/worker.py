"""独立执行进程：抢占式领取排队中的研究任务并执行。

API 只负责持久入队；本类可运行于独立进程，也可嵌入 API 进程领取同一队列。
API 与执行分离带来两点：

* **水平扩展**：worker 可任意起多个副本（``docker compose up --scale worker=3``），
  数据库统一约束 ``max_active_runs``，增加副本不会放大全局上限；
* **故障隔离**：重启或杀掉 API 不影响进行中的研究；杀掉任一 worker，其租约到期后
  另一个 worker 从 checkpoint 接管续跑。

正确性完全建立在既有的租约 fencing 上——本模块不引入新的一致性机制，只是把
「谁调用 :meth:`RunExecutor.execute`」从 HTTP 请求换成了领取循环。

    python -m deep_research.worker
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import signal
import socket
import sys
import time
from collections.abc import Awaitable, Callable
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncEngine

from .catalog.repository import CatalogRepository
from .config import Settings
from .execution import ExecutionContext, RunExecutor, settings_for_resume
from .library.repository import SqlLibraryRepository
from .observability import Event
from .persistence.db import make_engine, make_sessionmaker, prepare_sqlite_schema
from .persistence.repository import ClaimedRun, LeaseLostError, ResearchRepository
from .persistence.sql_repository import SqlRepository

logger = logging.getLogger(__name__)

# 领取到任务后立刻再试一次，不等轮询间隔——队列积压时不应人为限速。
_BUSY_POLL_SECONDS = 0.0
_HEARTBEAT_SECONDS = 5.0
# Client/lease cleanup gets a separate, finite budget after the run grace period.
_CLEANUP_SECONDS = 5.0
_CANCELLATION_PAGE_SIZE = 32

ExecuteRun = Callable[..., Awaitable[None]]
TaskCallback = Callable[[ClaimedRun, asyncio.Task[None]], None]


class Worker:
    """一个执行进程：并发领取并执行研究任务，直到收到停止信号。"""

    def __init__(
        self,
        repo: ResearchRepository,
        executor: RunExecutor,
        settings: Settings,
        *,
        name: str | None = None,
        execute: ExecuteRun | None = None,
        on_task_started: TaskCallback | None = None,
        on_task_finished: TaskCallback | None = None,
    ) -> None:
        self.repo = repo
        self.executor = executor
        self.settings = settings
        self._execute_override = execute
        self._on_task_started = on_task_started
        self._on_task_finished = on_task_finished
        # Display/heartbeat identity is separate from each claim's fencing token.
        # Reused names and same-process takeovers must not revive an old owner.
        self.name = name or f"worker-{uuid4().hex[:12]}"
        self._stopping = asyncio.Event()
        self._wake = asyncio.Event()
        self._running: set[asyncio.Task[None]] = set()
        self._cancellation_offset = 0
        self._last_heartbeat = 0.0
        self._stop_started: float | None = None
        self._admission: asyncio.Task[float] | None = None
        self.requires_hard_exit = False

    def request_stop(self, reason: str = "signal") -> None:
        """停止领取新任务，给在途任务有限的完成宽限期。"""
        if not self._stopping.is_set():
            self._stop_started = time.monotonic()
            logger.info(
                "worker %s draining; reason=%s grace=%ss active=%s",
                self.name,
                reason,
                self.settings.worker_shutdown_grace_seconds,
                self._active_names(),
            )
        self._stopping.set()
        self.wake()

    def wake(self) -> None:
        """Wake admission after local queue/configuration changes; calls coalesce."""
        self._wake.set()

    def _notify(
        self, callback: TaskCallback | None, claimed: ClaimedRun, task: asyncio.Task[None]
    ) -> None:
        if callback is not None:
            try:
                callback(claimed, task)
            except Exception:
                # API bookkeeping cannot abandon a claimed run or prevent cleanup.
                logger.exception("worker %s task callback failed for %s", self.name, claimed.run_id)

    def _active_names(self) -> list[str]:
        return sorted(task.get_name() for task in self._running if not task.done())

    @property
    def capacity(self) -> int:
        return max(0, self.settings.max_active_runs - len(self._running))

    async def run_forever(self) -> None:
        logger.info(
            "worker %s started (max_active_runs=%s, poll=%ss)",
            self.name,
            self.settings.max_active_runs,
            self.settings.worker_poll_seconds,
        )
        stop_wait = asyncio.create_task(self._stopping.wait(), name="worker-stop")
        try:
            while not self._stopping.is_set():
                # A stalled claim/heartbeat must not keep signal handling outside
                # the shutdown budget. An interrupted claim is recovered by lease.
                self._admission = asyncio.create_task(self._tick(), name="worker-admission")
                await asyncio.wait(
                    {self._admission, stop_wait}, return_when=asyncio.FIRST_COMPLETED
                )
                if self._stopping.is_set():
                    self._admission.cancel()
                    break
                delay = await self._admission
                self._admission = None
                if delay <= 0:
                    # 让出事件循环，避免忙等把 CPU 跑满。
                    await asyncio.sleep(0)
                    continue
                await self._sleep_or_stop(delay)
        finally:
            self.request_stop(reason="loop_exit")
            stop_wait.cancel()
            await asyncio.gather(stop_wait, return_exceptions=True)
            try:
                await self._drain()
            finally:
                await self._remove_registration()

    async def _tick(self) -> float:
        """领取并派发至多一个任务；返回下一次领取前应等待的秒数。"""
        if self._stopping.is_set():
            return self.settings.worker_poll_seconds
        if not await self._heartbeat():
            return self.settings.worker_poll_seconds
        await self.settle_cancellations()
        if self._stopping.is_set() or self.capacity <= 0:
            return self.settings.worker_poll_seconds
        try:
            claimed = await self.repo.claim_next_run(
                f"claim-{uuid4().hex}", max_active_runs=self.settings.max_active_runs
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("worker %s failed to claim a run", self.name)
            return self.settings.worker_poll_seconds
        if claimed is None:
            return self.settings.worker_poll_seconds
        if self._stopping.is_set():
            logger.info(
                "worker %s stopped during claim; run %s retained for lease recovery",
                self.name,
                claimed.run_id,
            )
            return self.settings.worker_poll_seconds

        recoveries = claimed.execution.checkpoint.get("scratch", {}).get("_recovery", {})
        if (
            claimed.claim_attempts - int(recoveries.get("count", 0))
            > self.settings.max_claim_attempts
        ):
            # 领取次数超限意味着这个任务每次执行都崩：再派发一次只是重复浪费。
            await self._poison(claimed)
            return _BUSY_POLL_SECONDS

        task = asyncio.create_task(self._execute_claimed(claimed), name=f"run:{claimed.run_id}")
        self._running.add(task)

        def finished(done: asyncio.Task[None]) -> None:
            self._running.discard(done)
            if not done.cancelled() and done.exception() is not None:
                logger.error(
                    "worker %s execution setup failed for %s: %s",
                    self.name,
                    claimed.run_id,
                    done.exception(),
                )
            self._notify(self._on_task_finished, claimed, done)
            self.wake()

        task.add_done_callback(finished)
        return _BUSY_POLL_SECONDS

    async def settle_cancellations(self) -> int:
        """Settle one bounded page without consuming execution slots or stealing leases."""
        try:
            page = await self.repo.list_runs(
                status="cancelling", limit=_CANCELLATION_PAGE_SIZE, offset=self._cancellation_offset
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("worker %s failed to scan queued cancellations", self.name)
            return 0
        settled = 0
        for run in page:
            if self._stopping.is_set():
                break
            settled += await self._settle_cancellation(run.id)
        # Settled rows disappear from the result set. Rotate past active leases,
        # so one full page of busy owners cannot hide later queued cancellations.
        self._cancellation_offset = (
            self._cancellation_offset + len(page) - settled
            if len(page) == _CANCELLATION_PAGE_SIZE
            else 0
        )
        return settled

    async def _settle_cancellation(self, run_id: str, owner: str | None = None) -> bool:
        owned = owner is not None
        token = owner or f"cancel-{uuid4().hex}"
        settled = False
        hub = self.executor.ctx.live.get(run_id)
        try:
            if not owned:
                owned = await self.repo.acquire_lease(run_id, token)
                if not owned:
                    return False
            # The scan is only a candidate lookup; inspect again behind fencing.
            if await self.repo.get_run_status(run_id) != "cancelling":
                return False
            event = Event(
                stage="ORCHESTRATOR",
                type="cancelled",
                message="运行已取消",
                data={"status": "cancelled", "queued": True},
            )
            try:
                stored = await self.repo.append_events(run_id, [event], lease_owner=token)
                if stored:
                    event = stored[0]
            except Exception:
                logger.exception(
                    "worker %s failed to record queued cancellation %s", self.name, run_id
                )
            await self.repo.set_status(run_id, "cancelled", lease_owner=token)
            settled = True
            if hub is not None:
                hub.publish(event)
            return True
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("worker %s failed to settle queued cancellation %s", self.name, run_id)
            return False
        finally:
            if settled and hub is not None:
                self.executor.ctx.close_hub(run_id, hub)
            if owned:
                with contextlib.suppress(Exception):
                    await self.repo.release_lease(run_id, token)

    async def _poison(self, claimed: ClaimedRun) -> None:
        """把反复失败的任务置终态，并留下可审计的原因。"""
        logger.error(
            "run %s exceeded %s claim attempts; marking as failed",
            claimed.run_id,
            self.settings.max_claim_attempts,
        )
        event = Event(
            stage="ORCHESTRATOR",
            type="error",
            message="任务反复执行失败，已停止重试",
            data={"status": "error", "reason": "poison_run", "attempts": claimed.claim_attempts},
        )
        try:
            await self.repo.append_events(claimed.run_id, [event], lease_owner=claimed.lease_owner)
        except Exception:
            # The audit event is best-effort.  A transient event-write failure
            # must not prevent the terminal status from fencing the run.
            logger.exception("failed to append poison event for run %s", claimed.run_id)
        try:
            await self.repo.set_status(claimed.run_id, "error", lease_owner=claimed.lease_owner)
        except Exception:
            logger.exception("failed to mark run %s as poisoned", claimed.run_id)
        finally:
            with contextlib.suppress(Exception):
                await self.repo.release_lease(claimed.run_id, claimed.lease_owner)

    async def _execute_claimed(self, claimed: ClaimedRun) -> None:
        """执行一个已领取的任务。

        ``execute`` 自己负责终态落库、租约释放与资源清理，所以这里不做兜底状态
        写入——重复写会覆盖 run() 内部更精确的失败原因。
        """
        execution_started = False
        task = asyncio.current_task()
        try:
            status = await self.repo.get_run_status(claimed.run_id)
            if status == "cancelling":
                await self._settle_cancellation(claimed.run_id, claimed.lease_owner)
                return
            if status not in {"pending", "running"}:
                return
            settings = settings_for_resume(self.settings, claimed.execution)
            if task is not None:
                self._notify(self._on_task_started, claimed, task)
            dispatch = getattr(claimed, "dispatch", None)
            if dispatch:
                event = Event(
                    stage="ORCHESTRATOR",
                    type="info",
                    message="任务已从队列领取，开始执行",
                    data={
                        **dispatch,
                        "category": "schedule_dispatch",
                        "worker": self.name,
                        "attempt": claimed.attempt,
                    },
                )
                try:
                    await self.repo.append_events(
                        claimed.run_id, [event], lease_owner=claimed.lease_owner
                    )
                except LeaseLostError:
                    raise
                except Exception:
                    logger.exception("worker %s failed to record task dispatch", self.name)
            execution_started = True
            await self._invoke_execution(claimed, settings)
        finally:
            if not execution_started:
                # No provider work started. A cancellation during the preflight
                # read must not strand this unused lease for its full TTL.
                with contextlib.suppress(Exception):
                    await self.repo.release_lease(claimed.run_id, claimed.lease_owner)
                self.wake()

    async def _invoke_execution(self, claimed: ClaimedRun, settings: Settings) -> None:
        # 黑板 query 用 checkpoint 里的 input（可能是多轮消解后的完整问题），
        # 回退到 run 记录的原始 query。
        query = str(claimed.execution.input.get("query") or claimed.query)
        scratch = claimed.execution.checkpoint.get("scratch", {})
        requested_workflow = (
            scratch.get("requested_workflow")
            if isinstance(scratch, dict) and isinstance(scratch.get("requested_workflow"), str)
            else None
        )
        logger.info(
            "worker %s %s run %s (attempt=%s)",
            self.name,
            "resuming" if claimed.resumed else "starting",
            claimed.run_id,
            claimed.attempt,
        )
        try:
            execute = self._execute_override or self.executor.execute
            await execute(
                claimed.run_id,
                query,
                settings,
                workflow=claimed.execution.workflow_name,
                requested_workflow=requested_workflow,
                resume_execution=claimed.execution if claimed.resumed else None,
                initial_execution=None if claimed.resumed else claimed.execution,
                lease_owner=claimed.lease_owner,
            )
        except asyncio.CancelledError:
            # 进程收到硬停止信号。租约不释放也不置终态：让它自然过期，
            # 由下一个 worker 从 checkpoint 接管——与 kill -9 的语义一致。
            logger.warning("run %s interrupted by worker shutdown", claimed.run_id)
            raise
        except Exception:
            logger.exception("run %s failed in worker %s", claimed.run_id, self.name)

    async def _sleep_or_stop(self, delay: float) -> None:
        """Queue notifications and shutdown both interrupt the idle poll interval."""
        if self._stopping.is_set():
            return
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._wake.wait(), timeout=delay)
        self._wake.clear()

    async def _drain(self) -> None:
        tasks: set[asyncio.Task] = set(self._running)
        if self._admission is not None:
            self._admission.cancel()
            tasks.add(self._admission)
        heartbeat = asyncio.create_task(self._drain_heartbeat(), name="worker-drain-heartbeat")
        pending = tasks
        try:
            started = self._stop_started if self._stop_started is not None else time.monotonic()
            remaining = self.settings.worker_shutdown_grace_seconds - (time.monotonic() - started)
            if tasks:
                _, pending = await asyncio.wait(tasks, timeout=max(0.0, remaining))
        finally:
            cleanup_deadline = time.monotonic() + _CLEANUP_SECONDS
            pending = {task for task in pending if not task.done()}
            if pending:
                logger.warning(
                    "worker %s cancelling in-flight tasks; reason=shutdown_deadline active=%s; "
                    "unfinished runs remain recoverable from checkpoint",
                    self.name,
                    sorted(task.get_name() for task in pending),
                )
                for task in pending:
                    task.cancel()
                _, pending = await asyncio.wait(pending, timeout=_CLEANUP_SECONDS)
            heartbeat.cancel()
            _, heartbeat_pending = await asyncio.wait(
                {heartbeat}, timeout=max(0.0, cleanup_deadline - time.monotonic())
            )
            pending.update(heartbeat_pending)
            if pending:
                self.requires_hard_exit = True
                logger.error(
                    "worker %s cleanup deadline exceeded; "
                    "reason=shutdown_cleanup_timeout active=%s",
                    self.name,
                    sorted(task.get_name() for task in pending),
                )

    async def _heartbeat(self, *, force: bool = False) -> bool:
        if not force and time.monotonic() - self._last_heartbeat < _HEARTBEAT_SECONDS:
            return True
        try:
            await self.repo.heartbeat_worker(self.name, len(self._running))
            self._last_heartbeat = time.monotonic()
            return True
        except Exception:
            logger.exception("worker %s heartbeat failed; pausing admission", self.name)
            return False

    async def _drain_heartbeat(self) -> None:
        # Admission has stopped, but readiness remains fresh until execution and
        # cleanup have finished. Lease renewal still belongs to RunExecutor.
        while True:
            await self._heartbeat(force=True)
            await asyncio.sleep(_HEARTBEAT_SECONDS)

    async def _remove_registration(self) -> None:
        removal = asyncio.create_task(self.repo.remove_worker(self.name), name="worker-unregister")
        _, pending = await asyncio.wait({removal}, timeout=_CLEANUP_SECONDS)
        if pending:
            removal.cancel()
            # Give cooperative cancellation one turn without an unbounded gather.
            _, pending = await asyncio.wait({removal}, timeout=0)
            self.requires_hard_exit |= bool(pending)
            logger.warning("worker %s unregister timed out; heartbeat will expire", self.name)
        elif not removal.cancelled() and removal.exception() is not None:
            logger.error("worker %s unregister failed: %s", self.name, removal.exception())


async def _build_worker(settings: Settings) -> tuple[Worker, AsyncEngine]:
    engine = make_engine(settings.database_url)
    if settings.database_url.startswith("sqlite"):
        # 与 API 启动路径一致：本地 SQLite 自备 schema，PostgreSQL 由迁移负责。
        await prepare_sqlite_schema(engine, settings.database_url)
    sessionmaker = make_sessionmaker(engine)
    repo = SqlRepository(sessionmaker)
    catalog = CatalogRepository(sessionmaker)
    # live 为空字典：worker 没有 SSE 订阅者，事件经仓储落库供 API 侧读取。
    library = SqlLibraryRepository(sessionmaker)
    executor = RunExecutor(ExecutionContext(repo=repo, catalog=catalog, library=library, live={}))
    return Worker(repo, executor, settings), engine


async def main_async(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deep Research Agent 执行 worker")
    parser.add_argument(
        "--name",
        default=os.getenv("DR_WORKER_NAME") or socket.gethostname(),
        help=(
            "worker 心跳与日志标识，默认主机名；各副本应使用不同标识。"
            "执行租约由每次领取独立生成。"
        ),
    )
    parser.add_argument("--check", action="store_true", help="检查本 worker 的持久化心跳后退出")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = Settings()
    settings.validate_deployment()
    if args.check:
        from datetime import UTC, datetime, timedelta

        from sqlalchemy import select

        from .persistence.orm import WorkerHeartbeatRow

        engine = make_engine(settings.database_url)
        try:
            async with make_sessionmaker(engine)() as session:
                seen = await session.scalar(
                    select(WorkerHeartbeatRow.seen_at).where(WorkerHeartbeatRow.name == args.name)
                )
                return (
                    0
                    if seen and seen.replace(tzinfo=UTC) > datetime.now(UTC) - timedelta(seconds=30)
                    else 1
                )
        finally:
            await engine.dispose()
    worker, engine = await _build_worker(settings)
    if args.name:
        worker.name = args.name

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, worker.request_stop)
        except NotImplementedError:
            # Windows 的 ProactorEventLoop 不支持 add_signal_handler；
            # 退回到同步 handler（KeyboardInterrupt 仍由下面的 except 兜住）。
            signal.signal(sig, lambda *_: worker.request_stop())

    try:
        await worker.run_forever()
    except KeyboardInterrupt:
        worker.request_stop()
    finally:
        if worker.requires_hard_exit:
            # asyncio.run() waits for all tasks even after main_async returns.
            # An uncooperative provider/cleanup task therefore needs a process
            # boundary; this only applies to the dedicated worker CLI.
            logger.error("worker %s forcing process exit after bounded shutdown", worker.name)
            logging.shutdown()
            os._exit(0)
        await engine.dispose()
    return 0


def main() -> None:
    sys.exit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
