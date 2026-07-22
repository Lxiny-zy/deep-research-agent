"""建表脚本：容器启动 / 本地初始化用（create_all，幂等）。

正式的 schema 版本管理用 Alembic（见项目根 alembic/）；本脚本用于快速建表与演示，
也是容器 entrypoint 的建表入口。运行：python -m deep_research.persistence.init_db
"""

from __future__ import annotations

import asyncio

from ..config import Settings
from .db import create_all, make_engine, prepare_sqlite_schema


async def init() -> None:
    settings = Settings()
    engine = make_engine(settings.database_url)
    if settings.database_url.startswith("sqlite"):
        await prepare_sqlite_schema(engine, settings.database_url)
    else:
        await create_all(engine)
    await engine.dispose()


def main() -> None:
    asyncio.run(init())


if __name__ == "__main__":
    main()
