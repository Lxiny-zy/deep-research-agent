"""持久化层：仓储接口 + 内存实现（轻量，不依赖 SQLAlchemy）。

注意：本 __init__ 刻意只导出不依赖 SQLAlchemy 的部分（接口 + InMemoryRepository），
这样 CLI / eval / 离线测试 import 持久化时不会被迫加载数据库驱动。
SQL 实现请显式 `from deep_research.persistence.sql_repository import SqlRepository`，
引擎工厂请 `from deep_research.persistence.db import make_engine, make_sessionmaker`。
"""

from .memory_repository import InMemoryRepository
from .repository import ResearchRepository, RunDetail, RunSummary

__all__ = [
    "InMemoryRepository",
    "ResearchRepository",
    "RunDetail",
    "RunSummary",
]
