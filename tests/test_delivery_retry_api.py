from __future__ import annotations

import asyncio

import httpx

from deep_research.models import Report
from deep_research.workbench.delivery import pdf
from tests.test_workbench import _execution
from tests.test_workbench import api_repo as api_repo


async def test_format_retry_updates_cached_registry_and_keeps_versioned_downloads(
    api_repo, monkeypatch, cooperative_render
):
    api, repo = api_repo
    _, execution = _execution("q", "autoResearch", api.app.state.settings)
    run = await repo.create_run("q", execution=execution)
    await repo.save_report(
        run, Report(query="q", markdown="## 结论\n\n格式验收用综述。", citations=[])
    )
    await repo.set_status(run, "done")
    actual = pdf.render_pdf
    calls = 0

    def failed(*args, **kwargs):
        raise pdf.PdfRenderError("temporary failure")

    monkeypatch.setattr(pdf, "render_pdf", failed)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test"
    ) as client:
        first = await client.get(f"/api/runs/{run}/deliverables")
        assert first.status_code == 200
        initial = first.json()
        assert initial["failures"][0]["format"] == "pdf"
        old = next(item for item in initial["items"] if item["format"] == "html")
        old_path = (
            f"/api/runs/{run}/deliverables/{old['name']}?version={initial['content_version']}"
        )
        before = await client.get(old_path)

        def success(*args, **kwargs):
            nonlocal calls
            calls += 1
            return actual(*args, **kwargs)

        monkeypatch.setattr(pdf, "render_pdf", success)
        payload = {
            "version": initial["content_version"],
            "format": "pdf",
            "request_id": "same-retry-request",
        }
        responses = await asyncio.gather(
            *[client.post(f"/api/runs/{run}/deliverables/retry", json=payload) for _ in range(3)]
        )
        assert all(response.status_code == 200 for response in responses)
        versions = {response.json()["content_version"] for response in responses}
        assert len(versions) == calls == 1
        latest = (await client.get(f"/api/runs/{run}/deliverables")).json()
        assert (
            latest["content_version"] in versions
            and latest["content_version"] != initial["content_version"]
        )
        assert not latest["failures"]
        after = await client.get(old_path)
        assert before.content == after.content
        assert before.headers["x-content-version"] == after.headers["x-content-version"]
        stale = await client.post(
            f"/api/runs/{run}/deliverables/retry", json={**payload, "request_id": "different-retry"}
        )
        assert stale.status_code == 409
        assert stale.json()["detail"]["code"] == "delivery_version_changed"
        source = next(item for item in latest["items"] if item["format"] == "pdf")
        download = await client.get(
            f"/api/runs/{run}/deliverables/{source['name']}?version={latest['content_version']}"
        )
        assert download.content.startswith(b"%PDF")


async def test_retry_requires_research_permission_and_run_ownership(api_repo):
    from deep_research.access import ApiCredential, Principal

    api, repo = api_repo
    api.app.state.settings.api_key = ""
    api.app.state.settings.api_credentials = (
        ApiCredential(Principal("writer", "researcher"), "writer-key"),
        ApiCredential(Principal("reader", "reader"), "reader-key"),
    )
    _, execution = _execution("q", "autoResearch", api.app.state.settings)
    run, _ = await repo.create_run_once("q", request_hash="", execution=execution, owner_id="owner")
    payload = {"version": "a" * 64, "format": "pdf", "request_id": "retry-request"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test"
    ) as client:
        readonly = await client.post(
            f"/api/runs/{run}/deliverables/retry",
            json=payload,
            headers={"Authorization": "Bearer reader-key"},
        )
        stranger = await client.post(
            f"/api/runs/{run}/deliverables/retry",
            json=payload,
            headers={"Authorization": "Bearer writer-key"},
        )
    assert readonly.status_code == 403 and stranger.status_code == 404
