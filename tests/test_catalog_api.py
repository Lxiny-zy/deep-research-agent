"""catalog REST API：模型档案 / 角色卡片 / 搜索 key 的 CRUD 端点，密钥脱敏、404、校验。"""

from __future__ import annotations

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from deep_research import api
from deep_research.catalog.repository import CatalogRepository
from deep_research.config import Settings
from deep_research.persistence.db import create_all


@pytest.mark.asyncio
async def test_model_config_probe_and_discovery(cat_app, monkeypatch) -> None:
    async def ok_probe(profile, settings):  # type: ignore[no-untyped-def]
        assert profile.api_key == "sk-test"

    monkeypatch.setattr("deep_research.catalog_api._probe_llm", ok_probe)

    class Item:
        def __init__(self, id_: str) -> None:
            self.id = id_

    class Models:
        async def list(self):  # type: ignore[no-untyped-def]
            return type("Page", (), {"data": [Item("model-b"), Item("model-a")]})()

    class Client:
        def __init__(self, **kwargs):  # type: ignore[no-untyped-def]
            self.models = Models()

        async def close(self) -> None:
            return None

    monkeypatch.setattr("openai.AsyncOpenAI", Client)
    async with _client() as c:
        tested = await c.post(
            "/api/models/test-config",
            json={"api_key": "sk-test", "model": "model-a"},
        )
        assert tested.status_code == 200 and tested.json()["ok"] is True

        discovered = await c.post(
            "/api/models/discover",
            json={"api_key": "sk-test", "base_url": "https://example.test/v1"},
        )
        assert discovered.status_code == 200
        assert discovered.json()["models"] == ["model-a", "model-b"]


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=api.app), base_url="http://test")


@pytest.fixture
async def cat_app():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    await create_all(engine)
    api.app.state.settings = Settings()
    api.app.state.catalog = CatalogRepository(async_sessionmaker(engine, expire_on_commit=False))
    yield
    await engine.dispose()


@pytest.mark.asyncio
async def test_model_profile_crud_and_mask(cat_app):
    async with _client() as c:
        created = await c.post(
            "/api/models",
            json={"name": "gpt", "api_key": "sk-secret9999", "model": "gpt-4o", "is_default": True},
        )
        assert created.status_code == 201
        body = created.json()
        assert body["api_key_set"] and body["api_key_hint"] == "…9999"
        assert "secret" not in str(body)  # 明文不外泄
        pid = body["id"]

        listed = await c.get("/api/models")
        assert any(p["id"] == pid for p in listed.json())

        updated = await c.put(f"/api/models/{pid}", json={"model": "gpt-4o-mini"})
        assert updated.json()["model"] == "gpt-4o-mini"

        assert (await c.delete(f"/api/models/{pid}")).status_code == 204
        assert (await c.put(f"/api/models/{pid}", json={"model": "x"})).status_code == 404


@pytest.mark.asyncio
async def test_agent_card_crud_and_behavior_validation(cat_app):
    async with _client() as c:
        bad = await c.post("/api/agents", json={"name": "x", "behavior": "nonsense"})
        assert bad.status_code == 422  # 非法 behavior 被拒

        ok = await c.post(
            "/api/agents",
            json={
                "name": "my-critic",
                "behavior": "critique",
                "system_prompt": "挑刺",
                "icon": "🔍",
            },
        )
        assert ok.status_code == 201
        aid = ok.json()["id"]

        behaviors = await c.get("/api/behaviors")
        assert "critique" in behaviors.json()

        disabled = await c.put(f"/api/agents/{aid}", json={"enabled": False})
        assert disabled.json()["enabled"] is False

        assert (await c.delete(f"/api/agents/{aid}")).status_code == 204


@pytest.mark.asyncio
async def test_search_key_crud_and_mask(cat_app):
    async with _client() as c:
        r1 = await c.post(
            "/api/search-keys", json={"label": "主", "api_key": "tvly-primary", "priority": 1}
        )
        r2 = await c.post(
            "/api/search-keys", json={"label": "备", "api_key": "tvly-backup", "priority": 5}
        )
        assert r1.status_code == 201 and r2.status_code == 201

        keys = (await c.get("/api/search-keys")).json()
        assert [k["label"] for k in keys] == ["主", "备"]  # 按优先级
        assert all("tvly" not in k["api_key_hint"] for k in keys)  # 脱敏

        kid = r2.json()["id"]
        assert (await c.delete(f"/api/search-keys/{kid}")).status_code == 204


@pytest.mark.asyncio
async def test_model_test_endpoint(cat_app, monkeypatch):
    from deep_research import catalog_api

    async with _client() as c:
        pid = (
            await c.post("/api/models", json={"name": "p", "model": "m", "api_key": "sk-xxxx"})
        ).json()["id"]

        # 成功路径:探针被替换为无操作
        async def ok_probe(profile, settings):
            return None

        monkeypatch.setattr(catalog_api, "_probe_llm", ok_probe)
        body = (await c.post(f"/api/models/{pid}/test")).json()
        assert body["ok"] is True and body["latency_ms"] >= 0

        # 失败路径:探针抛异常 → ok=false + detail
        async def bad_probe(profile, settings):
            raise RuntimeError("boom")

        monkeypatch.setattr(catalog_api, "_probe_llm", bad_probe)
        body = (await c.post(f"/api/models/{pid}/test")).json()
        assert body["ok"] is False and "boom" in body["detail"]

        # 404:不存在的档案
        assert (await c.post("/api/models/nope/test")).status_code == 404


@pytest.mark.asyncio
async def test_model_test_without_key(cat_app):
    async with _client() as c:
        pid = (await c.post("/api/models", json={"name": "p", "model": "m"})).json()["id"]
        body = (await c.post(f"/api/models/{pid}/test")).json()
        assert body["ok"] is False and "API Key" in body["detail"]


@pytest.mark.asyncio
async def test_search_key_test_endpoint(cat_app, monkeypatch):
    from deep_research import catalog_api

    async with _client() as c:
        kid = (
            await c.post("/api/search-keys", json={"api_key": "tvly-x"})
        ).json()["id"]

        async def ok_probe(api_key):
            assert api_key == "tvly-x"  # 传入明文 key

        monkeypatch.setattr(catalog_api, "_probe_search", ok_probe)
        body = (await c.post(f"/api/search-keys/{kid}/test")).json()
        assert body["ok"] is True

        async def bad_probe(api_key):
            raise RuntimeError("429 quota")

        monkeypatch.setattr(catalog_api, "_probe_search", bad_probe)
        body = (await c.post(f"/api/search-keys/{kid}/test")).json()
        assert body["ok"] is False and "quota" in body["detail"]

        assert (await c.post("/api/search-keys/nope/test")).status_code == 404
