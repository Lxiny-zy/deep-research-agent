"""自定义工作流 REST API：CRUD + 服务端校验（非法 422 / 同名内置 422）+ 选择器合并 + 角色列表。"""

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


_VALID = [
    {"kind": "agent", "agent": "planner"},
    {"kind": "agent", "agent": "researcher"},
    {"kind": "agent", "agent": "synthesizer"},
]


@pytest.mark.asyncio
async def test_create_list_and_merge_into_workflows(cat_app):
    async with _client() as c:
        r = await c.post(
            "/api/workflows/custom",
            json={"name": "my-deep", "display_name": "我的深度", "steps": _VALID},
        )
        assert r.status_code == 201, r.text
        created = r.json()
        assert len(created["nodes"]) == 3
        assert len(created["edges"]) == 2
        assert created["version"] == 1

        custom = (await c.get("/api/workflows/custom")).json()
        assert any(w["name"] == "my-deep" for w in custom)

        merged = (await c.get("/api/workflows")).json()
        mine = [w for w in merged if w["name"] == "my-deep"]
        assert mine and mine[0]["custom"] == "True"  # 标记为自定义
        assert any(w["name"] == "deep" for w in merged)  # 内置仍在


@pytest.mark.asyncio
async def test_create_rejects_invalid_steps(cat_app):
    async with _client() as c:
        no_synth = await c.post(
            "/api/workflows/custom",
            json={"name": "no-synth", "steps": [{"kind": "agent", "agent": "planner"}]},
        )
        assert no_synth.status_code == 422  # 缺终端 synthesizer

        ghost = await c.post(
            "/api/workflows/custom",
            json={"name": "ghost", "steps": [{"kind": "agent", "agent": "不存在"}, *_VALID]},
        )
        assert ghost.status_code == 422  # 引用未注册角色


@pytest.mark.asyncio
async def test_create_accepts_linear_and_branch_graph(cat_app):
    nodes = [
        {"id": "a", "type": "step", "position": {"x": 0, "y": 0}, "step": _VALID[0]},
        {"id": "b", "type": "step", "position": {"x": 0, "y": 100}, "step": _VALID[1]},
        {"id": "c", "type": "step", "position": {"x": 0, "y": 200}, "step": _VALID[2]},
    ]
    async with _client() as c:
        ok = await c.post(
            "/api/workflows/custom",
            json={
                "name": "graph-linear",
                "nodes": nodes,
                "edges": [
                    {"id": "ab", "source": "a", "target": "b"},
                    {"id": "bc", "source": "b", "target": "c"},
                ],
            },
        )
        assert ok.status_code == 201, ok.text
        assert [step["agent"] for step in ok.json()["steps"]] == [
            "planner",
            "researcher",
            "synthesizer",
        ]

        branch = await c.post(
            "/api/workflows/custom",
            json={
                "name": "graph-branch",
                "nodes": nodes,
                "edges": [
                    {"id": "ab", "source": "a", "target": "b"},
                    {"id": "ac", "source": "a", "target": "c"},
                ],
            },
        )
        assert branch.status_code == 201, branch.text
        assert len(branch.json()["edges"]) == 2

        invalid_condition = await c.post(
            "/api/workflows/custom",
            json={
                "name": "bad-condition",
                "nodes": nodes,
                "edges": [
                    {
                        "id": "ab",
                        "source": "a",
                        "target": "b",
                        "condition": "__import__('os')",
                    },
                    {"id": "bc", "source": "b", "target": "c"},
                ],
            },
        )
        assert invalid_condition.status_code == 422


@pytest.mark.asyncio
async def test_create_rejects_builtin_name(cat_app):
    async with _client() as c:
        r = await c.post("/api/workflows/custom", json={"name": "deep", "steps": _VALID})
        assert r.status_code == 422  # 与内置流程同名


@pytest.mark.asyncio
async def test_roles_endpoint_lists_composable_builtins(cat_app):
    async with _client() as c:
        roles = (await c.get("/api/roles")).json()
        names = {r["name"] for r in roles}
        assert {"planner", "researcher", "synthesizer"} <= names
        assert "coordinator" not in names  # compose 专用原语，不在可编排列表


@pytest.mark.asyncio
async def test_update_and_delete(cat_app):
    async with _client() as c:
        wid = (await c.post("/api/workflows/custom", json={"name": "w1", "steps": _VALID})).json()[
            "id"
        ]

        upd = await c.put(f"/api/workflows/custom/{wid}", json={"description": "改了"})
        assert upd.status_code == 200 and upd.json()["description"] == "改了"

        bad = await c.put(
            f"/api/workflows/custom/{wid}",
            json={"steps": [{"kind": "agent", "agent": "planner"}]},
        )
        assert bad.status_code == 422  # 更新成非法 steps 被拒

        assert (await c.delete(f"/api/workflows/custom/{wid}")).status_code == 204
        assert (
            await c.put(f"/api/workflows/custom/{wid}", json={"description": "x"})
        ).status_code == 404
