"""Manual acceptance binds explicit selections to immutable report identities."""

import asyncio
import hashlib

import httpx
import pytest

from deep_research.models import Report, ResearchResult, Source
from deep_research.workbench.acceptance_context import build_context
from deep_research.workbench.delivery_store import delivery_store
from tests.fakes import verified_finding
from tests.test_workbench import _execution
from tests.test_workbench import api_repo as api_repo


async def seed(api, repo, tmp_path, *, owner="local"):
    api.app.state.settings.artifact_root = str(tmp_path)
    query = "请说明方法和局限"
    _, execution = _execution(query, "autoResearch", api.app.state.settings)
    run, _ = await repo.create_run_once(query, execution=execution, request_hash="", owner_id=owner)
    await repo.save_report(
        run,
        Report(
            query=query,
            markdown=("## 方法\n\nAlpha scored 95 [1]。\n\n## 私人补充\n\nUNSELECTED_PRIVATE_BODY"),
            citations=["https://example.org/paper"],
        ),
    )
    finding = verified_finding("Alpha scored 95", "https://example.org/paper", "Alpha scored 95")
    source = Source(
        url="https://example.org/paper",
        title="Private manuscript",
        content="UNSELECTED_PRIVATE_SOURCE\nAlpha scored 95",
        locator="page 2, paragraph 3",
    )
    source.content_hash = hashlib.sha256(source.content.encode()).hexdigest()
    finding.verification.source_content_hash = source.content_hash
    await repo.save_result(run, ResearchResult(sub_question="方法", findings=[finding]))
    await repo.save_sources(run, [source])
    await repo.set_status(run, "needs_review")
    return run


async def context(client, run, headers=None):
    document = await client.get(f"/api/runs/{run}/document", headers=headers)
    assert document.status_code == 200, document.text
    version = document.json()["content_version"]
    response = await client.get(
        f"/api/runs/{run}/acceptance/context?version={version}", headers=headers
    )
    assert response.status_code == 200, response.text
    return response.json()


def selection(snapshot, request_id="manual-selection"):
    location = next(
        row for row in snapshot["locations"] if row["kind"] == "prose" and "Alpha" in row["preview"]
    )
    evidence = snapshot["evidence"][0]
    return {
        "request_id": request_id,
        "document_version": snapshot["document_version"],
        "issues": [
            {
                "location_id": location["id"],
                "excerpt": "Alpha scored 95",
                "source_ids": evidence["source_ids"],
                "category": "correctness",
                "observation": "请人工检查该结论与原文的对应",
                "evidence_selections": [
                    {"evidence_id": evidence["id"], "excerpt": "Alpha scored 95"}
                ],
            }
        ],
    }


async def test_template_context_locations_and_minimal_package_are_wired(api_repo, tmp_path):
    api, repo = api_repo
    run = await seed(api, repo, tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test"
    ) as client:
        template = await client.get(f"/api/runs/{run}/acceptance/template")
        assert template.status_code == 200
        assert template.json()["example"]["conclusion"] == "pending"
        snapshot = await context(client, run)
        assert snapshot["requirements"]
        assert snapshot["material_status"] == "material_available"
        assert "_texts" not in snapshot and "_evidence_texts" not in snapshot
        body = selection(snapshot)
        issue = body["issues"][0]
        located = await client.get(
            f"/api/runs/{run}/acceptance/locations/{issue['location_id']}?version={snapshot['document_version']}"
        )
        assert "Alpha scored 95" in located.json()["excerpt"]
        evidence_id = issue["evidence_selections"][0]["evidence_id"]
        original = await client.get(
            f"/api/runs/{run}/acceptance/evidence/{evidence_id}?version={snapshot['document_version']}"
        )
        assert original.json()["excerpt"] == "Alpha scored 95"
        submitted = await client.post(f"/api/runs/{run}/acceptance/records", json=body)
        assert submitted.status_code == 201, submitted.text
        record = submitted.json()
        assert record["conclusion"] == "pending"
        assert record["issues"][0]["next_work"] == ["N1"]
        assert record["application"]["package_code_sha256"]
        package = await client.get(f"/api/runs/{run}/acceptance/records/{record['id']}/package")
        assert package.status_code == 200
        assert package.headers["x-content-version"] == snapshot["document_version"]
        assert package.headers["x-content-sha256"] == hashlib.sha256(package.content).hexdigest()
        assert "UNSELECTED_PRIVATE" not in package.text
        assert "Private manuscript" not in package.text
        assert package.json()["issues"][0]["sources"][0]["locator"] == "page 2, paragraph 3"


