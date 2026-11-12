"""模型名额是软限流：占满或短暂冷却时排队等待，不让本次尝试失败。"""

from __future__ import annotations

import asyncio
import time

import pytest

from deep_research.config import Settings
from deep_research.persistence.db import create_all, make_engine, make_sessionmaker
from deep_research.persistence.orm import ProviderStateRow
from deep_research.provider_limits import ProviderCoordinator, coordinator_for, fingerprint
from deep_research.runtime_config import EDITABLE_FIELDS, apply_overrides


@pytest.fixture
async def sessions(tmp_path):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'soft.sqlite'}")
    await create_all(engine)
    yield make_sessionmaker(engine)
    await engine.dispose()


@pytest.mark.asyncio
async def test_more_callers_than_slots_all_finish_without_busy_errors(sessions):
    coordinator = ProviderCoordinator(sessions, concurrency=2, wait_seconds=10)
    key = fingerprint("https://provider.example", "soft-queue")
    active = peak = 0

    async def call() -> None:
        nonlocal active, peak
        async with coordinator.slot(key):
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.05)
            active -= 1

    await asyncio.gather(*(call() for _ in range(6)))
    assert peak == 2


@pytest.mark.asyncio
async def test_short_provider_cooldown_is_waited_out_in_place(sessions):
    coordinator = ProviderCoordinator(sessions, concurrency=1, wait_seconds=5)
    key = fingerprint("https://provider.example", "short-cooldown")
    async with sessions() as session, session.begin():
        session.add(
            ProviderStateRow(
                fingerprint=key,
                retry_at=time.time() + 0.3,
                window_start=time.time(),
                window_count=0,
                requests=0,
                limited=1,
            )
        )
    lease = await coordinator.acquire(key)
    await coordinator.release(lease)


def test_concurrency_is_user_editable_and_queue_budget_is_long():
    assert "provider_max_concurrency" in EDITABLE_FIELDS
    settings = apply_overrides(Settings(), {"provider_max_concurrency": 6})
    assert settings.provider_max_concurrency == 6

    class Repo:
        _sm = make_sessionmaker(make_engine("sqlite+aiosqlite://"))

    coordinator = coordinator_for(Repo(), settings)
    assert coordinator is not None
    assert coordinator.concurrency == 6
    assert coordinator.wait_seconds >= 600
