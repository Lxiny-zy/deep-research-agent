from __future__ import annotations

import asyncio

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from deep_research.agents.base import Blackboard, RunContext
from deep_research.catalog.dto import AgentCardCreate, AgentCardUpdate, SearchProfileInput
from deep_research.catalog.repository import CatalogRepository
from deep_research.catalog.runtime import create_catalog_runtime_snapshot, load_catalog_runtime
from deep_research.catalog.search import build_profile_tool
from deep_research.config import Settings
from deep_research.models import ResearchPlan, Source, SubQuestion
from deep_research.observability import Tracer
from deep_research.persistence.db import create_all
from deep_research.reproducibility import RecordingSearchTool
from deep_research.tools.base import SearchTool
from deep_research.tools.key_pool import ApiKeyPoolSearch
from tests.fakes import FakeLLM, FakeSearch


@pytest.fixture
async def catalog():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    await create_all(engine)
    yield CatalogRepository(async_sessionmaker(engine, expire_on_commit=False))
    await engine.dispose()


async def make_profile(catalog, name, provider="serper"):
    key = await catalog.create_key(
        provider=provider, label=name, api_key=f"secret-{name}", priority=0, enabled=True
    )
    profile = await catalog.save_search_profile(
        SearchProfileInput(name=name, provider=provider, key_ids=[key.id])
    )
    return profile, key


async def test_explicit_profiles_isolate_keys_and_protect_references(catalog):
    first, key = await make_profile(catalog, "first")
    second, other = await make_profile(catalog, "second")
    tool = await build_profile_tool(first, catalog, Settings())
    other_tool = await build_profile_tool(second, catalog, Settings())
    assert tool._keys == ["secret-first"]
    assert other_tool._keys == ["secret-second"]
    assert "secret" not in first.model_dump_json()
    with pytest.raises(ValueError, match="引用"):
        await catalog.delete_key(key.id)
    with pytest.raises(ValueError, match="引用"):
        await catalog.update_key(key.id, {"provider": "brave"})
    with pytest.raises(ValueError, match="匹配"):
        await catalog.save_search_profile(
            SearchProfileInput(name="wrong", provider="brave", key_ids=[other.id])
        )
    await tool.aclose()
    await other_tool.aclose()


async def test_real_role_steps_route_separately_and_record_sources(catalog, monkeypatch):
    first, _ = await make_profile(catalog, "news")
    second, _ = await make_profile(catalog, "papers")
    for name, profile in [("news", first), ("papers", second)]:
        await catalog.create_agent(
            AgentCardCreate(name=name, behavior="research", search_profile_ids=[profile.id])
        )
    calls = []
    closed = []

    class Selected(SearchTool):
        def __init__(self, name):
            self.name = name

        async def search(self, query, *, max_results=5):
            calls.append(self.name)
            return [
                Source(
                    title=self.name,
                    url=f"https://example.com/{self.name}",
                    content="A measured fact.",
                )
            ]

        async def aclose(self):
            closed.append(self.name)

    async def factory(profile, *args, **kwargs):
        return Selected(profile.name)

    monkeypatch.setattr("deep_research.catalog.search.build_profile_tool", factory)
    settings = Settings(search_profile_ids=(first.id,))
    runtime = await load_catalog_runtime(catalog, Tracer(), settings)
    recorder = RecordingSearchTool(FakeSearch())
    recorded = []

    async def save(sources):
        recorded.extend(sources)

    recorder.set_sink(save)
    ctx = RunContext(
        llm=FakeLLM(),
        search_tool=recorder,
        tracer=Tracer(),
        settings=settings,
        search_resolver=runtime.resolve_search,
    )
    for name in ("news", "papers"):
        bb = Blackboard(
            query="test",
            plan=ResearchPlan(interpretation="test", sub_questions=[SubQuestion(question="test")]),
        )
        await runtime.resolve_agent(name).step(bb, ctx)
    inherited = await ctx.search_for("researcher")
    await inherited.search("default")
    assert calls == ["news", "papers", "news"]
    assert {source.title for source in recorded} == {"news", "papers"}
    assert all(source.content_hash for source in recorded)
    await runtime.aclose()
    assert sorted(closed) == ["news", "papers"]