async def test_duplicate_request_replays_old_record_after_document_changed(api_repo, tmp_path):
    api, repo = api_repo
    run = await seed(api, repo, tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test"
    ) as client:
        snapshot = await context(client, run)
        body = selection(snapshot)
        endpoint = f"/api/runs/{run}/acceptance/records"
        responses = await asyncio.gather(*(client.post(endpoint, json=body) for _ in range(4)))
        assert all(response.status_code == 201 for response in responses), [
            r.text for r in responses
        ]
        original = responses[0].json()
        assert len({response.json()["id"] for response in responses}) == 1
        await repo.save_report(
            run, Report(query="changed", markdown="NEW PRIVATE DOCUMENT", citations=[])
        )
        replay = await client.post(endpoint, json=body)
        assert replay.json() == original
        old_context = await client.get(
            f"/api/runs/{run}/acceptance/context?version={snapshot['document_version']}"
        )
        assert old_context.status_code == 409
        stale = await client.post(endpoint, json={**body, "request_id": "new-old-version"})
        assert stale.status_code == 409
        conflict = await client.post(endpoint, json={**body, "note": "changed parameters"})
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "acceptance_request_conflict"
        package = await client.get(endpoint + f"/{original['id']}/package")
        assert package.json() == original
        assert "NEW PRIVATE DOCUMENT" not in package.text


async def test_recovery_does_not_overwrite_first_result(api_repo, tmp_path):
    api, repo = api_repo
    run = await seed(api, repo, tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test"
    ) as client:
        first_context = await context(client, run)
        body = {
            **selection(first_context),
            "conclusion": "fail",
            "first_attempt_status": "needs_review",
        }
        endpoint = f"/api/runs/{run}/acceptance/records"
        first = (await client.post(endpoint, json=body)).json()
        await repo.set_status(run, "done")
        recovery_context = await context(client, run)
        resumed = await client.post(
            endpoint,
            json={
                **selection(recovery_context, "manual-recovery"),
                "phase": "recovery",
                "parent_record_id": first["id"],
                "conclusion": "pass",
            },
        )
        assert resumed.status_code == 201, resumed.text
        second = resumed.json()
        assert second["initial_status"] == "needs_review" and second["recovery_status"] == "done"
        assert second["initial_record_id"] == first["id"]
        assert second["first_attempt_status"] == "needs_review"
        conflict = await client.post(
            endpoint,
            json={
                **selection(recovery_context, "rewrite-initial"),
                "phase": "recovery",
                "parent_record_id": first["id"],
                "first_attempt_status": "done",
            },
        )
        assert conflict.status_code == 409
        unchanged = await client.get(endpoint + "/" + first["id"])
        assert unchanged.json() == first
        listed = (await client.get(endpoint)).json()
        assert {row["conclusion"] for row in listed["items"]} == {"fail", "pass"}


@pytest.mark.parametrize("change", ["location", "excerpt", "source", "evidence", "extra"])
async def test_out_of_scope_selections_and_unbounded_payloads_are_rejected(
    api_repo, tmp_path, change
):
    api, repo = api_repo
    run = await seed(api, repo, tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test"
    ) as client:
        snapshot = await context(client, run)
        body = selection(snapshot)
        issue = body["issues"][0]
        if change == "location":
            issue["location_id"] = "a" * 64
        elif change == "excerpt":
            issue["excerpt"] = "not present in selected document"
        elif change == "source":
            issue["source_ids"] = ["a" * 64]
        elif change == "evidence":
            issue["evidence_selections"][0]["excerpt"] = "not a source quotation"
        else:
            body["full_conversation"] = "this field is not allowed"
        rejected = await client.post(f"/api/runs/{run}/acceptance/records", json=body)
        assert rejected.status_code == (422 if change == "extra" else 409), rejected.text


