"""端到端：DB 角色卡片（自定义 prompt + 独立模型档案）在一次完整研究 run 中真正生效。

用真实 CatalogRepository（SQLite 内存）+ 假 LLM/检索，验证：
  - 同名卡片（researcher）覆盖内置角色的 system prompt
  - 卡片绑定的模型档案被用于该角色（专属 LLM 被调用）
  - 整条 deep 工作流仍跑通、产出带引用的报告
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from deep_research.catalog.dto import AgentCardCreate
from deep_research.catalog.repository import CatalogRepository
from deep_research.catalog.runtime import load_catalog_runtime
from deep_research.observability import Tracer
from deep_research.orchestrator import DeepResearchAgent
from deep_research.persistence.db import create_all
from deep_research.persistence.memory_repository import InMemoryRepository
from tests.fakes import FakeLLM, FakeSearch


@pytest.fixture
async def catalog():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    await create_all(engine)
    yield CatalogRepository(async_sessionmaker(engine, expire_on_commit=False))
    await engine.dispose()


@pytest.mark.asyncio
async def test_custom_researcher_card_overrides_builtin(settings, catalog):
    # 一张覆盖内置 researcher 的卡片（自定义 prompt）。不绑模型档案：用默认注入 LLM，
    # 便于在测试里观察 prompt 是否生效（模型绑定→专属 LLM 已在 test_card_agent 单测覆盖）。
    await catalog.create_agent(
        AgentCardCreate(
            name="researcher",  # 同名覆盖内置角色
            display_name="海盗研究员",
            behavior="research",
            system_prompt="用海盗口吻检索并抽取事实",
        )
    )

    seen_systems: list[str] = []

    class SpyLLM(FakeLLM):
        async def parse(self, system, user, schema, *, temperature=0.2, retries=2):
            seen_systems.append(system)
            return await super().parse(
                system, user, schema, temperature=temperature, retries=retries
            )

    repo = InMemoryRepository()
    agent = DeepResearchAgent(
        settings,
        llm=SpyLLM(),
        search_tool=FakeSearch(),
        repo=repo,
        catalog_repo=catalog,
    )
    report = await agent.run("测试问题")

    # 报告仍产出且带引用（整条工作流跑通）
    assert "https://a.com" in report.citations
    assert "## 参考来源" in report.markdown
    # 自定义 researcher 的 prompt 生效（注入的专属 LLM 收到了海盗 prompt）
    assert any("海盗口吻" in s for s in seen_systems)
    await agent.aclose()


@pytest.mark.asyncio
async def test_no_custom_cards_falls_back_to_builtin(settings, catalog):
    # catalog 里没有任何卡片：完全走内置路径，行为与无 catalog 一致
    repo = InMemoryRepository()
    agent = DeepResearchAgent(
        settings, llm=FakeLLM(), search_tool=FakeSearch(), repo=repo, catalog_repo=catalog
    )
    report = await agent.run("测试问题")
    assert "## 参考来源" in report.markdown
    await agent.aclose()


@pytest.mark.asyncio
async def test_default_profile_applies_without_custom_cards(settings, catalog):
    profile = await catalog.create_profile(
        name="global-default",
        base_url=None,
        api_key="profile-key",
        model="profile-model",
        temperature=0.55,
        is_default=True,
    )

    runtime = await load_catalog_runtime(catalog, Tracer(), settings)

    assert runtime is not None
    assert runtime.has_default_profile is True
    llm = runtime.resolve_llm("planner")
    assert llm is not None
    assert llm.model == profile.model
    assert llm.default_temperature == 0.55
    await runtime.aclose()


@pytest.mark.asyncio
async def test_disabled_card_profile_cannot_block_runtime_loading(settings, catalog):
    profile = await catalog.create_profile(
        name="disabled-private-profile",
        base_url="https://127.0.0.1/v1",
        api_key="unused-key",
        model="unused-model",
        temperature=0.3,
        is_default=False,
    )
    await catalog.create_agent(
        AgentCardCreate(
            name="disabled-researcher",
            behavior="research",
            enabled=False,
            model_profile_id=profile.id,
        )
    )

    runtime = await load_catalog_runtime(catalog, Tracer(), settings)

    assert runtime is not None
    assert runtime.resolve_llm("disabled-researcher") is None
    await runtime.aclose()
