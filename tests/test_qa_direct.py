"""The product's default paper question makes one answer request, including cold starts."""

from __future__ import annotations

import asyncio
import base64
import threading
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest
from openai import InternalServerError

from deep_research import api
from deep_research.agents.base import RunContext
from deep_research.call_budget import ModelCallBudget
from deep_research.llm import LLM
from deep_research.models import Source
from deep_research.observability import Tracer
from deep_research.orchestrator import DeepResearchAgent
from deep_research.token_budget import TokenBudget
from deep_research.workbench.attachments import Attachment
from deep_research.workbench.qa import answer_question
from tests.fakes import FakeLLM, FakeSearch
from tests.test_paper_reader import ALICE, _headers, _paper_run, _pdf
from tests.test_paper_reader import reader_client as reader_client

ANSWER = "核心创新是根据原文中的结构关系推断缺失信息；这是原理解释 [1]。$I=aG+b$。"


class AnswerOnly(FakeLLM):
    def __init__(self):
        super().__init__()
        self.prompts = []

    async def parse(self, *args, **kwargs):
        raise AssertionError("A direct paper question must not plan, extract, verify or revise")

    async def stream(self, system, user, **kwargs):
        self.prompts.append((system, user))
        yield ANSWER


async def test_cold_and_followup_paper_questions_generate_once_without_verifier(settings):
    model = AnswerOnly()

    def resolve(role):
        assert role == "synthesizer"
        return model

    ctx = RunContext(
        llm=model, llm_resolver=resolve, search_tool=FakeSearch(),
        tracer=Tracer(), settings=settings,
    )
    source = Source(
        url="https://paper.test/method", title="Methods", locator="Section 3",
        content="We propose guided reconstruction. I = aG + b. The method fills occlusions.",
    )
    first = await answer_question(
        "按照最好的创新点分析原理", history=[], ctx=ctx, paper_sources=[source],
    )
    second = await answer_question(
        "这个原理有什么局限？",
        history=[{"query": "按照最好的创新点分析原理", "answer": first.answer}],
        ctx=ctx, paper_sources=[source],
    )
    assert len(model.prompts) == 2 and first.answer == second.answer == ANSWER
    assert all(source.content in prompt for _, prompt in model.prompts)
    assert "按照最好的创新点分析原理" in model.prompts[-1][1]
    assert not first.findings and not first.revision_state and not first.fallback
    assert first.source_evidence[0]["evidence_quote"] == source.content
    assert not {"claim_check", "answer_revision", "extraction_audit"} & {
        thought["tool"] for thought in first.thoughts
    }


async def test_large_paper_uses_local_method_retrieval_without_selection_model(settings):
    settings.llm_max_input_chars = 12_000
    model = AnswerOnly()
    sources = [
        Source(url=f"https://paper.test/{i}", title=f"Section {i}", content="Background. " * 500)
        for i in range(12)
    ]
    sources[-1] = Source(
        url="https://paper.test/method", title="Novel method and contributions",
        content="Our proposed architecture uses cross-view guidance to fill occlusions. " * 50,
    )
    ctx = RunContext(llm=model, search_tool=FakeSearch(), tracer=Tracer(), settings=settings)
    result = await answer_question("分析创新点的原理", history=[], ctx=ctx, paper_sources=sources)
    assert len(model.prompts) == 1
    system, prompt = model.prompts[0]
    assert len(system) + len(prompt) <= settings.llm_max_input_chars
    assert "cross-view guidance" in prompt and "未覆盖全部材料" in prompt
    assert all(any(e["evidence_quote"] in source.content for source in sources)
               for e in result.source_evidence)


async def test_long_dialogue_does_not_start_a_summary_model(settings):
    model = AnswerOnly()
    ctx = RunContext(llm=model, search_tool=FakeSearch(), tracer=Tracer(), settings=settings)
    history = [{"query": f"第 {i} 轮讨论方法", "answer": "讨论内容。" * 200} for i in range(20)]
    await answer_question(
        "按照最好的创新点分析原理", history=history, ctx=ctx,
        paper_sources=await FakeSearch().search("paper"),
    )
    assert len(model.prompts) == 1


async def test_paper_preparation_does_not_block_an_unrelated_question(settings, monkeypatch):
    from deep_research.workbench import qa_direct

    entered, release = threading.Event(), threading.Event()
    event_loop_thread = threading.get_ident()
    select = qa_direct._select_passages

    def slow_selection(*args, **kwargs):
        assert threading.get_ident() != event_loop_thread
        entered.set()
        assert release.wait(3), "test did not release paper preparation"
        return select(*args, **kwargs)

    monkeypatch.setattr(qa_direct, "_select_passages", slow_selection)
    model = AnswerOnly()
    ctx = RunContext(llm=model, search_tool=FakeSearch(), tracer=Tracer(), settings=settings)
    paper = asyncio.create_task(answer_question(
        "解释这篇论文的方法", history=[], ctx=ctx,
        paper_sources=[Source(
            url="https://paper.test/method", title="Paper", content="A reconstruction method.",
        )],
    ))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        quick = await asyncio.wait_for(answer_question(
            "解释一下什么是矩阵", history=[], ctx=ctx,
        ), 1)
        assert quick.answer and not paper.done()
        release.set()
        assert (await asyncio.wait_for(paper, 2)).answer
    finally:
        release.set()
        await asyncio.gather(paper, return_exceptions=True)


