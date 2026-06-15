"""Alembic 运行环境（async）。

要点：
  - 连接串从 deep_research.config.Settings 读取（与应用同源，单一事实来源）。
  - target_metadata 指向 ORM 的 Base.metadata，使 --autogenerate 可对比模型与库。
  - 用 async 引擎驱动（sqlite+aiosqlite / postgresql+asyncpg 都走这条路径）。
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from deep_research.config import Settings
from deep_research.persistence import orm

config = context.config

# 用应用配置覆盖连接串；% 转义 %% 以免 ConfigParser 插值在含特殊字符的密码上报错
config.set_main_option("sqlalchemy.url", Settings().database_url.replace("%", "%%"))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = orm.Base.metadata


def run_migrations_offline() -> None:
    """离线模式：仅按 URL 生成 SQL，不建立实际连接。"""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """在线模式：建 async 引擎，run_sync 把同步迁移跑在 async 连接上。"""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
