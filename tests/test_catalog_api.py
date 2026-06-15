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
