from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from deep_research import api
from deep_research.agents.base import Blackboard
from deep_research.agents.card_agent import CardAgent
from deep_research.catalog.dto import AgentCardCreate, AgentCardUpdate
from deep_research.catalog.repository import CatalogRepository
from deep_research.catalog.runtime import (
    create_catalog_runtime_snapshot,
    load_catalog_runtime,
)
from deep_research.config import Settings
from deep_research.models import ResearchPlan, SubQuestion
from deep_research.observability import Tracer
from deep_research.orchestration import OrchestrationRuntime
from deep_research.orchestrator import (
    RUN_CATALOG_CHECKPOINT_KEY,
    DeepResearchAgent,
    create_initial_execution,
    snapshot_catalog_for_execution,
)
from deep_research.persistence.db import create_all
from deep_research.persistence.memory_repository import InMemoryRepository
from deep_research.workflow import Step, Workflow
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
async def test_catalog_snapshot_freezes_semantics_without_secrets(catalog):
    profile = await catalog.create_profile(
        name="frozen-profile",
        base_url=None,
        api_key="profile-secret",
        model="frozen-model",
        temperature=0.4,
        is_default=True,
    )
    card = await catalog.create_agent(
        AgentCardCreate(
            name="frozen-writer",
            behavior="synthesize",
            system_prompt="original prompt",
            model_profile_id=profile.id,
        )
    )

    snapshot = await create_catalog_runtime_snapshot(catalog, {card.name})
    encoded = json.dumps(snapshot.model_dump(mode="json"))

    assert snapshot.cards[0].behavior == "synthesize"
    assert snapshot.cards[0].system_prompt == "original prompt"
    assert snapshot.cards[0].model_profile_id == profile.id
    assert snapshot.default_profile_id == profile.id
    assert "frozen-writer" in snapshot.terminal_roles
    assert "profile-secret" not in encoded
    assert "api_key" not in encoded


