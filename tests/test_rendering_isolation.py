"""Heavy delivery generation cannot exhaust ordinary API blocking capacity."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest

from deep_research.blocking import run_blocking, run_rendering
from deep_research.models import Report
from deep_research.persistence.repository import RunDetail
from deep_research.render_service import service_for
from deep_research.workbench import api as workbench_api
from deep_research.workbench import delivery_render, publish
from deep_research.workbench.delivery_store import build_or_load
from deep_research.workbench.publish import DeliveryBundle

pytestmark = pytest.mark.usefixtures("cooperative_render")


@pytest.mark.parametrize(
    ("saturate", "independent"),
    [(run_rendering, run_blocking), (run_blocking, run_rendering)],
)
async def test_blocking_and_rendering_have_independent_bounded_capacity(saturate, independent):
    loop = asyncio.get_running_loop()
    entered = [asyncio.Event() for _ in range(3)]
    release = threading.Event()
    active = peak = 0
    lock = threading.Lock()

    def work(index):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        loop.call_soon_threadsafe(entered[index].set)
        try:
            assert release.wait(10), "test did not release the blocking workers"
            return index
        finally:
            with lock:
                active -= 1

    tasks = [asyncio.create_task(saturate(work, index)) for index in range(3)]
    try:
        async with asyncio.timeout(5):
            await asyncio.gather(entered[0].wait(), entered[1].wait())
            assert await independent(lambda *, value: value, value="available") == "available"
            assert not entered[2].is_set()
            assert peak == 2
    finally:
        release.set()
        results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=5)
    assert results == [0, 1, 2]
    assert entered[2].is_set()
    assert peak == 2


@pytest.mark.parametrize("operation", ["cold_build", "format_retry"])
async def test_delivery_endpoints_leave_capacity_for_interactive_reads(
    operation, settings, monkeypatch, tmp_path
):
    from deep_research.http import auth

    loop = asyncio.get_running_loop()
    entered = [asyncio.Event(), asyncio.Event()]
    release = threading.Event()

    async def get_run(run_id):
        return RunDetail(
            id=run_id,
            query="q",
            status="done",
            report=Report(query="q", markdown="Finished report", citations=[]),
        )

    async def get_run_status(_run_id):
        return "done"

    def bundle(run_id):
        return DeliveryBundle(
            "autoResearch", "report", [], [], "pass", "", render_context={"id": run_id}
        )

    def render(detail, *_args, **_kwargs):
        run_id = detail["id"] if isinstance(detail, dict) else detail.id
        loop.call_soon_threadsafe(entered[int(run_id)].set)
        assert release.wait(10), "test did not release delivery generation"
        return bundle(run_id)

    versions = {}
    if operation == "format_retry":
        for run_id in ("0", "1"):
            initial = bundle(run_id)
            initial.failures = [{"format": "pdf", "retryable": True, "message": "temporary"}]
            saved = await run_blocking(
                build_or_load,
                await get_run(run_id),
                settings.artifact_root,
                None,
                lambda _, initial=initial: initial,
            )
            versions[run_id] = saved.content_version
    monkeypatch.setattr(publish, "build_bundle", render)
    monkeypatch.setattr(delivery_render, "render_bundle", render)
    monkeypatch.setattr(auth, "principal_for", lambda _: SimpleNamespace(can_research=True))
    repo = SimpleNamespace(get_run=get_run, get_run_status=get_run_status)
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(repo=repo, settings=settings)), headers={}
    )
    ordinary_file = tmp_path / "source.txt"
    ordinary_file.write_text("source remains readable", encoding="utf-8")

    async def generate(run_id):
        if operation == "cold_build":
            return await workbench_api._stored_bundle(request, run_id)
        body = workbench_api.DeliveryRetryRequest(
            version=versions[run_id], format="pdf", request_id=f"retry-run-{run_id}"
        )
        return await workbench_api.retry_deliverable(run_id, body, request)

    tasks = [asyncio.create_task(generate(str(index))) for index in range(2)]
    try:
        async with asyncio.timeout(5):
            await asyncio.gather(*(event.wait() for event in entered))
            content = await run_blocking(ordinary_file.read_text, encoding="utf-8")
            assert content == "source remains readable"
            assert not any(task.done() for task in tasks)
    finally:
        release.set()
        try:
            results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=5)
        finally:
            await service_for(repo, settings).close()
    assert len(results) == 2
