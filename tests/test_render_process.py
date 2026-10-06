"""Real child hangs/crashes cannot retain rendering capacity or publish partials."""

# Local filesystem probes deliberately observe a different OS process.
# ruff: noqa: ASYNC110, ASYNC240

import asyncio
import ctypes
import json
import os
import sys
import time
from pathlib import Path

import pytest

from deep_research import render_tasks
from deep_research.config import Settings
from deep_research.models import Report
from deep_research.persistence.memory_repository import InMemoryRepository
from deep_research.render_capacity import _permit
from deep_research.render_process import RenderProcess
from deep_research.render_service import RenderService
from deep_research.report.document import ProseBlock, ReportDocument


def alive(pid):
    if os.name == "nt":
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x100000, False, pid)
        if not handle:
            return False
        try:
            return kernel.WaitForSingleObject(handle, 0) == 0x102
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


async def marker(path):
    async with asyncio.timeout(10):
        while not path.exists():
            await asyncio.sleep(0.02)


async def setup(tmp_path, monkeypatch, mode="hang", kind="export"):
    monkeypatch.setattr(RenderProcess, "command", lambda _: [
        sys.executable, "-m", "tests.render_subprocess_fixture", mode, str(tmp_path),
    ])
    repo = InMemoryRepository()
    run_id = await repo.create_run("Q")
    detail = await repo.get_run(run_id)
    detail.report = Report(query="Q", markdown="A verified result.")
    service = RenderService(repo, Settings(artifact_root=str(tmp_path / "artifacts")))
    service.progress_timeout, service.execution_timeout, service.progress_poll = 10, 15, 0.05
    document = ReportDocument(query="Q", blocks=[ProseBlock(markdown="Verified body")])
    payload = (
        render_tasks.export_payload(detail, document, "md", {}, service.quota)
        if kind == "export" else render_tasks.bundle_payload(detail, service.quota)
    )
    job = await service.queue.reserve(
        key="test", pool=service.pool, run_id=run_id, kind=kind, payload=payload,
    )
    claimed = await service.queue.claim(service.pool, "owner", limit=2, lease_seconds=90)
    return service, detail, job, claimed


def assert_slots_free(root):
    for index in range(2):
        with _permit(root, [f"render-capacity/slot-{index}.lock"], timeout=0.1):
            pass


@pytest.mark.parametrize("mode", ["hang", "cpu", "exit", "tree"])
async def test_real_child_failure_releases_slots_and_never_publishes(tmp_path, monkeypatch, mode):
    service, _, job, claimed = await setup(tmp_path, monkeypatch, mode)
    started = time.monotonic()
    task = asyncio.create_task(service._execute(claimed))
    await marker(tmp_path / "entered.txt")
    if mode == "tree":
        await marker(tmp_path / "descendant.txt")
    if mode != "exit":
        service.progress_timeout = 0.15
    await task
    assert time.monotonic() - started < 8
    assert (tmp_path / "entered.txt").exists(), "fault must occur inside the native render boundary"
    assert not alive(int((tmp_path / "entered.txt").read_text().strip()))
    if mode == "tree":
        assert not alive(int((tmp_path / "descendant.txt").read_text().strip()))
    state = await service.queue.get(job.id)
    assert state.status == "pending" and state.stalls == 1
    assert state.error["kind"] == ("OSError" if mode == "exit" else "TimeoutError")
    assert not list(Path(service.root).rglob("*.bin"))
    assert_slots_free(service.root)
    await service.close(seconds=0.1)


async def test_absolute_deadline_also_kills_a_busy_renderer(tmp_path, monkeypatch):
    service, _, job, claimed = await setup(tmp_path, monkeypatch, "cpu")
    service.progress_timeout = 100
    service.execution_timeout = 3
    await service._execute(claimed)
    assert "期限" in (await service.queue.get(job.id)).error["message"]
    assert not alive(int((tmp_path / "entered.txt").read_text().strip()))
    assert_slots_free(service.root)


async def test_real_bundle_timeout_reuses_completed_formats(tmp_path, monkeypatch):
    from deep_research.workbench.delivery_store import current_version, delivery_store, load_version

    service, detail, job, claimed = await setup(tmp_path, monkeypatch, "bundle", "bundle")
    task = asyncio.create_task(service._execute(claimed))
    await marker(tmp_path / "pdf.txt")
    service.progress_timeout = 0.15
    await task
    assert (tmp_path / "pdf.txt").exists()
    assert current_version(detail, service.root) is None
    store, slug = delivery_store(detail, service.root)
    assert store.list_artifacts(slug) == []
    assert_slots_free(service.root)
    (tmp_path / "release").touch()
    service.progress_timeout = 10
    resumed = await service.queue.claim(
        service.pool, "resumed", limit=2, lease_seconds=90, now=time.time() + 10,
    )
    await service._execute(resumed)
    assert (await service.queue.get(job.id)).status == "done"
    assert len((tmp_path / "html.txt").read_text().splitlines()) == 1
    assert len((tmp_path / "docx.txt").read_text().splitlines()) == 1
    assert len((tmp_path / "pdf.txt").read_text().splitlines()) == 2
    version = current_version(detail, service.root)
    assert version is not None
    assert load_version(detail, service.root, version).status == "pass"


