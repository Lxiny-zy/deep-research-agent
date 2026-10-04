"""Open research follow-ups reuse their own frozen evidence without web retrieval."""

from unittest.mock import AsyncMock

import pytest

from deep_research import api
from deep_research.models import ResearchResult
from deep_research.orchestrator import DeepResearchAgent, create_initial_execution
from deep_research.workbench.contract import CONTRACT_SCRATCH_KEY, build_contract
from deep_research.workbench.templates import get_template
from tests.fakes import FakeSearch
from tests.test_paper_evidence_reuse import SelectionLLM, setup
from tests.test_paper_reader import ALICE, BOB, _headers
from tests.test_paper_reader import reader_client as reader_client


class FollowupModel(SelectionLLM):
    def __init__(self):
        super().__init__()
        self.answer_prompts = []

    async def stream(self, system, user, **kwargs):
        self.answer_prompts.append((system, user))
        yield "发现X [1]。"


async def research_run(repo, settings, *, status="done"):
    query = "研究该方向的关键发现与适用条件"
    template = get_template("autoResearch")
    execution = create_initial_execution(query, template.workflow, settings)
    execution.checkpoint.setdefault("scratch", {})[CONTRACT_SCRATCH_KEY] = build_contract(
        template, query
    ).model_dump(mode="json")
    run_id, _ = await repo.create_run_once(
        query, request_hash="", owner_id="alice", execution=execution
    )
    sources, finding, _, _ = await setup(settings)
    await repo.save_sources(run_id, sources)
    await repo.save_result(run_id, ResearchResult(sub_question=query, findings=[finding]))
    await repo.set_status(run_id, status)
    return run_id


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("status", ["done", "needs_review"])
async def test_research_followup_uses_saved_sources_and_findings(
    reader_client, monkeypatch, stream, status
):
    client, repo, settings = reader_client
    run_id = await research_run(repo, settings, status=status)
    model, search = FollowupModel(), FakeSearch()
    search.search = AsyncMock(side_effect=AssertionError("must not search outside the task"))

    async def build(app, settings, **kwargs):
        return DeepResearchAgent(settings, llm=model, search_tool=search), None

    monkeypatch.setattr(api, "_build_agent", build)
    reader = await client.get(f"/api/runs/{run_id}/reader", headers=_headers(ALICE))
    assert reader.json()["can_ask"] is True
    assert reader.json()["qa_scope"] == "research" and reader.json()["source_count"] == 2
    created = await client.post(
        "/api/qa/conversations", headers=_headers(ALICE), json={"run_id": run_id}
    )
    cid = created.json()["id"]
    response = await client.post(
        f"/api/qa/conversations/{cid}/messages" + ("/stream" if stream else ""),
        headers=_headers(ALICE),
        json={"query": "关键发现有哪些？", "sources": []},
    )
    assert response.status_code == (200 if stream else 201), response.text
    stored = (await client.get(f"/api/qa/conversations/{cid}", headers=_headers(ALICE))).json()
    message = stored["messages"][0]
    assert message["status"] == "done" and message["answer"] == "发现X [1]。"
    assert message["evidence"][0]["origin"] == "research"
    assert model.extractions == 0 and len(model.answer_prompts) == 1
    system, prompt = model.answer_prompts[0]
    assert "本次任务" in system and "本次任务" in prompt
    assert "这是针对一篇论文" not in system
    search.search.assert_not_awaited()


async def test_research_followup_is_owned_and_cannot_use_report_text_as_evidence(
    reader_client, monkeypatch
):
    from deep_research.models import Report

    client, repo, settings = reader_client
    run_id = await research_run(repo, settings)
    hidden = await client.post(
        "/api/qa/conversations", headers=_headers(BOB), json={"run_id": run_id}
    )
    assert hidden.status_code == 404
    detail = await repo.get_run(run_id)
    detail.sources = []
    detail.report = Report(query=detail.query, markdown="未经核验的历史回答")
    # In-memory repository exposes this fixture's run; no external data is used.
    repo._runs[run_id] = detail
    builder = AsyncMock()
    monkeypatch.setattr(api, "_build_agent", builder)
    created = await client.post(
        "/api/qa/conversations", headers=_headers(ALICE), json={"run_id": run_id}
    )
    response = await client.post(
        f"/api/qa/conversations/{created.json()['id']}/messages",
        headers=_headers(ALICE),
        json={"query": "这项结论可靠吗？", "sources": []},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "research_sources_unavailable"
    builder.assert_not_awaited()


async def test_scope_uses_bound_snapshot_and_never_combines_conflicting_versions(reader_client):
    import hashlib

    from deep_research.workbench.qa_scope import task_qa_scope

    _, repo, settings = reader_client
    run_id = await research_run(repo, settings)
    detail = await repo.get_run(run_id)
    original = detail.sources[0]
    changed = original.model_copy(update={"content": original.content + " Changed content."})
    detail.sources.append(changed)
    scope = task_qa_scope(detail)
    assert [source.content for source in scope.sources if source.url == original.url] == [
        original.content
    ]
    conflicting = detail.results[0].findings[0].model_copy(deep=True)
    conflicting.verification.source_content_hash = hashlib.sha256(
        changed.content.encode()
    ).hexdigest()
    detail.results[0].findings.append(conflicting)
    assert not any(source.url == original.url for source in task_qa_scope(detail).sources)


async def test_scope_recovers_frozen_sources_from_extraction_audit(reader_client):
    from deep_research.models import ExtractionAudit
    from deep_research.workbench.qa_scope import task_qa_scope

    _, repo, settings = reader_client
    run_id = await research_run(repo, settings)
    detail = await repo.get_run(run_id)
    detail.results[0].extraction_audit = ExtractionAudit(
        question=detail.query, sources=detail.sources
    )
    detail.sources = []
    assert len(task_qa_scope(detail).sources) == 2


async def test_source_deduplication_never_discards_retraction_metadata(reader_client):
    from deep_research.models import ScholarlyMetadata
    from deep_research.workbench.qa_scope import task_qa_scope

    _, repo, settings = reader_client
    run_id = await research_run(repo, settings)
    detail = await repo.get_run(run_id)
    original = detail.sources[0]
    detail.sources.append(
        original.model_copy(update={"scholarly": ScholarlyMetadata(retracted=True)})
    )
    selected = next(
        source for source in task_qa_scope(detail).sources if source.url == original.url
    )
    assert selected.scholarly is not None and selected.scholarly.retracted is True


@pytest.mark.parametrize("status", ["pending", "running", "failed", "cancelled"])
async def test_unready_research_is_blocked_before_model_creation(
    reader_client, monkeypatch, status
):
    client, repo, settings = reader_client
    run_id = await research_run(repo, settings, status=status)
    builder = AsyncMock()
    monkeypatch.setattr(api, "_build_agent", builder)
    created = await client.post(
        "/api/qa/conversations", headers=_headers(ALICE), json={"run_id": run_id}
    )
    response = await client.post(
        f"/api/qa/conversations/{created.json()['id']}/messages",
        headers=_headers(ALICE),
        json={"query": "关键发现是什么？", "sources": []},
    )
    assert response.status_code == 409
    builder.assert_not_awaited()
