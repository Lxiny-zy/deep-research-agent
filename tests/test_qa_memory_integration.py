"""HTTP turns persist private grounded memory and reuse it only in its own history."""

import json
from copy import deepcopy

from deep_research import api
from deep_research.orchestrator import DeepResearchAgent
from deep_research.workbench.conversation_memory import (
    ConversationMemory,
    SummaryDraft,
    history_turns,
    memory_hash,
    valid_memory,
)
from deep_research.workbench.qa_store import QaMessage
from tests.fakes import FakeLLM, FakeSearch
from tests.test_paper_reader import ALICE, BOB, _headers
from tests.test_paper_reader import reader_client as reader_client


class MemoryModel(FakeLLM):
    parameter_mode = "reasoning"
    reasoning_effort = "high"
    input_capacity_chars = 20_000

    def __init__(self):
        super().__init__()
        self.summaries = []
        self.answer_prompts = []

    async def parse(self, system, user, schema, **kwargs):
        assert schema is SummaryDraft, "Knowledge-only turns must not invoke retrieval/review"
        payload = json.loads(user)
        self.summaries.append((deepcopy(payload), kwargs))
        draft = deepcopy(payload["previous_summary"]) if payload["previous_summary"] else {
            "research_subject": [], "user_constraints": [],
            "prior_conclusions": [], "open_questions": [],
        }
        for turn in payload["new_turns"]:
            quote = turn["query"].split("。", 1)[0]
            draft["research_subject"].append({
                "text": quote,
                "source_refs": [{
                    "turn_id": turn["turn_id"], "field": "query", "source_quote": quote,
                }],
            })
        return SummaryDraft.model_validate(draft)

    async def stream(self, system, user, **kwargs):
        self.stream_calls += 1
        self.answer_prompts.append(str(user))
        yield "评估时应明确研究对象、比较范围和不确定性。"


class NoSearch(FakeSearch):
    async def search(self, *args, **kwargs):
        raise AssertionError("Knowledge-only conversation unexpectedly searched")


def install_model(monkeypatch):
    model = MemoryModel()

    async def build(app, settings, **kwargs):
        return DeepResearchAgent(settings, llm=model, search_tool=NoSearch()), None

    monkeypatch.setattr(api, "_build_agent", build)
    return model


async def seed_conversation(client, *, key=ALICE, topic="ALPHA光谱重建", count=6):
    created = await client.post(
        "/api/qa/conversations", headers=_headers(key), json={"title": topic},
    )
    assert created.status_code == 201, created.text
    cid = created.json()["id"]
    messages = []
    for index in range(count):
        messages.append(await api.app.state.qa_store.append(cid, QaMessage(
            id="", position=0, query=f"{topic}第{index + 1}阶段。只讨论2026年的评估设置。",
            answer=f"第{index + 1}阶段的原始回答。" + "历史背景段落仅用于说明研究过程。" * 240,
        )))
    return cid, messages


async def ask(client, cid, request_id, *, key=ALICE):
    response = await client.post(
        f"/api/qa/conversations/{cid}/messages", headers=_headers(key),
        json={"query": "请解释当前研究的评估重点。", "sources": [], "request_id": request_id},
    )
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "done"
    return response.json()


def private_memory(message):
    entries = [row for row in message.thoughts if row.get("tool") == "conversation_memory"]
    assert len(entries) == 1
    return ConversationMemory.model_validate(entries[0]["memory"])


def as_history(messages):
    return [{"id": row.id, "position": row.position, "query": row.query, "answer": row.answer}
            for row in messages]


def assert_public_memory_boundary(payload):
    serialized = json.dumps(payload, ensure_ascii=False)
    for private_key in (
        "conversation_memory", "conversation_memory_status", "processed_source_refs",
        "prefix_hash", "previous_memory_hash", "query_ranges", "answer_ranges",
        "content_hash", "source_quote", "source_refs",
    ):
        assert private_key not in serialized
    messages = payload.get("messages", [payload])
    notices = [thought["observation"] for message in messages for thought in message["thoughts"]
               if thought.get("tool") == "conversation_context"]
    assert "已整理较早对话背景，原始消息仍保留。" in notices


