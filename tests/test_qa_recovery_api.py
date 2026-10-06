"""Real ASGI stop/resume contract; no upstream requests in these tests."""

import asyncio

import pytest

from deep_research import api
from deep_research.orchestrator import DeepResearchAgent
from deep_research.workbench.qa_requests import MemoryQaRequests
from tests.test_paper_reader import ALICE, BOB, _headers
from tests.test_paper_reader import reader_client as reader_client
from tests.test_prose_review import CorrelationSearch
from tests.test_qa_continue_revision import ContinueModel


async def _interrupted(client, settings, monkeypatch):
    settings.quality = {"max_revisions": 0, "qa_claim_max_revisions": 0}
    model = ContinueModel()
    builds = []

    async def build(app, configured, **kwargs):
        builds.append(True)
        return DeepResearchAgent(configured, llm=model, search_tool=CorrelationSearch()), None

    monkeypatch.setattr(api, "_build_agent", build)
    cid = (await client.post("/api/qa/conversations", headers=_headers(ALICE), json={})).json()[
        "id"
    ]
    original = MemoryQaRequests.checkpoint

    async def commit_then_stop(self, cid, rid, owner, state):
        accepted = await original(self, cid, rid, owner, state)
        if accepted and state["stage"] == "draft":
            await self.cancel(cid, rid)
            raise asyncio.CancelledError()
        return accepted

    monkeypatch.setattr(MemoryQaRequests, "checkpoint", commit_then_stop)
    payload = {"query": "解释变量关系", "sources": ["web"], "request_id": "interrupted-turn"}
    response = await client.post(
        f"/api/qa/conversations/{cid}/messages", headers=_headers(ALICE), json=payload
    )
    assert response.status_code == 201, response.text
    parent = response.json()
    assert parent["status"] == "cancelled" and parent["recovery"] == {
        "available": True,
        "stage": "draft",
    }
    assert "_checkpoint" not in parent["request_payload"]
    monkeypatch.setattr(MemoryQaRequests, "checkpoint", original)
    return (
        cid,
        parent,
        model,
        builds,
        {**payload, "request_id": "resume-turn", "resume_message_id": parent["id"]},
    )


async def test_explicit_resume_is_owned_idempotent_and_preserves_cancelled_parent(
    reader_client, monkeypatch
):
    client, _, settings = reader_client
    cid, parent, model, builds, payload = await _interrupted(client, settings, monkeypatch)
    path = f"/api/qa/conversations/{cid}/messages"
    assert (await client.post(path, headers=_headers(BOB), json=payload)).status_code == 404
    responses = await asyncio.gather(
        *[client.post(path, headers=_headers(ALICE), json=payload) for _ in range(2)]
    )
    assert all(response.status_code == 201 for response in responses), [r.text for r in responses]
    assert responses[0].json()["id"] == responses[1].json()["id"]
    assert len(builds) == 2 and model.stream_calls == 1 and model.extractions == 1
    messages = (await client.get(f"/api/qa/conversations/{cid}", headers=_headers(ALICE))).json()[
        "messages"
    ]
    assert len(messages) == 2 and messages[0]["status"] == "cancelled"
    # A reconnect to the original request returns its terminal state, never resends it.
    old = await client.post(
        path,
        headers=_headers(ALICE),
        json={k: v for k, v in parent["request_payload"].items()}
        | {"request_id": parent["request_id"]},
    )
    assert old.status_code == 201 and old.json()["status"] == "cancelled" and len(builds) == 2


@pytest.mark.parametrize(
    "change", ["settings", "history", "scope", "source_snapshot", "corruption"]
)
async def test_resume_rejects_changed_context_before_any_model_build(
    reader_client, monkeypatch, change
):
    from deep_research.workbench.qa_store import QaMessage

    client, _, settings = reader_client
    cid, parent, model, builds, payload = await _interrupted(client, settings, monkeypatch)
    if change == "settings":
        settings.llm_max_input_chars += 1
    elif change == "history":
        await api.app.state.qa_store.append(
            cid, QaMessage(id="", position=0, query="new", answer="new context")
        )
    elif change == "scope":
        payload["sources"] = []
    elif change == "source_snapshot":
        from deep_research.models import Source
        from deep_research.workbench import qa_api

        original = qa_api._paper_scope

        async def changed_scope(*args):
            scope = await original(*args)
            scope["paper_sources"] = [
                Source(url="https://example.org/changed", title="Changed", content="new content")
            ]
            return scope

        monkeypatch.setattr(qa_api, "_paper_scope", changed_scope)
    else:
        row = (await api.app.state.qa_store.get(cid)).messages[0]
        row.request_payload["_checkpoint"]["material"]["draft"] += " altered"
    response = await client.post(
        f"/api/qa/conversations/{cid}/messages", headers=_headers(ALICE), json=payload
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "qa_recovery_unavailable"
    assert len(builds) == 1 and model.stream_calls == 1


async def test_cancel_endpoint_is_owned_and_does_not_delete_other_turns(reader_client):
    client, _, _ = reader_client
    cid = (await client.post("/api/qa/conversations", headers=_headers(ALICE), json={})).json()[
        "id"
    ]
    jobs = MemoryQaRequests(api.app.state.qa_store)
    await jobs.reserve(cid, "queued-request", "hash", {"query": "queued"})
    path = f"/api/qa/conversations/{cid}/requests/queued-request/cancel"
    assert (await client.post(path, headers=_headers(BOB))).status_code == 404
    stopped = await client.post(path, headers=_headers(ALICE))
    assert stopped.status_code == 200 and stopped.json()["status"] == "cancelled"
    assert stopped.json()["recovery"]["available"] is False
    assert (await api.app.state.qa_store.get(cid)).message_count == 1


async def test_actual_resolved_model_change_cannot_reuse_checkpoint_even_if_settings_match(
    reader_client, monkeypatch
):
    client, _, settings = reader_client
    cid, _, model, builds, payload = await _interrupted(client, settings, monkeypatch)
    model.model = "changed-model-after-admission"
    response = await client.post(
        f"/api/qa/conversations/{cid}/messages", headers=_headers(ALICE), json=payload
    )
    assert response.status_code == 502, response.text
    assert "实际模型或角色配置已变化" in response.text
    assert len(builds) == 2 and model.stream_calls == 1 and model.extractions == 1
