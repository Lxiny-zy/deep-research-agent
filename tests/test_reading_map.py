"""Paginated, version-bound reading views over real stored review bindings."""

import hashlib

import httpx

from deep_research.models import Report, ResearchResult, Source
from deep_research.workbench.prose_review import reviewer_for_report
from tests.fakes import FakeLLM, verified_finding
from tests.test_workbench import _execution
from tests.test_workbench import api_repo as api_repo


async def seed_map(api, repo, *, bound=True):
    query = "说明Alpha结果"
    _, execution = _execution(query, "autoResearch", api.app.state.settings)
    run = await repo.create_run(query, execution=execution)
    quote = "Alpha scored 95"
    text = "First experiment. " + quote + ". Second experiment. " + quote + "."
    source = Source(url="https://example.org/paper", content=text, locator="page 2, paragraph 3")
    source.content_hash = hashlib.sha256(text.encode()).hexdigest()
    finding = verified_finding(quote, source.url, quote)
    finding.verification.source_content_hash = source.content_hash
    await repo.save_report(
        run,
        Report(
            query=query,
            markdown="## 方法\n\nAlpha scored 95 [1]。\n\n## 后续\nUNSELECTED_BODY",
            citations=[source.url],
        ),
    )
    await repo.save_result(run, ResearchResult(sub_question="结果", findings=[finding]))
    await repo.save_sources(
        run, [source, source.model_copy(update={"url": "https://example.org/other"})]
    )
    if bound:
        detail = await repo.get_run(run)
        scratch = detail.orchestration.checkpoint["scratch"]
        checker = reviewer_for_report(
            FakeLLM(),
            query,
            detail.results,
            detail.report.citations,
            scratch,
            50000,
            sources=detail.sources,
        )
        scratch["prose_review"] = await checker.review(detail.report.markdown)
        await repo.save_orchestration(run, detail.orchestration)
    return run, source


async def test_map_never_turns_unreviewed_citations_into_bound_anchors(api_repo):
    api, repo = api_repo
    run, _ = await seed_map(api, repo, bound=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test"
    ) as client:
        document = (await client.get(f"/api/runs/{run}/document")).json()
        base = f"/api/runs/{run}/acceptance/reading-map"
        page = await client.get(base, params={"version": document["content_version"], "limit": 1})
        assert page.status_code == 200, page.text
        payload = page.json()
        assert payload["review_bound"] is False and len(payload["units"]) == 1
        assert "UNSELECTED_BODY" not in page.text and "First experiment" not in page.text
        unit = await client.get(
            base + "/units/" + payload["units"][0]["id"],
            params={"version": document["content_version"]},
        )
        assert unit.status_code == 200 and unit.json()["anchor_total"] == 0


async def test_repeated_quotes_are_distinct_paginated_text_anchors_not_pdf_boxes(api_repo):
    api, repo = api_repo
    run, source = await seed_map(api, repo)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test"
    ) as client:
        document = (await client.get(f"/api/runs/{run}/document")).json()
        version = document["content_version"]
        base = f"/api/runs/{run}/acceptance/reading-map"
        page = (await client.get(base, params={"version": version})).json()
        assert page["review_bound"]
        picked = next(unit for unit in page["units"] if "Alpha" in unit["preview"])
        first = await client.get(
            base + "/units/" + picked["id"], params={"version": version, "anchor_limit": 1}
        )
        assert first.status_code == 200, first.text
        data = first.json()
        assert data["anchor_total"] == 2 and len(data["anchors"]) == 1
        assert data["evidence_status"][0]["status"] == "ambiguous"
        anchor = data["anchors"][0]
        assert anchor["coordinate_system"] == "source_text_characters_not_pdf_geometry"
        assert source.content[anchor["start"] : anchor["end"]] == anchor["quote"]
        assert anchor["source_url"] == source.url
        assert anchor["document_id"] is None and not anchor["pdf_available"]
        second = (
            await client.get(
                base + "/units/" + picked["id"],
                params={"version": version, "anchor_offset": 1, "anchor_limit": 1},
            )
        ).json()
        assert second["anchors"][0]["id"] != anchor["id"]
        assert "Second experiment" in second["anchors"][0]["context_before"]
        bad_limit = await client.get(base, params={"version": version, "limit": 1000})
        assert bad_limit.status_code == 422
        await repo.save_report(run, Report(query="changed", markdown="新版", citations=[]))
        stale = await client.get(base, params={"version": version})
        assert stale.status_code == 409


async def test_hsi_variant_and_unknown_location_are_explicit(api_repo):
    api, repo = api_repo
    run, _ = await seed_map(api, repo)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test"
    ) as client:
        document = (
            await client.get(f"/api/runs/{run}/document", params={"include_hsi_tables": True})
        ).json()
        base = f"/api/runs/{run}/acceptance/reading-map"
        page = await client.get(
            base, params={"version": document["content_version"], "include_hsi_tables": True}
        )
        assert page.status_code == 200, page.text
        missing = await client.get(
            base + "/units/missing",
            params={"version": document["content_version"], "include_hsi_tables": True},
        )
        assert missing.status_code == 404


async def test_bound_fulltext_counterexample_is_separate_from_support_anchors(api_repo, tmp_path):
    from frontend.tests.browser.reading_fixture import seed_counter_fixture

    api, repo = api_repo
    api.app.state.settings.artifact_root = str(tmp_path)
    metadata = await seed_counter_fixture(api, api.app)
    run = metadata["reading_counter_run_id"]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test"
    ) as client:
        document = (await client.get(f"/api/runs/{run}/document")).json()
        version = document["content_version"]
        base = f"/api/runs/{run}/acceptance/reading-map"
        page = (await client.get(base, params={"version": version})).json()
        assert page["review_bound"]
        unit = next(item for item in page["units"] if "未给出" in item["preview"])
        response = await client.get(base + "/units/" + unit["id"], params={"version": version})
        assert response.status_code == 200, response.text
        value = response.json()
        assert value["unit"]["verification_status"] == "unsupported"
        assert value["anchors"] == []
        assert value["fulltext_total"] == 1
        passage = value["fulltext_passages"][0]
        assert (
            passage["verdict"] == "refutes"
            and passage["quote"] == metadata["reading_counter_quote"]
        )
        assert passage["document_id"] == metadata["reading_counter_document_id"]
        assert passage["pdf_available"]
