"""Recovery must discover runnable conversations behind unrelated backlogs."""

from datetime import UTC, datetime, timedelta

from deep_research.workbench.qa_admission import QaLimits
from tests.test_qa_requests import stores as stores


async def test_blocked_backlogs_do_not_hide_an_independent_ready_conversation(stores):
    store, jobs, other, first = stores
    # Simulate a legacy backlog created before bounded admission existed.
    jobs.limits = other.limits = QaLimits(pending=1000, pending_per_owner=500)
    busy = [first, (await store.create("local", "second busy conversation")).id]
    for cid in busy:
        await jobs.reserve(cid, "active-request", "active", {"query": "active"})
        assert await jobs.claim(cid, "active-request", "worker", 3600)
        for index in range(199):
            rid = f"queued-{index}"
            await jobs.reserve(cid, rid, rid, {"query": rid})
    ready = (await store.create("another-user", "ready conversation")).id
    await other.reserve(ready, "ready-request", "ready", {"query": "ready"})

    pending = await jobs.pending()
    assert [(cid, row.request_id) for cid, row in pending] == [(ready, "ready-request")]
    assert await other.claim(ready, "ready-request", "independent-worker", 90)
    assert await jobs.pending() == []


async def test_recovery_yields_one_head_and_can_settle_an_expired_predecessor(stores):
    _, jobs, other, cid = stores
    clock = datetime(2026, 10, 6, tzinfo=UTC)
    for rid in ("lost-request", "next-request", "last-request"):
        await jobs.reserve(cid, rid, rid, {"query": rid})
    assert await jobs.claim(cid, "lost-request", "lost-worker", 1, now=clock)
    pending = await other.pending(now=clock + timedelta(seconds=2))
    assert [row.request_id for _, row in pending] == ["next-request"]
    assert await other.claim(
        cid, "next-request", "new-worker", 90, now=clock + timedelta(seconds=2)
    )
    previous = await jobs.get(cid, "lost-request", now=clock + timedelta(seconds=2))
    assert previous.status == "error"
    assert previous.execution_owner is None
    assert not await jobs.update(cid, "lost-request", "lost-worker", result={"answer": "late"})
