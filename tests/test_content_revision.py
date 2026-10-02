from __future__ import annotations

import asyncio
from dataclasses import replace

import httpx
import pytest

from deep_research import api
from deep_research.access import ApiCredential, Principal
from deep_research.agents.base import Blackboard
from deep_research.config import Settings
from deep_research.models import Report, ResearchResult, Source
from deep_research.orchestrator import DeepResearchAgent, create_initial_execution
from deep_research.persistence.memory_repository import InMemoryRepository
from deep_research.workbench.content_revision import REVISION_KEY, source_version
from deep_research.workbench.contract import CONTRACT_SCRATCH_KEY, build_contract
from deep_research.workbench.prose_review import ProseReviewer
from deep_research.workbench.templates import AUTO_RESEARCH
from tests.fakes import FakeSearch, verified_finding
from tests.test_prose_edit import Judge

BODY = (
    "## 摘要\n\n变量关系需要在具体研究条件下讨论。\n\n## 分析\n\n"
    + "观测结果支持变量存在相关关系，解释时应保留适用范围，后续还需通过独立研究检验其稳定性。" * 16
    + " [1]。\n\n已经证明因果 [1]。\n\n## 结论\n\n局限需要进一步讨论 [1]。"
)


@pytest.fixture(params=["memory", "sqlite"])
async def service(monkeypatch, tmp_path, request):
    settings = Settings(
        api_key="revision-admin",
        api_credentials=(
            ApiCredential(Principal("alice", "researcher"), "revision-alice"),
            ApiCredential(Principal("bob", "researcher"), "revision-bob"),
            ApiCredential(Principal("reader", "reader"), "revision-reader"),
        ),
        intent_enabled=False,
        orchestration_mode="legacy",
        execution_mode="worker",
        artifact_root=str(tmp_path / "artifacts"),
        quality={"research_min_citations": 1, "max_revisions": 0, "register_check": False},
    )
    engine = None
    if request.param == "sqlite":
        from deep_research.persistence.db import create_all, make_engine, make_sessionmaker
        from deep_research.persistence.sql_repository import SqlRepository

        engine = make_engine(f"sqlite+aiosqlite:///{(tmp_path / 'revision.db').as_posix()}")
        await create_all(engine)
        repo = SqlRepository(make_sessionmaker(engine))
    else:
        repo = InMemoryRepository()
    for name, value in {
        "settings": settings,
        "repo": repo,
        "catalog": None,
        "live": {},
        "tasks": set(),
        "run_tasks": {},
        "cancellation_requested": set(),
        "config_lock": asyncio.Lock(),
        "delivery_cache": {},
        "delivery_pending": {},
        "run_admission": api.RunAdmission(2, 2),
    }.items():
        monkeypatch.setattr(api.app.state, name, value, raising=False)
    monkeypatch.setattr(api, "_run_limiter", api._RateLimiter(1000, 60))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app),
        base_url="http://test",
        headers={"Authorization": "Bearer revision-alice"},
    ) as client:
        yield client, repo, settings
    for task in list(api.app.state.tasks):
        await task
    if engine is not None:
        await engine.dispose()


async def failed_parent(repo, settings):
    results = [ResearchResult(sub_question="变量关系", findings=[verified_finding()])]
    checker = ProseReviewer.research(
        Judge(),
        results,
        {"https://a.com": 1},
        50000,
        query="解释变量关系",
        uncited_sections=("摘要",),
    )
    review = await checker.review(BODY)
    assert review["status"] == "fail"
    execution = create_initial_execution("解释变量关系", "research_quick", settings)
    scratch = execution.checkpoint["scratch"]
    scratch.update(
        {
            CONTRACT_SCRATCH_KEY: build_contract(
                AUTO_RESEARCH,
                "解释变量关系",
                strategy="quick",
                quality=settings.quality,
            ).model_dump(mode="json"),
            "workbench": {"template": "autoResearch", "extras": {"prose_review": review}},
            "prose_review": review,
            "_artifact_run_id": "old-parent-tree",
            "_deadline_at": 1,
            "_runtime_metrics": {"total_tokens": 100000},
            "_catalog_runtime": {"old-catalog": True},
        }
    )
    report = Report(query="解释变量关系", markdown=BODY, citations=["https://a.com"])
    execution.checkpoint = Blackboard(
        query=report.query, results=results, report=report, scratch=scratch
    ).model_dump(mode="json")
    run_id, _ = await repo.create_run_once(
        report.query, request_hash="", execution=execution, owner_id="alice"
    )
    await repo.save_result(run_id, results[0])
    await repo.save_sources(run_id, [Source(url="https://a.com", title="原始资料", content="原文")])
    await repo.save_report(run_id, report)
    await repo.set_status(run_id, "done")
    return await repo.get_run(run_id)


