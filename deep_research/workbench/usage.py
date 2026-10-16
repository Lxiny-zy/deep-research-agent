"""用量与额度：每个身份每个自然日（UTC）的研究次数与 token 消耗。

受理次数与最终 token 快照保存在不随任务删除的 usage_ledger。
额度检查在入队事务内执行，页面汇总仅用于展示。

额度只在**创建**研究时检查：已开始的运行不会被中途打断——那会丢掉已经花掉的
token 换来的中间结果。用户在创建前就能从 ``/api/usage`` 看到剩余额度。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

_PAGE = 200


async def usage_today(repo: Any, owner_id: str | None) -> dict[str, int]:
    """汇总今天（UTC）创建的运行数与累计 token。"""
    start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    if hasattr(repo, "usage_since"):
        return await repo.usage_since(owner_id, start)
    runs = 0
    tokens = 0
    offset = 0
    while True:
        page = await repo.list_runs(limit=_PAGE, offset=offset, owner_id=owner_id)
        stop = False
        for summary in page:
            created = summary.created_at
            if created is not None and created.tzinfo is None:
                created = created.replace(tzinfo=UTC)
            if created is not None and created < start:
                stop = True  # list_runs 按创建时间倒序，越过今天即可停止
                break
            runs += 1
            tokens += int(summary.total_tokens or 0)
        if stop or len(page) < _PAGE:
            break
        offset += _PAGE
    return {"runs": runs, "tokens": tokens}


def quota_view(used: dict[str, int], settings: Any) -> dict[str, Any]:
    run_quota = settings.daily_run_quota
    return {
        "period": "day",
        # 额度在下一个 UTC 零点重置；今天的零点已经过去
        "resets_at": (
            datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        ).isoformat(),
        "runs": {"used": used["runs"], "limit": run_quota},
        "tokens": {"used": used["tokens"], "limit": None},
        "exhausted": bool(run_quota is not None and used["runs"] >= run_quota),
    }


__all__ = ["quota_view", "usage_today"]
