"""All cleanup phases share a deadline, including cancellation-resistant tasks."""

import asyncio
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from deep_research import worker as worker_module
from deep_research.api import _shutdown_application
from deep_research.config import Settings
from deep_research.render_service import RenderService


@pytest.mark.parametrize("mode", ["dispatcher", "close", "dispose", "run", "unregister"])
def test_worker_cli_exits_despite_uncooperative_shutdown_phase(mode):
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
    started = time.monotonic()
    process = subprocess.run(
        [sys.executable, "-m", "tests.worker_shutdown_subprocess", mode],
        capture_output=True, text=True, timeout=10, **options,
    )
    assert process.returncode == 0, process.stderr
    assert "shutdown-fixture-started" in process.stdout
    assert "forcing process exit" in process.stderr
    assert time.monotonic() - started < 8


async def test_render_close_does_not_join_a_repeatedly_cancelled_dispatcher(tmp_path):
    service = RenderService(None, Settings(artifact_root=str(tmp_path)))
    release, entered = asyncio.Event(), asyncio.Event()
    cancellations = []

    async def stuck():
        entered.set()
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancellations.append(1)

    service._drainer = asyncio.create_task(stuck())
    await entered.wait()
    service._drainer.cancel()
    await asyncio.sleep(0)
    started = time.monotonic()
    try:
        await service.close(seconds=0.15)
        assert time.monotonic() - started < 0.4
        assert service.requires_hard_exit
        assert cancellations == [1]  # no immediate repeated cancellation
    finally:
        release.set()
        await service._drainer


async def test_api_dispatcher_qa_renderer_and_engine_share_one_shutdown_budget(monkeypatch):
    monkeypatch.setattr(worker_module, "_CLEANUP_SECONDS", 0.15)
    app = FastAPI()
    release = asyncio.Event()
    phases = []

    async def stuck(name):
        phases.append(name)
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                pass

    dispatcher = asyncio.create_task(stuck("dispatcher"))
    qa = asyncio.create_task(stuck("qa"))
    app.state.tasks = {dispatcher}
    app.state.qa_tasks = {qa}
    app.state.cleanup_tasks = set()

    class Rendering:
        requires_hard_exit = False

        async def close(self, *, deadline):
            await stuck("render-close")

    class Engine:
        async def dispose(self):
            await stuck("dispose")

    existing = asyncio.all_tasks()
    started = time.monotonic()
    try:
        await asyncio.sleep(0)
        await _shutdown_application(
            app, SimpleNamespace(worker_shutdown_grace_seconds=0), Engine(),
            render_dispatcher=dispatcher, render_service=Rendering(),
        )
        assert time.monotonic() - started < 0.4
        assert app.state.shutdown_incomplete
        assert set(phases) == {"dispatcher", "qa", "render-close", "dispose"}
    finally:
        release.set()
        await asyncio.gather(dispatcher, qa, *(asyncio.all_tasks() - existing))


async def test_render_claim_completing_after_close_does_not_start_work(tmp_path, monkeypatch):
    service = RenderService(None, Settings(artifact_root=str(tmp_path)))
    entered, release = asyncio.Event(), asyncio.Event()
    original = service.queue.claim
    started = []
    job = await service.queue.reserve(
        key="late", pool=service.pool, run_id="late", kind="export", payload={},
    )

    async def late_claim(*args, **kwargs):
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()
        return await original(*args, **kwargs)

    async def execute(job):
        started.append(job.id)

    monkeypatch.setattr(service.queue, "claim", late_claim)
    monkeypatch.setattr(service, "_execute", execute)
    service.wake()
    await entered.wait()
    await service.close(seconds=0.1)
    release.set()
    await service._drainer
    assert started == []
    recovered = await original(
        service.pool, "recovery", limit=2, lease_seconds=90, now=time.time() + 100,
    )
    assert recovered.id == job.id


async def test_api_rescans_qa_children_registered_during_driver_cleanup(monkeypatch):
    monkeypatch.setattr(worker_module, "_CLEANUP_SECONDS", 0.15)
    app = FastAPI()
    release, entered = asyncio.Event(), asyncio.Event()
    app.state.tasks = set()
    app.state.qa_tasks = set()
    app.state.cleanup_tasks = set()

    async def child():
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                pass

    async def driver():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            app.state.qa_tasks.add(asyncio.create_task(child()))

    class Engine:
        async def dispose(self):
            pass

    task = asyncio.create_task(driver())
    app.state.qa_tasks.add(task)
    await entered.wait()
    try:
        await _shutdown_application(app, SimpleNamespace(worker_shutdown_grace_seconds=0), Engine())
        assert app.state.shutdown_incomplete
    finally:
        release.set()
        await asyncio.gather(*list(app.state.qa_tasks), return_exceptions=True)


async def test_api_drains_all_generations_of_late_background_work(monkeypatch):
    monkeypatch.setattr(worker_module, "_CLEANUP_SECONDS", 0.3)
    app = FastAPI()
    app.state.tasks = set()
    app.state.cleanup_tasks = set()
    stopped = set()
    entered = [asyncio.Event() for _ in range(3)]

    def start(depth):
        task = asyncio.create_task(worker(depth))
        app.state.tasks.add(task)
        task.add_done_callback(app.state.tasks.discard)

    async def worker(depth):
        entered[depth].set()
        try:
            await asyncio.Event().wait()
        finally:
            if depth:
                start(depth - 1)
                await entered[depth - 1].wait()
            stopped.add(depth)

    class Engine:
        async def dispose(self):
            assert stopped == {0, 1, 2}

    start(2)
    await entered[2].wait()
    await _shutdown_application(app, SimpleNamespace(worker_shutdown_grace_seconds=0), Engine())
    assert not app.state.tasks and not app.state.shutdown_incomplete


async def test_api_does_not_silently_ignore_engine_disposal_failure():
    app = FastAPI()

    class Engine:
        async def dispose(self):
            raise OSError("disposal failed")

    with pytest.raises(OSError, match="disposal failed"):
        await _shutdown_application(app, SimpleNamespace(worker_shutdown_grace_seconds=0), Engine())
    assert app.state.shutdown_incomplete
