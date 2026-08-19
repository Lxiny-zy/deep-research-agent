"""catalog REST API：模型档案 / 角色卡片 / 搜索 key 的 CRUD 端点，密钥脱敏、404、校验。"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from deep_research import api, catalog_api
from deep_research.catalog.repository import CatalogRepository
from deep_research.config import Settings
from deep_research.persistence.db import create_all


@pytest.mark.asyncio
async def test_model_config_probe_and_discovery(cat_app, monkeypatch) -> None:
    async def ok_probe(profile, settings):  # type: ignore[no-untyped-def]
        assert profile.api_key == "sk-test"

    monkeypatch.setattr("deep_research.catalog_api._probe_llm", ok_probe)

    async def allow_test_host(*args, **kwargs):  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr("deep_research.catalog_api.validate_provider_url_resolved", allow_test_host)

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
    catalog_api._probe_limiter = catalog_api._ProbeLimiter()
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


@pytest.fixture
async def cat_app_fk():
    """与 cat_app 相同，但开启 SQLite 外键强制（与生产 make_engine 的 PRAGMA 一致）。"""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @sa_event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn, _record):  # type: ignore[no-untyped-def]
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

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
async def test_updates_reject_null_for_non_nullable_catalog_fields(cat_app) -> None:
    async with _client() as c:
        profile = (await c.post("/api/models", json={"name": "profile", "model": "model"})).json()
        agent = (
            await c.post(
                "/api/agents",
                json={"name": "reviewer", "behavior": "critique"},
            )
        ).json()
        search_key = (await c.post("/api/search-keys", json={"api_key": "tvly-test"})).json()

        responses = [
            await c.put(f"/api/models/{profile['id']}", json={"model": None}),
            await c.put(f"/api/agents/{agent['id']}", json={"enabled": None}),
            await c.put(f"/api/search-keys/{search_key['id']}", json={"priority": None}),
        ]

    assert [response.status_code for response in responses] == [422, 422, 422]


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
    async with _client() as c:
        kid = (await c.post("/api/search-keys", json={"api_key": "tvly-x"})).json()["id"]

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


@pytest.mark.asyncio
async def test_search_probe_has_hard_timeout(monkeypatch) -> None:
    closed = False

    class Client:
        def __init__(self, *, api_key):  # type: ignore[no-untyped-def]
            assert api_key == "tvly-test"

        async def search(self, query, *, max_results):  # type: ignore[no-untyped-def]
            await asyncio.Event().wait()

        async def close(self):  # type: ignore[no-untyped-def]
            nonlocal closed
            closed = True

    monkeypatch.setattr("tavily.AsyncTavilyClient", Client)
    monkeypatch.setattr(catalog_api, "_PROBE_TIMEOUT_SECONDS", 0.001)

    with pytest.raises(TimeoutError):
        await catalog_api._probe_search("tvly-test")
    assert closed is True


@pytest.mark.asyncio
async def test_probe_endpoints_return_429_when_probe_budget_is_exhausted(cat_app) -> None:
    catalog_api._probe_limiter = catalog_api._ProbeLimiter(max_calls=1, window_seconds=60.0)
    async with _client() as c:
        first = await c.post("/api/models/test-config", json={"api_key": ""})
        second = await c.post("/api/models/test-config", json={"api_key": ""})

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.headers["Retry-After"] == "5"


@pytest.mark.asyncio
async def test_duplicate_names_return_409(cat_app):
    """重名模型档案 / 角色卡片：唯一约束冲突应翻译为 409 而非 500。"""
    async with _client() as c:
        assert (await c.post("/api/models", json={"name": "dup", "model": "m"})).status_code == 201
        conflict = await c.post("/api/models", json={"name": "dup", "model": "m"})
        assert conflict.status_code == 409
        assert "已存在" in conflict.json()["detail"]

        ok = await c.post("/api/agents", json={"name": "dup-agent", "behavior": "critique"})
        assert ok.status_code == 201
        conflict = await c.post("/api/agents", json={"name": "dup-agent", "behavior": "critique"})
        assert conflict.status_code == 409
        assert "已存在" in conflict.json()["detail"]


@pytest.mark.asyncio
async def test_update_model_to_duplicate_name_returns_409(cat_app):
    async with _client() as c:
        await c.post("/api/models", json={"name": "a", "model": "m"})
        pid = (await c.post("/api/models", json={"name": "b", "model": "m"})).json()["id"]
        conflict = await c.put(f"/api/models/{pid}", json={"name": "a"})
        assert conflict.status_code == 409
        assert "已存在" in conflict.json()["detail"]


@pytest.mark.asyncio
async def test_agent_with_invalid_model_profile_returns_422(cat_app_fk):
    """创建 / 更新角色卡片引用不存在的模型档案：外键冲突应翻译为 422 而非 500。"""
    async with _client() as c:
        bad = await c.post(
            "/api/agents",
            json={"name": "fk-a", "behavior": "critique", "model_profile_id": "nope"},
        )
        assert bad.status_code == 422
        assert "模型档案" in bad.json()["detail"]

        aid = (await c.post("/api/agents", json={"name": "fk-b", "behavior": "critique"})).json()[
            "id"
        ]
        bad = await c.put(f"/api/agents/{aid}", json={"model_profile_id": "nope"})
        assert bad.status_code == 422
        assert "模型档案" in bad.json()["detail"]


@pytest.mark.asyncio
async def test_reserved_agent_name_orchestrator_rejected(cat_app):
    """角色名 "orchestrator"（大小写不敏感）为运行终态事件保留 Stage，创建时应拒绝。"""
    async with _client() as c:
        for name in ("orchestrator", "Orchestrator", "  ORCHESTRATOR "):
            r = await c.post("/api/agents", json={"name": name, "behavior": "critique"})
            assert r.status_code == 422
            assert "保留" in r.json()["detail"]
