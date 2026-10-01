"""Live fan-out over durable Q&A requests. Only a valid lease may save an answer."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from fastapi import HTTPException

from .qa_requests import ACTIVE, INTERRUPTED
from .qa_store import message_payload

LEASE_SECONDS = 90.0
HEARTBEAT_SECONDS = 20.0
POLL_SECONDS = 0.25
FLUSH_SECONDS = 0.2
MAX_QUEUED_EVENTS = 128


async def resume_pending(app: Any) -> None:
    """Resume only never-started work, after rechecking its current identity."""
    from starlette.requests import Request

    from ..access import Principal
    from .qa_api import AskRequest, _prepare_turn, _requests

    request = Request({"type": "http", "app": app})
    store = _requests(request)
    settings = app.state.settings
    actors = {
        credential.principal.id: credential.principal for credential in settings.api_credentials
    }
    if settings.api_key:
        actors["admin"] = Principal("admin", "admin")
    elif not settings.api_credentials:
        actors["local"] = Principal("local", "admin")
    for cid, message in await store.pending():
        actor = actors.get(message.request_payload.get("_actor"))
        if actor is None or not actor.can_research:
            await store.reject_pending(
                cid, message.request_id, "原请求身份已不可用，未发起模型调用"
            )
            continue
        authenticated = Request({"type": "http", "app": app, "state": {"principal": actor}})
        try:
            await _prepare_turn(
                cid,
                AskRequest(**message.request_payload, request_id=message.request_id),
                authenticated,
            )
        except HTTPException as exc:
            await store.reject_pending(cid, message.request_id, str(exc.detail))


async def pending_loop(app: Any) -> None:
    while True:
        try:
            await resume_pending(app)
        except Exception as exc:
            logging.getLogger(__name__).warning("Q&A pending recovery: %s", type(exc).__name__)
        await asyncio.sleep(5)


class LiveTurn:
    def __init__(self) -> None:
        self.listeners: set[asyncio.Queue] = set()
        self.draft = ""
        self.reasoning: dict[str, dict[str, Any]] = {}
        self.status = "正在读取本轮任务状态…"
        self.task: asyncio.Task | None = None

    def emit(self, kind: str, payload: dict[str, Any]) -> None:
        if kind == "delta":
            self.draft += payload.get("delta", "")
        elif kind == "reset":
            self.draft = ""
        elif kind == "reasoning":
            key = str(payload.get("call_id", ""))
            old = self.reasoning.get(key, {}).get("reasoning_delta", "")
            self.reasoning[key] = {
                **payload,
                "reasoning_delta": old + payload.get("reasoning_delta", ""),
            }
        elif kind == "status":
            self.status = str(payload.get("message", ""))
        for queue in self.listeners:
            if queue.qsize() >= MAX_QUEUED_EVENTS:
                # A slow browser gets the current state, not an unbounded queue
                # of obsolete token fragments. Revision resets remain intact.
                while not queue.empty():
                    queue.get_nowait()
                self._replay(queue, force=True)
            else:
                queue.put_nowait((kind, payload))

    def _replay(self, queue: asyncio.Queue, *, force: bool = False) -> None:
        if force or self.draft or self.reasoning:
            queue.put_nowait(
                ("reset", {"type": "reset", "replay": True, "message": "恢复已有生成内容"})
            )
            for data in self.reasoning.values():
                queue.put_nowait(("reasoning", data))
            if self.draft:
                queue.put_nowait(("delta", {"delta": self.draft}))
        queue.put_nowait(("status", {"type": "status", "message": self.status}))

    def attach(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._replay(queue)
        self.listeners.add(queue)
        if self.task is not None and self.task.done():
            queue.put_nowait(None)
        return queue


def start_turn(
    app: Any, requests: Any, cid: str, rid: str, execute: Callable, max_seconds: float
) -> LiveTurn:
    active = getattr(app.state, "qa_live_turns", None)
    if active is None:
        active = {}
        app.state.qa_live_turns = active
    key = (cid, rid)
    if key in active:
        return active[key]
    runtime = LiveTurn()
    active[key] = runtime
    tasks = getattr(app.state, "qa_tasks", None)
    if tasks is None:
        tasks = set()
        app.state.qa_tasks = tasks

    async def drive() -> dict[str, Any]:
        owner = str(uuid4())
        cursor = 0
        while True:
            row = await requests.get(cid, rid)
            if row is None:
                raise HTTPException(404, "本轮问题已删除")
            # Another API process may own the lease. Read only its committed
            # events, including any trace retained by an interrupted request.
            records = await requests.events(cid, rid, cursor)
            for sequence, kind, payload in records:
                runtime.emit(kind, payload)
                cursor = sequence
            if len(records) == 128:
                continue
            if row.status not in ACTIVE:
                return message_payload(row)
            if row.status == "pending" and await requests.claim(cid, rid, owner, LEASE_SECONDS):
                break
            label = (
                "正在等待前一个问题完成…"
                if row.status == "pending"
                else "本轮正在生成，已连接到原任务…"
            )
            if not cursor and runtime.status != label:
                runtime.emit("status", {"type": "status", "message": label})
            await asyncio.sleep(POLL_SECONDS)

        pending: list[tuple[str, dict[str, Any]]] = []

        def emit(kind: str, payload: dict[str, Any]) -> None:
            runtime.emit(kind, payload)
            field = (
                "delta" if kind == "delta" else "reasoning_delta" if kind == "reasoning" else None
            )
            if (
                field
                and pending
                and pending[-1][0] == kind
                and pending[-1][1].get("call_id") == payload.get("call_id")
            ):
                pending[-1][1][field] += payload.get(field, "")
            else:
                pending.append((kind, dict(payload)))

        work = asyncio.create_task(
            execute(
                on_delta=lambda text: emit("delta", {"delta": text}),
                on_event=lambda event: emit(event["type"], event),
            )
        )
        stop_writing = asyncio.Event()

        async def persist_events() -> None:
            nonlocal pending
            try:
                while True:
                    try:
                        await asyncio.wait_for(stop_writing.wait(), FLUSH_SECONDS)
                    except TimeoutError:
                        pass
                    if pending:
                        batch, pending = pending, []
                        if not await requests.append_events(cid, rid, owner, batch):
                            raise HTTPException(409, "执行状态已改变，实时内容未覆盖原任务")
                    if stop_writing.is_set():
                        return
            except Exception:
                work.cancel()
                raise

        writer = asyncio.create_task(persist_events())

        async def heartbeat() -> None:
            try:
                while True:
                    await asyncio.sleep(HEARTBEAT_SECONDS)
                    if not await requests.update(cid, rid, owner, seconds=LEASE_SECONDS):
                        work.cancel()
                        return
            except Exception:
                work.cancel()

        beat = asyncio.create_task(heartbeat())
        try:
            try:
                result = await asyncio.wait_for(work, timeout=max_seconds)
            finally:
                # Finish the in-flight transaction before finalization. Cancelling
                # it here could lose a batch or append it again after a commit.
                stop_writing.set()
                await writer
            if not await requests.update(cid, rid, owner, result=result):
                raise HTTPException(409, "执行状态已改变，未覆盖已有结果")
        except asyncio.CancelledError:
            await requests.update(cid, rid, owner, error=INTERRUPTED)
            raise
        except Exception as exc:
            if isinstance(exc, HTTPException):
                error = (
                    exc.detail.get("message", "本轮处理失败")
                    if isinstance(exc.detail, dict)
                    else str(exc.detail)
                )
            elif isinstance(exc, TimeoutError):
                error = "本轮处理超时，未自动重发。可以检查已有状态后重新提问。"
            else:
                error = f"本轮处理失败（{type(exc).__name__}），请检查配置后重试。"
            await requests.update(cid, rid, owner, error=error)
        finally:
            beat.cancel()
            await asyncio.gather(beat, return_exceptions=True)
        row = await requests.get(cid, rid)
        if row is None:
            raise HTTPException(404, "本轮问题已删除")
        return message_payload(row)

    runtime.task = asyncio.create_task(drive())
    tasks.add(runtime.task)

    def done(task: asyncio.Task) -> None:
        tasks.discard(task)
        if active.get(key) is runtime:
            active.pop(key, None)
        for queue in runtime.listeners:
            queue.put_nowait(None)
        if not task.cancelled():
            task.exception()

    runtime.task.add_done_callback(done)
    return runtime
