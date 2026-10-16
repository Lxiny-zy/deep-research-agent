"""Scenario dispatch spends model calls only on the requested source scope."""

from collections import Counter

import pytest

from deep_research.agents.base import RunContext
from deep_research.agents.researcher import Researcher
from deep_research.catalog.dto import AgentCardView, ModelProfileFull
from deep_research.catalog.runtime import create_catalog_runtime_snapshot, load_catalog_runtime
from deep_research.models import Source
from deep_research.observability import Tracer
from deep_research.prompting import LEAF_FACT_RULES, load_global_rules
from deep_research.workbench.paper_evidence import PaperEvidenceSelection, plan_findings
from deep_research.workbench.qa import answer_question
from deep_research.workbench.qa_cache import PaperEvidenceCache
from deep_research.workbench.qa_scope import qa_model_roles
from tests.fakes import FakeLLM, FakeSearch


class CountingModel(FakeLLM):
    def __init__(self):
        super().__init__()
        self.calls = Counter()
        self.prompts = []

    async def parse(self, system, user, schema, **kwargs):
        self.calls[schema.__name__] += 1
        self.prompts.append((schema.__name__, system))
        return await super().parse(system, user, schema, **kwargs)

    async def stream(self, system, user, **kwargs):
        self.calls["answer"] += 1
        yield "发现X [1]。"


async def test_knowledge_qa_resolves_only_the_answer_model(settings):
    model = CountingModel()
    resolved = []

    def resolve(role):
        resolved.append(role)
        assert role == "synthesizer", "Knowledge Q&A must not initialize retrieval models"
        return model

    ctx = RunContext(
        llm=model, llm_resolver=resolve, search_tool=FakeSearch(),
        tracer=Tracer(), settings=settings,
    )
    result = await answer_question("解释什么是过拟合", history=[], ctx=ctx)
    assert result.answer and not result.citations and not result.findings
    assert set(resolved) == {"synthesizer"}
    assert model.calls == {"answer": 1}


async def test_paper_followups_reuse_admission_until_sources_change(settings):
    model, search = CountingModel(), FakeSearch()
    sources = await search.search("paper")
    seed = await Researcher(model, search, Tracer(), settings).run("原论文发现")
    assert seed and seed.findings
    model.calls.clear()
    cache = PaperEvidenceCache()
    roles = []

    def resolve(role):
        roles.append(role)
        assert role != "planner"
        return model

    ctx = RunContext(
        llm=model, llm_resolver=resolve, search_tool=search, tracer=Tracer(), settings=settings,
        global_rules=load_global_rules() + "\n保留当前论文的作者归属。",
    )

    async def ask(question):
        return await answer_question(
            question, history=[], ctx=ctx, paper_sources=sources, paper_evidence=seed.findings,
            paper_cache=cache, cache_scope="alice/paper-run",
            answer_mode="verified",
        )

    first = await ask("核心贡献是什么？")
    second = await ask("这个结论的适用范围是什么？")
    assert first.findings and second.findings and not first.fallback and not second.fallback
    assert model.calls["SemanticEvidenceDecisionList"] == 1
    assert model.calls["ExtractedFindingList"] == 0
    assert model.calls["PaperEvidenceSelection"] == 2
    assert model.calls["answer"] == 2
    assert set(roles) == {"researcher", "synthesizer", "evidence_verifier"}
    sources[:] = [source.model_copy(update={"content": source.content + " 原文已更新"})
                  for source in sources]
    third = await ask("更新后核心贡献是什么？")
    assert third.findings
    assert model.calls["ExtractedFindingList"] == 1
    assert model.calls["SemanticEvidenceDecisionList"] == 2
    for schema, system in model.prompts:
        if schema in {"ExtractedFindingList", "PaperEvidenceSelection"}:
            assert LEAF_FACT_RULES in system
            assert load_global_rules() not in system
            if schema == "PaperEvidenceSelection":
                assert "保留当前论文的作者归属。" in system


