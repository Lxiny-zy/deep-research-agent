"""数据驱动角色端到端：DB 角色卡片 → CardAgent → 引擎调度，验证自定义 prompt 与专属模型。"""

from __future__ import annotations

import pytest

from deep_research.agents.base import Blackboard, RunContext
from deep_research.agents.card_agent import CardAgent, behavior_impls
from deep_research.catalog.dto import AgentCardView, ModelProfileFull
from deep_research.catalog.runtime import CatalogRuntime
from deep_research.config import Settings
from deep_research.observability import Tracer
from deep_research.prompting import load_global_rules
from tests.fakes import FakeLLM, FakeSearch


def test_behavior_impls_cover_all_behaviors():
    impls = behavior_impls()
    assert set(impls) == {"plan", "research", "reflect", "synthesize", "critique"}


@pytest.mark.asyncio
async def test_card_agent_uses_custom_prompt_and_named_model():
    """卡片自定义 prompt 覆盖内置默认；llm_for(卡片名) 解析到专属 LLM。"""
    captured = {}

    class CapturingLLM(FakeLLM):
        async def parse(self, system, user, schema, *, temperature=0.2, retries=2):
            captured["system"] = system
            return await super().parse(
                system, user, schema, temperature=temperature, retries=retries
            )

    special_llm = CapturingLLM()
    default_llm = FakeLLM()

    # CardAgent：plan 行为 + 自定义提示词，名为 "my-planner"
    card = CardAgent(name="my-planner", behavior="plan", system_prompt="用海盗口吻拆解问题")

    # RunContext 的 llm_resolver：只为 "my-planner" 返回专属 LLM
    def resolver(name: str):
        return special_llm if name == "my-planner" else None

    ctx = RunContext(
        llm=default_llm,
        search_tool=FakeSearch(),
        tracer=Tracer(),
        settings=Settings(),
        llm_resolver=resolver,
    )
    bb = await card.step(Blackboard(query="测试"), ctx)

    assert bb.plan is not None  # plan 行为产出了计划
    assert "用海盗口吻拆解问题" in captured["system"]  # 自定义 prompt 生效
    assert load_global_rules() in captured["system"]  # 全局规则对自定义角色同样生效
    assert special_llm.parse_calls == 1  # 用了专属模型
    assert default_llm.parse_calls == 0  # 没用默认模型


@pytest.mark.asyncio
async def test_catalog_runtime_resolves_card_and_falls_back():
    """CatalogRuntime：已注册卡片名 → CardAgent；未知名 → 回退内置注册表。"""
    cards = [
        AgentCardView(
            id="1",
            name="my-critic",
            behavior="critique",
            system_prompt="挑刺",
            enabled=True,
            model_profile_id="p1",
        ),
        AgentCardView(id="2", name="disabled-one", behavior="plan", enabled=False),
        AgentCardView(id="3", name="synthesizer", behavior="research", enabled=True),
        AgentCardView(id="4", name="my-writer", behavior="synthesize", enabled=True),
    ]
    profiles = {
        "p1": ModelProfileFull(id="p1", name="cheap", base_url=None, api_key="k", model="m")
    }
    rt = CatalogRuntime(
        cards=cards,
        profiles=profiles,
        default_profile=None,
        tracer=Tracer(),
        settings=Settings(),
    )

    # 启用的卡片 → CardAgent
    agent = rt.resolve_agent("my-critic")
    assert isinstance(agent, CardAgent) and agent.behavior == "critique"

    # 禁用的卡片不进解析 → 回退内置注册表（planner 是内置角色）
    assert not isinstance(rt.resolve_agent("planner"), CardAgent)

    # 绑定档案的卡片 → 解析出专属 LLM；未知角色 → None（回退默认）
    assert rt.resolve_llm("my-critic") is not None
    assert rt.resolve_llm("unknown") is None
    assert rt.terminal_roles == {"aggregator", "my-writer"}
    await rt.aclose()


@pytest.mark.asyncio
async def test_catalog_runtime_close_attempts_all_cached_llms():
    class Closable:
        def __init__(self, error: Exception | None = None) -> None:
            self.error = error
            self.closed = False

        async def aclose(self) -> None:
            self.closed = True
            if self.error is not None:
                raise self.error

    rt = CatalogRuntime(
        cards=[],
        profiles={},
        default_profile=None,
        tracer=Tracer(),
        settings=Settings(),
    )
    first = Closable(RuntimeError("first close failed"))
    second = Closable()
    rt._llm_cache = {"first": first, "second": second}  # type: ignore[dict-item]

    with pytest.raises(RuntimeError, match="first close failed"):
        await rt.aclose()
    assert first.closed and second.closed