async def test_revision_is_queued_once_with_only_the_writer_and_fresh_runtime(service):
    client, repo, settings = service
    parent = await failed_parent(repo, settings)
    registry = (await client.get(f"/api/runs/{parent.id}/deliverables")).json()
    offer = registry["content_revision"]
    assert offer["available"]
    body = {"source_version": offer["source_version"], "request_id": "same-revision-request"}
    responses = await asyncio.gather(
        *[client.post(f"/api/runs/{parent.id}/revise", json=body) for _ in range(3)]
    )
    assert all(r.status_code == 202 for r in responses), [r.text for r in responses]
    ids = {r.json()["run_id"] for r in responses}
    assert len(ids) == 1 and parent.id not in ids
    child = await repo.get_run(ids.pop())
    scratch = child.orchestration.checkpoint["scratch"]
    assert scratch[REVISION_KEY]["parent_run_id"] == parent.id
    assert "_artifact_run_id" not in scratch and "_catalog_runtime" not in scratch
    assert "_runtime_metrics" not in scratch and scratch["_deadline_at"] > 1
    assert [s["agent"] for s in child.orchestration.definition["steps"]] == ["research_writer"]
    assert child.owner_id == "alice" and child.total_tokens == 0
    assert api.app.state.run_tasks == {}
    claim = await repo.claim_next_run("revision-worker")
    assert claim is not None and claim.run_id == child.id
    assert await repo.claim_next_run("another-worker") is None