async def test_same_run_storage_survives_sql_repository_restart_and_detects_corruption(
    api_repo, tmp_path
):
    from deep_research.persistence.db import create_all, make_engine, make_sessionmaker
    from deep_research.persistence.sql_repository import SqlRepository

    api, _ = api_repo
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'acceptance.db'}")
    await create_all(engine)
    repo = SqlRepository(make_sessionmaker(engine))
    api.app.state.repo = repo
    try:
        run = await seed(api, repo, tmp_path / "files")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app), base_url="http://test"
        ) as client:
            snapshot = await context(client, run)
            record = (
                await client.post(f"/api/runs/{run}/acceptance/records", json=selection(snapshot))
            ).json()
            api.app.state.repo = SqlRepository(make_sessionmaker(engine))
            restored = await client.get(f"/api/runs/{run}/acceptance/records/{record['id']}")
            assert restored.json() == record
            detail = await repo.get_run(run)
            store, _ = delivery_store(detail, api.app.state.settings.artifact_root)
            path = f"acceptance/records/{record['id']}.json"
            changed = store.read_control_json(path)
            changed["document_version"] = "f" * 64
            store.write_control_json(path, changed)
            rejected = await client.get(
                f"/api/runs/{run}/acceptance/records/{record['id']}/package"
            )
            assert rejected.status_code == 409
    finally:
        await engine.dispose()


async def test_owner_isolation_and_credentials_are_not_exported(api_repo, tmp_path):
    from deep_research.access import ApiCredential, Principal

    api, repo = api_repo
    run = await seed(api, repo, tmp_path, owner="alice")
    api.app.state.settings.api_key = ""
    api.app.state.settings.api_credentials = (
        ApiCredential(Principal("alice", "researcher"), "alice-test-credential"),
        ApiCredential(Principal("bob", "researcher"), "bob-test-credential"),
    )
    headers = {"Authorization": "Bearer alice-test-credential"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test"
    ) as client:
        snapshot = await context(client, run, headers)
        body = selection(snapshot)
        body["note"] = (
            "Authorization: Bearer alice-test-credential password=hidden api_key=hidden-key"
        )
        response = await client.post(
            f"/api/runs/{run}/acceptance/records", json=body, headers=headers
        )
        assert response.status_code == 201, response.text
        record = response.json()
        package_url = f"/api/runs/{run}/acceptance/records/{record['id']}/package"
        package = await client.get(package_url, headers=headers)
        assert "alice-test-credential" not in package.text and "hidden-key" not in package.text
        assert "[REDACTED]" in package.text
        denied = await client.get(
            package_url, headers={"Authorization": "Bearer bob-test-credential"}
        )
        assert denied.status_code == 404


async def test_coverage_mapping_reuses_frozen_review_and_rejects_stale_mapping(api_repo, tmp_path):
    from deep_research.report.service import ReportService
    from deep_research.workbench.contract import contract_from_scratch
    from deep_research.workbench.coverage_review import coverage_hash, effective_contract, regions

    api, repo = api_repo
    run = await seed(api, repo, tmp_path)
    detail = await repo.get_run(run)
    scratch = detail.orchestration.checkpoint["scratch"]
    contract = effective_contract(contract_from_scratch(scratch), scratch)
    region = next(
        row
        for row in regions(detail.report.markdown)
        if row["kind"] == "paragraph" and "Alpha" in row["text"]
    )
    coverage = {
        "input_hash": coverage_hash(contract, detail.report.markdown, []),
        "decisions": [
            {
                "requirement_id": item.id,
                "status": "covered" if index == 0 else "missing",
                "locations": [{"region_id": region["id"], "quote": "Alpha scored 95"}]
                if index == 0
                else [],
                "basis_ids": [],
                "reason": "controlled coverage fixture",
            }
            for index, item in enumerate(contract.requested_items)
        ],
    }
    scratch["prose_review"] = {"requirements_review": coverage}
    await repo.save_orchestration(run, detail.orchestration)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test"
    ) as client:
        snapshot = await context(client, run)
        covered = snapshot["requirements"][0]
        assert snapshot["coverage_review_bound"]
        location = next(
            row for row in snapshot["locations"] if row["id"] == covered["location_ids"][0]
        )
        assert location["source_ids"] and location["evidence_ids"]
        assert any(row["gap_kind"] == "writing_omission" for row in snapshot["requirements"])
        assert snapshot["human_conclusion"] == "pending"
        await repo.save_report(
            run, Report(query="new", markdown="Completely new body", citations=[])
        )
        changed = await context(client, run)
        assert not changed["coverage_review_bound"]
        assert all(
            row["status"] == "not_checked" and not row["location_ids"]
            for row in changed["requirements"]
        )
    assert (await ReportService(repo).document(run)).content_version == changed["document_version"]


