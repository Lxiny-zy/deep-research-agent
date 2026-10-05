"""Task reasoning overrides and leaf prompts preserve model/executor boundaries."""

import asyncio
from types import SimpleNamespace
from typing import get_args

import pytest

from deep_research.agents.base import RunContext
from deep_research.agents.planner import Planner, SearchQueryPlan, plan_search_queries
from deep_research.generation_policy import GenerationTask, generation_options
from deep_research.intent.cascade import IntentCascade, QueryIntentJudgment
from deep_research.observability import Tracer
from deep_research.prompting import (
    LEAF_FACT_RULES,
    leaf_system_prompt,
    load_global_rules,
    structured_system_prompt,
)
from tests.fakes import FakeLLM, FakeSearch


@pytest.mark.parametrize("mode", ["temperature", "", None])
@pytest.mark.parametrize("task", get_args(GenerationTask))
def test_nonreasoning_models_receive_no_reasoning_parameter(mode, task):
    llm = SimpleNamespace(parameter_mode=mode, reasoning_effort="high")
    assert generation_options(llm, task) == {}
    assert llm.reasoning_effort == "high"


@pytest.mark.parametrize(
    "task",
    ["research_planning", "scientific_synthesis", "complex_derivation", "formula_verification"],
)
def test_scientific_tasks_keep_profile_effort(task):
    llm = SimpleNamespace(parameter_mode="reasoning", reasoning_effort="high")
    assert generation_options(llm, task) == {}
    assert llm.reasoning_effort == "high"


async def test_parallel_tasks_do_not_change_a_shared_model_profile():
    llm = SimpleNamespace(parameter_mode="reasoning", reasoning_effort="high")

    async def request(task):
        options = generation_options(llm, task)
        await asyncio.sleep(0)
        return options.get("reasoning_effort", llm.reasoning_effort)

    assert await asyncio.gather(
        request("summary"), request("scientific_synthesis"), request("formatting"),
        request("complex_derivation"), request("formula_verification"),
    ) == ["low", "high", "low", "high", "high"]
    options = generation_options(llm, "summary")
    options["reasoning_effort"] = "high"
    assert generation_options(llm, "summary") == {"reasoning_effort": "low"}
    assert llm.reasoning_effort == "high"


async def test_query_terms_use_low_effort_but_full_research_plan_keeps_profile(settings):
    class Capture(FakeLLM):
        parameter_mode = "reasoning"
        reasoning_effort = "high"

        def __init__(self):
            super().__init__()
            self.requests = []

        async def parse(self, system, user, schema, **kwargs):
            self.requests.append((system, user, schema, kwargs))
            await asyncio.sleep(0)
            if schema is SearchQueryPlan:
                return SearchQueryPlan(search_queries=["spectral reconstruction 2025"])
            return await super().parse(system, user, schema, **kwargs)

    llm = Capture()
    ctx = RunContext(
        llm=llm, search_tool=FakeSearch(), settings=settings, tracer=Tracer(),
        global_rules=load_global_rules() + "\n保留用户指定的2025年范围。",
    )
    await asyncio.gather(
        plan_search_queries("2025年光谱重建研究", ctx),
        Planner(llm, Tracer(), settings).run("比较光谱重建方法的适用条件"),
    )
    query_call = next(call for call in llm.requests if call[2] is SearchQueryPlan)
    full_plan_call = next(call for call in llm.requests if call[2] is not SearchQueryPlan)
    assert query_call[3] == {"reasoning_effort": "low"}
    assert "reasoning_effort" not in full_plan_call[3]
    assert LEAF_FACT_RULES in query_call[0]
    assert "保留用户指定的2025年范围。" in query_call[0]
    assert "work/<slug>" not in query_call[0]
    assert llm.reasoning_effort == "high"


async def test_intent_fallback_keeps_custom_constraints_without_executor_state():
    calls = []

    class Capture:
        parameter_mode = "reasoning"
        reasoning_effort = "high"

        async def parse(self, system, user, schema, **kwargs):
            calls.append((system, kwargs))
            return QueryIntentJudgment(intent="exploratory", confidence=0.8)

    router = IntentCascade(
        llm=Capture(), global_rules=load_global_rules() + "\n本次分类仅依据当前请求。",
    )
    judgment = await router._llm_judge_query("介绍光谱重建的研究方向")
    assert judgment is not None and judgment.intent == "exploratory"
    assert calls[0][1]["reasoning_effort"] == "low"
    assert LEAF_FACT_RULES in calls[0][0]
    assert "本次分类仅依据当前请求。" in calls[0][0]
    assert "checkpoint" not in calls[0][0]


def test_leaf_factual_rules_are_idempotent_and_do_not_replace_json_contract():
    custom = "本次必须保留文件路径字段，但不执行文件写入。"
    prompt = leaf_system_prompt("仅返回检索式。", load_global_rules() + "\n" + custom)
    assert leaf_system_prompt(prompt, load_global_rules() + "\n" + custom) == prompt
    rendered = structured_system_prompt(prompt, SearchQueryPlan)
    assert rendered.count(LEAF_FACT_RULES) == 1
    assert custom in rendered
    assert "只输出一个 JSON 对象" in rendered
    assert "search_queries" in rendered
    assert "work/<slug>" not in rendered
    assert "checkpoint" not in rendered
    # Executor policy itself is still available to runtime roles unchanged.
    assert "work/<slug>" in load_global_rules()
