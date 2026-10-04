"""Durable render execution does not belong to the first HTTP waiter."""

import asyncio

import pytest

from deep_research import report
from deep_research.config import Settings
from deep_research.persistence.memory_repository import InMemoryRepository
from deep_research.render_queue import MemoryRenderQueue
from deep_research.render_service import RenderService
from deep_research.report.document import ProseBlock, ReportDocument


async def setup(tmp_path, queue=None):
    settings = Settings(artifact_root=str(tmp_path))
    repo = InMemoryRepository()
    run_id = await repo.create_run("q")
    detail = await repo.get_run(run_id)
    document = ReportDocument(query="q", blocks=[ProseBlock(markdown="已核验正文")])
    queue = queue or MemoryRenderQueue()
    return (
        detail,
        document,
        RenderService(repo, settings, queue=queue),
        RenderService(repo, settings, queue=queue),
    )


async def test_two_services_share_one_export_and_saved_bytes(tmp_path, monkeypatch):
    detail, document, first, second = await setup(tmp_path)
    calls = []
    original = report.render_markdown

    def render(doc, **kwargs):
        calls.append(True)
        return original(doc, **kwargs)

    monkeypatch.setattr(report, "render_markdown", render)
    try:
        outputs = await asyncio.gather(
            first.export(detail, document, "md"), second.export(detail, document, "md")
        )
        assert outputs[0] == outputs[1] and "已核验正文" in outputs[0]
        assert len(calls) == 1
    finally:
        await first.close()
        await second.close()


async def test_new_job_uses_free_capacity_while_an_earlier_render_is_blocked(tmp_path, monkeypatch):
    import threading

    detail, document, service, other = await setup(tmp_path)
    next_id = await service.repo.create_run("second")
    next_detail = await service.repo.get_run(next_id)
    loop = asyncio.get_running_loop()
    entered = {"q": asyncio.Event(), "second": asyncio.Event()}
    release = threading.Event()

    def render(doc, **kwargs):
        loop.call_soon_threadsafe(entered[doc.query].set)
        assert release.wait(10)
        return doc.query

    monkeypatch.setattr(report, "render_markdown", render)
    tasks = [asyncio.create_task(service.export(detail, document, "md"))]
    try:
        await asyncio.wait_for(entered["q"].wait(), 2)
        tasks.append(
            asyncio.create_task(service.export(next_detail, ReportDocument(query="second"), "md"))
        )
        await asyncio.wait_for(entered["second"].wait(), 2)
        assert not any(task.done() for task in tasks)
    finally:
        release.set()
        await asyncio.wait_for(asyncio.gather(*tasks), 5)
        await service.close()
        await other.close()


async def test_completed_file_survives_a_lost_queue_commit(tmp_path, monkeypatch):
    class LostCommit(MemoryRenderQueue):
        lost = False

        async def finish(self, *args, **kwargs):
            if not self.lost:
                self.lost = True
                raise OSError("lost queue commit")
            return await super().finish(*args, **kwargs)

    detail, document, service, other = await setup(tmp_path, LostCommit())
    calls = []
    original = report.render_markdown

    def render(doc, **kwargs):
        calls.append(True)
        return original(doc, **kwargs)

    monkeypatch.setattr(report, "render_markdown", render)
    try:
        assert "已核验正文" in await service.export(detail, document, "md")
        assert len(calls) == 1
    finally:
        await service.close()
        await other.close()


async def test_disconnected_waiter_does_not_cancel_the_render(tmp_path, monkeypatch):
    import threading

    detail, document, service, other = await setup(tmp_path)
    started, release = threading.Event(), threading.Event()
    original = report.render_markdown

    def render(doc, **kwargs):
        started.set()
        assert release.wait(10)
        return original(doc, **kwargs)

    monkeypatch.setattr(report, "render_markdown", render)
    task = asyncio.create_task(service.export(detail, document, "md"))
    try:
        assert await asyncio.to_thread(started.wait, 10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        assert "已核验正文" in await other.export(detail, document, "md")
        assert len(service.queue.jobs) == 1
    finally:
        release.set()
        await service.close()
        await other.close()


async def test_lost_job_ownership_prevents_file_publication(tmp_path, monkeypatch):
    import threading

    detail, document, service, other = await setup(tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = report.render_markdown

    def render(doc, **kwargs):
        entered.set()
        assert release.wait(10)
        return original(doc, **kwargs)

    monkeypatch.setattr(report, "render_markdown", render)
    task = asyncio.create_task(service.export(detail, document, "md"))
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        job_id = next(iter(service.queue.jobs))
        await service.queue.cancel(job_id)
        release.set()
        with pytest.raises(FileNotFoundError):
            await task
        await service.close()
        assert not list(tmp_path.rglob("*.bin"))
    finally:
        release.set()
        await service.close()
        await other.close()


async def test_cancelled_run_does_not_continue_automatic_rendering_but_can_export_saved_data(
    tmp_path,
):
    detail, document, service, other = await setup(tmp_path)
    await service.repo.set_status(detail.id, "cancelled")
    try:
        with pytest.raises(FileNotFoundError):
            await service.build(detail, automatic=True)
        assert "已核验正文" in await service.export(detail, document, "md")
    finally:
        await service.close()
        await other.close()


async def test_deleting_a_legacy_run_removes_its_new_export_cache(tmp_path):
    from deep_research.artifact_lifecycle import cleanup_artifacts
    from deep_research.render_service import service_for

    repo = InMemoryRepository()
    settings = Settings(artifact_root=str(tmp_path))
    run_id = await repo.create_run("old report")
    service = service_for(repo, settings)
    document = ReportDocument(query="old report", blocks=[ProseBlock(markdown="已核验正文")])
    try:
        await service.export(await repo.get_run(run_id), document, "md")
        assert (tmp_path / "runs" / run_id).exists()
        await repo.delete_run(run_id)
        await cleanup_artifacts(repo, settings)
        assert not (tmp_path / "runs" / run_id).exists()
        assert not service.queue.jobs
        assert not await repo.pending_artifact_cleanup()
    finally:
        await service.close()


async def test_explicit_export_retry_reopens_a_failed_job_but_polling_does_not(
    tmp_path, monkeypatch
):
    detail, document, service, other = await setup(tmp_path)
    calls = []
    original = report.render_markdown

    def broken(*args, **kwargs):
        calls.append(True)
        raise ValueError("broken export")

    monkeypatch.setattr(report, "render_markdown", broken)
    try:
        for token in ("click-1", "click-1", None):
            with pytest.raises(ValueError, match="broken export"):
                await service.export(detail, document, "md", retry_token=token)
        assert len(calls) == 1
        monkeypatch.setattr(report, "render_markdown", original)
        assert "已核验正文" in await other.export(detail, document, "md", retry_token="click-2")
    finally:
        await service.close()
        await other.close()


async def test_changed_storage_limit_reuses_completed_bytes(tmp_path, monkeypatch):
    detail, document, service, other = await setup(tmp_path)
    try:
        first = await service.export(detail, document, "md")

        def forbidden(*args, **kwargs):
            raise AssertionError("storage policy must not regenerate identical document bytes")

        monkeypatch.setattr(report, "render_markdown", forbidden)
        other.quota = 1
        assert await other.export(detail, document, "md") == first
    finally:
        await service.close()
        await other.close()
