from dataclasses import replace

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from deep_research import api
from deep_research.catalog.dto import AgentCardCreate, SearchProfileInput
from deep_research.catalog.preflight import preflight_workflow
from deep_research.catalog.repository import CatalogRepository
from deep_research.config import Settings
from deep_research.persistence.db import create_all
from deep_research.persistence.memory_repository import InMemoryRepository


@pytest.fixture
async def resources(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    await create_all(engine)
    catalog = CatalogRepository(async_sessionmaker(engine, expire_on_commit=False))
    settings = Settings(
        tavily_api_key="",
        llm_api_key="",
        search_backends=("tavily",),
        search_profile_ids=(),
        intent_enabled=False,
        execution_mode="worker",
    )
    monkeypatch.setattr(api.app.state, "catalog", catalog, raising=False)
    monkeypatch.setattr(api.app.state, "settings", settings, raising=False)
    monkeypatch.setattr(api.app.state, "repo", InMemoryRepository(), raising=False)
    yield catalog, settings
    await engine.dispose()


async def test_preflight_blocks_missing_credentials_before_task_creation(resources):
    catalog, settings = resources
    profile = await catalog.save_search_profile(
        SearchProfileInput(
            name="explicit-news",
            provider="responses",
            endpoint="https://search.example/responses",
            model="news-model",
            key_ids=[],
        )
    )
    await catalog.create_agent(
        AgentCardCreate(name="researcher", behavior="research", search_profile_ids=[profile.id])
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test"
    ) as c:
        check = await c.get("/api/resource-preflight?workflow=deep")
        assert check.status_code == 200
        assert not check.json()["ok"]
        assert "没有可用 Key" in str(check.json()["errors"])
        response = await c.post(
            "/api/runs", json={"query": "compare frameworks", "workflow": "deep"}
        )
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "resource_unavailable"
        assert await api.app.state.repo.list_runs() == []


async def test_role_override_checks_only_used_search_and_reports_key_count(resources):
    catalog, settings = resources
    key = await catalog.create_key(
        provider="responses", label="main", api_key="synthetic-secret", priority=0, enabled=True
    )
    profile = await catalog.save_search_profile(
        SearchProfileInput(
            name="news",
            provider="responses",
            endpoint="https://search.example/responses",
            model="news-model",
            key_ids=[key.id],
        )
    )
    await catalog.create_agent(
        AgentCardCreate(name="researcher", behavior="research", search_profile_ids=[profile.id])
    )
    result = await preflight_workflow(catalog, settings, "deep")
    assert result.ok, result.errors
    role = next(r for r in result.roles if r["role"] == "researcher")
    assert not role["inherits_search"]
    assert role["search_profiles"][0]["key_count"] == 1
    assert "synthetic-secret" not in result.model_dump_json()
    await catalog.update_key(key.id, {"enabled": False})
    result = await preflight_workflow(catalog, settings, "deep")
    assert not result.ok
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test"
    ) as c:
        impacts = (await c.get("/api/search-resources/impact")).json()
        assert "researcher" in impacts["profiles"][profile.id]
        assert "news" in impacts["keys"][key.id]


async def test_free_provider_and_invalid_workflow(resources):
    catalog, settings = resources
    result = await preflight_workflow(
        catalog, replace(settings, search_backends=("arxiv",)), "deep"
    )
    assert result.ok
    assert not (await preflight_workflow(catalog, settings, "missing")).ok


async def test_repository_revalidates_role_references_in_transaction(resources):
    catalog, _ = resources
    with pytest.raises(ValueError, match="不存在"):
        await catalog.create_agent(
            AgentCardCreate(name="bad", behavior="research", search_profile_ids=["deleted-profile"])
        )