async def test_long_paper_without_candidates_selects_relevant_sections(settings):
    sources = [
        Source(url=f"https://paper.test/{i}", title=f"第 {i} 节", content="原文材料" * 1500)
        for i in range(6)
    ]
    requests = []

    class Selector:
        parameter_mode = "reasoning"
        reasoning_effort = "high"
        input_capacity_chars = 30_000

        async def parse(self, system, user, schema, **kwargs):
            requests.append((system, user, kwargs))
            return PaperEvidenceSelection(
                sufficient=False, source_urls=[sources[3].url],
                missing_topics=["第 3 节的方法"], next_step="read_selected",
            )

    researcher = Researcher(Selector(), FakeSearch(), Tracer(), settings)
    plan = await plan_findings(
        [], "解释第 3 节的方法", [], researcher, sources, max_read_chars=8000
    )
    assert plan.source_urls == [sources[3].url]
    assert plan.next_step == "read_selected"
    assert len(requests) == 1 and requests[0][2] == {"reasoning_effort": "low"}
    assert load_global_rules() not in requests[0][0]
    assert sources[0].content not in requests[0][1]
    exhausted = await plan_findings(
        [], "解释第 3 节的方法", [], researcher, sources, {source.url for source in sources},
    )
    assert exhausted.next_step == "answer_with_gaps" and len(requests) == 1


@pytest.mark.parametrize(
    ("grounded", "retrieval", "expected"),
    [
        (False, False, ("synthesizer",)),
        (True, False, ("researcher", "synthesizer", "evidence_verifier")),
        (False, True, ("planner", "researcher", "synthesizer", "evidence_verifier")),
        (True, True, ("planner", "researcher", "synthesizer", "evidence_verifier")),
    ],
)
async def test_qa_catalog_loads_only_models_for_its_source_scope(
    settings, monkeypatch, grounded, retrieval, expected,
):
    assert qa_model_roles(grounded=grounded, retrieval=retrieval, verified=True) == expected
    fetched, validated = [], []

    class Catalog:
        async def list_agents(self):
            return [
                AgentCardView(id=role, name=role, behavior="synthesize", model_profile_id=role)
                for role in ["planner", "researcher", "synthesizer", "evidence_verifier", "unused"]
            ]

        async def get_default_profile(self):
            return None

        async def get_profile_full(self, profile_id):
            assert profile_id in expected, "An unrelated model must not block this Q&A turn"
            fetched.append(profile_id)
            return ModelProfileFull(
                id=profile_id, name=profile_id, model="test", api_key="test-key",
                base_url=f"https://{profile_id}.test/v1",
            )

    async def validate(url, **kwargs):
        validated.append(url)

    monkeypatch.setattr("deep_research.catalog.runtime.validate_provider_url_resolved", validate)
    runtime = await load_catalog_runtime(
        Catalog(), Tracer(), settings, required_roles=expected, include_search=False,
    )
    assert runtime is not None and set(fetched) == set(expected)
    assert len(validated) == len(expected)
    frozen = await create_catalog_runtime_snapshot(
        Catalog(), expected, settings, include_search=False,
    )
    assert {card.name for card in frozen.cards} == set(expected)
    assert frozen.default_search_profile_ids == [] and frozen.search_profiles == []
    if not grounded and not retrieval:
        from deep_research.orchestrator import DeepResearchAgent

        settings.llm_api_key = ""
        agent = DeepResearchAgent(
            settings=settings, search_tool=FakeSearch(), catalog_repo=Catalog()
        )
        try:
            ctx = await agent.one_shot_context(required_roles=expected, include_search=False)
            assert ctx.llm_for("synthesizer").model == "test"
        finally:
            await agent.aclose()


