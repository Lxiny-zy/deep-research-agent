"""持久化仓储接口（Protocol）+ 读取用 DTO。

接口同时服务两条路径：
  - 写入（orchestrator）：create_run / save_plan / add_sub_questions / save_result /
    save_sources / save_report / save_events / finalize / set_status
  - 读取（API）：list_runs / get_run / get_events

两个实现：
  - InMemoryRepository：纯内存，离线单测零依赖（亦为本协议的参考实现）
  - SqlRepository      ：async SQLAlchemy（PostgreSQL / SQLite），API 默认使用
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from ..models import Report, ResearchPlan, ResearchResult, Source, SubQuestion
from ..observability import Event


@dataclass
class RunSummary:
    """历史列表行（轻量）。"""

    id: str
    query: str
    status: str
    created_at: datetime | None = None
    total_tokens: int = 0
    elapsed: float = 0.0


@dataclass
class RunDetail:
    """单次研究详情（含计划、结果、报告）。"""

    id: str
    query: str
    status: str
    interpretation: str = ""
    sub_questions: list[SubQuestion] = field(default_factory=list)
    results: list[ResearchResult] = field(default_factory=list)
    report: Report | None = None
    total_tokens: int = 0
    elapsed: float = 0.0
    created_at: datetime | None = None


class ResearchRepository(Protocol):
    """研究运行的持久化仓储。两个实现结构化满足本协议（无需显式继承）。"""

    async def create_run(self, query: str) -> str: ...

    async def set_status(self, run_id: str, status: str) -> None: ...

    async def save_plan(self, run_id: str, plan: ResearchPlan) -> None: ...

    async def add_sub_questions(
        self, run_id: str, sub_questions: list[SubQuestion], *, origin: str, round: int
    ) -> None: ...

    async def save_result(self, run_id: str, result: ResearchResult) -> None: ...

    async def save_sources(self, run_id: str, sources: list[Source]) -> None: ...

    async def save_report(self, run_id: str, report: Report) -> None: ...

    async def save_events(self, run_id: str, events: list[Event]) -> None: ...

    async def finalize(self, run_id: str, *, elapsed: float, total_tokens: int) -> None: ...

    async def list_runs(self, *, limit: int = 50, offset: int = 0) -> list[RunSummary]: ...

    async def get_run(self, run_id: str) -> RunDetail | None: ...

    async def get_events(self, run_id: str, *, after_seq: int = 0) -> list[Event]: ...
