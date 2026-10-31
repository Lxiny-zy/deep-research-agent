"""用本结果继续：调研 / 综述的已核验证据直接生成幻灯片与思维导图，不重新检索。"""

from __future__ import annotations

from copy import deepcopy

from deep_research.orchestrator import DeepResearchAgent
from deep_research.workbench.content_revision import REVISION_KEY, source_version
from deep_research.workbench.contract import CONTRACT_SCRATCH_KEY, build_contract
from deep_research.workbench.derivation import DERIVATION_KEY, derivation_offer
from deep_research.workbench.templates import get_template
from tests.fakes import FakeLLM, FakeSearch
from tests.test_content_revision import failed_parent, service  # noqa: F401  （夹具）


def _derive(client, parent, mode: str, request_id: str, **headers):  # type: ignore[no-untyped-def]
    return client.post(
        f"/api/runs/{parent.id}/revise",
        json={"source_version": source_version(parent), "request_id": request_id, "mode": mode},
        headers=headers or None,
    )


def _with_contract(detail, template_key: str, strategy: str):  # type: ignore[no-untyped-def]
    copy = deepcopy(detail)
    template = get_template(template_key)
    assert template is not None
    copy.orchestration.checkpoint["scratch"][CONTRACT_SCRATCH_KEY] = build_contract(
        template, "快照光谱成像", strategy=strategy
    ).model_dump(mode="json")
    return copy


async def test_research_results_seed_slides_and_mindmap_tasks(service):  # noqa: F811
    client, repo, settings = service
    parent = await failed_parent(repo, settings)
    offer = (await client.get(f"/api/runs/{parent.id}/deliverables")).json()["derivation"]
    assert offer["available"] and offer["targets"] == ["slides", "mindmap"]
    for mode, writer in (("slides", "slide_writer"), ("mindmap", "mindmap_writer")):
        response = await _derive(client, parent, mode, f"derive-{mode}-request")
        assert response.status_code == 202, response.text
        child = await repo.get_run(response.json()["run_id"])
        scratch = child.orchestration.checkpoint["scratch"]
        assert scratch[DERIVATION_KEY]["parent_run_id"] == parent.id
        assert scratch[DERIVATION_KEY]["from_template"] == "autoResearch"
        assert scratch[CONTRACT_SCRATCH_KEY]["template"] == mode
        assert scratch[CONTRACT_SCRATCH_KEY]["strategy"] == "quick"
        # 不是修订：不带原任务的正文、审核记录与修订标记
        assert REVISION_KEY not in scratch and "prose_review" not in scratch
        assert child.orchestration.checkpoint["report"] is None
        assert [s["agent"] for s in child.orchestration.definition["steps"]] == [writer]
        assert len(child.orchestration.checkpoint["results"]) == len(parent.results)
        assert child.owner_id == "alice"
    replay = await _derive(client, parent, "slides", "derive-slides-request")
    assert replay.status_code == 202 and replay.headers.get("Idempotency-Replayed") == "true"
    assert (await _derive(client, parent, "mindmap", "derive-slides-request")).status_code == 409


async def test_derived_slides_cite_parent_evidence_without_searching(service):  # noqa: F811
    client, repo, settings = service
    parent = await failed_parent(repo, settings)
    original = parent.orchestration.model_dump_json()
    response = await _derive(client, parent, "slides", "run-derived-slides")
    assert response.status_code == 202, response.text
    claim = await repo.claim_next_run("derive-worker")
    assert claim is not None

    class NoSearch(FakeSearch):
        async def search(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("派生任务不得重新检索")

    agent = DeepResearchAgent(
        settings,
        llm=FakeLLM(),
        search_tool=NoSearch(),
        repo=repo,
        run_id=claim.run_id,
        workflow=claim.execution.workflow_name,
        initial_execution=claim.execution,
        lease_owner=claim.lease_owner,
    )
    try:
        await agent.run(parent.query)
    finally:
        await agent.aclose()
    child = await repo.get_run(claim.run_id)
    assert child.status in {"done", "needs_review"}
    assert child.sources == parent.sources
    deck = child.orchestration.checkpoint["scratch"]["workbench"]["extras"]["deck"]
    assert deck["slides"] and all(slide["citations"] == [1] for slide in deck["slides"])
    after = await repo.get_run(parent.id)
    assert after.orchestration.model_dump_json() == original


async def test_derivation_needs_finished_open_research_with_verified_evidence(service):  # noqa: F811
    client, repo, settings = service
    parent = await failed_parent(repo, settings)
    assert derivation_offer(_with_contract(parent, "litReview", "deep"))["available"]
    closed = derivation_offer(_with_contract(parent, "litReview", "none"))
    assert not closed["available"] and "指定材料" in closed["reason"]
    reading = derivation_offer(_with_contract(parent, "paperRead", "none"))
    assert not reading["available"] and reading["targets"] == []
    empty = deepcopy(parent)
    empty.results = []
    assert "核验" in derivation_offer(empty)["reason"]
    legacy = deepcopy(parent)
    del legacy.orchestration.checkpoint["scratch"][CONTRACT_SCRATCH_KEY]
    assert "缺少任务信息" in derivation_offer(legacy)["reason"]
    for token, expected in [("revision-bob", 404), ("revision-reader", 403)]:
        result = await _derive(
            client, parent, "slides", f"derive-{token}", Authorization=f"Bearer {token}"
        )
        assert result.status_code == expected
    await repo.set_status(parent.id, "running")
    running = await repo.get_run(parent.id)
    assert derivation_offer(running)["targets"] == []
    assert (await _derive(client, running, "slides", "derive-running")).status_code == 409