@pytest.mark.parametrize("role_override", [False, True])
async def test_library_overlay_is_used_with_and_without_a_role_search(settings, role_override):
    calls = []

    class Search(FakeSearch):
        def __init__(self, name):
            self.name = name

        async def search(self, query, **kwargs):
            calls.append((self.name, query))
            return [Source(url=f"https://{self.name}.test", content="原文证据", title=self.name)]

    default, bound, library = Search("default"), Search("bound"), Search("library")

    async def resolve(role):
        return bound if role_override else None

    ctx = RunContext(
        llm=FakeLLM(), search_tool=default, search_resolver=resolve, search_overlay=library,
        tracer=Tracer(), settings=settings,
    )
    found = await (await ctx.search_for("researcher")).search("研究问题")
    expected = {"bound" if role_override else "default", "library"}
    assert {source.title for source in found} == expected
    assert len(calls) == 2 and {name for name, _ in calls} == expected


async def test_preflight_intent_uses_its_bound_reasoning_profile(settings, monkeypatch):
    from fastapi import FastAPI

    from deep_research.api import _intent_llm

    settings.llm_api_key = ""
    settings.llm_max_input_chars = 4321
    seen = []

    class Catalog:
        async def list_agents(self):
            return [
                AgentCardView(
                    id="intent", name="intent_router", behavior="plan", model_profile_id="intent",
                ),
                AgentCardView(
                    id="writer", name="synthesizer", behavior="synthesize",
                    model_profile_id="unrelated",
                ),
            ]

        async def get_default_profile(self):
            return None

        async def get_profile_full(self, profile_id):
            seen.append(profile_id)
            assert profile_id == "intent"
            return ModelProfileFull(
                id="intent", name="intent", model="intent-model", api_key="test-key",
                base_url="https://intent.test/v1", parameter_mode="reasoning",
                reasoning_effort="high", max_output_tokens=1024,
            )

    async def validate(*args, **kwargs):
        pass

    monkeypatch.setattr("deep_research.catalog.runtime.validate_provider_url_resolved", validate)
    app = FastAPI()
    app.state.catalog = Catalog()
    async with _intent_llm(app, settings) as model:
        assert model is not None and model.model == "intent-model"
        assert model.parameter_mode == "reasoning" and model.reasoning_effort == "high"
        assert model.settings.llm_max_input_chars == 4321
        assert model._call_role == "intent_router"
    assert seen == ["intent"]


@pytest.mark.parametrize(
    ("template", "strategy", "entity_calls"),
    [
        ("paperRead", "none", 0), ("peerReview", "none", 0), ("dataAnalysis", "none", 0),
        ("litReview", "none", 0), ("slides", "none", 0), ("mindmap", "none", 0),
        ("autoResearch", "deep", 1),
    ],
)
async def test_fixed_material_tasks_do_not_extract_unused_planner_entities(
    settings, template, strategy, entity_calls,
):
    from deep_research.agents.base import Blackboard
    from deep_research.agents.intent_router import INTENT_SCRATCH_KEY
    from deep_research.intent.types import IntentDecision
    from deep_research.orchestrator import DeepResearchAgent
    from deep_research.workbench.contract import CONTRACT_SCRATCH_KEY, TaskContract

    model = CountingModel()
    settings.intent_enabled = settings.intent_llm_fallback = True
    agent = DeepResearchAgent(settings=settings, llm=model, search_tool=FakeSearch())
    bb = Blackboard(query="比较两种方法")
    bb.scratch[CONTRACT_SCRATCH_KEY] = TaskContract(
        template=template, strategy=strategy, title="研究任务", original_request=bb.query,
    ).model_dump(mode="json")
    bb.scratch[INTENT_SCRATCH_KEY] = IntentDecision(
        intent="comparative", confidence=0.9, tier="rule",
    ).model_dump(mode="json")
    ctx = RunContext(llm=model, search_tool=FakeSearch(), tracer=Tracer(), settings=settings)
    await agent._apply_intent_gate(bb, ctx)
    assert model.calls["SlotExtraction"] == entity_calls