@pytest.mark.asyncio
async def test_create_run_persists_role_snapshot_before_background_start(catalog, monkeypatch):
    profile = await catalog.create_profile(
        name="run-profile",
        base_url=None,
        api_key="run-profile-secret",
        model="run-model",
        temperature=0.3,
        is_default=False,
    )
    await catalog.create_agent(
        AgentCardCreate(
            name="researcher",
            behavior="research",
            system_prompt="frozen API prompt",
            model_profile_id=profile.id,
        )
    )
    repo = InMemoryRepository()
    monkeypatch.setattr(api.app.state, "settings", Settings(), raising=False)
    monkeypatch.setattr(api.app.state, "repo", repo, raising=False)
    monkeypatch.setattr(api.app.state, "catalog", catalog, raising=False)
    monkeypatch.setattr(api.app.state, "live", {}, raising=False)
    monkeypatch.setattr(api.app.state, "tasks", set(), raising=False)

    async def noop_execute(*args, **kwargs):  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr(api, "_execute", noop_execute)
    monkeypatch.setattr(api, "_check_rate_limit", lambda request: None)
    async with httpx.AsyncClient(
        transport=ASGITransport(app=api.app), base_url="http://test"
    ) as client:
        response = await client.post("/api/runs", json={"query": "Q", "workflow": "quick"})
    await asyncio.gather(*api.app.state.tasks)

    assert response.status_code == 202
    detail = await repo.get_run(response.json()["run_id"])
    assert detail is not None and detail.orchestration is not None
    raw = detail.orchestration.checkpoint["scratch"][RUN_CATALOG_CHECKPOINT_KEY]
    encoded = json.dumps(raw)
    assert raw["cards"][0]["system_prompt"] == "frozen API prompt"
    assert raw["cards"][0]["model_profile_id"] == profile.id
    assert raw["profiles"][0]["model"] == "run-model"
    assert raw["profiles"][0]["base_url"] is None
    assert "run-profile-secret" not in encoded
    assert "api_key" not in encoded


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["update", "disable", "delete"])
async def test_resume_uses_role_snapshot_after_catalog_mutation(catalog, mutation):
    worker = await catalog.create_agent(
        AgentCardCreate(
            name="frozen-worker",
            behavior="research",
            system_prompt="original worker prompt",
        )
    )
    writer = await catalog.create_agent(
        AgentCardCreate(
            name="frozen-writer",
            behavior="synthesize",
            system_prompt="original writer prompt",
        )
    )
    workflow = Workflow(
        name="frozen-catalog-workflow",
        steps=[Step(agent="planner"), Step(agent=worker.name), Step(agent=writer.name)],
    )
    settings = Settings()
    execution = create_initial_execution(workflow.name, workflow.name, settings)
    execution.definition = workflow.model_dump(mode="json")
    await snapshot_catalog_for_execution(execution, catalog)
    runtime = OrchestrationRuntime()
    runtime.adopt(execution)
    planner_step = runtime.create_step(
        node_id="step-1", label="planner", kind="agent", agent="planner"
    )
    runtime.start_step(planner_step)
    runtime.complete_step(planner_step)
    blackboard = Blackboard.model_validate(execution.checkpoint)
    blackboard.plan = ResearchPlan(
        interpretation="interrupted plan",
        sub_questions=[SubQuestion(question="resume sub-question")],
    )
    execution = runtime.save_checkpoint(
        blackboard.model_dump(mode="json"), workflow.model_dump(mode="json")
    ).model_copy(deep=True)

    if mutation == "update":
        await catalog.update_agent(
            worker.id,
            AgentCardUpdate(behavior="critique", system_prompt="changed prompt"),
        )
    elif mutation == "disable":
        await catalog.update_agent(worker.id, AgentCardUpdate(enabled=False))
    else:
        assert await catalog.delete_agent(worker.id)
    assert await catalog.update_agent(
        writer.id, AgentCardUpdate(behavior="critique", system_prompt="changed writer")
    )

    class CapturingLLM(FakeLLM):
        def __init__(self) -> None:
            super().__init__()
            self.system_prompts: list[str] = []

        async def parse(self, system, user, schema, *, temperature=0.2, retries=2):
            self.system_prompts.append(system)
            return await super().parse(
                system, user, schema, temperature=temperature, retries=retries
            )

        async def stream(self, system, user, *, temperature=0.4):
            self.system_prompts.append(system)
            async for chunk in super().stream(system, user, temperature=temperature):
                yield chunk

    llm = CapturingLLM()
    agent = DeepResearchAgent(
        settings,
        llm=llm,
        search_tool=FakeSearch(),
        workflow=workflow.name,
        catalog_repo=catalog,
        resume_execution=execution,
    )
    try:
        report = await agent.run("resume query")
    finally:
        await agent.aclose()

    assert report.markdown
    assert any("original worker prompt" in prompt for prompt in llm.system_prompts)
    assert any("original writer prompt" in prompt for prompt in llm.system_prompts)
    assert agent._catalog_runtime is None


@pytest.mark.asyncio
async def test_snapshot_runtime_restores_terminal_role_after_card_delete(catalog):
    profile = await catalog.create_profile(
        name="writer-profile",
        base_url=None,
        api_key="writer-secret",
        model="writer-model",
        temperature=0.3,
        is_default=False,
    )
    card = await catalog.create_agent(
        AgentCardCreate(
            name="frozen-terminal",
            behavior="synthesize",
            system_prompt="terminal prompt",
            model_profile_id=profile.id,
        )
    )
    snapshot = await create_catalog_runtime_snapshot(catalog, {card.name})
    assert await catalog.delete_agent(card.id)

    runtime = await load_catalog_runtime(catalog, Tracer(), Settings(), snapshot=snapshot)
    assert runtime is not None
    try:
        restored = runtime.resolve_agent(card.name)
        assert isinstance(restored, CardAgent)
        assert restored.behavior == "synthesize"
        assert restored._impl.system == "terminal prompt"
        assert runtime.terminal_roles == {"synthesizer", "aggregator", card.name}
        assert profile.id in runtime._profiles
    finally:
        await runtime.aclose()
