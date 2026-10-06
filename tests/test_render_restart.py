"""A new process recovers a queued export after its predecessor publishes bytes."""

import asyncio
import multiprocessing
import os

from deep_research.config import Settings
from deep_research.persistence.db import create_all, make_engine, make_sessionmaker
from deep_research.persistence.sql_repository import SqlRepository
from deep_research.render_queue import SqlRenderQueue
from deep_research.render_service import RenderService
from deep_research.render_tasks import export_payload
from deep_research.report.document import ProseBlock, ReportDocument
from deep_research.workbench.support import digest


def _publish_then_crash(database_url, root, marker):
    async def run():
        import pytest

        from deep_research import render_service, report
        from tests.render_helpers import use_cooperative_render

        use_cooperative_render(pytest.MonkeyPatch())

        render_service.LEASE_SECONDS = 0.3
        render_service.HEARTBEAT_SECONDS = 0.05
        engine = make_engine(database_url)
        repo = SqlRepository(make_sessionmaker(engine))
        queue = SqlRenderQueue(make_sessionmaker(engine))
        original = report.render_markdown

        def rendered(document, **kwargs):
            with open(marker, "a", encoding="utf-8") as output:
                output.write("render\n")
            return original(document, **kwargs)

        async def crash(*args, **kwargs):
            os._exit(31)

        report.render_markdown = rendered
        queue.finish = crash
        service = RenderService(repo, Settings(artifact_root=root), queue=queue)
        await service.poll_pending()

    asyncio.run(run())


async def test_process_restart_reuses_published_export_without_rendering_again(
    tmp_path, monkeypatch
):
    from deep_research import report

    database_url = f"sqlite+aiosqlite:///{tmp_path / 'queue.db'}"
    root, marker = tmp_path / "artifacts", tmp_path / "calls.txt"
    engine = make_engine(database_url)
    await create_all(engine)
    repo = SqlRepository(make_sessionmaker(engine))
    run_id = await repo.create_run("frozen export")
    detail = await repo.get_run(run_id)
    document = ReportDocument(query="frozen export", blocks=[ProseBlock(markdown="原始正文")])
    service = RenderService(repo, Settings(artifact_root=str(root)))
    payload = export_payload(detail, document, "md", {}, service.quota)
    key = digest([service.pool, run_id, "export", payload])
    queued = await service.queue.reserve(
        key=key, pool=service.pool, run_id=run_id, kind="export", payload=payload
    )
    process = multiprocessing.get_context("spawn").Process(
        target=_publish_then_crash, args=(database_url, str(root), str(marker))
    )
    try:
        process.start()
        await asyncio.to_thread(process.join, 20)
        assert process.exitcode == 31, (await service.queue.get(queued.id)).error
        await asyncio.sleep(0.35)

        def forbidden(*args, **kwargs):
            raise AssertionError("the complete export must survive process loss")

        monkeypatch.setattr(report, "render_markdown", forbidden)
        assert "原始正文" in await asyncio.wait_for(service.export(detail, document, "md"), 10)
        final = await service.queue.get(queued.id)
        assert final.status == "done" and final.attempts == 2
        assert marker.read_text(encoding="utf-8") == "render\n"
    finally:
        if process.is_alive():
            process.terminate()
            process.join(5)
        await service.close()
        await engine.dispose()
