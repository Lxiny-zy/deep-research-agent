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
from collections.abc import AsyncIterator, Callable
from typing import Literal

from pydantic import BaseModel

# Stage 不再是封闭枚举：新增角色（Critic / FactChecker / Coder 等）可直接发自己的事件，
# 无需改动本文件。下列常量是内置角色的约定值，仅供参考与类型提示，不构成校验白名单。
Stage = str
BUILTIN_STAGES = ("PLANNER", "RESEARCHER", "REFLECTOR", "SYNTHESIZER", "ORCHESTRATOR")
EventType = Literal["start", "info", "finding", "round", "token", "report", "done", "error"]


class Event(BaseModel):
    stage: Stage
    type: EventType
    message: str = ""
    elapsed: float = 0.0  # 距开始的秒数
    tokens: int = 0  # 事件发生时的累计 token（供前端实时显示；不落库，回放时回退 0）
    tokens_estimated: bool = False  # 累计值中是否仍含尚未被 provider usage 校准的估算
    data: dict | None = None  # 结构化附带数据（子问题列表、报告内容、token 增量、统计等）


class Tracer:
    """收集事件、统计 token，并分发给订阅者与实时 sink。"""

    def __init__(self) -> None:
        self._t0 = time.perf_counter()
        self.total_tokens = 0
        self.estimated_tokens = 0
        self.events: list[Event] = []
        self._subscribers: list[Callable[[Event], None]] = []
        # 实时 sink：接收含 token 在内的全部事件，供 run_stream / EventHub(SSE) 消费
        self._sinks: list[Callable[[Event], None]] = []

    @property
    def elapsed(self) -> float:
        return time.perf_counter() - self._t0

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
        # token 增量是瞬态的：只实时推给 sink，不记录、不落库、不触发同步订阅者（如 CLI 打印）
        if type == "token":
            for sink in self._sinks:
                sink(event)
            return event
        self.events.append(event)
        for cb in self._subscribers:
            try:
                cb(event)
            except Exception:
                pass  # 订阅者异常不应影响主流程
        for sink in self._sinks:
            sink(event)
        return event


class EventHub:
    """单次 run 的事件中枢：作为 Tracer 的 sink 收事件，向多个 SSE 订阅者扇出。

    - publish 作为 Tracer sink 被同步调用（含 token 事件）。
    - 非 token 事件进缓冲，供迟到的订阅者回放历史（体量与落库事件一致，有界）。
    - token 事件仅实时转发给在线订阅者；无人订阅时直接丢弃，避免在内存中无界堆积。
    - 每个 SSE 连接经 stream() 拿到独立队列，互不抢占，可多端同时观看同一进行中的 run。
    """

    def __init__(self) -> None:
        self._buffer: list[Event] = []
        self._subscribers: set[asyncio.Queue[Event | None]] = set()
        self._closed = False

    # 单订阅者队列上界：慢消费者（TCP 缓冲满、移动网络）不再无界堆积内存。
    # 满时优先丢 token 增量（前端可经 report 事件全量恢复正文）。
    _QUEUE_MAXSIZE = 1024

    def publish(self, event: Event) -> None:
        if event.type != "token":
            self._buffer.append(event)  # 仅缓冲非 token 事件供回放
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # 慢消费者：丢弃本条。非 token 事件该订阅者会缺失，但 run 详情
                # 接口仍可兜底；阻塞 publish 会拖垮整个 run，不可取
                pass

    def close(self) -> None:
        """run 结束：通知所有在线订阅者收尾（None 哨兵）。"""
        self._closed = True
        for q in list(self._subscribers):
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:
                q.get_nowait()  # 腾出一格，保证哨兵必达，订阅者不会永久挂起
                q.put_nowait(None)

    async def stream(self) -> AsyncIterator[Event]:
        """订阅：先回放已发生的非 token 事件，再续收实时事件，直到 run 结束。"""
        q: asyncio.Queue[Event | None] = asyncio.Queue(maxsize=self._QUEUE_MAXSIZE)
        # 回放历史（上述 for/add 均同步，与 publish 无交错）；极端长 run 截尾保留最新，
        # 并给 None 哨兵留一格，保证 put_nowait 不会因满而抛
        for buffered in self._buffer[-(self._QUEUE_MAXSIZE - 1) :]:
            q.put_nowait(buffered)
        if self._closed:
            q.put_nowait(None)
        self._subscribers.add(q)
        try:
            while True:
                ev = await q.get()
                if ev is None:
                    break
                yield ev
        finally:
            self._subscribers.discard(q)
