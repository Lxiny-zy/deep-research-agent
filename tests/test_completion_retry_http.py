from __future__ import annotations

import httpx
import pytest

from deep_research.models import Report
from deep_research.workbench.completion import assess_completion
from deep_research.workbench.delivery import html
from deep_research.workbench.delivery_render import render_bundle
from deep_research.workbench.delivery_store import (
    build_or_load,
    current_version,
    delivery_store,
    workspace_files,
)
from deep_research.workbench.gates import GateResult
from tests.test_task_completion import execution
from tests.test_workbench import api_repo as api_repo


@pytest.mark.parametrize("damage_after_commit", [False, True])
async def test_retry_file_commit_then_db_failure_recovers_on_read_without_second_render(
    api_repo, monkeypatch, cooperative_render, damage_after_commit
):
    api, repo = api_repo
    settings = api.app.state.settings
    run_id = await repo.create_run("Q", execution=execution(settings, ["md", "html"]))
    await repo.save_report(run_id, Report(query="Q", markdown="Verified body"))
    detail = await repo.get_run(run_id)
    context = {
        "title": "Q",
        "markdown": "Verified body",
        "stem": "q",
        "template": "autoResearch",
        "kicker": "Report",
        "meta": [],
        "extras": {},
        "citations": [],
        "wants": ["md", "html"],
        "blocked": False,
        "fail_on_quality": True,
        "generated_at": "2026-10-04T00:00:00Z",
        "base_gates": [
            GateResult(name, "pass").to_dict()
            for name in ("markdown", "length", "structure", "prose_evidence")
        ],
    }
    actual_html = html.render_html

    def fail_html(*args, **kwargs):
        raise OSError("temporary renderer outage")

    monkeypatch.setattr(html, "render_html", fail_html)
    initial = build_or_load(
        detail,
        settings.artifact_root,
        settings.artifact_total_bytes,
        lambda _: render_bundle(context, []),
    )
    await repo.finalize(
        run_id, elapsed=5, total_tokens=7, completion=assess_completion(detail, initial)
    )
    assert await repo.get_run_status(run_id) == "needs_review"
    renders = 0

    def restored_html(*args, **kwargs):
        nonlocal renders
        renders += 1
        return actual_html(*args, **kwargs)

    monkeypatch.setattr(html, "render_html", restored_html)
    actual_update = repo.update_completion
    writes = 0

    async def failing_update(*args, **kwargs):
        nonlocal writes
        writes += 1
        if writes == 1:
            raise OSError("DB unavailable after file commit")
        return await actual_update(*args, **kwargs)

    monkeypatch.setattr(repo, "update_completion", failing_update)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test"
    ) as client:
        payload = {
            "version": initial.content_version,
            "format": "html",
            "request_id": "commit-then-db-recovery",
        }
        first = await client.post(f"/api/runs/{run_id}/deliverables/retry", json=payload)
        assert first.status_code == 503
        assert await repo.get_run_status(run_id) == "needs_review"
        if damage_after_commit:
            latest = current_version(detail, settings.artifact_root)
            store, _ = delivery_store(detail, settings.artifact_root)
            file = next(
                item for item in workspace_files(detail, settings.artifact_root)
                if item["content_version"] == latest and item["name"].endswith(".html")
            )
            path = store.absolute_path(file["path"])
            intact = path.read_bytes()
            path.write_bytes(b"damaged after commit")
            partial = await client.get(f"/api/runs/{run_id}/deliverables")
            assert partial.status_code == 200, partial.text
            assert any(item["available"] is False for item in partial.json()["items"])
            assert await repo.get_run_status(run_id) == "needs_review"
            good = next(item for item in partial.json()["items"] if item["format"] == "md")
            download = await client.get(
                f"/api/runs/{run_id}/deliverables/{good['name']}?version={latest}"
            )
            assert download.status_code == 200
            assert renders == 1
            path.write_bytes(intact)
        # Read the atomically published file version and reconcile its DB record.
        recovered = await client.get(f"/api/runs/{run_id}/deliverables")
        assert recovered.status_code == 200, recovered.text
        assert recovered.json()["status"] == "pass"
        updated = (await client.get(f"/api/runs/{run_id}")).json()
        assert updated["status"] == "done" and updated["completion"]["issues"] == []
        assert updated["completion"]["content_version"] == recovered.json()["content_version"]
        duplicate = await client.post(f"/api/runs/{run_id}/deliverables/retry", json=payload)
        assert duplicate.status_code == 200
        assert duplicate.json()["content_version"] == recovered.json()["content_version"]
        assert renders == 1 and updated["total_tokens"] == 7
