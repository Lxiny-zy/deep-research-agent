from __future__ import annotations

import pytest

from deep_research import orchestrator
from deep_research.catalog.dto import ModelProfileFull
from deep_research.config import Settings
from deep_research.llm import LLM
from deep_research.models import Report, Source
from deep_research.orchestrator import DeepResearchAgent
from tests.fakes import FakeLLM, FakeSearch


class _DefaultProfileCatalog:
    async def list_agents(self):  # type: ignore[no-untyped-def]
        return []

    async def get_default_profile(self):  # type: ignore[no-untyped-def]
        return ModelProfileFull(
            id="default",
            name="default",
            api_key="profile-key",
            model="profile-model",
        )

    async def get_profile_full(self, profile_id):  # type: ignore[no-untyped-def]
        return None

    async def get_workflow_def(self, name):  # type: ignore[no-untyped-def]
        return None


@pytest.mark.asyncio
async def test_injected_search_does_not_require_global_tavily_key() -> None:
    settings = Settings(llm_api_key="llm-key", tavily_api_key="")
    agent = DeepResearchAgent(settings, search_tool=FakeSearch())
    await agent.aclose()


@pytest.mark.asyncio
async def test_falsy_injected_llm_is_selected_and_not_owned(settings) -> None:
    class FalsyLLM(FakeLLM):
        def __init__(self) -> None:
            super().__init__()
            self.closed = False

        def __bool__(self) -> bool:
            return False

        async def aclose(self) -> None:
            self.closed = True

    injected = FalsyLLM()
    agent = DeepResearchAgent(settings, llm=injected, search_tool=FakeSearch())

    assert agent.llm is injected
    await agent.aclose()
    assert injected.closed is False


@pytest.mark.asyncio
async def test_owned_default_llm_is_lazy_and_closed(settings, monkeypatch) -> None:
    created = []

    class TrackingLLM(FakeLLM):
        def __init__(self, settings, tracer):  # type: ignore[no-untyped-def]
            super().__init__()
            self.closed = False
            created.append(self)

        async def aclose(self) -> None:
            self.closed = True

    monkeypatch.setattr(orchestrator, "LLM", TrackingLLM)
    settings.llm_api_key = "llm-key"
    agent = DeepResearchAgent(settings, search_tool=FakeSearch(), workflow="quick")
    assert created == []

    await agent.run("Q")
    assert len(created) == 1

    await agent.aclose()
    assert created[0].closed is True


def test_constructor_failure_does_not_open_owned_clients(settings, monkeypatch) -> None:
    created = []

    class TrackingLLM:
        def __init__(self, settings, tracer):  # type: ignore[no-untyped-def]
            created.append(self)

    class BrokenPlanner:
        def __init__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("planner construction failed")

    monkeypatch.setattr(orchestrator, "LLM", TrackingLLM)
    monkeypatch.setattr(orchestrator, "Planner", BrokenPlanner)
    settings.llm_api_key = "llm-key"
    settings.tavily_api_key = "search-key"

    with pytest.raises(RuntimeError, match="planner construction failed"):
        DeepResearchAgent(settings)
    assert created == []


