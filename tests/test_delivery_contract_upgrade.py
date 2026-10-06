"""Version conflicts, selective corruption and durable rendering receipts."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import zipfile

import httpx
import pytest

from deep_research.models import Report
from deep_research.report.document import ProseBlock, ReportDocument
from deep_research.report.versioning import document_version, stamp_document
from deep_research.workbench.delivery_store import (
    INDEX,
    build_or_load,
    delivery_store,
    load_version,
    retry_format,
    version_registry,
    workspace_files,
)
from deep_research.workbench.publish import build_bundle
from tests.test_delivery_persistence import detail
from tests.test_workbench import api_repo as api_repo


@pytest.mark.parametrize("damage", ["corrupt", "missing"])
def test_one_damaged_file_keeps_intact_files_and_repairs_to_new_version(tmp_path, damage):
    run = detail()
    original = build_or_load(run, str(tmp_path), None, build_bundle)
    target = next(file for file in original.files if file.format == "html")
    intact = next(file for file in original.files if file.format == "md")
    store, _ = delivery_store(run, str(tmp_path))
    record = next(
        item for item in workspace_files(run, str(tmp_path)) if item["name"] == target.name
    )
    path = store.absolute_path(record["path"])
    if damage == "missing":
        path.unlink()
    else:
        path.write_bytes(b"broken file")
    assert (
        load_version(run, str(tmp_path), original.content_version, name=intact.name).files[0].data
        == intact.data
    )
    registry = version_registry(run, str(tmp_path), original.content_version)
    assert not next(item for item in registry["items"] if item["name"] == target.name)["available"]
    assert next(item for item in registry["items"] if item["name"] == intact.name)["available"]
    with pytest.raises(ValueError):
        load_version(run, str(tmp_path), original.content_version)
    with pytest.raises(ValueError):
        load_version(run, str(tmp_path), original.content_version, name=target.name)
    repaired = retry_format(
        run, str(tmp_path), None, original.content_version, "html", "repair-once"
    )
    assert repaired.parent_version == original.content_version
    assert repaired.content_version != original.content_version
    assert next(file for file in repaired.files if file.name == intact.name).data == intact.data
    assert next(file for file in repaired.files if file.name == target.name).data == target.data
    assert (not path.exists()) if damage == "missing" else path.read_bytes() == b"broken file"
    repeated = retry_format(
        run, str(tmp_path), None, original.content_version, "html", "repair-once"
    )
    assert repeated.content_version == repaired.content_version


@pytest.mark.parametrize("tamper", ["snapshot", "path", "hash", "version"])
def test_selected_download_still_rejects_registry_tampering(tmp_path, tamper):
    run = detail()
    original = build_or_load(run, str(tmp_path), None, build_bundle)
    store, _ = delivery_store(run, str(tmp_path))
    index = store.read_control_json(INDEX)
    registry = index["versions"][original.content_version]
    if tamper == "snapshot":
        registry["_render_context"]["markdown"] = "changed after publication"
    elif tamper == "path":
        registry["items"][-1]["name"] = "../foreign.md"
    elif tamper == "hash":
        registry["items"][-1]["sha256"] = "a" * 64
    else:
        registry["content_version"] = "b" * 64
    store.write_control_json(INDEX, index)
    with pytest.raises(ValueError):
        load_version(run, str(tmp_path), original.content_version, name=original.files[0].name)


def test_document_identity_excludes_stamp_and_offline_bundle_records_identity():
    from deep_research.report.bundle import render_reproducibility_bundle
    from deep_research.report.latex import render_latex
    from deep_research.report.pdf import render_pdf_html

    document = stamp_document(ReportDocument(query="frozen", blocks=[ProseBlock(markdown="old")]))
    version = document.content_version
    assert document_version(document) == version
    document.content_version = "wrong-stamp"
    assert document_version(document) == version
    document.content_version = version
    assert version in render_pdf_html(document)
    assert version in render_latex(document)
    assert document_version(document) == version
    with zipfile.ZipFile(
        io.BytesIO(render_reproducibility_bundle(document, run_id="test"))
    ) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["content_version"] == version
        markdown = archive.read("report.md").decode()
        assert version in markdown and "核验状态" in markdown and "格式范围" in markdown
        for line in archive.read("checksums.sha256").decode().splitlines():
            checksum, name = line.split("  ", 1)
            assert hashlib.sha256(archive.read(name)).hexdigest() == checksum


async def _ready(client, status_url):
    for _ in range(400):
        response = await client.get(status_url)
        assert response.status_code == 200, response.text
        receipt = response.json()
        if receipt["status"] in {"done", "error", "cancelled"}:
            assert receipt["status"] == "done", receipt
            return receipt
        await asyncio.sleep(0.05)
    raise AssertionError("render operation did not finish")


async def test_http_corruption_isolated_but_complete_zip_stays_strict(api_repo, tmp_path):
    api, repo = api_repo
    api.app.state.settings.artifact_root = str(tmp_path)
    run = await repo.create_run("q")
    await repo.save_report(run, Report(query="q", markdown="committed body", citations=[]))
    await repo.set_status(run, "done")
    run_detail = await repo.get_run(run)
    bundle = build_or_load(run_detail, str(tmp_path), None, build_bundle)
    target = next(file for file in bundle.files if file.format == "html")
    intact = next(file for file in bundle.files if file.format == "md")
    store, _ = delivery_store(run_detail, str(tmp_path))
    record = next(
        item for item in workspace_files(run_detail, str(tmp_path)) if item["name"] == target.name
    )
    root = f"/api/runs/{run}"
    query = f"?version={bundle.content_version}"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test"
    ) as client:
        complete = await client.get(root + "/deliverables.zip" + query)
        assert complete.status_code == 200
        with zipfile.ZipFile(io.BytesIO(complete.content)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            for item in manifest["items"]:
                assert hashlib.sha256(archive.read(item["name"])).hexdigest() == item["sha256"]
        store.absolute_path(record["path"]).write_bytes(b"tampered")
        registry = await client.get(root + "/deliverables" + query)
        assert registry.status_code == 200
        assert not next(item for item in registry.json()["items"] if item["name"] == target.name)[
            "available"
        ]
        good = await client.get(root + f"/deliverables/{intact.name}" + query)
        assert good.status_code == 200 and good.content == intact.data
        bad = await client.get(root + f"/deliverables/{target.name}" + query)
        assert bad.status_code == 409
        rejected = await client.get(root + "/deliverables.zip" + query)
        assert rejected.status_code == 409


async def test_export_receipt_pins_old_version_after_new_tab_changes_report(api_repo, tmp_path):
    from deep_research.render_service import service_for

    api, repo = api_repo
    api.app.state.settings.artifact_root = str(tmp_path)
    run = await repo.create_run("q")
    await repo.save_report(run, Report(query="q", markdown="original body", citations=[]))
    await repo.set_status(run, "done")
    service = service_for(repo, api.app.state.settings)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app), base_url="http://test"
        ) as client:
            preview = await client.get(f"/api/runs/{run}/document")
            version = preview.json()["content_version"]
            assert preview.headers["x-content-version"] == version
            body = {
                "kind": "export",
                "format": "md",
                "version": version,
                "request_id": "export-original",
            }
            submitted = await client.post(f"/api/runs/{run}/render-operations", json=body)
            assert submitted.status_code == 202, submitted.text
            initial = submitted.json()
            found = await client.get(
                f"/api/runs/{run}/render-operations?request_id=export-original"
            )
            assert found.json()["operation_id"] == initial["operation_id"]
            duplicate = await client.post(
                f"/api/runs/{run}/render-operations",
                json={**body, "request_id": "export-second-tab"},
            )
            assert duplicate.json()["operation_id"] == initial["operation_id"]
            completed = await _ready(client, initial["status_url"])
            await repo.save_report(
                run, Report(query="q", markdown="new body from another tab", citations=[])
            )
            stale = await client.get(f"/api/runs/{run}/document.md?version={version}")
            assert (
                stale.status_code == 409
                and stale.json()["detail"]["code"] == "document_version_changed"
            )
            for suffix in (
                "", ".csv", ".xlsx", ".pdf", ".tex", ".bib", ".bundle.zip", ".paper.pdf",
            ):
                rejected = await client.get(f"/api/runs/{run}/document{suffix}?version={version}")
                assert rejected.status_code == 409, (suffix, rejected.text)
                assert rejected.json()["detail"]["code"] == "document_version_changed"
            new_preview = await client.get(f"/api/runs/{run}/document")
            assert new_preview.json()["content_version"] != version
            replay = await client.post(f"/api/runs/{run}/render-operations", json=body)
            assert replay.json()["operation_id"] == initial["operation_id"]
            result = await client.get(completed["result_url"])
            assert "original body" in result.text and "new body from another tab" not in result.text
            assert result.headers["x-content-version"] == version
            assert result.headers["x-content-sha256"] == hashlib.sha256(result.content).hexdigest()
            conflict = await client.post(
                f"/api/runs/{run}/render-operations",
                json={**body, "version": new_preview.json()["content_version"]},
            )
            assert conflict.status_code == 409
    finally:
        await service.close()


async def test_receipt_and_alias_survive_sql_repository_restart(api_repo, tmp_path, monkeypatch):
    from deep_research.persistence.db import create_all, make_engine, make_sessionmaker
    from deep_research.persistence.sql_repository import SqlRepository
    from deep_research.render_service import service_for

    api, _ = api_repo
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'receipt.db'}")
    await create_all(engine)
    repo = SqlRepository(make_sessionmaker(engine))
    api.app.state.repo = repo
    api.app.state.settings.artifact_root = str(tmp_path / "files")
    run = await repo.create_run("durable")
    await repo.save_report(run, Report(query="durable", markdown="persisted text", citations=[]))
    service = service_for(repo, api.app.state.settings)
    monkeypatch.setattr(service, "wake", lambda: None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test"
    ) as client:
        preview = (await client.get(f"/api/runs/{run}/document")).json()
        body = {
            "kind": "export",
            "format": "md",
            "version": preview["content_version"],
            "request_id": "survive-restart",
        }
        initial = (await client.post(f"/api/runs/{run}/render-operations", json=body)).json()
        assert initial["status"] == "pending"
        await service.close()
        replacement = SqlRepository(make_sessionmaker(engine))
        api.app.state.repo = replacement
        restored = service_for(replacement, api.app.state.settings)
        try:
            found = await client.get(
                f"/api/runs/{run}/render-operations?request_id=survive-restart"
            )
            assert found.json()["operation_id"] == initial["operation_id"]
            replay = await client.post(f"/api/runs/{run}/render-operations", json=body)
            assert replay.json()["operation_id"] == initial["operation_id"]
            completed = await _ready(client, initial["status_url"])
            result = await client.get(completed["result_url"])
            assert "persisted text" in result.text
            job = await restored.queue.get(initial["operation_id"])
            run_detail = await replacement.get_run(run)
            store, _ = delivery_store(run_detail, api.app.state.settings.artifact_root)
            store.control_path(job.result["path"]).write_bytes(b"corrupt persisted export")
            corrupted = await client.get(completed["result_url"])
            assert corrupted.status_code == 409
            from deep_research.workbench.render_operations import _alias_path

            alias_path = _alias_path(body["request_id"])
            alias = store.read_control_json(alias_path)
            alias["key"] = "a" * 64
            store.write_control_json(alias_path, alias)
            invalid_receipt = await client.get(initial["status_url"])
            assert invalid_receipt.status_code == 409
        finally:
            await restored.close()
            await engine.dispose()


async def test_render_receipt_enforces_owner_and_reader_kind_permissions(api_repo, tmp_path):
    from deep_research.access import ApiCredential, Principal
    from deep_research.render_service import service_for

    api, repo = api_repo
    api.app.state.settings.artifact_root = str(tmp_path)
    api.app.state.settings.api_key = ""
    api.app.state.settings.api_credentials = (
        ApiCredential(Principal("alice", "reader"), "alice-key"),
        ApiCredential(Principal("bob", "reader"), "bob-key"),
    )
    run, _ = await repo.create_run_once("owned", request_hash="", owner_id="alice")
    await repo.save_report(run, Report(query="owned", markdown="alice content", citations=[]))
    await repo.set_status(run, "done")
    headers = {"Authorization": "Bearer alice-key"}
    service = service_for(repo, api.app.state.settings)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app), base_url="http://test"
        ) as client:
            preview = (await client.get(f"/api/runs/{run}/document", headers=headers)).json()
            body = {
                "kind": "export",
                "format": "md",
                "version": preview["content_version"],
                "request_id": "owned-export",
            }
            allowed = await client.post(
                f"/api/runs/{run}/render-operations", json=body, headers=headers
            )
            assert allowed.status_code == 202, allowed.text
            denied = await client.post(
                f"/api/runs/{run}/render-operations",
                json={"kind": "bundle", "request_id": "reader-build"},
                headers=headers,
            )
            assert denied.status_code == 403
            for path in (
                allowed.json()["status_url"],
                f"/api/runs/{run}/render-operations?request_id=owned-export",
            ):
                forbidden = await client.get(path, headers={"Authorization": "Bearer bob-key"})
                assert forbidden.status_code == 404
    finally:
        await service.close()


async def test_export_rejects_mixed_run_snapshot_during_assembly(api_repo, tmp_path, monkeypatch):
    from deep_research.report.service import ReportService

    api, repo = api_repo
    api.app.state.settings.artifact_root = str(tmp_path)
    run = await repo.create_run("race")
    old_report = Report(query="race", markdown="old body", citations=[])
    new_report = Report(query="race", markdown="new body", citations=[])
    await repo.save_report(run, old_report)
    original = ReportService.document
    old_document = await ReportService(repo).document(run)

    async def change_after_assembly(service, run_id, **kwargs):
        document = await original(service, run_id, **kwargs)
        await repo.save_report(run, new_report)
        return document

    monkeypatch.setattr(ReportService, "document", change_after_assembly)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test"
    ) as client:
        response = await client.get(
            f"/api/runs/{run}/document.md?version={old_document.content_version}"
        )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "document_version_changed"
        new_document = await original(ReportService(repo), run)
        await repo.save_report(run, old_report)

        async def change_before_assembly(service, run_id, **kwargs):
            await repo.save_report(run, new_report)
            return await original(service, run_id, **kwargs)

        monkeypatch.setattr(ReportService, "document", change_before_assembly)
        response = await client.post(
            f"/api/runs/{run}/render-operations",
            json={
                "kind": "export",
                "format": "md",
                "version": new_document.content_version,
                "request_id": "mixed-snapshot",
            },
        )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "document_version_changed"


async def test_build_and_repair_receipts_publish_immutable_versions(api_repo, tmp_path):
    from deep_research.render_service import service_for

    api, repo = api_repo
    api.app.state.settings.artifact_root = str(tmp_path)
    run = await repo.create_run("receipt lifecycle")
    await repo.save_report(
        run, Report(query="receipt lifecycle", markdown="frozen body", citations=[])
    )
    await repo.set_status(run, "done")
    service = service_for(repo, api.app.state.settings)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app), base_url="http://test"
        ) as client:
            endpoint = f"/api/runs/{run}/render-operations"
            submitted = await client.post(
                endpoint, json={"kind": "bundle", "request_id": "first-build"}
            )
            assert submitted.status_code == 202, submitted.text
            first = await _ready(client, submitted.json()["status_url"])
            registry = (await client.get(first["result_url"])).json()
            target = next(item for item in registry["items"] if item["format"] == "html")
            run_detail = await repo.get_run(run)
            store, _ = delivery_store(run_detail, str(tmp_path))
            record = next(
                item
                for item in workspace_files(run_detail, str(tmp_path))
                if item["name"] == target["name"]
            )
            path = store.absolute_path(record["path"])
            path.write_bytes(b"broken original")
            body = {
                "kind": "retry",
                "format": "html",
                "version": first["content_version"],
                "request_id": "repair-receipt",
            }
            repair = await client.post(endpoint, json=body)
            assert repair.status_code == 202, repair.text
            duplicate = await client.post(endpoint, json=body)
            assert duplicate.json()["operation_id"] == repair.json()["operation_id"]
            final = await _ready(client, repair.json()["status_url"])
            assert final["content_version"] != first["content_version"]
            assert path.read_bytes() == b"broken original"
            final_registry = (await client.get(final["result_url"])).json()
            assert final_registry["parent_version"] == first["content_version"]
            assert all(item["available"] for item in final_registry["items"])
    finally:
        await service.close()


async def test_duplicate_content_reuses_queue_slot_while_distinct_work_is_bounded(
    api_repo, tmp_path, monkeypatch
):
    from deep_research.render_service import service_for

    api, repo = api_repo
    api.app.state.settings.artifact_root = str(tmp_path)
    run = await repo.create_run("bounded receipts")
    await repo.save_report(run, Report(query="bounded receipts", markdown="body", citations=[]))
    service = service_for(repo, api.app.state.settings)
    monkeypatch.setattr(service, "wake", lambda: None)
    reserve = service.queue.reserve

    async def one_slot(**kwargs):
        return await reserve(**kwargs, max_pending=1)

    monkeypatch.setattr(service.queue, "reserve", one_slot)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app), base_url="http://test"
        ) as client:
            version = (await client.get(f"/api/runs/{run}/document")).json()["content_version"]
            endpoint = f"/api/runs/{run}/render-operations"
            body = {
                "kind": "export",
                "format": "md",
                "version": version,
                "request_id": "bounded-first",
            }
            first = await client.post(endpoint, json=body)
            duplicate = await client.post(
                endpoint, json={**body, "request_id": "bounded-other-tab"}
            )
            assert first.status_code == duplicate.status_code == 202
            assert first.json()["operation_id"] == duplicate.json()["operation_id"]
            denied = await client.post(
                endpoint, json={**body, "format": "tex", "request_id": "bounded-new-format"}
            )
            assert denied.status_code == 503
            assert denied.json()["detail"]["code"] == "render_queue_full"
            assert len(service.queue.jobs) == 1
    finally:
        await service.close()
