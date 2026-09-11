"""轻量可观测性：统一事件模型 + Tracer。

Tracer 把系统内部发生的一切（每个 Agent 的开始/进展/产出）建模成 Event，
并支持两种消费方式：
  1) 同步订阅者（CLI 打印日志、持久化落库回调）
  2) 异步队列（FastAPI 用 SSE 把事件实时推到浏览器）

「能把 Agent 内部过程讲清楚」是大多数候选人简历里缺失的工程维度。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable, Iterable
from typing import Literal

from pydantic import BaseModel

from .token_budget import TokenBudget

# Stage 不再是封闭枚举：新增角色（Critic / FactChecker / Coder 等）可直接发自己的事件，
# 无需改动本文件。下列常量是内置角色的约定值，仅供参考与类型提示，不构成校验白名单。
Stage = str
BUILTIN_STAGES = ("PLANNER", "RESEARCHER", "REFLECTOR", "SYNTHESIZER", "ORCHESTRATOR")
EventType = Literal[
    "start",
    "info",
    "finding",
    "round",
    "token",
    "report",
    "done",
    "error",
    "cancelled",
]


class EventStreamGap(RuntimeError):
    """The in-memory event window no longer covers a subscriber's cursor.

    Callers must resume from the durable event store.  This is intentionally a
    distinct exception instead of silently dropping events: a reconnecting SSE
    client can then continue with its ``Last-Event-ID`` cursor.
    """


class _SubscriberOverflow:
    """Private queue marker used to wake a slow subscriber without blocking publish."""


class Event(BaseModel):
    version: int = 1
    # Tracers may create events without knowing storage state.  EventHub assigns
    # a provisional durable-compatible id for live delivery; the repository
    # reconciles it when the event is appended.
    seq: int | None = None
    attempt: int = 1
    stage: Stage
    type: EventType
    message: str = ""
    elapsed: float = 0.0  # 距开始的秒数
    tokens: int = 0  # 累计 token 随事件持久化，跨进程回放保持一致。
    tokens_estimated: bool = False  # 累计值中是否仍含尚未被 provider usage 校准的估算
    data: dict | None = None  # 结构化附带数据（子问题列表、报告内容、token 增量、统计等）


class Tracer:
    """收集事件、统计 token，并分发给订阅者与实时 sink。"""

    def __init__(self) -> None:
        self._t0 = time.perf_counter()
        self._elapsed_offset = 0.0
        self.total_tokens = 0
        self.estimated_tokens = 0
        self.budget: TokenBudget | None = None
        self.events: list[Event] = []
        self._subscribers: list[Callable[[Event], None]] = []
        # 实时 sink：接收含 token 在内的全部事件，供 run_stream / EventHub(SSE) 消费
        self._sinks: list[Callable[[Event], None]] = []
        self._token_pending: Event | None = None
        self._token_parts: list[str] = []
        self._token_size = 0
        self._token_timer: asyncio.TimerHandle | None = None

    @property
    def elapsed(self) -> float:
        return self._elapsed_offset + time.perf_counter() - self._t0

    def restore_metrics(
        self, *, total_tokens: int = 0, estimated_tokens: int = 0, elapsed: float = 0.0
    ) -> None:
        """Restore cumulative counters from a durable workflow checkpoint."""
        self.total_tokens = max(0, total_tokens)
        self.estimated_tokens = min(self.total_tokens, max(0, estimated_tokens))
        self._elapsed_offset = max(0.0, elapsed)
        self._t0 = time.perf_counter()

    def subscribe(self, callback: Callable[[Event], None]) -> None:
        """注册同步订阅者（如 CLI 打印函数、持久化落库回调）。只收非 token 事件。"""
        self._subscribers.append(callback)

    def add_sink(self, sink: Callable[[Event], None]) -> None:
        """注册实时 sink（接收含 token 在内的全部事件，供流式推送）。"""
        self._sinks.append(sink)

    def remove_sink(self, sink: Callable[[Event], None]) -> None:
        try:
            self._sinks.remove(sink)
        except ValueError:
            pass

    @property
    def tokens_estimated(self) -> bool:
        return self.estimated_tokens > 0

    def add_tokens(self, n: int, *, estimated: bool = False) -> None:
        amount = max(0, n)
        self.total_tokens += amount
        if estimated:
            self.estimated_tokens += amount

    def reconcile_tokens(self, estimated: int, exact: int) -> None:
        """把一次流式调用的临时估算替换为 provider 最终返回的精确 usage。"""
        estimated_amount = max(0, estimated)
        exact_amount = max(0, exact)
        self.total_tokens = max(0, self.total_tokens - estimated_amount + exact_amount)
        self.estimated_tokens = max(0, self.estimated_tokens - estimated_amount)

    def emit(
        self, stage: Stage, type: EventType, message: str = "", data: dict | None = None
    ) -> Event:
        event = Event(
            stage=stage,
            type=type,
            message=message,
            elapsed=round(self.elapsed, 2),
            tokens=self.total_tokens,
            tokens_estimated=self.tokens_estimated,
            data=data,
        )
        if type == "token":
            if self._token_pending is not None and self._token_pending.stage != stage:
                self.flush_tokens()
            self._token_pending = event
            delta = str((data or {}).get("delta", ""))
            self._token_parts.append(delta)
            self._token_size += len(delta)
            if self._token_size >= 4096:
                self.flush_tokens()
            elif self._token_timer is None:
                try:
                    self._token_timer = asyncio.get_running_loop().call_later(
                        0.1, self.flush_tokens
                    )
                except RuntimeError:
                    self.flush_tokens()
            return event
        self.flush_tokens()
        self.events.append(event)
        for cb in self._subscribers:
            try:
                cb(event)
            except Exception:
                pass  # 订阅者异常不应影响主流程
        for sink in self._sinks:
            sink(event)
        return event

    def flush_tokens(self) -> None:
        if self._token_timer is not None:
            self._token_timer.cancel()
            self._token_timer = None
        event = self._token_pending
        if event is None:
            return
        event.data = {**(event.data or {}), "delta": "".join(self._token_parts)}
        self._token_pending, self._token_parts, self._token_size = None, [], 0
        self.events.append(event)
        for sink in self._sinks:
            sink(event)


class EventHub:
    """单次 run 的事件中枢：作为 Tracer 的 sink 收事件，向多个 SSE 订阅者扇出。

    - publish 作为 Tracer sink 被同步调用（含 token 事件）。
    - 阶段事件与合并正文增量都进入有界缓冲，迟到订阅者可恢复正文。
    - 超出内存窗口时显式报告缺口，由调用方从数据库接续。
    - 每个 SSE 连接经 stream() 拿到独立队列，互不抢占，可多端同时观看同一进行中的 run。
    """

    def __init__(self) -> None:
        self._buffer: list[Event] = []
        self._subscribers: set[asyncio.Queue[Event | None | _SubscriberOverflow]] = set()
        self._closed = False
        self._next_seq = 0

    # 单订阅者队列上界：慢消费者（TCP 缓冲满、移动网络）不再无界堆积内存。
    # 满时显式通知缺口，由 SSE 层从持久化事件恢复。
    _QUEUE_MAXSIZE = 1024
    # 进程内历史也必须有界。超出这段窗口的 SSE 断点由 API 从持久化事件表补齐。
    _BUFFER_MAXSIZE = 4096

    def prime_sequence(self, events: Iterable[Event]) -> None:
        """Advance live ids past events already persisted for this run.

        Resume attempts retain the previous attempt's append-only history.  A
        resumed hub must see the old terminal event's sequence even when that
        event is intentionally not replayed to subscribers.
        """
        for event in events:
            if event.seq is not None:
                self._next_seq = max(self._next_seq, event.seq + 1)

    def publish(self, event: Event) -> None:
        if event.seq is None:
            event.seq = self._next_seq
        self._next_seq = max(self._next_seq, event.seq + 1)
        self._buffer.append(event)
        if len(self._buffer) > self._BUFFER_MAXSIZE:
            del self._buffer[: len(self._buffer) - self._BUFFER_MAXSIZE]
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Never block the producer on a network client.  Remove this
                # subscriber and wake it with an explicit gap signal; the API
                # then switches to the durable event log using its last cursor.
                self._subscribers.discard(q)
                while True:
                    try:
                        q.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                q.put_nowait(_SubscriberOverflow())

    def close(self) -> None:
        """run 结束：通知所有在线订阅者收尾（None 哨兵）。"""
        self._closed = True
        for q in list(self._subscribers):
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:
                q.get_nowait()  # 腾出一格，保证哨兵必达，订阅者不会永久挂起
                q.put_nowait(None)

    async def stream(self, *, after_seq: int = 0) -> AsyncIterator[Event]:
        """订阅：先回放已有事件，再续收实时事件，直到 run 结束。"""
        q: asyncio.Queue[Event | None | _SubscriberOverflow] = asyncio.Queue(
            maxsize=self._QUEUE_MAXSIZE
        )
        # 回放历史（上述 for/add 均同步，与 publish 无交错）。如果游标已经
        # 落在内存窗口之前，显式报告缺口，让调用方从持久化日志补齐；不能
        # 静默截尾，否则 SSE 恢复会产生不可检测的数据丢失。
        first_seq = self._buffer[0].seq if self._buffer else None
        if first_seq is not None and after_seq < first_seq:
            raise EventStreamGap(
                f"event cursor {after_seq} is older than in-memory window starting at {first_seq}"
            )
        replay = [
            buffered
            for buffered in self._buffer
            if buffered.seq is None or buffered.seq >= after_seq
        ]
        if len(replay) > self._QUEUE_MAXSIZE - 1:
            raise EventStreamGap("subscriber replay exceeds the in-memory queue window")
        for buffered in replay:
            q.put_nowait(buffered)
        if self._closed:
            q.put_nowait(None)
        self._subscribers.add(q)
        try:
            while True:
                ev = await q.get()
                if ev is None:
                    break
                if isinstance(ev, _SubscriberOverflow):
                    raise EventStreamGap("subscriber queue overflowed")
                yield ev
        finally:
            self._subscribers.discard(q)
