"""Authenticated real operational trends; no demo or generated quality scores."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from ..http.auth import principal_for, require_api_key
from .operations_source import MAX_ITEMS, memory_snapshot, sql_snapshot
from .operations_summary import summarize

router = APIRouter(prefix="/api", dependencies=[Depends(require_api_key)])


@router.get("/operations")
async def operations(
    request: Request, response: Response, days: int = Query(7, ge=1, le=30),
    scope: Literal["mine", "workspace"] = "mine",
) -> dict[str, Any]:
    principal = principal_for(request)
    if scope == "workspace" and not principal.can_manage:
        raise HTTPException(403, "只有管理员可以查看工作区汇总")
    owner = None if scope == "workspace" else principal.id
    until = datetime.now(UTC)
    since = until - timedelta(days=days)
    repo = request.app.state.repo
    try:
        async with asyncio.timeout(15):
            if sessionmaker := getattr(repo, "_sm", None):
                data = await sql_snapshot(sessionmaker, owner=owner, since=since, until=until)
            else:
                data = await memory_snapshot(
                    repo, request.app.state.qa_store, owner=owner, since=since, until=until,
                )
    except TimeoutError as exc:
        raise HTTPException(503, "统计查询超时，请稍后重试") from exc
    result = summarize(data, since=since, until=until)
    result.update(scope=scope, max_records_per_kind=MAX_ITEMS)
    if data["truncated"]:
        result["limitations"].append("记录超过读取上限，当前汇总不是该时间范围的全量统计。")
    if data["unknown_dates"]:
        result["limitations"].append("部分历史记录缺少创建时间，未归入当前时间窗口。")
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Vary"] = "Authorization, X-API-Key"
    return result