@pytest.mark.parametrize("mode", ["legacy", "planner-driven"])
async def test_revision_runs_local_repair_without_retrieval_and_keeps_parent(service, mode):
    client, repo, settings = service
    settings = replace(settings, orchestration_mode=mode)
    api.app.state.settings = settings
    parent = await failed_parent(repo, settings)
    original = parent.orchestration.model_dump_json()
    response = await client.post(
        f"/api/runs/{parent.id}/revise",
        json={
            "source_version": source_version(parent),
            "request_id": "execute-revision-request",
        },
    )
    assert response.status_code == 202, response.text
    claim = await repo.claim_next_run("revision-worker")

    class NoSearch(FakeSearch):
        async def search(self, *args, **kwargs):
            raise AssertionError("Revision must not search again")

    class LocalRepair(Judge):
        async def stream(self, *args, **kwargs):
            raise AssertionError("A bound paragraph failure should use a local edit")
            yield ""  # pragma: no cover

    llm = LocalRepair()
    agent = DeepResearchAgent(
        settings,
        llm=llm,
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
    assert child.status == "done"
    assert "已经证明因果" not in child.report.markdown
    assert "只有相关关系" in child.report.markdown
    assert child.results == parent.results and child.sources == parent.sources
    after = await repo.get_run(parent.id)
    assert after.report.markdown == BODY and after.orchestration.model_dump_json() == original


async def test_revision_rejects_other_users_readonly_stale_and_incomplete_inputs(service):
    client, repo, settings = service
    parent = await failed_parent(repo, settings)
    path = f"/api/runs/{parent.id}/revise"
    body = {"source_version": source_version(parent), "request_id": "guarded-revision-request"}
    for token, expected in [("revision-bob", 404), ("revision-reader", 403)]:
        result = await client.post(path, json=body, headers={"Authorization": f"Bearer {token}"})
        assert result.status_code == expected
    assert (await client.post(path, json={**body, "source_version": "0" * 64})).status_code == 409
    await repo.set_status(parent.id, "running")
    assert (await client.post(path, json=body)).status_code == 409
    await repo.set_status(parent.id, "done")
    api.app.state.settings = replace(settings, daily_run_quota=0)
    assert (await client.post(path, json=body)).status_code == 429


async def test_revision_replay_survives_full_queue_and_conflicting_request_is_rejected(service):
    client, repo, settings = service
    parent = await failed_parent(repo, settings)
    api.app.state.settings = replace(settings, max_active_runs=1, max_queued_runs=0)
    path = f"/api/runs/{parent.id}/revise"
    body = {"source_version": source_version(parent), "request_id": "queue-revision-request"}
    first = await client.post(path, json=body)
    assert first.status_code == 202
    assert (
        await client.post(path, json={**body, "request_id": "new-revision-request"})
    ).status_code == 503
    replay = await client.post(path, json=body)
    assert replay.status_code == 202 and replay.json() == first.json()
    assert (await client.post(path, json={**body, "source_version": "0" * 64})).status_code == 409


async def test_revision_retains_resolved_context_when_the_displayed_request_is_a_followup(service):
    from uuid import uuid4

    client, repo, settings = service
    original = await failed_parent(repo, settings)
    parent_id, _ = await repo.create_run_once(
        "继续",
        request_hash="",
        owner_id="alice",
        execution=original.orchestration.model_copy(update={"id": str(uuid4())}, deep=True),
    )
    await repo.replace_artifacts(
        parent_id, plan=None, reflection_rounds=[], results=original.results, report=original.report
    )
    await repo.set_status(parent_id, "done")
    parent = await repo.get_run(parent_id)
    response = await client.post(
        f"/api/runs/{parent_id}/revise",
        json={
            "source_version": source_version(parent),
            "request_id": "resolved-context-revision",
        },
    )
    assert response.status_code == 202, response.text
    child = await repo.get_run(response.json()["run_id"])
    assert child.query == "继续"
    assert child.orchestration.input["query"] == original.report.query
    assert child.orchestration.checkpoint["query"] == original.report.query
    before = source_version(parent)
    parent.orchestration.checkpoint["query"] = "另一个完整问题"
    assert source_version(parent) != before


async def test_revision_does_not_hide_unfinished_material_processing(service):
    from deep_research.models import ExtractionAudit

    client, repo, settings = service
    parent = await failed_parent(repo, settings)
    parent.results[0].extraction_audit = ExtractionAudit(
        question="变量关系", issues=["extraction_call_failed:TimeoutError"]
    )
    await repo.replace_artifacts(
        parent.id, plan=None, reflection_rounds=[], results=parent.results, report=parent.report
    )
    parent = await repo.get_run(parent.id)
    response = await client.post(
        f"/api/runs/{parent.id}/revise",
        json={
            "source_version": source_version(parent),
            "request_id": "incomplete-revision-request",
        },
    )
    assert response.status_code == 409 and "材料处理" in response.text
    assert await repo.claim_next_run("worker") is None


async def test_unverified_findings_are_not_offered_as_reusable_evidence(service):
    from deep_research.workbench.content_revision import revision_offer
    from deep_research.workbench.gates import GateResult

    _client, repo, settings = service
    parent = await failed_parent(repo, settings)
    parent.results[0].findings[0].verification.semantic_status = "uncertain"
    offer = revision_offer(parent, [GateResult("prose_evidence", "fail")])
    assert not offer["available"] and "材料核验" in offer["reason"]


async def test_data_writer_does_not_turn_lease_loss_into_a_fallback_report():
    from deep_research.agents.base import RunContext
    from deep_research.observability import Tracer
    from deep_research.persistence.repository import LeaseLostError
    from deep_research.workbench.analysis import DataAnalyst
    from deep_research.workbench.templates import DATA_ANALYSIS

    class Lost(Judge):
        async def stream(self, *args, **kwargs):
            raise LeaseLostError("ownership changed")
            yield ""  # pragma: no cover

    contract = build_contract(DATA_ANALYSIS, "分析", attachments_csv="x,y\n1,2\n2,3\n3,5\n")
    bb = Blackboard(query="分析", scratch={CONTRACT_SCRATCH_KEY: contract.model_dump(mode="json")})
    with pytest.raises(LeaseLostError):
        await DataAnalyst().step(
            bb,
            RunContext(llm=Lost(), search_tool=FakeSearch(), tracer=Tracer(), settings=Settings()),
        )
    assert bb.report is None


async def test_inline_revision_uses_shared_admission_and_replays_one_execution(
    service, monkeypatch
):
    client, repo, settings = service
    parent = await failed_parent(repo, settings)
    api.app.state.settings = replace(settings, execution_mode="inline")
    entered, release = asyncio.Event(), asyncio.Event()
    started = []

    async def execute(
        app,
        run_id,
        query,
        current,
        workflow,
        resume_execution,
        lease_owner,
        initial_execution,
        **kwargs,
    ):
        started.append(run_id)
        entered.set()
        await release.wait()
        await repo.set_status(run_id, "done", lease_owner=lease_owner)
        await repo.release_lease(run_id, lease_owner)

    monkeypatch.setattr(api, "_execute", execute)
    body = {"source_version": source_version(parent), "request_id": "inline-revision-request"}
    path = f"/api/runs/{parent.id}/revise"
    first = await client.post(path, json=body)
    assert first.status_code == 202
    await asyncio.wait_for(entered.wait(), 5)
    try:
        second = await client.post(path, json=body)
        assert second.json() == first.json() and len(started) == 1
    finally:
        release.set()
        await asyncio.gather(*list(api.app.state.tasks))


async def test_mindmap_revision_reuses_tree_and_repairs_only_rejected_nodes():
    from deep_research.agents.base import RunContext
    from deep_research.observability import Tracer
    from deep_research.workbench.templates import MINDMAP
    from deep_research.workbench.writers import MindmapWriter, mindmap_to_markdown
    from tests.test_mindmap_edit import setup_review

    llm, model, results, citations, _checker, record = await setup_review()
    settings = Settings(quality={"max_revisions": 0})
    bb = Blackboard(
        query="比较两种方法",
        results=results,
        report=Report(
            query="比较两种方法", markdown=mindmap_to_markdown(model), citations=citations
        ),
        scratch={
            REVISION_KEY: {"parent_run_id": "parent"},
            CONTRACT_SCRATCH_KEY: build_contract(
                MINDMAP, "比较两种方法", quality=settings.quality
            ).model_dump(mode="json"),
            "workbench": {
                "template": "mindmap",
                "extras": {"mindmap": model.model_dump(mode="json"), "node_review": record},
            },
        },
    )
    await MindmapWriter().step(
        bb, RunContext(llm=llm, search_tool=FakeSearch(), tracer=Tracer(), settings=settings)
    )
    assert llm.writes == 0 and [item["unit_id"] for item in llm.edited] == ["0"]
    assert bb.scratch["workbench"]["extras"]["node_review"]["status"] == "pass"


async def test_missing_abstract_is_recovered_from_retained_original_without_mutating_parent(
    service,
):
    import pymupdf

    from deep_research.workbench.attachments import parse_attachment, save_original
    from deep_research.workbench.content_revision import prepare_revision
    from deep_research.workbench.paper_abstract import checked_abstracts
    from deep_research.workbench.templates import PAPER_READ

    _client, repo, settings = service
    parent = await failed_parent(repo, settings)
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((72, 60), "Abstract")
        page.insert_text((72, 90), "A complete abstract on spectral reconstruction.")
        page.insert_text((72, 130), "Introduction")
        page.insert_text((72, 160), "Full paper body.")
        raw = pdf.tobytes()
    attachment = await parse_attachment(raw, "paper.pdf")
    save_original(settings, attachment.id, raw)
    attachment.stored = True
    for chunk in attachment.chunks:
        chunk.section_start = chunk.section_end = False
    scratch = parent.orchestration.checkpoint["scratch"]
    scratch[CONTRACT_SCRATCH_KEY] = build_contract(
        PAPER_READ, parent.query, quality=settings.quality
    ).model_dump(mode="json")
    scratch["attachments"] = [attachment.model_dump(mode="json")]
    snapshot = parent.orchestration.model_dump_json()
    execution, _ = await prepare_revision(parent, settings)
    assert len(checked_abstracts(execution.checkpoint["scratch"])) == 1
    assert parent.orchestration.model_dump_json() == snapshot
