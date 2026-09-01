"""SSE 事件流：进行中实时推送 / 已结束或跨进程时回放仓储。

本模块只做一件事，但这件事有三个容易写错的地方：

* **本地事件中心只是加速层**。事件同时落库，因此没有本地 hub（例如
  ``execution_mode=worker`` 下执行在别的进程）时，订阅者直接 tail 仓储即可，
  浏览器无感——跨进程订阅不需要任何消息中间件；
* **重放语义**。恢复续跑会重写 attempt 并可能复用 seq，因此状态/attempt 变化时
  游标要回绕，再靠事件指纹去重，否则用户会看到重复行或漏掉最终报告；
* **终态判定**。上一 attempt 的终态标记必须留在库里供审计，但绝不能终止一条
  正在续跑的流。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator, AsyncIterator
from typing import cast

from fastapi import FastAPI

from ..observability import Event, EventHub, EventStreamGap
from ..persistence.repository import RUN_ACTIVE_STATUSES, ResearchRepository

_REMOTE_STREAM_POLL_SECONDS = 0.5
_REMOTE_STREAM_TERMINAL_GRACE_SECONDS = 2.0
_SSE_HEARTBEAT_SECONDS = 15.0
_SSE_EVENT_BATCH_SIZE = 512
_SSE_DEDUP_WINDOW = 8192


def _sse(event: Event) -> str:
    event_id = f"id: {event.seq}\n" if event.seq is not None else ""
    return f"{event_id}data: {event.model_dump_json()}\n\n"


async def _stream_run_sse(app: FastAPI, run_id: str, *, after_seq: int = 0) -> AsyncIterator[str]:
    """Stream a run with durable replay and bounded live delivery.

    The local hub is only an acceleration layer.  Durable events are replayed
    first, then a bounded hub subscription catches up events published during
    that query.  If the subscriber falls behind (or the in-memory window is
    too old), the stream transparently switches to the append-only repository.
    """
    repo: ResearchRepository = app.state.repo
    cursor = after_seq
    terminal_deadline: float | None = None
    previous_status: str | None = None
    previous_attempt: int | None = None
    emitted: dict[tuple[int, int], str] = {}

    def fingerprint(event: Event) -> str:
        return event.model_dump_json()

    def should_emit(event: Event) -> bool:
        """Suppress duplicate durable rows after a status/attempt rewind."""
        if event.seq is None:
            return True
        key = (event.attempt, event.seq)
        value = fingerprint(event)
        if emitted.get(key) == value:
            return False
        emitted[key] = value
        if len(emitted) > _SSE_DEDUP_WINDOW:
            emitted.pop(next(iter(emitted)))
        return True

    def observe_state(status: str | None, attempt: int) -> None:
        nonlocal cursor, terminal_deadline, previous_status, previous_attempt
        if previous_status is not None and (
            status != previous_status or attempt != previous_attempt
        ):
            # ``save_events`` is a compatibility overwrite operation and may
            # reuse sequence numbers.  Rewind on state transitions, then use
            # fingerprints to avoid duplicating unchanged rows.
            cursor = after_seq
            terminal_deadline = None
        previous_status = status
        previous_attempt = attempt

    def classify(event: Event, status: str | None, attempt: int) -> tuple[bool, bool]:
        """Return ``(emit, terminal)`` for the current durable run state."""
        terminal = event.stage == "ORCHESTRATOR" and event.type in {
            "done",
            "error",
            "cancelled",
        }
        if terminal and (event.attempt < attempt or status in RUN_ACTIVE_STATUSES):
            return False, terminal
        expected_type = (
            {
                "done": "done",
                "error": "error",
                "cancelled": "cancelled",
            }.get(status)
            if status is not None
            else None
        )
        if terminal and expected_type is not None and event.type != expected_type:
            return False, terminal
        return True, terminal

    async def stable_terminal(status: str | None, attempt: int) -> bool:
        if status is None:
            # Small in-memory/unit-test apps may not have a repository row; a
            # terminal hub event is still authoritative for that local stream.
            return True
        return (
            await repo.get_run_status(run_id) == status
            and (await repo.get_run_attempt(run_id) or 1) == attempt
        )

    async def emit_durable_batch(status: str | None, attempt: int) -> tuple[bool, bool]:
        """Replay one bounded durable batch; return ``(terminal, exhausted)``."""
        nonlocal cursor
        events = await repo.get_events(run_id, after_seq=cursor, limit=_SSE_EVENT_BATCH_SIZE)
        for event in events:
            if event.seq is not None and event.seq < cursor:
                continue
            emit, terminal = classify(event, status, attempt)
            if not emit:
                # A terminal row can be a stale marker that is about to be
                # replaced by ``save_events`` at the same sequence number.
                # Keep the cursor on it until a matching terminal row arrives.
                if not terminal and event.seq is not None:
                    cursor = max(cursor, event.seq + 1)
                continue
            if event.seq is not None:
                cursor = max(cursor, event.seq + 1)
            if not should_emit(event):
                if terminal and await stable_terminal(status, attempt):
                    return True, True
                continue
            yield_event = _sse(event)
            # Async generators cannot yield from this helper, so stash the
            # pending frame for the caller through a small local queue.
            pending_frames.append(yield_event)
            if terminal and await stable_terminal(status, attempt):
                return True, True
        return False, len(events) < _SSE_EVENT_BATCH_SIZE

    # ``emit_durable_batch`` needs to communicate frames without duplicating
    # terminal filtering logic.  Keeping this queue local avoids any producer
    # task or unbounded relay.
    pending_frames: list[str] = []

    hub: EventHub | None = app.state.live.get(run_id)
    if hub is not None:
        status = await repo.get_run_status(run_id)
        attempt = await repo.get_run_attempt(run_id) or 1
        observe_state(status, attempt)
        while True:
            terminal, exhausted = await emit_durable_batch(status, attempt)
            while pending_frames:
                yield pending_frames.pop(0)
            if terminal:
                return
            if exhausted:
                break
            status = await repo.get_run_status(run_id)
            attempt = await repo.get_run_attempt(run_id) or 1
            observe_state(status, attempt)

        live_stream = cast(AsyncGenerator[Event, None], hub.stream(after_seq=cursor))

        async def next_live_event() -> Event:
            return await anext(live_stream)

        next_task: asyncio.Task[Event] | None = asyncio.create_task(next_live_event())
        try:
            while True:
                try:
                    assert next_task is not None
                    event = await asyncio.wait_for(
                        asyncio.shield(next_task), timeout=_SSE_HEARTBEAT_SECONDS
                    )
                except TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                except StopAsyncIteration:
                    break
                except EventStreamGap:
                    break

                status = await repo.get_run_status(run_id)
                attempt = await repo.get_run_attempt(run_id) or 1
                observe_state(status, attempt)
                if event.seq is not None and event.seq < cursor:
                    emit = False
                    terminal = False
                else:
                    emit, terminal = classify(event, status, attempt)
                    if emit and event.seq is not None:
                        cursor = max(cursor, event.seq + 1)
                if emit:
                    if should_emit(event):
                        yield _sse(event)
                if terminal and emit and await stable_terminal(status, attempt):
                    return
                next_task = asyncio.create_task(next_live_event())
        finally:
            if next_task is not None and not next_task.done():
                next_task.cancel()
                await asyncio.gather(next_task, return_exceptions=True)
            await live_stream.aclose()
        # A closed hub normally follows a durable terminal flush.  Still tail
        # the repository below so a queue overflow/window gap cannot lose the
        # final report or terminal marker.

    next_heartbeat = time.monotonic() + _SSE_HEARTBEAT_SECONDS
    while True:
        # Read attempt on both sides of the status query.  A recovery can bump
        # the attempt while a status read is in flight; detecting that race is
        # what prevents us from skipping a newly rewritten seq=0 event.
        attempt_before = await repo.get_run_attempt(run_id) or 1
        status = await repo.get_run_status(run_id)
        if status is None:
            yield _sse(
                Event(
                    stage="ORCHESTRATOR",
                    type="error",
                    message="运行已被删除",
                    data={"status": "missing"},
                )
            )
            return
        attempt = await repo.get_run_attempt(run_id) or 1
        if attempt != attempt_before:
            previous_attempt = attempt_before
        observe_state(status, attempt)
        batch_start_cursor = cursor
        events = await repo.get_events(run_id, after_seq=cursor, limit=_SSE_EVENT_BATCH_SIZE)
        expected_type = {
            "done": "done",
            "error": "error",
            "cancelled": "cancelled",
        }.get(status)
        for event in events:
            terminal = event.stage == "ORCHESTRATOR" and event.type in {
                "done",
                "error",
                "cancelled",
            }
            # Prior-attempt terminal markers remain in storage for audit but
            # must never terminate a resumed stream.  Likewise, a terminal
            # marker observed while the root status is active is stale.
            emit, terminal = classify(event, status, attempt)
            if event.seq is not None:
                if event.seq < cursor:
                    continue
            else:
                if emit:
                    cursor += 1
            if not emit:
                if not terminal and event.seq is not None:
                    cursor = max(cursor, event.seq + 1)
                continue
            if event.seq is not None:
                cursor = max(cursor, event.seq + 1)
            if not should_emit(event):
                if terminal and await stable_terminal(status, attempt):
                    return
                continue
            yield _sse(event)
            if terminal and await stable_terminal(status, attempt):
                return

        # Drain a durable backlog before starting the terminal grace period.
        # Otherwise a completed remote run with several pages of events can
        # synthesize its terminal frame before the real tail has been replayed.
        if len(events) == _SSE_EVENT_BATCH_SIZE and cursor > batch_start_cursor:
            continue

        now = time.monotonic()
        if expected_type is not None:
            if terminal_deadline is None:
                terminal_deadline = now + _REMOTE_STREAM_TERMINAL_GRACE_SECONDS
            elif now >= terminal_deadline:
                messages = {
                    "done": "运行已完成",
                    "error": "运行失败",
                    "cancelled": "运行已取消",
                }
                yield _sse(
                    Event(
                        attempt=attempt,
                        stage="ORCHESTRATOR",
                        type=expected_type,
                        message=messages[expected_type],
                        data={"status": status},
                    )
                )
                return
        else:
            terminal_deadline = None
        if now >= next_heartbeat:
            yield ": keep-alive\n\n"
            next_heartbeat = now + _SSE_HEARTBEAT_SECONDS
        await asyncio.sleep(_REMOTE_STREAM_POLL_SECONDS)
