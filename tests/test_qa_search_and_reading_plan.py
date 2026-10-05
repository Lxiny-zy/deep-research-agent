"""QA retrieval uses planned terms; paper reading retains honest chunk lengths."""

import json

import pytest

from deep_research import api
from deep_research.agents.base import RunContext
from deep_research.agents.planner import SearchQueryPlan
from deep_research.agents.researcher import Researcher
from deep_research.models import ExtractedFindingList, Source
from deep_research.observability import Tracer
from deep_research.orchestrator import DeepResearchAgent
from deep_research.persistence.repository import LeaseLostError
from deep_research.workbench.paper_evidence import PaperEvidenceSelection, plan_findings
from deep_research.workbench.qa import answer_question
from tests.fakes import FakeLLM, FakeSearch, verified_finding
from tests.test_paper_reader import ALICE, _headers
from tests.test_paper_reader import reader_client as reader_client

QUERIES = ["conformal prediction coverage 2025", "distribution shift conformal 2025"]


class PlanningLLM(FakeLLM):
    def __init__(self, error=None):
        super().__init__()
        self.plans = []
        self.extractions = []
        self.error = error

    async def parse(self, system, user, schema, **kwargs):
        if schema.__name__ == "SearchQueryPlan":
            self.plans.append((system, user))
            if self.error:
                raise self.error
            return schema.model_validate({
                "search_queries": [QUERIES[0], " ", QUERIES[1], QUERIES[0]],
            })
        if schema is ExtractedFindingList:
            self.extractions.append(str(user))
        return await super().parse(system, user, schema, **kwargs)


class Search(FakeSearch):
    def __init__(self):
        self.queries = []

    async def search(self, query, **kwargs):
        self.queries.append(query)
        return await super().search(query, **kwargs) if query in QUERIES else []


@pytest.mark.parametrize("backend", ["web", "library", "both"])
async def test_qa_plans_terms_once_and_keeps_question_for_reading(settings, backend):
    llm, planner, web, library = PlanningLLM(), PlanningLLM(), Search(), Search()
    ctx = RunContext(
        llm=llm, search_tool=web, tracer=Tracer(), settings=settings,
        llm_resolver=lambda role: planner if role == "planner" else llm,
        global_rules="本次问答规则标记",
    )
    previous = "2025 年共形预测在分布漂移下的覆盖保证"
    question = "它的局限呢？"
    answer = await answer_question(
        question, history=[{"query": previous, "answer": "旧回答不能当作证据"}], ctx=ctx,
        include_web=backend != "library", extra_search=library if backend != "web" else None,
    )
    assert web.queries == (QUERIES if backend != "library" else [])
    assert library.queries == (QUERIES if backend != "web" else [])
    assert len(planner.plans) == 1 and not llm.plans
    system, prompt = planner.plans[0]
    assert "本次问答规则标记" in system and "英文" in system
    assert previous in prompt and question in prompt
    assert "旧回答不能当作证据" not in prompt
    assert llm.extractions and all(previous in p and question in p for p in llm.extractions)
    assert any(t.get("queries") == QUERIES for t in answer.thoughts)


@pytest.mark.parametrize("intermediate", [[], [{"query": "有哪些常用指标？", "answer": "PSNR"}]])
async def test_long_followup_keeps_original_topic_through_search_and_extraction(
    settings, intermediate
):
    llm, planner, search = PlanningLLM(), PlanningLLM(), Search()
    ctx = RunContext(
        llm=llm, search_tool=search, tracer=Tracer(), settings=settings,
        llm_resolver=lambda role: planner if role == "planner" else llm,
    )
    topic = "CASSI 重建质量采用哪些评价指标？"
    question = "联网搜索2026年是否提出了新的指标"
    history = [{"query": topic, "answer": "历史答案不是证据"}, *intermediate]
    await answer_question(question, history=history, ctx=ctx, include_web=True)
    assert len(planner.plans) == 1
    prompts = [planner.plans[0][1], *llm.extractions]
    assert llm.extractions
    assert all(topic in prompt and question in prompt for prompt in prompts)
    assert all("历史答案不是证据" not in prompt for prompt in prompts)
    if intermediate:
        assert all(intermediate[0]["query"] in prompt for prompt in prompts)


@pytest.mark.parametrize("mode", ["paper", "knowledge", "greeting"])
async def test_closed_or_nonresearch_qa_does_not_plan_search(settings, mode):
    llm, search = PlanningLLM(AssertionError("unexpected planner")), Search()
    ctx = RunContext(llm=llm, search_tool=search, tracer=Tracer(), settings=settings)
    await answer_question(
        "你好" if mode == "greeting" else "这篇论文的方法是什么？", history=[], ctx=ctx,
        paper_sources=await FakeSearch().search("paper") if mode == "paper" else None,
        include_web=mode == "greeting",
    )
    assert not llm.plans and not search.queries


