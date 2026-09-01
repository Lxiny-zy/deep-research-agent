"""Alembic 运行环境（async）。

要点：
  - 连接串优先取 config.attributes["database_url"]（应用启动时经
    persistence/db.py 程序内调用显式注入），无则回退
    deep_research.config.Settings（保持 CLI `alembic upgrade head` 可用）。
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

# 连接串：程序内注入的 attributes 优先，回退应用配置；
# % 转义 %% 以免 ConfigParser 插值在含特殊字符的密码上报错
_database_url = config.attributes.get("database_url") or Settings().database_url
config.set_main_option("sqlalchemy.url", _database_url.replace("%", "%%"))

if config.config_file_name is not None:
    # disable_existing_loggers 默认为 True，会把此刻已存在的 logger 全部禁用。
    # 迁移不只由 `alembic` CLI 触发：API 启动时的 SQLite schema 准备与
    # migrate.upgrade_head 都在**进程内**跑 command.upgrade，一旦沿用默认值，
    # 应用自己的 deep_research.* 日志会在迁移之后集体失声（且毫无提示）。
    fileConfig(config.config_file_name, disable_existing_loggers=False)

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
