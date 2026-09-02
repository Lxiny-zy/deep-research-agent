"""独立执行进程：抢占式领取排队中的研究任务并执行。

``execution_mode=worker`` 下，API 只负责入队（写 ``claimable_at``），本进程负责执行。
API 与执行分离带来两点：

* **水平扩展**：worker 可任意起多个副本（``docker compose up --scale worker=3``），
  全局并发 = 副本数 × ``max_active_runs``；
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
import signal
import sys
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncEngine

from .catalog.repository import CatalogRepository
from .config import Settings
from .execution import ExecutionContext, RunExecutor, settings_for_resume
from .observability import Event
from .persistence.db import make_engine, make_sessionmaker, prepare_sqlite_schema
from .persistence.repository import ClaimedRun, ResearchRepository
from .persistence.sql_repository import SqlRepository

logger = logging.getLogger(__name__)

# 领取到任务后立刻再试一次，不等轮询间隔——队列积压时不应人为限速。
_BUSY_POLL_SECONDS = 0.0


class Worker:
    """一个执行进程：并发领取并执行研究任务，直到收到停止信号。"""

    def __init__(
        self,
        repo: ResearchRepository,
        executor: RunExecutor,
        settings: Settings,
        *,
        name: str | None = None,
    ) -> None:
        self.repo = repo
        self.executor = executor
        self.settings = settings
        # 每个 worker 一个稳定身份，作为租约 owner。重启后是新身份，
        # 因此旧租约只能靠过期回收——这正是崩溃接管所依赖的语义。
        self.name = name or f"worker-{uuid4().hex[:12]}"
        self._stopping = asyncio.Event()
        self._running: set[asyncio.Task[None]] = set()

    def request_stop(self) -> None:
        """停止领取新任务。**不**取消在跑的任务：优雅退出要让它们跑完。"""
        if not self._stopping.is_set():
            logger.info("worker %s draining; no new runs will be claimed", self.name)
        self._stopping.set()

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
        try:
            while not self._stopping.is_set():
                delay = await self._tick()
                if delay <= 0:
                    # 让出事件循环，避免忙等把 CPU 跑满。
                    await asyncio.sleep(0)
                    continue
                await self._sleep_or_stop(delay)
        finally:
            await self._drain()

    async def _tick(self) -> float:
        """领取并派发至多一个任务；返回下一次领取前应等待的秒数。"""
        if self.capacity <= 0:
            return self.settings.worker_poll_seconds
        try:
            claimed = await self.repo.claim_next_run(self.name)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("worker %s failed to claim a run", self.name)
            return self.settings.worker_poll_seconds
        if claimed is None:
            return self.settings.worker_poll_seconds

        if claimed.claim_attempts > self.settings.max_claim_attempts:
            # 领取次数超限意味着这个任务每次执行都崩：再派发一次只是重复浪费。
            await self._poison(claimed)
            return _BUSY_POLL_SECONDS

        task = asyncio.create_task(self._execute_claimed(claimed))
        self._running.add(task)
        task.add_done_callback(self._running.discard)
        return _BUSY_POLL_SECONDS

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
        settings = settings_for_resume(self.settings, claimed.execution)
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
            await self.executor.execute(
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
        """空闲等待，但停止信号必须立刻生效而不是等满一个轮询周期。"""
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._stopping.wait(), timeout=delay)

    async def _drain(self) -> None:
        if not self._running:
            return
        logger.info("worker %s waiting for %s in-flight run(s)", self.name, len(self._running))
        await asyncio.gather(*list(self._running), return_exceptions=True)


async def _build_worker(settings: Settings) -> tuple[Worker, AsyncEngine]:
    engine = make_engine(settings.database_url)
    if settings.database_url.startswith("sqlite"):
        # 与 API 启动路径一致：本地 SQLite 自备 schema，PostgreSQL 由迁移负责。
        await prepare_sqlite_schema(engine, settings.database_url)
    sessionmaker = make_sessionmaker(engine)
    repo = SqlRepository(sessionmaker)
    catalog = CatalogRepository(sessionmaker)
    # live 为空字典：worker 没有 SSE 订阅者，事件经仓储落库供 API 侧读取。
    executor = RunExecutor(ExecutionContext(repo=repo, catalog=catalog, live={}))
    return Worker(repo, executor, settings), engine


async def main_async(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deep Research Agent 执行 worker")
    parser.add_argument(
        "--name",
        default=None,
        help="worker 身份（租约 owner）。默认随机生成；多副本部署无需指定。",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = Settings()
    settings.validate_deployment()
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
        await engine.dispose()
    return 0


def main() -> None:
    sys.exit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