@pytest.mark.parametrize("error", [LeaseLostError("lost"), ValueError("invalid query plan")])
async def test_planning_failure_does_not_start_an_unplanned_search(settings, error):
    llm, search = PlanningLLM(error), Search()
    ctx = RunContext(llm=llm, search_tool=search, tracer=Tracer(), settings=settings)
    with pytest.raises(type(error)):
        await answer_question("查找共形预测论文", history=[], ctx=ctx, include_web=True)
    assert not search.queries and not llm.extractions


def paper_chunks():
    return [
        Source(
            url="https://paper.test/heading", title="方法", content="短标题片段只有十四个汉字而已",
        ),
        Source(
            url="https://paper.test/method", title="方法正文",
            content=" Full method details. " * 12,
        ),
        Source(url="https://paper.test/empty", title="空白页", content=" \n\t "),
    ]


@pytest.mark.parametrize("empty_selection", [False, True])
async def test_reading_catalog_reports_lengths_and_rejects_empty_targets(settings, empty_selection):
    class Selection(FakeLLM):
        catalog = None

        async def parse(self, system, user, schema, **kwargs):
            if schema is PaperEvidenceSelection:
                self.catalog, _ = json.JSONDecoder().raw_decode(
                    user.split("【可补读来源目录】\n", 1)[1]
                )
                return PaperEvidenceSelection(
                    sufficient=not empty_selection, finding_ids=["e1"],
                    source_urls=[chunks[-1].url] if empty_selection else [],
                )
            return await super().parse(system, user, schema, **kwargs)

    chunks, llm = paper_chunks(), Selection()
    researcher = Researcher(llm, FakeSearch(), Tracer(), settings)
    if empty_selection:
        with pytest.raises(ValueError, match="不在本论文目录"):
            await plan_findings([verified_finding()], "方法细节", [], researcher, chunks)
    else:
        await plan_findings([verified_finding()], "方法细节", [], researcher, chunks)
        assert {c["url"]: c["content_chars"] for c in llm.catalog} == {
            s.url: len(s.content.strip()) for s in chunks[:2]
        }
        assert llm.catalog[0]["content_chars"] == 14


async def test_initial_paper_read_skips_empty_chunks_without_dropping_short_ones(settings):
    llm, search = PlanningLLM(), Search()
    chunks = paper_chunks()
    ctx = RunContext(llm=llm, search_tool=search, tracer=Tracer(), settings=settings)
    await answer_question("解释论文方法", history=[], ctx=ctx, paper_sources=chunks)
    assert llm.extractions
    assert all(chunks[-1].url not in p for p in llm.extractions)
    assert all(any(s.url in p for p in llm.extractions) for s in chunks[:2])
    assert not search.queries and not llm.plans


@pytest.mark.parametrize("queries", [[], [" ", "\n"]])
def test_query_plan_cannot_be_empty_after_normalization(queries):
    with pytest.raises(ValueError):
        SearchQueryPlan(search_queries=queries)


@pytest.mark.parametrize("stream", [False, True])
async def test_product_qa_persists_actual_search_queries(reader_client, monkeypatch, stream):
    client, _, _ = reader_client
    llm, search = PlanningLLM(), Search()

    async def build(app, settings, **kwargs):
        return DeepResearchAgent(settings, llm=llm, search_tool=search), None

    monkeypatch.setattr(api, "_build_agent", build)
    created = await client.post("/api/qa/conversations", headers=_headers(ALICE), json={})
    cid = created.json()["id"]
    path = f"/api/qa/conversations/{cid}/messages" + ("/stream" if stream else "")
    payload = {
        "query": "2025 年共形预测覆盖保证", "sources": ["web"], "request_id": "planned-search-1",
    }
    response = await client.post(
        path, headers=_headers(ALICE), json=payload,
    )
    assert response.status_code == (200 if stream else 201), response.text
    detail = (await client.get(f"/api/qa/conversations/{cid}", headers=_headers(ALICE))).json()
    message = detail["messages"][0]
    assert search.queries == QUERIES and len(llm.plans) == 1
    assert any(t.get("queries") == QUERIES for t in message["thoughts"])
    assert message["citations"] == ["https://a.com"]
    # A repeated HTTP request reuses the durable result, including its plan.
    repeated = await client.post(
        path, headers=_headers(ALICE), json=payload,
    )
    assert repeated.status_code == response.status_code
    assert search.queries == QUERIES and len(llm.plans) == 1
