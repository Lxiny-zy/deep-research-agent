"""Real HTTP authorization checks using synthetic identities and isolated state."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from deep_research import api
from deep_research.access import ApiCredential, Principal, authenticate, load_api_credentials
from deep_research.config import Settings
from deep_research.persistence.memory_repository import InMemoryRepository

ADMIN = "test-administrator-credential"
ALICE = "test-alice-research-credential"
BOB = "test-bob-research-credential"
READER = "test-readonly-user-credential"


@pytest.fixture
async def access_client(monkeypatch, tmp_path):
    repo = InMemoryRepository()
    settings = Settings(
        api_key=ADMIN,
        api_credentials=(
            ApiCredential(Principal("alice", "researcher"), ALICE),
            ApiCredential(Principal("bob", "researcher"), BOB),
            ApiCredential(Principal("viewer", "reader"), READER),
        ),
        execution_mode="worker",
        artifact_root=str(tmp_path / "artifacts"),
    )
    for name, value in {
        "settings": settings,
        "repo": repo,
        "catalog": None,
        "live": {},
        "tasks": set(),
        "run_tasks": {},
        "cancellation_requested": set(),
        "config_lock": asyncio.Lock(),
    }.items():
        monkeypatch.setattr(api.app.state, name, value, raising=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test"
    ) as client:
        yield client, repo


def _headers(key):
    return {"Authorization": f"Bearer {key}"}


async def _owned_run(repo, owner="alice"):
    run_id, _ = await repo.create_run_once("private research", request_hash="", owner_id=owner)
    await repo.set_status(run_id, "error")
    await repo.set_tags(run_id, [f"private-{owner}"])
    return run_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,suffix",
    [
        ("GET", ""),
        ("GET", "/events"),
        ("GET", "/stream"),
        ("GET", "/document"),
        ("GET", "/document.md"),
        ("GET", "/document.csv"),
        ("GET", "/document.xlsx"),
        ("GET", "/document.pdf"),
        ("GET", "/deliverables"),
        ("GET", "/deliverables/report.pdf?version=" + "a" * 64),
        ("DELETE", ""),
        ("POST", "/cancel"),
        ("POST", "/resume"),
        ("PUT", "/tags"),
    ],
)
async def test_other_owner_cannot_access_any_run_route(access_client, method, suffix):
    client, repo = access_client
    run_id = await _owned_run(repo)
    response = await client.request(
        method, f"/api/runs/{run_id}{suffix}", headers=_headers(BOB), json={"tags": []}
    )
    assert response.status_code == 404
    assert await repo.get_run(run_id) is not None


@pytest.mark.asyncio
async def test_lists_tags_batch_delete_and_admin_visibility(access_client):
    client, repo = access_client
    alice, bob = await _owned_run(repo), await _owned_run(repo, "bob")
    for path in ("/api/runs", "/api/tags"):
        response = await client.get(path, headers=_headers(ALICE))
        assert response.status_code == 200
        assert "private-bob" not in response.text and bob not in response.text
    response = await client.get("/api/runs", headers=_headers(ADMIN))
    assert {row["id"] for row in response.json()} == {alice, bob}
    response = await client.post(
        "/api/runs/batch_delete", headers=_headers(ALICE), json={"ids": [alice, bob]}
    )
    assert response.json() == {"deleted": 1, "deleted_ids": [alice], "skipped": 1}
    assert await repo.get_run(bob) is not None


async def test_versioned_delivery_survives_run_changes_and_reports_missing_files(access_client):
    from deep_research.models import Report
    from deep_research.workbench.delivery_store import (
        build_or_load,
        delivery_store,
        workspace_files,
    )
    from deep_research.workbench.publish import DeliveryBundle, DeliveryFile

    client, repo = access_client
    rid = await _owned_run(repo)
    await repo.save_report(rid, Report(query="q", markdown="saved report", citations=[]))
    detail = await repo.get_run(rid)
    root = api.app.state.settings.artifact_root

    def render(_):
        return DeliveryBundle(
            "test",
            "title",
            [DeliveryFile("report.md", "md", "title", "source", b"saved report")],
            [],
            "pass",
            "2026-10-02",
        )

    saved = build_or_load(detail, root, None, render)
    path = f"/api/runs/{rid}/deliverables/report.md?version={saved.content_version}"
    await repo.set_status(rid, "running")
    response = await client.get(path, headers=_headers(ALICE))
    assert response.status_code == 200 and response.content == b"saved report"
    assert response.headers["x-content-sha256"] == saved.files[0].sha256
    assert (await client.get(path, headers=_headers(BOB))).status_code == 404

    store, _ = delivery_store(detail, root)
    store.absolute_path(workspace_files(detail, root)[0]["path"]).unlink()
    missing = await client.get(path, headers=_headers(ALICE))
    assert missing.status_code == 409 and missing.json()["detail"]["code"] == "delivery_integrity"


@pytest.mark.asyncio
@pytest.mark.parametrize("key", [ALICE, READER])
@pytest.mark.parametrize(
    "method,path,body",
    [
        ("PUT", "/api/config", {"max_rounds": 2}),
        ("POST", "/api/models", {}),
        ("POST", "/api/search-keys", {}),
        ("POST", "/api/workflows/custom", {}),
        ("DELETE", "/api/agents/not-owned", None),
        ("GET", "/metrics", None),
    ],
)
async def test_administration_requires_admin(access_client, key, method, path, body):
    client, _ = access_client
    response = await client.request(method, path, headers=_headers(key), json=body)
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_reader_cannot_mutate_even_own_run_and_config_reports_role(access_client):
    client, repo = access_client
    run_id = await _owned_run(repo, "viewer")
    assert (await client.get(f"/api/runs/{run_id}", headers=_headers(READER))).status_code == 200
    for method, path, body in [
        ("POST", "/api/runs", {"query": "new research"}),
        ("GET", "/api/research?q=new", None),
        ("POST", "/api/intent/assess", {"query": "new research"}),
        ("DELETE", f"/api/runs/{run_id}", None),
        ("PUT", f"/api/runs/{run_id}/tags", {"tags": ["edited"]}),
    ]:
        assert (
            await client.request(method, path, headers=_headers(READER), json=body)
        ).status_code == 403
    response = await client.get("/api/config", headers=_headers(READER))
    assert response.json()["access"] == {"id": "viewer", "role": "reader"}
    assert response.json()["llm_api_key_hint"] == ""


@pytest.mark.asyncio
async def test_idempotency_is_scoped_to_identity_and_replays_when_queue_is_full(access_client):
    client, repo = access_client
    api.app.state.settings.max_active_runs = 1
    api.app.state.settings.max_queued_runs = 1
    request = {"query": "a sufficiently specific research question", "clarified": True}

    async def create(key):
        return await client.post(
            "/api/runs",
            headers={**_headers(key), "Idempotency-Key": "logical-submission"},
            json=request,
        )

    first, other = await create(ALICE), await create(BOB)
    assert first.status_code == other.status_code == 202
    assert first.json()["run_id"] != other.json()["run_id"]
    replay = await create(ALICE)
    assert replay.status_code == 202 and replay.json() == first.json()
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert len(await repo.list_runs()) == 2
    rejected = await client.post(
        "/api/runs", headers=_headers(ALICE), json={**request, "query": "another research"}
    )
    assert rejected.status_code == 503


@pytest.mark.asyncio
async def test_missing_invalid_or_conflicting_keys_fail_closed(access_client):
    client, _ = access_client
    for headers in ({}, _headers("wrong"), {**_headers(ALICE), "X-API-Key": ADMIN}):
        assert (await client.get("/api/runs", headers=headers)).status_code == 401
    assert (await client.get("/api/runs", headers={"X-API-Key": ALICE})).status_code == 200


async def test_known_failed_or_stale_prose_review_blocks_both_pdf_routes(
    access_client, monkeypatch
):
    from deep_research.models import Report
    from deep_research.orchestrator import create_initial_execution
    from deep_research.workbench.prose_review import ProseReviewer
    from tests.test_prose_review import Judge, findings

    client, repo = access_client
    execution = create_initial_execution("解释相关关系", "quick", api.app.state.settings)
    run_id, _ = await repo.create_run_once(
        "解释相关关系", request_hash="", owner_id="alice", execution=execution
    )
    results = findings()
    body = "变量已经证明因果关系 [1]。"
    audit = await ProseReviewer.research(
        Judge(), results, {"https://a.com": 1}, 50000, query="解释相关关系"
    ).review(body)
    execution.checkpoint["scratch"]["prose_review"] = audit
    await repo.save_orchestration(run_id, execution)
    await repo.save_result(run_id, results[0])
    await repo.save_report(
        run_id, Report(query="解释相关关系", markdown=body, citations=["https://a.com"])
    )
    await repo.set_status(run_id, "done")

    def forbidden(*args, **kwargs):
        raise AssertionError("failed report must not reach a PDF renderer")

    monkeypatch.setattr(api, "render_pdf", forbidden)
    monkeypatch.setattr(api, "render_latex_pdf", forbidden)
    for endpoint in ("document.pdf", "document.paper.pdf"):
        response = await client.get(f"/api/runs/{run_id}/{endpoint}", headers=_headers(ALICE))
        assert (
            response.status_code == 409
            and response.json()["detail"]["code"] == "report_support_failed"
        )
    document = (await client.get(f"/api/runs/{run_id}/document", headers=_headers(ALICE))).json()
    assert document["final_validation"]["support_status"] == "fail"
    markdown = await client.get(f"/api/runs/{run_id}/document.md", headers=_headers(ALICE))
    assert markdown.status_code == 200 and "待核验草稿" in markdown.text


def test_credential_revocation_and_invalid_configuration_do_not_echo_keys(monkeypatch):
    credentials = (ApiCredential(Principal("alice", "researcher"), ALICE),)
    assert authenticate(ADMIN, credentials, [ALICE]) == Principal("alice", "researcher")
    assert authenticate(ADMIN, (), [ALICE]) is None
    monkeypatch.setenv("DR_API_KEYS", '[{"id":"alice","role":"admin","key":"short-secret"}]')
    with pytest.raises(ValueError) as error:
        load_api_credentials()
    assert "short-secret" not in str(error.value)