@pytest.mark.asyncio
async def test_catalog_default_avoids_global_llm_and_agent_is_single_use(
    monkeypatch,
) -> None:
    profile_clients = []

    class ProfileLLM(FakeLLM):
        def __init__(self) -> None:
            super().__init__()
            self.closed = False

        async def aclose(self) -> None:
            self.closed = True

    def build_profile(cls, tracer, **kwargs):  # type: ignore[no-untyped-def]
        client = ProfileLLM()
        profile_clients.append(client)
        return client

    def reject_global(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("global LLM must stay lazy when catalog has a default")

    monkeypatch.setattr(LLM, "from_params", classmethod(build_profile))
    monkeypatch.setattr(orchestrator, "LLM", reject_global)
    agent = DeepResearchAgent(
        Settings(llm_api_key="", tavily_api_key=""),
        search_tool=FakeSearch(),
        catalog_repo=_DefaultProfileCatalog(),
        workflow="quick",
    )

    await agent.run("Q")
    with pytest.raises(RuntimeError, match="single-use"):
        await agent.run("Q again")

    assert len(profile_clients) == 1
    await agent.aclose()
    assert profile_clients[0].closed is True
    assert agent._catalog_runtime is None


@pytest.mark.asyncio
async def test_aclose_attempts_owned_resources_after_close_failure(settings) -> None:
    class Closable:
        def __init__(self, error: Exception | None = None) -> None:
            self.error = error
            self.closed = False

        async def aclose(self) -> None:
            self.closed = True
            if self.error is not None:
                raise self.error

    agent = DeepResearchAgent(settings, llm=FakeLLM(), search_tool=FakeSearch())
    llm = Closable(RuntimeError("llm close failed"))
    search = Closable()
    agent.llm = llm  # type: ignore[assignment]
    agent.search_tool = search  # type: ignore[assignment]
    agent._owns_llm = True
    agent._owns_search_tool = True

    with pytest.raises(RuntimeError, match="llm close failed"):
        await agent.aclose()
    assert llm.closed and search.closed


def test_missing_search_dependency_validates_only_tavily() -> None:
    settings = Settings(llm_api_key="", tavily_api_key="")
    with pytest.raises(RuntimeError, match="TAVILY_API_KEY"):
        DeepResearchAgent(settings, llm=FakeLLM())


@pytest.mark.asyncio
async def test_run_produces_grounded_report(settings):
    agent = DeepResearchAgent(settings, llm=FakeLLM(), search_tool=FakeSearch())
    report = await agent.run("测试问题")

    assert isinstance(report, Report)
    assert report.markdown
    # 引用溯源：报告来源必须来自真实检索结果
    assert "https://a.com" in report.citations
    assert "## 参考来源" in report.markdown


@pytest.mark.asyncio
async def test_events_emitted(settings):
    agent = DeepResearchAgent(settings, llm=FakeLLM(), search_tool=FakeSearch())
    await agent.run("测试问题")
    types = {e.type for e in agent.tracer.events}
    assert "report" in types and "done" in types
    stages = {e.stage for e in agent.tracer.events}
    assert {"PLANNER", "RESEARCHER", "REFLECTOR", "SYNTHESIZER"} <= stages


@pytest.mark.asyncio
async def test_persistence_cleanup_errors_do_not_mask_run_failure(settings) -> None:
    class BrokenRepository:
        async def set_status(
            self, run_id: str, status: str, *, lease_owner: str | None = None
        ) -> None:
            if status == "error":
                raise RuntimeError("status persistence failed")

        async def save_events(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
            raise RuntimeError("event persistence failed")

    agent = DeepResearchAgent(
        settings,
        llm=FakeLLM(),
        search_tool=FakeSearch(),
        repo=BrokenRepository(),  # type: ignore[arg-type]
        run_id="run-1",
    )

    async def fail_workflow(query: str, run_id: str | None):
        raise ValueError("primary workflow failure")

    agent._run_workflow = fail_workflow  # type: ignore[method-assign]

    with pytest.raises(ValueError, match="primary workflow failure"):
        await agent.run("Q")


@pytest.mark.asyncio
async def test_run_stream_terminates(settings):
    agent = DeepResearchAgent(settings, llm=FakeLLM(), search_tool=FakeSearch())
    events = [ev async for ev in agent.run_stream("测试问题")]
    assert events[-1].type in ("done", "error")
    assert any(e.type == "report" for e in events)


class _FlakyOnceSearch(FakeSearch):
    """第一次检索抛异常，之后正常——模拟单个子问题的瞬时检索失败。"""

    def __init__(self) -> None:
        self.calls = 0

    async def search(self, query: str, *, max_results: int = 5) -> list[Source]:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("检索后端瞬时故障")
        return await super().search(query, max_results=max_results)


@pytest.mark.asyncio
async def test_run_stream_survives_researcher_error(settings):
    """RESEARCHER 的 error 是被隔离的单点失败：流不得提前断，最终仍产出报告。"""
    agent = DeepResearchAgent(settings, llm=FakeLLM(), search_tool=_FlakyOnceSearch())
    events = [ev async for ev in agent.run_stream("测试问题")]
    # 流中确实出现了 RESEARCHER error 事件……
    assert any(e.stage == "RESEARCHER" and e.type == "error" for e in events)
    # ……但流走到了 ORCHESTRATOR 终态，且是 done 而非 error
    assert events[-1].stage == "ORCHESTRATOR"
    assert events[-1].type == "done"
    assert any(e.type == "report" for e in events)
