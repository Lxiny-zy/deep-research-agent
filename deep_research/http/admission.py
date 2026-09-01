"""进程级研究任务准入控制。

限的是**本进程同时执行**多少次研究，与按客户端限流（deps 里的 _RateLimiter）
是两回事：后者防单个调用方刷接口，前者防本进程被压垮。

队列刻意有界：超载要变成可观测的 503，而不是无限增长的 asyncio 任务集合。
跨进程的全局上限不在这里——那由 ``execution_mode=worker`` 下的副本数
× ``max_active_runs`` 表达。
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import FastAPI, HTTPException

logger = logging.getLogger(__name__)


class RunAdmissionLimit(RuntimeError):
    """Raised when the process-local run admission queue is saturated."""

    def __init__(self, *, queue_full: bool) -> None:
        self.queue_full = queue_full
        super().__init__("run admission capacity exhausted")


class RunAdmissionLease:
    """A single active-run slot, released exactly once."""

    def __init__(self, admission: RunAdmission) -> None:
        self._admission = admission
        self._active = False
        self._released = False

    async def wait(self) -> None:
        if not self._released and not self._active:
            await self._admission.activate(self)

    def release(self) -> None:
        if self._released:
            return
        self._admission.release(self)
        self._released = True


class RunAdmission:
    """Bounded process-local admission for background research runs.

    ``asyncio`` tasks are intentionally not created until a lease is acquired.
    Requests beyond ``max_active_runs`` wait in a bounded queue; once that
    queue is full the caller receives an overload response instead of growing
    memory without limit.  Cross-process deployments still need an external
    coordinator for a cluster-wide limit.
    """

    def __init__(self, max_active_runs: int, max_queued_runs: int) -> None:
        if max_active_runs < 1:
            raise ValueError("max_active_runs must be >= 1")
        if max_queued_runs < 0:
            raise ValueError("max_queued_runs must be >= 0")
        self.max_active_runs = max_active_runs
        self.max_queued_runs = max_queued_runs
        self.active = 0
        self.queued = 0
        self._wake = asyncio.Event()

    def configure(self, max_active_runs: int, max_queued_runs: int) -> None:
        """Apply runtime changes without invalidating existing leases."""
        if max_active_runs < 1 or max_queued_runs < 0:
            raise ValueError("invalid run admission limits")
        increased = max_active_runs > self.max_active_runs
        self.max_active_runs = max_active_runs
        self.max_queued_runs = max_queued_runs
        if increased:
            self._wake.set()

    def acquire(self) -> RunAdmissionLease:
        if self.active < self.max_active_runs:
            self.active += 1
            lease = RunAdmissionLease(self)
            lease._active = True
            return lease
        if self.queued >= self.max_queued_runs:
            raise RunAdmissionLimit(queue_full=True)
        self.queued += 1
        return RunAdmissionLease(self)

    async def activate(self, lease: RunAdmissionLease) -> None:
        try:
            while self.active >= self.max_active_runs and not lease._released:
                await self._wake.wait()
                self._wake.clear()
            if lease._released:
                return
            self.queued -= 1
            self.active += 1
            lease._active = True
        except asyncio.CancelledError:
            if not lease._released:
                self.queued = max(0, self.queued - 1)
                lease._released = True
                self._wake.set()
            raise

    def try_acquire(self) -> RunAdmissionLease | None:
        """Acquire immediately for recovery workers; never joins the queue."""
        if self.active >= self.max_active_runs:
            return None
        self.active += 1
        lease = RunAdmissionLease(self)
        lease._active = True
        return lease

    def release(self, lease: RunAdmissionLease) -> None:
        if lease._active:
            if self.active <= 0:
                logger.error("run admission release underflow")
                return
            self.active -= 1
            lease._active = False
        elif not lease._released:
            self.queued = max(0, self.queued - 1)
        self._wake.set()


def _run_admission(app: FastAPI) -> RunAdmission:
    settings = app.state.settings
    admission = getattr(app.state, "run_admission", None)
    if not isinstance(admission, RunAdmission):
        admission = RunAdmission(settings.max_active_runs, settings.max_queued_runs)
        app.state.run_admission = admission
    else:
        admission.configure(settings.max_active_runs, settings.max_queued_runs)
    return admission


async def _acquire_run_slot(app: FastAPI) -> RunAdmissionLease:
    admission = _run_admission(app)
    try:
        return admission.acquire()
    except RunAdmissionLimit as exc:
        # This is service saturation, distinct from the per-client request
        # limiter above; advertise temporary unavailability consistently.
        status = 503
        raise HTTPException(
            status_code=status,
            detail="research service is overloaded; retry later",
            headers={"Retry-After": "5"},
        ) from exc
