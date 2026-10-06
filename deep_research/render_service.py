"""Execute durable render jobs independently of individual HTTP waiters."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any
from uuid import uuid4
from weakref import WeakKeyDictionary

from . import render_tasks
from .blocking import run_blocking
from .config import Settings
from .persistence.repository import LeaseLostError, RunDetail
from .render_capacity import RENDER_CAPACITY
from .render_diagnostics import exception_diagnostic
from .render_process import RenderProcess
from .render_queue import (
    MemoryRenderQueue,
    RenderConflict,
    RenderFailed,
    RenderJob,
    SqlRenderQueue,
    _hash,
)
from .shutdown import cancel, wait_until

logger = logging.getLogger(__name__)
LEASE_SECONDS = 90.0
HEARTBEAT_SECONDS = 20.0
POLL_SECONDS = 0.5
_standalone: WeakKeyDictionary = WeakKeyDictionary()


class RenderService:
    def __init__(self, repo: Any, settings: Settings, *, queue: Any = None) -> None:
        self.repo = repo
        self.root = str(Path(settings.artifact_root).resolve())
        self.pool = hashlib.sha256(os.path.normcase(self.root).encode()).hexdigest()
        self.quota = settings.artifact_total_bytes
        self.queue = queue or (
            SqlRenderQueue(repo._sm)
            if getattr(repo, "_sm", None) is not None
            else MemoryRenderQueue()
        )
        self.loop = asyncio.get_running_loop()
        self._drainer: asyncio.Task | None = None
        self._wakeup = asyncio.Event()
        self._active: set[asyncio.Task] = set()
        self._fences: dict[str, threading.Event] = {}
        self._processes: set[RenderProcess] = set()
        self.execution_timeout = getattr(settings, "render_execution_timeout_seconds", 600.0)
        self.progress_timeout = getattr(settings, "render_progress_timeout_seconds", 180.0)
        self.progress_poll = getattr(settings, "render_progress_poll_seconds", 1.0)
        self.requires_hard_exit = False
        self._shutdown_deadline: float | None = None
        self._inflight: dict[str, tuple[str, asyncio.Task]] = {}
        self._changed = 0
        self._listeners: set[asyncio.Future] = set()
        self.stopping = False

    async def _exists(self, run_id: str) -> bool:
        return self.repo is None or await self.repo.get_run_status(run_id) is not None

    async def _owned(self, job: RenderJob) -> bool:
        if job.lease_owner is None:
            return False
        return await self._can_execute(job) and await self.queue.owned(job.id, job.lease_owner)

    async def _can_execute(self, job: RenderJob) -> bool:
        if self.repo is None:
            return True
        status = await self.repo.get_run_status(job.run_id)
        return status is not None and not (
            job.payload.get("automatic") and status in {"cancelling", "cancelled"}
        )

    def _notify(self) -> None:
        self._changed += 1
        for future in list(self._listeners):
            if not future.done():
                future.set_result(None)

    def wake(self) -> None:
        self._wakeup.set()
        if not self.stopping and (self._drainer is None or self._drainer.done()):
            self._drainer = asyncio.create_task(self._drain())
            self._drainer.add_done_callback(
                lambda task: None if task.cancelled() else task.exception()
            )

    async def poll_pending(self) -> None:
        while not self.stopping:
            try:
                if await self.queue.pending(self.pool):
                    self.wake()
            except Exception as exc:
                logger.warning("render queue poll: %s", type(exc).__name__)
            await asyncio.sleep(1)

    async def _drain(self) -> None:
        while not self.stopping:
            try:
                self._wakeup.clear()
                while not self.stopping and len(self._active) < RENDER_CAPACITY:
                    job = await self.queue.claim(
                        self.pool, str(uuid4()), limit=RENDER_CAPACITY, lease_seconds=LEASE_SECONDS
                    )
                    if job is None:
                        break
                    if self.stopping:
                        # A cancelled database claim may complete late. Keep
                        # its lease recoverable without starting work on exit.
                        return
                    task = asyncio.create_task(self._execute(job))
                    self._active.add(task)
                    task.add_done_callback(self._active.discard)
                    task.add_done_callback(
                        lambda done: None if done.cancelled() else done.exception()
                    )
                if not self._active:
                    if self._wakeup.is_set():
                        continue
                    return
                # New jobs may arrive while existing renders still occupy only
                # part of the pool. Refill on admission as well as completion.
                wakeup = asyncio.create_task(self._wakeup.wait())
                try:
                    await asyncio.wait(
                        {*self._active, wakeup}, return_when=asyncio.FIRST_COMPLETED
                    )
                finally:
                    wakeup.cancel()
                    await asyncio.gather(wakeup, return_exceptions=True)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("render queue drain: %s", type(exc).__name__)
                await asyncio.sleep(1)

    async def _execute(self, job: RenderJob) -> None:
        assert job.lease_owner is not None
        owner = job.lease_owner
        fence = threading.Event()
        self._fences[job.id] = fence
        before: str | None = None

        async def heartbeat() -> None:
            try:
                while True:
                    await asyncio.sleep(HEARTBEAT_SECONDS)
                    if not await self.queue.renew(job.id, owner, lease_seconds=LEASE_SECONDS):
                        fence.set()
                        return
            except asyncio.CancelledError:
                raise
            except Exception:
                fence.set()

        async def check() -> bool:
            if fence.is_set() or self.loop.is_closed():
                return False
            return await self._owned(job)

        process = RenderProcess()
        self._processes.add(process)
        work: asyncio.Task | None = None
        watchdog: asyncio.Task | None = None

        async def watch() -> None:
            token = before
            changed_at = time.monotonic()
            while True:
                await asyncio.sleep(self.progress_poll)
                if fence.is_set() or not await self._owned(job):
                    raise LeaseLostError("渲染执行权已失效")
                current = await run_blocking(render_tasks.progress_token, job, self.root)
                if current != token:
                    changed_at, token = time.monotonic(), current
                    if not await self.queue.progress(job.id, owner, current):
                        raise LeaseLostError("渲染执行权已失效")
                if time.monotonic() - changed_at >= self.progress_timeout:
                    raise TimeoutError("渲染未产生新的格式检查点，已终止本次执行")

        beat = asyncio.create_task(heartbeat())
        try:
            if not await self._can_execute(job):
                await self.queue.cancel(job.id)
                return
            if job.payload_hash != _hash(job.pool, job.run_id, job.kind, job.payload):
                raise ValueError("持久化渲染输入校验失败")
            before = await run_blocking(render_tasks.progress_token, job, self.root)
            if not await self.queue.progress(job.id, job.lease_owner, before):
                return
            work = asyncio.create_task(process.run(
                job, self.root, job.payload.get("quota", self.quota), check,
            ))
            watchdog = asyncio.create_task(watch())
            done, _ = await asyncio.wait(
                {work, watchdog}, timeout=self.execution_timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                raise TimeoutError("渲染执行期限已到，已终止本次执行")
            # Cancellation/authority loss wins a simultaneous completion race.
            if watchdog in done:
                await watchdog
            result = await work
            if not await check():
                raise LeaseLostError("渲染执行权已失效")
            await self.queue.finish(job.id, job.lease_owner, result)
        except asyncio.CancelledError:
            fence.set()
            raise
        except Exception as exc:
            from .execution_policy import transient_failure

            logger.warning(
                "render attempt failed; job=%s attempt=%s origin=%s diagnostic=%s",
                job.id, job.attempts, "child" if process.diagnostic else "parent",
                json.dumps(process.diagnostic or exception_diagnostic(exc), ensure_ascii=True),
            )
            fence.set()
            process.abort()
            after: str | None
            try:
                after = await run_blocking(render_tasks.progress_token, job, self.root)
            except Exception:
                after = before
            try:
                error = render_tasks.error_record(exc)
            except Exception:
                error = {"kind": "RuntimeError", "message": f"渲染失败（{type(exc).__name__}）"}
            await self.queue.fail(
                job.id,
                job.lease_owner,
                error,
                retryable=isinstance(exc, (OSError, LeaseLostError)) or transient_failure(exc),
                progress=after,
                delay=min(8, 2**job.stalls),
            )
        finally:
            fence.set()
            process.abort()
            cleanup_deadline = time.monotonic() + 1
            if self._shutdown_deadline is not None:
                cleanup_deadline = min(cleanup_deadline, self._shutdown_deadline)
            tasks = {task for task in (work, watchdog, beat) if task is not None}
            cancel(tasks)
            pending = await wait_until(tasks, cleanup_deadline)
            # Killing is asynchronous on Windows. Do not advertise a free
            # physical slot until the child has actually exited, even when a
            # cancellation interrupted run()'s own finally/reaping step.
            reaped = await process.reap(cleanup_deadline)
            self.requires_hard_exit |= bool(pending) or not reaped
            self._processes.discard(process)
            self._fences.pop(job.id, None)
            self._notify()

    async def _wait(self, job_id: str) -> Any:
        while True:
            observed = self._changed
            state = await self.queue.state(job_id)
            if state is None or state["status"] == "cancelled":
                raise FileNotFoundError("渲染任务已删除")
            if state["status"] == "done":
                job = await self.queue.get(job_id)
                if job is None:
                    raise FileNotFoundError("渲染任务已删除")
                return await run_blocking(render_tasks.load_result, job, self.root, self.quota)
            if state["status"] == "error":
                raise RenderFailed(job_id, state["error"] or {})
            now = time.time()
            if (state["status"] == "pending" and state["available_at"] <= now) or (
                state["status"] == "running" and (state["lease_until"] or 0) <= now
            ):
                self.wake()
            future = self.loop.create_future()
            self._listeners.add(future)
            try:
                if observed == self._changed:
                    try:
                        await asyncio.wait_for(future, POLL_SECONDS)
                    except TimeoutError:
                        pass
            finally:
                self._listeners.discard(future)

    async def request(
        self,
        kind: str,
        run_id: str,
        key: str,
        payload: dict[str, Any],
        *,
        restart_failed: bool = False,
        request_token: str | None = None,
    ) -> Any:
        fingerprint = _hash(self.pool, run_id, kind, payload)
        existing = self._inflight.get(key)
        if existing is not None and not existing[1].done() and request_token is None:
            if existing[0] != fingerprint:
                raise RenderConflict("同一渲染请求不能用于不同输入")
            return await asyncio.shield(existing[1])
        job = await self.reserve(
            kind, run_id, key, payload,
            restart_failed=restart_failed,
            request_token=request_token,
        )
        waiter = asyncio.create_task(self._wait(job.id))
        self._inflight[key] = (fingerprint, waiter)

        def finished(task: asyncio.Task) -> None:
            if self._inflight.get(key, (None, None))[1] is task:
                self._inflight.pop(key, None)
            if not task.cancelled():
                task.exception()

        waiter.add_done_callback(finished)
        self.wake()
        return await asyncio.shield(waiter)

    async def reserve(
        self, kind: str, run_id: str, key: str, payload: dict[str, Any], *,
        restart_failed: bool = False, request_token: str | None = None,
    ) -> RenderJob:
        if self.stopping:
            raise RuntimeError("渲染服务正在关闭")
        if not await self._exists(run_id):
            raise FileNotFoundError("研究任务已删除")
        job = await self.queue.reserve(
            key=key, pool=self.pool, run_id=run_id, kind=kind, payload=payload,
            restart_failed=restart_failed, request_token=request_token,
        )
        self.wake()
        return job

    async def build(
        self, detail: RunDetail, *, automatic: bool = False, retry_token: str | None = None
    ) -> Any:
        from .workbench.delivery_store import current_version, load_version

        version = await run_blocking(current_version, detail, self.root)
        if version is not None:
            return await run_blocking(load_version, detail, self.root, version)
        payload = {**render_tasks.bundle_payload(detail, self.quota), "automatic": automatic}
        key = render_tasks.digest(
            [self.pool, detail.id, "bundle", payload["input_version"], automatic, self.quota]
        )
        token = (
            f"attempt-{detail.orchestration.attempt}"
            if automatic and detail.orchestration
            else retry_token
        )
        await self.request(
            "bundle", detail.id, key, payload, restart_failed=bool(token), request_token=token
        )
        version = await run_blocking(current_version, detail, self.root)
        if version is None:
            raise FileNotFoundError("交付生成后未找到已发布版本")
        return await run_blocking(load_version, detail, self.root, version)

    async def retry(
        self,
        detail: RunDetail,
        version: str,
        target: str,
        request_id: str,
        *,
        automatic: bool = False,
    ) -> Any:
        payload = {
            **render_tasks.bundle_payload(detail, self.quota),
            "base_version": version,
            "format": target,
            "automatic": automatic,
        }
        key = render_tasks.digest([self.pool, detail.id, "retry", request_id, automatic])
        if await self.queue.by_key(key) is None:
            from .workbench.delivery_store import DeliveryConflict, current_version

            current = await run_blocking(current_version, detail, self.root)
            if current != version:
                raise DeliveryConflict(
                    "delivery_version_changed", "交付物已有新版本，请刷新后再重试"
                )
        token = (
            f"attempt-{detail.orchestration.attempt}"
            if automatic and detail.orchestration
            else None
        )
        try:
            return await self.request(
                "retry", detail.id, key, payload, restart_failed=bool(token), request_token=token
            )
        except RenderFailed as exc:
            if automatic:
                raise
            render_tasks.raise_render_error(exc.error)

    async def export(
        self,
        detail: RunDetail,
        document: Any,
        format: str,
        *,
        retry_token: str | None = None,
        **options: Any,
    ) -> Any:
        payload = render_tasks.export_payload(detail, document, format, options, self.quota)
        key = render_tasks.digest([self.pool, detail.id, "export", payload])
        try:
            return await self.request(
                "export",
                detail.id,
                key,
                payload,
                restart_failed=bool(retry_token),
                request_token=retry_token,
            )
        except RenderFailed as exc:
            render_tasks.raise_render_error(exc.error)

    async def close(self, *, seconds: float = 5, deadline: float | None = None) -> None:
        deadline = time.monotonic() + seconds if deadline is None else deadline
        if self._shutdown_deadline is not None:
            deadline = min(deadline, self._shutdown_deadline)
        self._shutdown_deadline = deadline
        self.stopping = True
        if self._drainer is not None:
            cancel({self._drainer})
        try:
            if self._active:
                # Reserve part of the same total budget for process reaping.
                await wait_until(self._active, max(time.monotonic(), deadline - 1))
        finally:
            # Even cancellation of close() must synchronously terminate native
            # processes before its first cancellable cleanup wait.
            for fence in self._fences.values():
                fence.set()
            for process in self._processes:
                process.abort()
            tasks = [*self._active, *(item[1] for item in self._inflight.values())]
            if self._drainer is not None:
                tasks.append(self._drainer)
            cancel(tasks)
            pending = await wait_until(tasks, deadline)
            self.requires_hard_exit |= bool(pending)


def service_for(repo: Any, settings: Settings) -> RenderService:
    loop = asyncio.get_running_loop()
    root = os.path.normcase(str(Path(settings.artifact_root).resolve()))
    if repo is None:
        services = _standalone.setdefault(loop, {})
    else:
        services = getattr(repo, "_render_services", None)
        if services is None:
            services = {}
            repo._render_services = services
    service = services.get(root)
    if service is None or service.loop is not loop or service.stopping:
        service = RenderService(repo, settings)
        services[root] = service
    service.quota = settings.artifact_total_bytes
    return service