async def test_optional_search_uses_context_without_a_query_planning_model(settings):
    class Search(FakeSearch):
        def __init__(self):
            self.queries = []

        async def search(self, query, **kwargs):
            self.queries.append(query)
            return await super().search(query, **kwargs)

    model, search = AnswerOnly(), Search()
    ctx = RunContext(llm=model, search_tool=search, tracer=Tracer(), settings=settings)
    result = await answer_question(
        "2026年有哪些新的评价指标？",
        history=[{"query": "CASSI重建质量采用哪些指标？", "answer": "旧回答不是原文证据"}],
        ctx=ctx, include_web=True,
    )
    assert len(model.prompts) == 1 and len(search.queries) == 1
    assert "CASSI" in search.queries[0] and "2026" in search.queries[0]
    assert "旧回答不是原文证据" not in search.queries[0]
    assert result.source_evidence and not result.findings


async def test_model_search_reserves_the_last_request_for_the_answer(settings):
    from deep_research.tools.composite import MultiBackendSearch

    tracer = Tracer()
    budget = tracer.call_budget = ModelCallBudget(3)
    searched = []

    class Search(FakeSearch):
        def __init__(self, name):
            self.name = name

        async def search(self, query, **kwargs):
            tracer.call_budget.reserve()
            searched.append(self.name)
            return [Source(
                url=f"https://{self.name}.test", title=self.name,
                content="A reported method result.",
            )]

    class Model(AnswerOnly):
        async def stream(self, system, user, **kwargs):
            tracer.call_budget.reserve()
            async for part in super().stream(system, user, **kwargs):
                yield part

    model = Model()
    ctx = RunContext(
        llm=model, search_tool=MultiBackendSearch([Search("a"), Search("b"), Search("c")]),
        tracer=tracer, settings=settings,
    )
    result = await answer_question("查找相关方法", history=[], ctx=ctx, include_web=True)
    assert searched == ["a", "b"] and budget.used == 3
    assert tracer.call_budget is budget and len(model.prompts) == 1
    assert result.answer == ANSWER and not result.fallback
    assert any(t["tool"] == "search_status" for t in result.thoughts)


async def _wire_setup(reader_client, monkeypatch, *, failures=0):
    client, repo, settings = reader_client
    raw = _pdf("Our novel method reconstructs occlusions using cross-view guidance.")
    uploaded = await client.post(
        "/api/attachments", headers=_headers(ALICE),
        json={"filename": "paper.pdf", "mime_type": "application/pdf",
              "data_base64": base64.b64encode(raw).decode()},
    )
    assert uploaded.status_code == 201
    attachment = Attachment.model_validate(uploaded.json()["attachment"])
    run_id = await _paper_run(repo, settings, attachments=[attachment])
    await repo.set_status(run_id, "done")
    created = await client.post(
        "/api/qa/conversations", headers=_headers(ALICE), json={"run_id": run_id},
    )
    cid = created.json()["id"]
    requests = []
    models = []

    async def build(app, current, **kwargs):
        model = LLM(replace(current, llm_api_key="wire-test-key"), Tracer())
        models.append(model)
        agent = DeepResearchAgent(current, llm=model, search_tool=FakeSearch())
        agent.tracer.budget = TokenBudget()
        model.tracer = agent.tracer

        async def create(**arguments):
            requests.append(arguments)
            if len(requests) <= failures:
                raise InternalServerError(
                    "fixture gateway failure", body=None,
                    response=httpx.Response(500, request=httpx.Request("POST", "https://model.test")),
                )

            async def response():
                yield SimpleNamespace(
                    choices=[SimpleNamespace(
                        delta=SimpleNamespace(content=ANSWER), finish_reason="stop",
                    )],
                    usage=SimpleNamespace(prompt_tokens=30, completion_tokens=10, total_tokens=40),
                )

            return response()

        monkeypatch.setattr(model.client.chat.completions, "create", create)
        return agent, None

    monkeypatch.setattr(api, "_build_agent", build)
    return client, cid, requests, models


@pytest.mark.parametrize("stream", [False, True])
async def test_default_http_paper_question_one_wire_request_and_reconnect_reuses_it(
    reader_client, monkeypatch, stream,
):
    client, cid, requests, models = await _wire_setup(reader_client, monkeypatch)
    payload = {"query": "按照最好的创新点分析原理", "request_id": "direct-wire-test"}
    url = f"/api/qa/conversations/{cid}/messages" + ("/stream" if stream else "")
    try:
        result = await client.post(url, headers=_headers(ALICE), json=payload)
        assert result.status_code == (200 if stream else 201), result.text
        replay = await client.post(url, headers=_headers(ALICE), json=payload)
        assert replay.status_code == result.status_code
        assert len(requests) == 1
        conversation = await client.get(f"/api/qa/conversations/{cid}", headers=_headers(ALICE))
        message = conversation.json()["messages"][-1]
        assert message["answer"] == ANSWER and message["status"] == "done"
        assert message["request_payload"]["answer_mode"] == "direct"
        budget = next(t for t in message["thoughts"] if t["tool"] == "model_budget")
        assert budget["used"] == 1 and budget["limit"] == 2
        calls = [t["call"] for t in message["thoughts"] if t["tool"] == "model_call"]
        assert len(calls) == 1 and calls[0]["role"] == "synthesizer"
        assert not message["revision"]["available"]
    finally:
        for model in models:
            await model.aclose()


async def test_default_paper_question_has_a_hard_two_wire_request_ceiling(
    reader_client, monkeypatch,
):
    client, cid, requests, models = await _wire_setup(reader_client, monkeypatch, failures=100)
    try:
        response = await client.post(
            f"/api/qa/conversations/{cid}/messages", headers=_headers(ALICE),
            json={"query": "按照最好的创新点分析原理", "request_id": "direct-retry-budget"},
        )
        assert response.status_code == 201, response.text
        assert len(requests) == 2
        assert response.json()["status"] == "fallback"
        assert "调用上限" in response.json()["answer"]
    finally:
        for model in models:
            await model.aclose()
