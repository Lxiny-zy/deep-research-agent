"""Legacy report downloads reserve ordinary blocking capacity for API reads."""

from __future__ import annotations

import asyncio
import threading

import httpx
import pytest

from deep_research import api
from deep_research import report as report_renderers
from deep_research.blocking import run_blocking
from deep_research.persistence.memory_repository import InMemoryRepository
from deep_research.report import capabilities

pytestmark = pytest.mark.usefixtures("cooperative_render")


@pytest.mark.parametrize(
    ("suffix", "renderer", "payload"),
    [
        ("md", "render_markdown", "# Report"),
        ("csv", "render_csv", "column\nvalue\n"),
        ("xlsx", "render_xlsx", b"PK workbook"),
        ("pdf", "render_pdf", b"%PDF report"),
        ("tex", "render_latex", "% report source"),
        ("bib", "render_bibtex", "@misc{reference}"),
        ("bundle.zip", "render_reproducibility_bundle", b"PK bundle"),
        ("paper.pdf", "render_latex_pdf", b"%PDF paper"),
    ],
)
async def test_legacy_exports_leave_capacity_for_reads_and_capabilities(
    suffix, renderer, payload, settings, monkeypatch, tmp_path
):
    repo = InMemoryRepository()
    settings.api_key = ""
    settings.api_credentials = ()
    monkeypatch.setattr(api.app.state, "settings", settings, raising=False)
    monkeypatch.setattr(api.app.state, "repo", repo, raising=False)
    monkeypatch.setattr(api.app.state, "catalog", None, raising=False)
    monkeypatch.setattr(capabilities, "export_capabilities", lambda: {"pdf": True})
    run_ids = [await repo.create_run(str(index)) for index in range(3)]
    loop = asyncio.get_running_loop()
    entered = [asyncio.Event() for _ in run_ids]
    release = threading.Event()

    def render(document, **_kwargs):
        loop.call_soon_threadsafe(entered[int(document.query)].set)
        assert release.wait(10), "test did not release report rendering"
        return payload

    monkeypatch.setattr(report_renderers, renderer, render)
    ordinary_file = tmp_path / "source.txt"
    ordinary_file.write_text("source remains readable", encoding="utf-8")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test"
    ) as client:
        tasks = [
            asyncio.create_task(client.get(f"/api/runs/{run_id}/document.{suffix}"))
            for run_id in run_ids[:2]
        ]
        try:
            async with asyncio.timeout(5):
                await asyncio.gather(entered[0].wait(), entered[1].wait())
                tasks.append(
                    asyncio.create_task(client.get(f"/api/runs/{run_ids[2]}/document.{suffix}"))
                )
                content = await run_blocking(ordinary_file.read_text, encoding="utf-8")
                assert content == "source remains readable"
                response = await client.get("/api/capabilities")
                assert response.status_code == 200
                assert response.json() == {"exports": {"pdf": True}}
                assert not entered[2].is_set()
                assert not any(task.done() for task in tasks)
        finally:
            release.set()
            responses = await asyncio.wait_for(asyncio.gather(*tasks), timeout=5)

    assert entered[2].is_set()
    for response in responses:
        assert response.status_code == 200
        assert response.headers["cache-control"] == "private, no-store"
        assert "attachment;" in response.headers["content-disposition"]
        if isinstance(payload, bytes):
            assert response.content == payload
        else:
            assert response.text.lstrip("\ufeff") == payload