async def test_recovery_freezes_default_endpoint_role_and_key_references(catalog):
    key = await catalog.create_key(
        provider="responses",
        label="custom",
        api_key="never-in-checkpoint",
        priority=0,
        enabled=True,
    )
    profile = await catalog.save_search_profile(
        SearchProfileInput(
            name="original",
            provider="responses",
            endpoint="https://original.example/v1/responses",
            model="original-model",
            key_ids=[key.id],
        )
    )
    card = await catalog.create_agent(
        AgentCardCreate(
            name="custom",
            behavior="research",
            search_profile_ids=[profile.id],
            system_prompt="original prompt",
            prompt_mode="replace",
        )
    )
    settings = Settings(search_profile_ids=(profile.id,))
    snapshot = await create_catalog_runtime_snapshot(catalog, {card.name}, settings)
    assert "never-in-checkpoint" not in snapshot.model_dump_json()
    await catalog.save_search_profile(
        SearchProfileInput(
            name="changed",
            provider="responses",
            endpoint="https://changed.example/v1/responses",
            model="changed-model",
            key_ids=[],
        ),
        profile.id,
    )
    await catalog.update_agent(
        card.id, AgentCardUpdate(search_profile_ids=["builtin:arxiv"], system_prompt="changed")
    )
    runtime = await load_catalog_runtime(
        catalog, Tracer(), Settings(search_profile_ids=("builtin:arxiv",)), snapshot=snapshot
    )
    assert runtime.snapshot({card.name}).default_search_profile_ids == [profile.id]
    restored = await runtime.resolve_search(card.name)
    client = restored._client(0)
    assert client.endpoint == "https://original.example/v1/responses"
    assert client.model == "original-model"
    assert "original prompt" in runtime.resolve_agent(card.name)._impl.system
    await runtime.aclose()
    # A revoked frozen credential cannot silently switch to another key or environment secret.
    await catalog.update_key(key.id, {"enabled": False})
    runtime = await load_catalog_runtime(catalog, Tracer(), settings, snapshot=snapshot)
    with pytest.raises(ValueError, match="没有可用 Key"):
        await runtime.resolve_search(card.name)
    await runtime.aclose()


async def test_builtin_snapshot_does_not_adopt_new_keys(catalog):
    _, key = await make_profile(catalog, "old")
    settings = Settings(search_profile_ids=("builtin:serper",), serper_api_key="env-secret")
    snapshot = await create_catalog_runtime_snapshot(catalog, {"researcher"}, settings)
    await make_profile(catalog, "new")
    await catalog.update_key(key.id, {"enabled": False})
    runtime = await load_catalog_runtime(catalog, Tracer(), settings, snapshot=snapshot)
    with pytest.raises(ValueError, match="没有可用 Key"):
        await runtime.resolve_search("researcher")
    await runtime.aclose()


async def test_recovery_preserves_key_priority_order(catalog):
    first, first_key = await make_profile(catalog, "first")
    _, second_key = await make_profile(catalog, "second")
    await catalog.update_key(first_key.id, {"priority": 10})
    await catalog.save_search_profile(
        SearchProfileInput(name="first", provider="serper", key_ids=[first_key.id, second_key.id]),
        first.id,
    )
    settings = Settings(search_profile_ids=(first.id,))
    snapshot = await create_catalog_runtime_snapshot(catalog, {"researcher"}, settings)
    assert snapshot.search_profiles[0].key_ids == [second_key.id, first_key.id]
    await catalog.update_key(first_key.id, {"priority": 0})
    await catalog.update_key(second_key.id, {"priority": 20})
    runtime = await load_catalog_runtime(catalog, Tracer(), settings, snapshot=snapshot)
    tool = await runtime.resolve_search("researcher")
    assert tool._keys == ["secret-second", "secret-first"]
    await runtime.aclose()


async def test_key_pool_failover_cancellation_and_cleanup():
    calls = []
    closed = []

    class Backend(SearchTool):
        def __init__(self, key):
            self.key = key

        async def search(self, query, *, max_results=5):
            calls.append(self.key)
            if query == "cancel":
                raise asyncio.CancelledError
            if self.key == "a" or query == "exhaust":
                response = httpx.Response(429, request=httpx.Request("GET", "https://example.com"))
                raise httpx.HTTPStatusError(
                    "secret-in-upstream-error", request=response.request, response=response
                )
            return []

        async def aclose(self):
            closed.append(self.key)
            if self.key == "a":
                raise RuntimeError("close failed")

    pool = ApiKeyPoolSearch("serper", ["a", "b"], Backend)
    await pool.search("first")
    await pool.search("second")
    assert calls == ["a", "b", "b"]
    with pytest.raises(RuntimeError, match="全部不可用") as exc:
        await pool.search("exhaust")
    assert "secret" not in str(exc.value)
    # All credentials are cooling down. A fresh namespace tests propagation
    # of cancellation while an upstream request is actually running.
    pool._namespace = "cancellation-test"
    before = len(calls)
    with pytest.raises(asyncio.CancelledError):
        await pool.search("cancel")
    assert len(calls) == before + 1
    with pytest.raises(RuntimeError, match="close failed"):
        await pool.aclose()
    assert closed == ["a", "b"]


async def test_pool_does_not_retry_protocol_errors():
    calls = []

    class Backend(SearchTool):
        async def search(self, query, *, max_results=5):
            calls.append(query)
            raise ValueError("malformed response")

    pool = ApiKeyPoolSearch("serper", ["a", "b"], lambda _: Backend())
    with pytest.raises(ValueError, match="malformed"):
        await pool.search("query")
    assert calls == ["query"]
    assert await pool.search("unused", max_results=0) == []
    await pool.aclose()
