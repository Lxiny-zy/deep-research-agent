"""Bounded-memory safety aggregation, independent of event display limits."""

from __future__ import annotations

from .repository import ResearchRepository


async def blocked_source_count(repo: ResearchRepository, run_id: str) -> int | None:
    if hasattr(repo, "source_policy_summary"):
        return await repo.source_policy_summary(run_id)
    cursor = 0
    total = 0
    found = False
    while True:
        batch = await repo.get_events(run_id, after_seq=cursor, limit=512)
        for event in batch:
            data = event.data or {}
            if data.get("category") == "source_policy":
                found = True
                value = data.get("blocked")
                if isinstance(value, int) and not isinstance(value, bool):
                    total += value
        if len(batch) < 512:
            return total if found else None
        following = max((event.seq for event in batch if event.seq is not None), default=-1) + 1
        if following <= cursor:
            raise RuntimeError("repository returned events without an advancing cursor")
        cursor = following
