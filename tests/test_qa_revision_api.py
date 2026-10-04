"""Revision endpoints share durable QA identity, ownership, and replay semantics."""

import asyncio

import pytest

from deep_research import api
from deep_research.orchestrator import DeepResearchAgent
from tests.test_paper_reader import ALICE, BOB, _headers
from tests.test_paper_reader import reader_client as reader_client
from tests.test_prose_review import CorrelationSearch
from tests.test_qa_continue_revision import FIXED, GOOD, ContinueModel


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("legacy", [False, True])
async def test_explicit_revision_is_private_owned_and_idempotent(
    reader_client, monkeypatch, stream, legacy
):
    client, _, settings = reader_client
    settings.quality = {"max_revisions": 0, "qa_claim_max_revisions": 0}
    model = ContinueModel()
    builds = []

    async def build(app, settings, **kwargs):
        builds.append(True)
        return DeepResearchAgent(settings, llm=model, search_tool=CorrelationSearch()), None

    monkeypatch.setattr(api, "_build_agent", build)
    conversation = (
        await client.post("/api/qa/conversations", headers=_headers(ALICE), json={})
    ).json()
    cid = conversation["id"]
    first = await client.post(
        f"/api/qa/conversations/{cid}/messages",
        headers=_headers(ALICE),
        json={"query": "解释变量关系", "sources": ["web"], "request_id": "initial-question-1"},
    )
    assert first.status_code == 201, first.text
    parent = first.json()
    assert parent["status"] == "fallback" and parent["revision"]["available"]
    assert all(row["tool"] != "_qa_revision_state" for row in parent["thoughts"])
    if legacy:
        old = (await api.app.state.qa_store.get(cid)).messages[0]
        old.thoughts = [row for row in old.thoughts if row["tool"] != "_qa_revision_state"]
    path = f"/api/qa/conversations/{cid}/messages/{parent['id']}/revise" + (
        "/stream" if stream else ""
    )
    forbidden = await client.post(path, headers=_headers(BOB), json={"request_id": "revision-1"})
    assert forbidden.status_code == 404 and len(builds) == 1
    settings.quality = {"max_revisions": 1, "qa_claim_max_revisions": 1}
    responses = await asyncio.gather(
        *[
            client.post(path, headers=_headers(ALICE), json={"request_id": "revision-1"})
            for _ in range(2)
        ]
    )
    assert all(response.status_code == (200 if stream else 201) for response in responses)
    stored = (await client.get(f"/api/qa/conversations/{cid}", headers=_headers(ALICE))).json()
    assert len(stored["messages"]) == 2
    assert stored["messages"][0]["answer"] == parent["answer"]
    revised = stored["messages"][1]
    assert revised["status"] == "done" and revised["answer"] == GOOD + "\n\n" + FIXED
    assert revised["revision"]["available"] is False
    assert len(model.edits) == 1 and model.stream_calls == 1 and model.extractions == 1
    assert len(builds) == 2
    assert model.evidence_checks == (2 if legacy else 1)
    completed = await client.post(
        f"/api/qa/conversations/{cid}/messages/{revised['id']}/revise",
        headers=_headers(ALICE),
        json={"request_id": "already-completed"},
    )
    assert completed.status_code == 409 and len(builds) == 2
    if not legacy and not stream:
        private_parent = (await api.app.state.qa_store.get(cid)).messages[0]
        state = next(
            row["state"] for row in private_parent.thoughts if row["tool"] == "_qa_revision_state"
        )
        state["draft"] += " 篡改"
        blocked = await client.post(
            path, headers=_headers(ALICE), json={"request_id": "corrupted-state"}
        )
        assert blocked.status_code == 409 and len(builds) == 2


async def test_pending_request_from_before_revision_support_keeps_its_identity(
    reader_client, monkeypatch
):
    import hashlib
    import json

    from deep_research.workbench.qa_requests import MemoryQaRequests

    client, _, settings = reader_client
    settings.quality = {"max_revisions": 0, "qa_claim_max_revisions": 0}
    model = ContinueModel()

    async def build(app, settings, **kwargs):
        return DeepResearchAgent(settings, llm=model, search_tool=CorrelationSearch()), None

    monkeypatch.setattr(api, "_build_agent", build)
    cid = (await client.post("/api/qa/conversations", headers=_headers(ALICE), json={})).json()[
        "id"
    ]
    payload = {"query": "解释变量关系", "sources": ["web"], "project_id": None}
    old_hash = hashlib.sha256(
        json.dumps({"actor": "alice", **payload}, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    jobs = MemoryQaRequests(api.app.state.qa_store)
    await jobs.reserve(cid, "before-revision-support", old_hash, {**payload, "_actor": "alice"})
    response = await client.post(
        f"/api/qa/conversations/{cid}/messages",
        headers=_headers(ALICE),
        json={**payload, "request_id": "before-revision-support"},
    )
    assert response.status_code == 201, response.text
    assert (await api.app.state.qa_store.get(cid)).message_count == 1
    assert model.stream_calls == 1