async def test_http_memory_is_private_incremental_and_request_replay_does_not_resummarize(
    reader_client, monkeypatch
):
    client, _, _ = reader_client
    model = install_model(monkeypatch)
    cid, originals = await seed_conversation(client)
    first = await ask(client, cid, "memory-first-request")
    conversation = await api.app.state.qa_store.get(cid)
    memory1 = private_memory(conversation.messages[-1])
    assert memory1.covered_turns == 3
    assert valid_memory(memory1, history_turns(as_history(originals))) == memory1
    assert [source.turn_id for source in memory1.processed_source_refs] == [
        message.id for message in originals[:3]
    ]
    assert model.summaries[0][0]["previous_summary"] is None
    assert model.summaries[0][1]["reasoning_effort"] == "low"
    assert model.summaries[0][1]["retries"] == 0
    assert model.reasoning_effort == "high"
    assert_public_memory_boundary(first)
    public = await client.get(f"/api/qa/conversations/{cid}", headers=_headers(ALICE))
    assert public.status_code == 200
    assert_public_memory_boundary(public.json())
    assert public.json()["messages"][0]["answer"] == originals[0].answer

    second = await ask(client, cid, "memory-second-request")
    conversation = await api.app.state.qa_store.get(cid)
    memory2 = private_memory(conversation.messages[-1])
    assert len(model.summaries) == model.stream_calls == 2
    assert memory2.covered_turns == 4 and memory2.previous_memory_hash == memory_hash(memory1)
    assert valid_memory(memory2, history_turns(as_history(conversation.messages[:-1]))) == memory2
    second_input = model.summaries[1][0]
    assert [turn["turn_id"] for turn in second_input["new_turns"]] == [originals[3].id]
    assert second_input["previous_summary"]["research_subject"][0]["source_refs"][0][
        "turn_id"
    ] == originals[0].id
    assert all(original.answer not in json.dumps(second_input, ensure_ascii=False)
               for original in originals[:3])
    assert "可追溯语义摘要" in model.answer_prompts[-1]
    assert_public_memory_boundary(second)
    replay = await ask(client, cid, "memory-second-request")
    assert replay["id"] == second["id"]
    assert len(model.summaries) == model.stream_calls == 2


async def test_http_memory_rejects_changed_original_hash_and_rebuilds_from_retained_turns(
    reader_client, monkeypatch
):
    client, _, _ = reader_client
    model = install_model(monkeypatch)
    cid, originals = await seed_conversation(client)
    await ask(client, cid, "memory-before-source-change")
    stored = await api.app.state.qa_store.get(cid)
    old_memory = private_memory(stored.messages[-1])
    # Simulate a corrected retained source, while leaving the persisted memory
    # untouched. The next HTTP turn must validate, not blindly inherit it.
    stored.messages[0].answer = "历史回答已更正：此前评估口径不适用。"
    assert valid_memory(old_memory, history_turns(as_history(stored.messages))) is None
    response = await ask(client, cid, "memory-after-source-change")
    assert len(model.summaries) == 2
    refreshed_input = model.summaries[1][0]
    assert refreshed_input["previous_summary"] is None
    assert refreshed_input["new_turns"][0]["turn_id"] == originals[0].id
    assert refreshed_input["new_turns"][0]["answer"] == stored.messages[0].answer
    updated = (await api.app.state.qa_store.get(cid)).messages[-1]
    memory = private_memory(updated)
    assert memory.previous_memory_hash is None and memory.prefix_hash != old_memory.prefix_hash
    assert (
        memory.processed_source_refs[0].content_hash
        != old_memory.processed_source_refs[0].content_hash
    )
    status = next(row for row in updated.thoughts
                  if row.get("tool") == "conversation_memory_status")
    assert status["previous_invalidated"] is True and status["model_calls"] == 1
    assert_public_memory_boundary(response)


async def test_http_memory_never_crosses_conversation_or_owner(reader_client, monkeypatch):
    client, _, _ = reader_client
    model = install_model(monkeypatch)
    alice_cid, alice_history = await seed_conversation(client, topic="ALPHA私人主题")
    await ask(client, alice_cid, "alice-memory-request")
    other_cid, other_history = await seed_conversation(client, topic="BETA独立主题")
    await ask(client, other_cid, "other-conversation-request")
    assert len(model.summaries) == 2
    assert model.summaries[1][0]["previous_summary"] is None
    assert {turn["turn_id"] for turn in model.summaries[1][0]["new_turns"]} == {
        row.id for row in other_history[:3]
    }
    assert not {row.id for row in alice_history}.intersection(
        source.turn_id for source in private_memory(
            (await api.app.state.qa_store.get(other_cid)).messages[-1]
        ).processed_source_refs
    )
    assert "ALPHA私人主题" not in model.answer_prompts[-1]
    for method in ("get", "post"):
        path = f"/api/qa/conversations/{alice_cid}" + ("/messages" if method == "post" else "")
        kwargs = {"json": {"query": "解释历史研究", "sources": []}} if method == "post" else {}
        forbidden = await getattr(client, method)(path, headers=_headers(BOB), **kwargs)
        assert forbidden.status_code == 404
    assert len(model.summaries) == model.stream_calls == 2
    bob_cid, _ = await seed_conversation(client, key=BOB, topic="BOB新主题", count=0)
    response = await ask(client, bob_cid, "bob-new-request", key=BOB)
    assert len(model.summaries) == 2 and model.stream_calls == 3
    assert "ALPHA私人主题" not in model.answer_prompts[-1]
    assert "BETA独立主题" not in model.answer_prompts[-1]
    assert "可追溯语义摘要" not in model.answer_prompts[-1]
    assert all(row["tool"] != "conversation_context" for row in response["thoughts"])
    assert all(row.get("tool") != "conversation_memory" for row in (
        await api.app.state.qa_store.get(bob_cid)
    ).messages[-1].thoughts)
