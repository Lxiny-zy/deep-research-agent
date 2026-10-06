"""Reserve turns before model work; serialize conversations and fence late writers.

Expired running turns are interrupted, never automatically re-executed: an
upstream call may already have been billed even when its response was lost.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import aliased

from ..persistence import orm
from ..persistence.coordination import transaction_lock
from .qa_admission import QaLimits
from .qa_store import (
    MAX_MESSAGES_PER_CONVERSATION,
    ConversationFullError,
    InMemoryQaStore,
    QaMessage,
    _message,
)

ACTIVE = {"pending", "running"}
INTERRUPTED = "执行连接已中断，未自动重发模型请求。请检查已有结果后再决定是否重新提问。"
CANCELLED = "本轮已停止，历史对话与已保存的阶段结果仍保留。"


class RequestConflict(ValueError):
    pass


def _expired(row: Any, now: datetime) -> bool:
    until = row.lease_until
    if until is not None:
        until = until.astimezone(UTC) if until.tzinfo else until.replace(tzinfo=UTC)
    return row.status == "running" and (until is None or until <= now)


def _expire(row: Any) -> None:
    row.status, row.error = "error", INTERRUPTED
    row.execution_owner, row.lease_until = None, None


def _complete(row: Any, result: dict[str, Any]) -> None:
    for name in ("answer", "citations", "evidence", "thoughts"):
        setattr(row, name, result.get(name, "" if name == "answer" else []))
    row.status = "fallback" if result.get("status") == "fallback" else "done"
    row.tokens = int(result.get("tokens", 0))
    row.execution_owner, row.lease_until, row.error = None, None, None


class SqlQaRequests:
    def __init__(self, sessions: Any, limits: QaLimits | None = None) -> None:
        self.sessions = sessions
        self.limits = limits or QaLimits()

    async def _counts(self, session: Any, cid: str, status: str, now: datetime) -> tuple[int, int]:
        rows = (
            select(orm.QaConversationRow.owner_id, func.count())
            .join(
                orm.QaMessageRow,
                orm.QaMessageRow.conversation_id == orm.QaConversationRow.id,
            )
            .where(orm.QaMessageRow.status == status)
        )
        if status == "running":
            rows = rows.where(orm.QaMessageRow.lease_until > now)
        counts = (await session.execute(rows.group_by(orm.QaConversationRow.owner_id))).all()
        owner = await session.scalar(
            select(orm.QaConversationRow.owner_id).where(orm.QaConversationRow.id == cid)
        )
        return sum(count for _, count in counts), next(
            (count for key, count in counts if key == owner), 0
        )

    async def events(self, cid: str, rid: str, after: int) -> list[tuple[int, str, dict]]:
        async with self.sessions() as session:
            records = (
                await session.scalars(
                    select(orm.QaStreamEventRow)
                    .join(orm.QaMessageRow)
                    .where(
                        orm.QaMessageRow.conversation_id == cid,
                        orm.QaMessageRow.request_id == rid,
                        orm.QaStreamEventRow.sequence > after,
                    )
                    .order_by(orm.QaStreamEventRow.sequence)
                    .limit(128)
                )
            ).all()
            return [(record.sequence, record.kind, record.payload) for record in records]

    async def append_events(
        self, cid: str, rid: str, owner: str, events: list[tuple[str, dict]]
    ) -> bool:
        async with self.sessions() as session, session.begin():
            await transaction_lock(session, f"qa:{cid}")
            row = await session.scalar(
                select(orm.QaMessageRow).where(
                    orm.QaMessageRow.conversation_id == cid,
                    orm.QaMessageRow.request_id == rid,
                )
            )
            if (
                row is None
                or row.status != "running"
                or row.execution_owner != owner
                or _expired(row, datetime.now(UTC))
            ):
                return False
            last = (
                await session.scalar(
                    select(func.max(orm.QaStreamEventRow.sequence)).where(
                        orm.QaStreamEventRow.message_id == row.id
                    )
                )
                or 0
            )
            session.add_all(
                [
                    orm.QaStreamEventRow(
                        message_id=row.id, sequence=last + i, kind=kind, payload=payload
                    )
                    for i, (kind, payload) in enumerate(events, 1)
                ]
            )
            return True

    async def pending(self, *, now: datetime | None = None) -> list[tuple[str, QaMessage]]:
        now = now or datetime.now(UTC)
        earlier = aliased(orm.QaMessageRow)
        blocked = (
            select(earlier.id)
            .where(
                earlier.conversation_id == orm.QaMessageRow.conversation_id,
                earlier.position < orm.QaMessageRow.position,
                or_(
                    earlier.status == "pending",
                    and_(earlier.status == "running", earlier.lease_until > now),
                ),
            )
            .exists()
        )
        async with self.sessions() as session:
            live = aliased(orm.QaMessageRow)
            live_conv = aliased(orm.QaConversationRow)
            owner_running = (
                select(func.count())
                .select_from(live)
                .join(
                    live_conv,
                    live.conversation_id == live_conv.id,
                )
                .where(
                    live.status == "running",
                    live.lease_until > now,
                    live_conv.owner_id == orm.QaConversationRow.owner_id,
                )
                .correlate(orm.QaConversationRow)
                .scalar_subquery()
            )
            rows = (
                await session.scalars(
                    select(orm.QaMessageRow)
                    .join(orm.QaConversationRow)
                    .where(
                        orm.QaMessageRow.status == "pending",
                        orm.QaMessageRow.request_id.is_not(None),
                        ~blocked,
                        owner_running < self.limits.active_per_owner,
                    )
                    .order_by(orm.QaMessageRow.created_at, orm.QaMessageRow.id)
                    .limit(200)
                )
            ).all()
            return [(row.conversation_id, _message(row)) for row in rows]

    async def reject_pending(self, cid: str, rid: str, error: str) -> None:
        async with self.sessions() as session, session.begin():
            await transaction_lock(session, f"qa:{cid}")
            row = await session.scalar(
                select(orm.QaMessageRow).where(
                    orm.QaMessageRow.conversation_id == cid,
                    orm.QaMessageRow.request_id == rid,
                )
            )
            if row is not None and row.status == "pending":
                _expire(row)
                row.error = error

    async def cancel(self, cid: str, rid: str) -> QaMessage | None:
        async with self.sessions() as session, session.begin():
            await transaction_lock(session, f"qa:{cid}")
            row = await session.scalar(
                select(orm.QaMessageRow).where(
                    orm.QaMessageRow.conversation_id == cid,
                    orm.QaMessageRow.request_id == rid,
                )
            )
            if row is not None and row.status in ACTIVE:
                _expire(row)
                row.status, row.error = "cancelled", CANCELLED
            return _message(row) if row is not None else None

    async def checkpoint(self, cid: str, rid: str, owner: str, state: dict) -> bool:
        async with self.sessions() as session, session.begin():
            await transaction_lock(session, f"qa:{cid}")
            row = await session.scalar(
                select(orm.QaMessageRow).where(
                    orm.QaMessageRow.conversation_id == cid,
                    orm.QaMessageRow.request_id == rid,
                )
            )
            if (
                row is None
                or row.status != "running"
                or row.execution_owner != owner
                or _expired(row, datetime.now(UTC))
            ):
                return False
            row.request_payload = {**row.request_payload, "_checkpoint": deepcopy(state)}
            return True

    async def reserve(self, cid: str, rid: str, digest: str, payload: dict) -> QaMessage:
        async with self.sessions() as session, session.begin():
            await transaction_lock(session, "qa:admission")
            await transaction_lock(session, f"qa:{cid}")
            conversation = await session.get(orm.QaConversationRow, cid)
            if conversation is None:
                raise KeyError(cid)
            row = await session.scalar(
                select(orm.QaMessageRow).where(
                    orm.QaMessageRow.conversation_id == cid,
                    orm.QaMessageRow.request_id == rid,
                )
            )
            if row is not None:
                if row.request_hash != digest:
                    raise RequestConflict("同一请求 ID 不能用于不同问题或来源范围")
                return _message(row)
            self.limits.check_pending(
                *await self._counts(session, cid, "pending", datetime.now(UTC))
            )
            highest = await session.scalar(
                select(func.max(orm.QaMessageRow.position)).where(
                    orm.QaMessageRow.conversation_id == cid
                )
            )
            position = 0 if highest is None else highest + 1
            if position >= MAX_MESSAGES_PER_CONVERSATION:
                raise ConversationFullError("会话消息数已达上限，请新建会话")
            row = orm.QaMessageRow(
                conversation_id=cid,
                position=position,
                query=payload["query"],
                answer="",
                status="pending",
                request_id=rid,
                request_hash=digest,
                request_payload=payload,
            )
            session.add(row)
            conversation.updated_at = datetime.now(UTC)
            await session.flush()
            return _message(row)

    async def get(self, cid: str, rid: str, *, now: datetime | None = None) -> QaMessage | None:
        # Streaming observers read frequently. Only take the shared write lock
        # when a lease appears expired, then recheck it after acquiring the lock.
        async with self.sessions() as session:
            row = await session.scalar(
                select(orm.QaMessageRow).where(
                    orm.QaMessageRow.conversation_id == cid,
                    orm.QaMessageRow.request_id == rid,
                )
            )
            if row is None or not _expired(row, now or datetime.now(UTC)):
                return _message(row) if row else None
        async with self.sessions() as session, session.begin():
            await transaction_lock(session, f"qa:{cid}")
            row = await session.scalar(
                select(orm.QaMessageRow).where(
                    orm.QaMessageRow.conversation_id == cid,
                    orm.QaMessageRow.request_id == rid,
                )
            )
            if row is not None and _expired(row, now or datetime.now(UTC)):
                _expire(row)
            return _message(row) if row else None

    async def claim(
        self, cid: str, rid: str, owner: str, seconds: float, *, now: datetime | None = None
    ) -> bool:
        async with self.sessions() as session, session.begin():
            await transaction_lock(session, "qa:admission")
            await transaction_lock(session, f"qa:{cid}")
            while True:
                row = await session.scalar(
                    select(orm.QaMessageRow)
                    .where(
                        orm.QaMessageRow.conversation_id == cid,
                        orm.QaMessageRow.status.in_(ACTIVE),
                    )
                    .order_by(orm.QaMessageRow.position)
                    .limit(1)
                )
                if row is None:
                    return False
                clock = now or datetime.now(UTC)
                if _expired(row, clock):
                    _expire(row)
                    await session.flush()
                    continue
                if row.request_id != rid or row.status != "pending":
                    return False
                if not self.limits.can_claim(*await self._counts(session, cid, "running", clock)):
                    return False
                row.status, row.execution_owner = "running", owner
                row.lease_until = (now or datetime.now(UTC)) + timedelta(seconds=seconds)
                return True

    async def update(
        self,
        cid: str,
        rid: str,
        owner: str,
        *,
        seconds: float | None = None,
        result: dict | None = None,
        error: str | None = None,
        now: datetime | None = None,
    ) -> bool:
        async with self.sessions() as session, session.begin():
            await transaction_lock(session, f"qa:{cid}")
            row = await session.scalar(
                select(orm.QaMessageRow).where(
                    orm.QaMessageRow.conversation_id == cid,
                    orm.QaMessageRow.request_id == rid,
                )
            )
            now = now or datetime.now(UTC)
            if (
                row is None
                or row.status != "running"
                or row.execution_owner != owner
                or _expired(row, now)
            ):
                return False
            if seconds is not None:
                row.lease_until = now + timedelta(seconds=seconds)
            elif error is not None:
                _expire(row)
                row.error = error
            elif result is not None:
                _complete(row, result)
                # The final message owns the verified body and full reasoning.
                # Drop transient copies atomically; errors retain their trace.
                await session.execute(
                    delete(orm.QaStreamEventRow).where(orm.QaStreamEventRow.message_id == row.id)
                )
                conversation = await session.get(orm.QaConversationRow, cid)
                if conversation:
                    conversation.updated_at = now
            return True


class MemoryQaRequests:
    def __init__(self, store: InMemoryQaStore, limits: QaLimits | None = None) -> None:
        self.store = store
        self.limits = limits or QaLimits()
        # Multiple request managers over one store must share admission/fencing.
        self.lock = store._request_lock

    def _counts(self, cid: str, status: str, now: datetime) -> tuple[int, int]:
        conv = self.store._items.get(cid)
        if conv is None:
            raise KeyError(cid)
        total = owned = 0
        for other in self.store._items.values():
            count = sum(row.status == status and not _expired(row, now) for row in other.messages)
            total += count
            if other.owner_id == conv.owner_id:
                owned += count
        return total, owned

    async def events(self, cid: str, rid: str, after: int) -> list[tuple[int, str, dict]]:
        row = next((m for m in self._rows(cid) if m.request_id == rid), None)
        if row is None:
            return []
        return deepcopy(self.store._stream_events.get(row.id, [])[after : after + 128])

    async def append_events(
        self, cid: str, rid: str, owner: str, events: list[tuple[str, dict]]
    ) -> bool:
        async with self.lock:
            row = next((m for m in self._rows(cid) if m.request_id == rid), None)
            if (
                row is None
                or row.status != "running"
                or row.execution_owner != owner
                or _expired(row, datetime.now(UTC))
            ):
                return False
            records = self.store._stream_events.setdefault(row.id, [])
            last = len(records)
            records.extend(
                (last + i, kind, deepcopy(data)) for i, (kind, data) in enumerate(events, 1)
            )
            return True

    async def pending(self, *, now: datetime | None = None) -> list[tuple[str, QaMessage]]:
        now = now or datetime.now(UTC)
        heads = []
        async with self.lock:
            for cid, conv in self.store._items.items():
                if self._counts(cid, "running", now)[1] >= self.limits.active_per_owner:
                    continue
                for row in conv.messages:
                    if row.status not in ACTIVE or _expired(row, now):
                        continue
                    if row.status == "pending" and row.request_id:
                        heads.append((cid, deepcopy(row)))
                    break
        return sorted(heads, key=lambda item: (item[1].created_at or now, item[1].id))[:200]

    async def reject_pending(self, cid: str, rid: str, error: str) -> None:
        async with self.lock:
            row = next((m for m in self._rows(cid) if m.request_id == rid), None)
            if row is not None and row.status == "pending":
                _expire(row)
                row.error = error

    async def cancel(self, cid: str, rid: str) -> QaMessage | None:
        async with self.lock:
            row = next((m for m in self._rows(cid) if m.request_id == rid), None)
            if row is not None and row.status in ACTIVE:
                _expire(row)
                row.status, row.error = "cancelled", CANCELLED
            return deepcopy(row)

    async def checkpoint(self, cid: str, rid: str, owner: str, state: dict) -> bool:
        async with self.lock:
            row = next((m for m in self._rows(cid) if m.request_id == rid), None)
            if (
                row is None
                or row.status != "running"
                or row.execution_owner != owner
                or _expired(row, datetime.now(UTC))
            ):
                return False
            row.request_payload = {**row.request_payload, "_checkpoint": deepcopy(state)}
            return True

    def _rows(self, cid: str) -> list[QaMessage]:
        conversation = self.store._items.get(cid)
        return conversation.messages if conversation is not None else []

    async def reserve(self, cid: str, rid: str, digest: str, payload: dict) -> QaMessage:
        async with self.lock:
            row = next((m for m in self._rows(cid) if m.request_id == rid), None)
            if row:
                if row.request_hash != digest:
                    raise RequestConflict("同一请求 ID 不能用于不同问题或来源范围")
                return deepcopy(row)
            self.limits.check_pending(*self._counts(cid, "pending", datetime.now(UTC)))
            return await self.store.append(
                cid,
                QaMessage(
                    id="",
                    position=0,
                    query=payload["query"],
                    answer="",
                    status="pending",
                    request_id=rid,
                    request_hash=digest,
                    request_payload=deepcopy(payload),
                ),
            )

    async def get(self, cid: str, rid: str, *, now: datetime | None = None) -> QaMessage | None:
        async with self.lock:
            row = next((m for m in self._rows(cid) if m.request_id == rid), None)
            if row and _expired(row, now or datetime.now(UTC)):
                _expire(row)
            return deepcopy(row)

    async def claim(
        self, cid: str, rid: str, owner: str, seconds: float, *, now: datetime | None = None
    ) -> bool:
        async with self.lock:
            now = now or datetime.now(UTC)
            for row in self._rows(cid):
                if _expired(row, now):
                    _expire(row)
                if row.status not in ACTIVE:
                    continue
                if row.request_id != rid or row.status != "pending":
                    return False
                if not self.limits.can_claim(*self._counts(cid, "running", now)):
                    return False
                row.status, row.execution_owner = "running", owner
                row.lease_until = now + timedelta(seconds=seconds)
                return True
            return False

    async def update(
        self,
        cid: str,
        rid: str,
        owner: str,
        *,
        seconds: float | None = None,
        result: dict | None = None,
        error: str | None = None,
        now: datetime | None = None,
    ) -> bool:
        async with self.lock:
            now = now or datetime.now(UTC)
            row = next((m for m in self._rows(cid) if m.request_id == rid), None)
            if (
                row is None
                or row.status != "running"
                or row.execution_owner != owner
                or _expired(row, now)
            ):
                return False
            if seconds is not None:
                row.lease_until = now + timedelta(seconds=seconds)
            elif error is not None:
                _expire(row)
                row.error = error
            elif result is not None:
                _complete(row, result)
                self.store._stream_events.pop(row.id, None)
            return True
