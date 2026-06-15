"""数据库引擎与会话工厂（async SQLAlchemy 2.0）。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .orm import Base


def make_engine(database_url: str, *, echo: bool = False) -> AsyncEngine:
    """创建 async 引擎。database_url 形如 postgresql+asyncpg://... 或 sqlite+aiosqlite://..."""
    engine = create_async_engine(database_url, echo=echo, pool_pre_ping=True)
    if database_url.startswith("sqlite"):
        # SQLite 单写者模型：WAL 允许读写并发、加长 busy_timeout，
        # 避免多个 run 并发落库时直接抛 "database is locked"
        @sa_event.listens_for(engine.sync_engine, "connect")
        def _sqlite_pragma(dbapi_conn: Any, _record: Any) -> None:
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=15000")
            cursor.close()

    return engine


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def create_all(engine: AsyncEngine) -> None:
    """直接建表（测试 / 无 Alembic 的快速启动用；生产用 alembic upgrade head）。"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
