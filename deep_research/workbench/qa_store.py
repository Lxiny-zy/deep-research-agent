"""学术问答会话存储：SQL 实现与内存实现（测试 / 无数据库的直接调用）。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..persistence import orm
from ..persistence.coordination import transaction_lock

MAX_MESSAGES_PER_CONVERSATION = 200


@dataclass
class QaMessage:
    id: str
    position: int
    query: str
    answer: str
    citations: list[str] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    thoughts: list[dict[str, Any]] = field(default_factory=list)
    status: str = "done"
    created_at: datetime | None = None
    request_id: str | None = None
    request_hash: str | None = None
    request_payload: dict[str, Any] = field(default_factory=dict)
    execution_owner: str | None = None
    lease_until: datetime | None = None
    error: str | None = None
    tokens: int | None = None


@dataclass
class QaConversation:
    id: str
    owner_id: str
    title: str
    created_at: datetime | None = None
    updated_at: datetime | None = None
    messages: list[QaMessage] = field(default_factory=list)
    message_count: int = 0
    # 论文精读工作区的对话绑定所属任务；学术问答页的独立会话为 None
    run_id: str | None = None


class ConversationFullError(ValueError):
    pass


def message_payload(message: QaMessage, *, include_private: bool = False) -> dict[str, Any]:
    from .qa_revision_state import PRIVATE_REVISION_TOOL, revision_availability

    public = (
        message
        if include_private
        else replace(
            message,
            thoughts=[
                thought
                for thought in message.thoughts
                if thought.get("tool") != PRIVATE_REVISION_TOOL
            ],
        )
    )
    data = asdict(public)
    data["revision"] = revision_availability(message.status, message.thoughts, message.evidence)
    for name in ("request_hash", "execution_owner", "lease_until"):
        data.pop(name, None)
    if message.created_at is not None:
        data["created_at"] = message.created_at.isoformat()
    data["request_payload"] = {
        k: v for k, v in message.request_payload.items() if not k.startswith("_")
    }
    return data


class QaStore(Protocol):
    async def create(
        self, owner_id: str, title: str, run_id: str | None = None
    ) -> QaConversation: ...

    async def list(
        self, owner_id: str | None, run_id: str | None = None
    ) -> list[QaConversation]: ...

    async def get(self, conversation_id: str) -> QaConversation | None: ...

    async def append(self, conversation_id: str, message: QaMessage) -> QaMessage: ...

    async def delete(self, conversation_id: str) -> bool: ...


def _message(row: orm.QaMessageRow) -> QaMessage:
    return QaMessage(
        id=row.id,
        position=row.position,
        query=row.query,
        answer=row.answer,
        citations=list(row.citations or []),
        evidence=list(row.evidence or []),
        thoughts=list(row.thoughts or []),
        status=row.status,
        created_at=row.created_at,
        request_id=row.request_id,
        request_hash=row.request_hash,
        request_payload=dict(row.request_payload or {}),
        execution_owner=row.execution_owner,
        lease_until=row.lease_until,
        error=row.error,
        tokens=row.tokens,
    )


class SqlQaStore:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sessionmaker

    async def create(self, owner_id: str, title: str, run_id: str | None = None) -> QaConversation:
        async with self._sm() as s, s.begin():
            row = orm.QaConversationRow(owner_id=owner_id, title=title[:200], run_id=run_id)
            s.add(row)
            await s.flush()
            return QaConversation(id=row.id, owner_id=owner_id, title=row.title, run_id=run_id)

    async def list(self, owner_id: str | None, run_id: str | None = None) -> list[QaConversation]:
        async with self._sm() as s:
            counts = (
                select(orm.QaMessageRow.conversation_id, func.count().label("n"))
                .group_by(orm.QaMessageRow.conversation_id)
                .subquery()
            )
            stmt = (
                select(orm.QaConversationRow, counts.c.n)
                .outerjoin(counts, counts.c.conversation_id == orm.QaConversationRow.id)
                .order_by(orm.QaConversationRow.updated_at.desc())
                .limit(200)
            )
            if owner_id is not None:
                stmt = stmt.where(orm.QaConversationRow.owner_id == owner_id)
            # 问答页只列独立会话；精读页只列本任务的会话，两边互不串
            stmt = stmt.where(
                orm.QaConversationRow.run_id.is_(None)
                if run_id is None
                else orm.QaConversationRow.run_id == run_id
            )
            rows = (await s.execute(stmt)).all()
            return [
                QaConversation(
                    id=row.id,
                    owner_id=row.owner_id,
                    title=row.title,
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                    message_count=int(count or 0),
                    run_id=row.run_id,
                )
                for row, count in rows
            ]

    async def get(self, conversation_id: str) -> QaConversation | None:
        async with self._sm() as s:
            row = await s.get(orm.QaConversationRow, conversation_id)
            if row is None:
                return None
            messages = (
                await s.scalars(
                    select(orm.QaMessageRow)
                    .where(orm.QaMessageRow.conversation_id == conversation_id)
                    .order_by(orm.QaMessageRow.position)
                )
            ).all()
            return QaConversation(
                id=row.id,
                owner_id=row.owner_id,
                title=row.title,
                created_at=row.created_at,
                updated_at=row.updated_at,
                messages=[_message(m) for m in messages],
                message_count=len(messages),
                run_id=row.run_id,
            )

    async def append(self, conversation_id: str, message: QaMessage) -> QaMessage:
        async with self._sm() as s, s.begin():
            await transaction_lock(s, f"qa:{conversation_id}")
            conversation = await s.get(orm.QaConversationRow, conversation_id, with_for_update=True)
            if conversation is None:
                raise KeyError(conversation_id)
            highest = await s.scalar(
                select(func.max(orm.QaMessageRow.position)).where(
                    orm.QaMessageRow.conversation_id == conversation_id
                )
            )
            position = 0 if highest is None else int(highest) + 1
            if position >= MAX_MESSAGES_PER_CONVERSATION:
                raise ConversationFullError("会话消息数已达上限，请新建会话")
            row = orm.QaMessageRow(
                conversation_id=conversation_id,
                position=position,
                query=message.query,
                answer=message.answer,
                citations=message.citations,
                evidence=message.evidence,
                thoughts=message.thoughts,
                status=message.status,
            )
            s.add(row)
            conversation.updated_at = datetime.now(UTC)
            await s.flush()
            return _message(row)

    async def delete(self, conversation_id: str) -> bool:
        async with self._sm() as s, s.begin():
            await transaction_lock(s, f"qa:{conversation_id}")
            row = await s.get(orm.QaConversationRow, conversation_id)
            if row is None:
                return False
            await s.delete(row)
            return True


class InMemoryQaStore:
    def __init__(self) -> None:
        self._items: dict[str, QaConversation] = {}
        self._stream_events: dict[str, list[tuple[int, str, dict[str, Any]]]] = {}

    async def create(self, owner_id: str, title: str, run_id: str | None = None) -> QaConversation:
        now = datetime.now(UTC)
        conversation = QaConversation(
            id=str(uuid4()),
            owner_id=owner_id,
            title=title[:200],
            created_at=now,
            updated_at=now,
            run_id=run_id,
        )
        self._items[conversation.id] = conversation
        return QaConversation(**{**conversation.__dict__, "messages": []})

    async def list(self, owner_id: str | None, run_id: str | None = None) -> list[QaConversation]:
        items = [
            c
            for c in self._items.values()
            if (owner_id is None or c.owner_id == owner_id) and c.run_id == run_id
        ]
        items.sort(key=lambda c: c.updated_at or datetime.min.replace(tzinfo=UTC), reverse=True)
        return [
            QaConversation(**{**c.__dict__, "messages": [], "message_count": len(c.messages)})
            for c in items
        ]

    async def get(self, conversation_id: str) -> QaConversation | None:
        conversation = self._items.get(conversation_id)
        if conversation is None:
            return None
        return QaConversation(
            **{
                **conversation.__dict__,
                "messages": list(conversation.messages),
                "message_count": len(conversation.messages),
            }
        )

    async def append(self, conversation_id: str, message: QaMessage) -> QaMessage:
        conversation = self._items.get(conversation_id)
        if conversation is None:
            raise KeyError(conversation_id)
        if len(conversation.messages) >= MAX_MESSAGES_PER_CONVERSATION:
            raise ConversationFullError("会话消息数已达上限，请新建会话")
        stored = QaMessage(
            **{
                **message.__dict__,
                "id": str(uuid4()),
                "position": len(conversation.messages),
                "created_at": datetime.now(UTC),
            }
        )
        conversation.messages.append(stored)
        conversation.updated_at = datetime.now(UTC)
        return stored

    async def delete(self, conversation_id: str) -> bool:
        conversation = self._items.pop(conversation_id, None)
        if conversation is not None:
            for message in conversation.messages:
                self._stream_events.pop(message.id, None)
        return conversation is not None


__all__ = [
    "ConversationFullError",
    "InMemoryQaStore",
    "MAX_MESSAGES_PER_CONVERSATION",
    "QaConversation",
    "QaMessage",
    "QaStore",
    "SqlQaStore",
]