async def test_cancel_racing_child_completion_cannot_commit_queue_result(tmp_path, monkeypatch):
    service, _, job, claimed = await setup(tmp_path, monkeypatch, "gate")
    task = asyncio.create_task(service._execute(claimed))
    await marker(tmp_path / "entered.txt")
    await service.queue.cancel(job.id)
    (tmp_path / "release").touch()
    await task
    state = await service.queue.get(job.id)
    assert state.status == "cancelled" and state.result is None
    assert not list(Path(service.root).rglob("*.bin"))
    assert_slots_free(service.root)


async def test_close_kills_real_native_render_within_one_budget(tmp_path, monkeypatch):
    service, _, _, claimed = await setup(tmp_path, monkeypatch)
    task = asyncio.create_task(service._execute(claimed))
    service._active.add(task)
    task.add_done_callback(service._active.discard)
    await marker(tmp_path / "entered.txt")
    started = time.monotonic()
    await service.close(seconds=0.5)
    assert time.monotonic() - started < 0.8
    assert not alive(int((tmp_path / "entered.txt").read_text().strip()))
    assert_slots_free(service.root)
    assert not service._active


async def test_default_child_executes_a_real_export(tmp_path):
    repo = InMemoryRepository()
    run_id = await repo.create_run("Q")
    service = RenderService(repo, Settings(artifact_root=str(tmp_path)))
    try:
        document = ReportDocument(query="Q", blocks=[ProseBlock(markdown="actual output")])
        assert "actual output" in await service.export(await repo.get_run(run_id), document, "md")
    finally:
        await service.close()


@pytest.mark.parametrize("mode", ["noise", "flood", "nonce"])
async def test_child_protocol_rejects_noise_flood_and_wrong_nonce(tmp_path, monkeypatch, mode):
    service, _, job, claimed = await setup(tmp_path, monkeypatch, mode)
    await service._execute(claimed)
    assert (await service.queue.get(job.id)).status == "error"
    assert not alive(int((tmp_path / "entered.txt").read_text().strip()))
    assert not list(Path(service.root).rglob("*.bin"))
    assert_slots_free(service.root)


async def test_cancelling_close_itself_still_terminates_real_render(tmp_path, monkeypatch):
    service, _, _, claimed = await setup(tmp_path, monkeypatch)
    work = asyncio.create_task(service._execute(claimed))
    service._active.add(work)
    work.add_done_callback(service._active.discard)
    await marker(tmp_path / "entered.txt")
    closing = asyncio.create_task(service.close(seconds=2))
    await asyncio.sleep(0)
    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closing
    assert not alive(int((tmp_path / "entered.txt").read_text().strip()))
    assert_slots_free(service.root)


async def test_repeated_native_hangs_reach_existing_three_stall_limit(tmp_path, monkeypatch):
    service, _, job, claimed = await setup(tmp_path, monkeypatch)
    for attempt in range(3):
        service.progress_timeout = 10
        task = asyncio.create_task(service._execute(claimed))
        await marker(tmp_path / "entered.txt")
        service.progress_timeout = 0.15
        await task
        state = await service.queue.get(job.id)
        assert state.stalls == attempt + 1
        assert_slots_free(service.root)
        if attempt < 2:
            assert state.status == "pending"
            (tmp_path / "entered.txt").unlink()
            claimed = await service.queue.claim(
                service.pool, f"owner-{attempt}", limit=2, lease_seconds=90,
                now=time.time() + 30,
            )
    assert state.status == "error" and state.attempts == 3
    assert await service.queue.claim(
        service.pool, "no-more", limit=2, lease_seconds=90, now=time.time() + 60,
    ) is None


async def test_child_failure_logs_only_safe_exception_types_and_frame_locations(
    tmp_path, monkeypatch, caplog,
):
    service, _, job, claimed = await setup(tmp_path, monkeypatch, "diagnostic")
    await service._execute(claimed)
    record = next(
        record for record in caplog.records
        if record.name == "deep_research.render_service" and "diagnostic=" in record.msg
    )
    assert record.args[0] == job.id and record.args[2] == "child"
    diagnostic = json.loads(record.args[3])
    assert [entry["type"] for entry in diagnostic["exceptions"]] == ["RuntimeError", "ValueError"]
    frames = diagnostic["exceptions"][0]["frames"]
    assert any(
        frame["file"] == "render_subprocess_fixture.py" and frame["function"] == "native_failure"
        and frame["line"] > 0 for frame in frames
    )
    assert all(set(frame) == {"file", "function", "line"} for frame in frames)
    assert all("/" not in frame["file"] and "\\" not in frame["file"] for frame in frames)
    assert "PRIVATE-" not in caplog.text
    state = await service.queue.get(job.id)
    assert state.status == "error" and set(state.error) == {"kind", "message"}
    assert "PRIVATE-" not in json.dumps(state.error)


async def test_parent_failure_logs_safe_locations_without_raw_exception(
    tmp_path, monkeypatch, caplog,
):
    service, _, job, claimed = await setup(tmp_path, monkeypatch)

    async def failing_status(job):
        raise RuntimeError("PRIVATE-PARENT-STATUS-NOT-FOR-LOGS")

    monkeypatch.setattr(service, "_can_execute", failing_status)
    await service._execute(claimed)
    record = next(
        record for record in caplog.records
        if record.name == "deep_research.render_service" and "diagnostic=" in record.msg
    )
    assert record.args[0] == job.id and record.args[2] == "parent"
    assert record.exc_info is None and "PRIVATE-" not in caplog.text
    diagnostic = json.loads(record.args[3])
    assert diagnostic["exceptions"][0]["type"] == "RuntimeError"
    assert diagnostic["exceptions"][0]["frames"][-1]["function"] == "failing_status"