async def test_structured_locations_keep_cell_citations_and_chart_column_scope(api_repo, tmp_path):
    from deep_research.report.document import (
        ChartBlock,
        EvidenceRecord,
        TableBlock,
        TableCell,
        TableRow,
    )
    from deep_research.report.service import ReportService
    from deep_research.report.versioning import stamp_document

    api, repo = api_repo
    run = await seed(api, repo, tmp_path)
    document = await ReportService(repo).document(run)
    document.evidence.append(
        EvidenceRecord(
            citation=2,
            support_id="other-evidence",
            source_url="https://example.org/other",
            quote="not graphed",
        )
    )
    document.blocks = [
        TableBlock(
            id="data",
            title="Results",
            rows=[
                TableRow(
                    label="Alpha",
                    citation=1,
                    cells={
                        "score": TableCell(value="95", numeric=95),
                        "unused": TableCell(value="3", numeric=3, citations=[2]),
                    },
                )
            ],
        ),
        ChartBlock(id="plot", source_table="data", value_columns=["score"]),
    ]
    stamp_document(document)
    result = build_context(await repo.get_run(run), document)
    cell = next(
        row
        for row in result["locations"]
        if row["kind"] == "table_cell" and row["pointer"]["column"] == "score"
    )
    chart = next(row for row in result["locations"] if row["kind"] == "chart")
    assert cell["citations"] == chart["citations"] == [1]
    assert chart["pointer"]["source_table"] == "data"
    assert "other-evidence" not in chart["evidence_ids"]


async def test_evidence_does_not_borrow_same_url_from_another_snapshot(api_repo, tmp_path):
    from deep_research.report.service import ReportService

    api, repo = api_repo
    run = await seed(api, repo, tmp_path)
    detail = await repo.get_run(run)
    original = detail.sources[0]
    different = original.model_copy(
        update={"content": "different edition", "content_hash": "f" * 64, "locator": "wrong page"}
    )
    detail.sources.append(different)
    document = await ReportService(repo).document(run)
    result = build_context(detail, document)
    referenced = result["evidence"][0]["source_ids"]
    assert len(referenced) == 1
    assert (
        next(row for row in result["sources"] if row["id"] == referenced[0])["locator"]
        == original.locator
    )


async def test_material_absence_is_distinct_from_unread_material(api_repo, tmp_path):
    from deep_research.report.service import ReportService

    api, repo = api_repo
    api.app.state.settings.artifact_root = str(tmp_path)
    run = await repo.create_run("no material")
    document = await ReportService(repo).document(run)
    detail = await repo.get_run(run)
    assert build_context(detail, document)["material_status"] == "no_material"
    detail.sources = [Source(url="https://example.org/unread", content="")]
    assert build_context(detail, document)["material_status"] == "material_unread"


async def test_selected_delivery_must_share_the_documents_input_snapshot(api_repo, tmp_path):
    from deep_research.workbench.delivery_store import build_or_load
    from deep_research.workbench.publish import DeliveryBundle, DeliveryFile

    api, repo = api_repo
    run = await seed(api, repo, tmp_path)
    detail = await repo.get_run(run)
    bundle = build_or_load(
        detail,
        str(tmp_path),
        None,
        lambda value: DeliveryBundle(
            "test",
            "title",
            [DeliveryFile("report.md", "md", "source", "source", value.report.markdown.encode())],
            [],
            "pass",
            "2026-10-06T00:00:00Z",
        ),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test"
    ) as client:
        old = await context(client, run)
        endpoint = f"/api/runs/{run}/acceptance/records"
        saved = await client.post(
            endpoint, json={**selection(old), "delivery_version": bundle.content_version}
        )
        assert saved.status_code == 201, saved.text
        await repo.save_report(run, Report(query="changed", markdown="another body", citations=[]))
        current = await context(client, run)
        rejected = await client.post(
            endpoint,
            json={
                "request_id": "mismatched-delivery",
                "document_version": current["document_version"],
                "delivery_version": bundle.content_version,
            },
        )
        assert rejected.status_code == 409
        assert rejected.json()["detail"]["code"] == "acceptance_delivery_mismatch"
