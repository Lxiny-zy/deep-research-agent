"""Repeated graceful/forced SQLite shutdown must not wait on an exited worker thread."""

import asyncio
import threading

import pytest

from deep_research.persistence.db import make_engine


@pytest.mark.parametrize("after_exit", [False, True])
async def test_repeated_stop_requests_finish_before_and_after_worker_exit(after_exit):
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    connection = await engine.connect()
    driver = (await connection.get_raw_connection()).driver_connection
    release = threading.Event()
    started = threading.Event()
    blocked = None
    try:
        if not hasattr(driver, "stop"):
            pytest.skip("This driver predates the stop-future API")
        if not after_exit:
            def hold():
                started.set()
                release.wait(5)
            blocked = asyncio.create_task(driver._execute(hold))
            assert await asyncio.to_thread(started.wait, 2)
        first = driver.stop()
        if after_exit:
            await first
            await asyncio.to_thread(driver._thread.join, 2)
            assert not driver._thread.is_alive()
        second = driver.stop()
        release.set()
        if blocked:
            await blocked
        await asyncio.wait_for(asyncio.gather(first, second), timeout=2)
        await asyncio.to_thread(driver._thread.join, 2)
        assert not driver._thread.is_alive()
    finally:
        release.set()
        if blocked:
            await asyncio.gather(blocked, return_exceptions=True)
        await connection.invalidate()
        await connection.close()
        await engine.dispose()


async def test_graceful_close_settles_when_forced_stop_was_already_requested():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    connection = await engine.connect()
    driver = (await connection.get_raw_connection()).driver_connection
    release, started = threading.Event(), threading.Event()
    blocked = close = None
    try:
        if not hasattr(driver, "stop"):
            pytest.skip("This driver predates the stop-future API")

        def hold():
            started.set()
            release.wait(5)

        blocked = asyncio.create_task(driver._execute(hold))
        assert await asyncio.to_thread(started.wait, 2)
        stopped = driver.stop()
        close = asyncio.create_task(driver.close())
        await asyncio.sleep(0)
        release.set()
        await blocked
        await stopped
        result = await asyncio.wait_for(asyncio.gather(close, return_exceptions=True), timeout=2)
        assert result[0] is None or isinstance(result[0], ValueError)
        await asyncio.to_thread(driver._thread.join, 2)
        assert not driver._thread.is_alive()
    finally:
        release.set()
        if close and not close.done():
            close.cancel()
        await asyncio.gather(*(task for task in [blocked, close] if task), return_exceptions=True)
        await connection.invalidate()
        await connection.close()
        await engine.dispose()


async def test_cancelled_graceful_close_and_forced_stop_share_a_finite_shutdown():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    connection = await engine.connect()
    driver = (await connection.get_raw_connection()).driver_connection
    release, started = threading.Event(), threading.Event()
    blocked = close = None
    try:
        if not hasattr(driver, "stop"):
            pytest.skip("This driver predates the stop-future API")

        def hold():
            started.set()
            release.wait(5)

        blocked = asyncio.create_task(driver._execute(hold))
        assert await asyncio.to_thread(started.wait, 2)
        close = asyncio.create_task(driver.close())
        await asyncio.sleep(0)
        close.cancel()
        await asyncio.sleep(0)  # close() enters its finally block and requests stop.
        stopped = driver.stop()
        release.set()
        await blocked
        result = await asyncio.wait_for(
            asyncio.gather(close, stopped, return_exceptions=True), timeout=2,
        )
        assert isinstance(result[0], asyncio.CancelledError)
        await asyncio.to_thread(driver._thread.join, 2)
        assert not driver._thread.is_alive()
    finally:
        release.set()
        if close and not close.done():
            close.cancel()
        await asyncio.gather(*(task for task in [blocked, close] if task), return_exceptions=True)
        await connection.invalidate()
        await connection.close()
        await engine.dispose()
