import asyncio

import pytest

from deep_research.workbench.qa_admission import QaLimits, QaQueueFull
from tests.test_qa_requests import stores as stores


async def test_reservations_obey_global_and_owner_caps_without_breaking_idempotency(stores):
    store, jobs, other, cid = stores
    jobs.limits = other.limits = QaLimits(pending=3, pending_per_owner=2)
    sibling = (await store.create("local", "sibling")).id
    foreign = (await store.create("another", "foreign")).id
    candidates = [(cid, "request-a"), (sibling, "request-b"), (cid, "request-c")]
    results = await asyncio.gather(
        *[
            manager.reserve(conv, rid, rid, {"query": rid})
            for manager, (conv, rid) in zip([jobs, other, jobs], candidates, strict=True)
        ],
        return_exceptions=True,
    )
    assert sum(isinstance(result, QaQueueFull) for result in results) == 1
    index = next(i for i, value in enumerate(results) if not isinstance(value, Exception))
    conv, rid = candidates[index]
    assert (await other.reserve(conv, rid, rid, {"query": rid})).id == results[index].id
    await other.reserve(foreign, "request-d", "request-d", {"query": "d"})
    with pytest.raises(QaQueueFull):
        await jobs.reserve(foreign, "request-e", "request-e", {"query": "e"})


async def test_concurrent_claims_respect_shared_caps_and_recovery_skips_saturated_owner(stores):
    store, jobs, other, cid = stores
    jobs.limits = other.limits = QaLimits(active=2, active_per_owner=1)
    sibling = (await store.create("local", "sibling")).id
    foreign = (await store.create("another", "foreign")).id
    third = (await store.create("third", "third")).id
    for conv in (cid, sibling, foreign, third):
        await jobs.reserve(conv, "request-one", "hash", {"query": "q"})
    claims = await asyncio.gather(
        jobs.claim(cid, "request-one", "a", 90),
        other.claim(sibling, "request-one", "b", 90),
    )
    assert sum(claims) == 1
    assert {conv for conv, _ in await jobs.pending()} == {foreign, third}
    assert await other.claim(foreign, "request-one", "c", 90)
    assert not await jobs.claim(third, "request-one", "d", 90)
    assert await jobs.update(foreign, "request-one", "c", result={"answer": "done"})
    assert await other.claim(third, "request-one", "d", 90)
