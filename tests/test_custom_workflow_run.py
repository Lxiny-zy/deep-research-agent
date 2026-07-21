"""编排器按名解析并运行自定义工作流（catalog DB 来源）；未命中回退内置 deep。"""

from __future__ import annotations

import pytest

from deep_research.catalog.dto import WorkflowDefView
from deep_research.models import Report
from deep_research.orchestration import RunStatus
from deep_research.orchestrator import DeepResearchAgent
from deep_research.persistence.memory_repository import InMemoryRepository
from tests.fakes import FakeLLM, FakeSearch


class FakeCatalog:
    """最小 CatalogSource：无自定义角色，仅按名提供一个自定义工作流定义。"""

    def __init__(self, wd: WorkflowDefView | None) -> None:
        self._wd = wd

    async def list_agents(self):  # type: ignore[no-untyped-def]
        return []

    async def get_profile_full(self, profile_id: str):  # type: ignore[no-untyped-def]
        return None

    async def get_default_profile(self):  # type: ignore[no-untyped-def]
        return None

    async def get_workflow_def(self, name: str):  # type: ignore[no-untyped-def]
        return self._wd if (self._wd and self._wd.name == name) else None


@pytest.mark.asyncio
async def test_runs_custom_workflow_by_name(settings) -> None:
    wd = WorkflowDefView(
        id="1",
        name="my-quick",
        steps=[
            {"kind": "agent", "agent": "planner"},
            {"kind": "agent", "agent": "researcher"},
            {"kind": "agent", "agent": "synthesizer"},
        ],
    )
    agent = DeepResearchAgent(
        settings,
        llm=FakeLLM(),
        search_tool=FakeSearch(),
        workflow="my-quick",
        catalog_repo=FakeCatalog(wd),
    )
    report = await agent.run("测试问题")

    assert isinstance(report, Report)
    stages = {e.stage for e in agent.tracer.events}
    assert {"PLANNER", "RESEARCHER", "SYNTHESIZER"} <= stages
    assert "REFLECTOR" not in stages  # 自定义流程未放反思 → 不应出现


@pytest.mark.asyncio
async def test_unknown_custom_name_falls_back_to_deep(settings) -> None:
    agent = DeepResearchAgent(
        settings,
        llm=FakeLLM(),
        search_tool=FakeSearch(),
        workflow="does-not-exist",
        catalog_repo=FakeCatalog(None),
    )
    await agent.run("测试问题")
    stages = {e.stage for e in agent.tracer.events}
    assert {"PLANNER", "RESEARCHER", "REFLECTOR", "SYNTHESIZER"} <= stages  # 回退 deep


@pytest.mark.asyncio
async def test_existing_graph_with_nonterminal_synthesizer_fails(settings) -> None:
    wd = WorkflowDefView(
        id="1",
        name="invalid-order",
        steps=[
            {"kind": "agent", "agent": "synthesizer"},
            {"kind": "agent", "agent": "researcher"},
        ],
        nodes=[
            {"id": "synth", "step": {"kind": "agent", "agent": "synthesizer"}},
            {"id": "research", "step": {"kind": "agent", "agent": "researcher"}},
        ],
        edges=[{"id": "sr", "source": "synth", "target": "research"}],
    )
    agent = DeepResearchAgent(
        settings,
        llm=FakeLLM(),
        search_tool=FakeSearch(),
        workflow="invalid-order",
        catalog_repo=FakeCatalog(wd),
    )

    with pytest.raises(ValueError, match="Synthesizer"):
        await agent.run("测试问题")
    assert any(
        event.stage == "ORCHESTRATOR" and event.type == "error"
        for event in agent.tracer.events
    )


@pytest.mark.asyncio
async def test_existing_legacy_steps_with_nonterminal_synthesizer_fails(settings) -> None:
    wd = WorkflowDefView(
        id="1",
        name="invalid-legacy-order",
        steps=[
            {"kind": "agent", "agent": "synthesizer"},
            {"kind": "agent", "agent": "researcher"},
        ],
    )
    agent = DeepResearchAgent(
        settings,
        llm=FakeLLM(),
        search_tool=FakeSearch(),
        workflow="invalid-legacy-order",
        catalog_repo=FakeCatalog(wd),
    )

    with pytest.raises(ValueError, match="收尾"):
        await agent.run("测试问题")


@pytest.mark.asyncio
async def test_run_fails_when_terminal_condition_prevents_report(settings) -> None:
    wd = WorkflowDefView(
        id="1",
        name="conditional-no-report",
        steps=[
            {"kind": "agent", "agent": "planner"},
            {"kind": "agent", "agent": "researcher"},
            {"kind": "agent", "agent": "synthesizer"},
        ],
        nodes=[
            {"id": "plan", "step": {"kind": "agent", "agent": "planner"}},
            {"id": "research", "step": {"kind": "agent", "agent": "researcher"}},
            {"id": "synth", "step": {"kind": "agent", "agent": "synthesizer"}},
        ],
        edges=[
            {"id": "pr", "source": "plan", "target": "research"},
            {
                "id": "rs",
                "source": "research",
                "target": "synth",
                "condition": "state.scratch.never == true",
            },
        ],
    )
    repo = InMemoryRepository()
    run_id = await repo.create_run("测试问题")
    agent = DeepResearchAgent(
        settings,
        llm=FakeLLM(),
        search_tool=FakeSearch(),
        workflow="conditional-no-report",
        catalog_repo=FakeCatalog(wd),
        repo=repo,
        run_id=run_id,
    )

    with pytest.raises(RuntimeError, match="未生成报告"):
        await agent.run("测试问题")

    detail = await repo.get_run(run_id)
    assert detail is not None
    assert detail.status == "error"
    assert detail.report is None
    assert detail.orchestration is not None
    assert detail.orchestration.status == RunStatus.FAILED
