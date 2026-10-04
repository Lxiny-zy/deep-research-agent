"""Content warnings can request revision independently of file rendering failures."""

from __future__ import annotations

import pytest

from deep_research.config import Settings
from deep_research.models import Report, ResearchResult
from deep_research.orchestrator import create_initial_execution
from deep_research.persistence.repository import RunDetail
from deep_research.workbench.content_revision import revision_offer, source_version
from deep_research.workbench.contract import CONTRACT_SCRATCH_KEY, build_contract
from deep_research.workbench.gates import GateResult, citation_gate
from deep_research.workbench.templates import AUTO_RESEARCH
from tests.fakes import verified_finding
from tests.test_content_revision import failed_parent
from tests.test_content_revision import service as service


def _detail(status="needs_review"):
    query = "Review a draft"
    execution = create_initial_execution(query, "research_quick", Settings())
    execution.checkpoint["scratch"][CONTRACT_SCRATCH_KEY] = build_contract(
        AUTO_RESEARCH, query
    ).model_dump(mode="json")
    return RunDetail(
        id="draft",
        query=query,
        status=status,
        report=Report(
            query=query, markdown="## Findings\nA result [1].", citations=["https://a.com"]
        ),
        results=[ResearchResult(sub_question=query, findings=[verified_finding()])],
        orchestration=execution,
    )


@pytest.mark.parametrize("status", ["done", "needs_review"])
def test_citation_count_warning_offers_revision_for_completed_drafts(status):
    detail = _detail(status)
    detail.report.citations.append("https://b.com")
    detail.results[0].findings.append(verified_finding(source_url="https://b.com"))
    gate = citation_gate(detail.report.markdown, detail.report.citations, AUTO_RESEARCH, 2)
    assert gate.name == "citation" and gate.status == "warn"
    assert revision_offer(detail, [gate])["available"]


@pytest.mark.parametrize("gate", ["structure", "length", "scholarly", "markdown", "review"])
def test_content_warnings_are_revisable(gate):
    assert revision_offer(_detail(), [GateResult(gate, "warn", ["content needs attention"])])[
        "available"
    ]


@pytest.mark.parametrize("gate", ["consistency", "slides", "territory"])
def test_file_only_failures_do_not_offer_content_revision(gate):
    offer = revision_offer(_detail(), [GateResult("citation", "pass"), GateResult(gate, "fail")])
    assert not offer["available"]
    assert "文件生成失败" in offer["reason"]


@pytest.mark.parametrize("status", ["pending", "running", "error", "cancelled"])
def test_unfinished_or_interrupted_drafts_do_not_offer_revision(status):
    offer = revision_offer(_detail(status), [GateResult("citation", "warn")])
    assert not offer["available"]


def test_review_status_without_a_draft_cannot_offer_revision():
    detail = _detail()
    detail.report = None
    assert not revision_offer(detail, [GateResult("citation", "fail")])["available"]


async def test_needs_review_parent_can_enqueue_independent_content_revision(service):
    client, repo, settings = service
    parent = await failed_parent(repo, settings)
    await repo.set_status(parent.id, "needs_review")
    parent = await repo.get_run(parent.id)
    response = await client.post(
        f"/api/runs/{parent.id}/revise",
        json={"source_version": source_version(parent), "request_id": "review-required-revision"},
    )
    assert response.status_code == 202, response.text
    child = await repo.get_run(response.json()["run_id"])
    assert child.id != parent.id
    assert child.owner_id == parent.owner_id
    assert (await repo.get_run(parent.id)).status == "needs_review"
    claimed = await repo.claim_next_run("revision-worker")
    assert claimed is not None and claimed.run_id == child.id
