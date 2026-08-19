"""自定义工作流 REST API：CRUD + 服务端校验（非法 422 / 同名内置 422）+ 选择器合并 + 角色列表。"""

from __future__ import annotations

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from deep_research import api
from deep_research.catalog.dto import WorkflowDefCreate
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
            json={
                "name": "my-deep",
                "display_name": "我的深度",
                "steps": _VALID,
                "viewport": {"x": 20, "y": 30, "zoom": 2},
            },
        )
        assert r.status_code == 201, r.text
        created = r.json()
        assert len(created["nodes"]) == 3
        assert len(created["edges"]) == 2
        assert created["viewport"]["zoom"] == 2
        assert created["version"] == 1

        custom = (await c.get("/api/workflows/custom")).json()
        assert any(w["name"] == "my-deep" for w in custom)

        merged = (await c.get("/api/workflows")).json()
        mine = [w for w in merged if w["name"] == "my-deep"]
        assert mine and mine[0]["custom"] == "True"  # 标记为自定义
        assert any(w["name"] == "deep" for w in merged)  # 内置仍在


@pytest.mark.asyncio
async def test_update_rejects_explicit_null_for_non_nullable_field(cat_app) -> None:
    async with _client() as c:
        created = await c.post(
            "/api/workflows/custom", json={"name": "null-update", "steps": _VALID}
        )
        workflow = created.json()

        response = await c.put(
            f"/api/workflows/custom/{workflow['id']}",
            json={"description": None, "version": workflow["version"]},
        )

    assert created.status_code == 201
    assert response.status_code == 422


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
async def test_create_rejects_duplicate_edge_ids(cat_app) -> None:
    nodes = [
        {"id": "a", "step": _VALID[0]},
        {"id": "b", "step": _VALID[1]},
        {"id": "c", "step": _VALID[2]},
    ]
    async with _client() as c:
        response = await c.post(
            "/api/workflows/custom",
            json={
                "name": "duplicate-edge-ids",
                "nodes": nodes,
                "edges": [
                    {"id": "duplicate", "source": "a", "target": "b"},
                    {"id": "duplicate", "source": "b", "target": "c"},
                ],
            },
        )

    assert response.status_code == 422
    assert "id" in response.json()["detail"]


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

        branch_nodes = [
            nodes[0],
            nodes[1],
            {
                "id": "d",
                "type": "step",
                "position": {"x": 100, "y": 100},
                "step": _VALID[1],
            },
            nodes[2],
        ]
        branch = await c.post(
            "/api/workflows/custom",
            json={
                "name": "graph-branch",
                "nodes": branch_nodes,
                "edges": [
                    {"id": "ab", "source": "a", "target": "b"},
                    {"id": "ad", "source": "a", "target": "d"},
                    {"id": "bc", "source": "b", "target": "c"},
                    {"id": "dc", "source": "d", "target": "c"},
                ],
            },
        )
        assert branch.status_code == 201, branch.text
        assert len(branch.json()["edges"]) == 4

        unmerged_branch = await c.post(
            "/api/workflows/custom",
            json={
                "name": "graph-unmerged-branch",
                "nodes": nodes,
                "edges": [
                    {"id": "ab", "source": "a", "target": "b"},
                    {"id": "ac", "source": "a", "target": "c"},
                ],
            },
        )
        assert unmerged_branch.status_code == 422

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
async def test_create_rejects_graph_with_nonterminal_synthesizer(cat_app):
    nodes = [
        {"id": "synth", "step": {"kind": "agent", "agent": "synthesizer"}},
        {"id": "research", "step": {"kind": "agent", "agent": "researcher"}},
    ]
    async with _client() as c:
        response = await c.post(
            "/api/workflows/custom",
            json={
                "name": "synth-before-research",
                "nodes": nodes,
                "edges": [{"id": "sr", "source": "synth", "target": "research"}],
            },
        )

    assert response.status_code == 422
    assert "Synthesizer" in response.json()["detail"]


@pytest.mark.asyncio
async def test_create_rejects_builtin_name(cat_app):
    async with _client() as c:
        r = await c.post("/api/workflows/custom", json={"name": "deep", "steps": _VALID})
        assert r.status_code == 422  # 与内置流程同名


@pytest.mark.asyncio
async def test_roles_endpoint_lists_composable_builtins(cat_app):
    async with _client() as c:
        writer = await c.post(
            "/api/agents",
            json={
                "name": "my-writer",
                "behavior": "synthesize",
                "display_name": "Writer",
                "description": "把发现写成终稿报告",
            },
        )
        critic = await c.post("/api/agents", json={"name": "my-critic", "behavior": "critique"})
        disabled = await c.post(
            "/api/agents",
            json={"name": "disabled-writer", "behavior": "synthesize", "enabled": False},
        )
        assert writer.status_code == critic.status_code == disabled.status_code == 201

        roles = (await c.get("/api/roles")).json()
        by_name = {role["name"]: role for role in roles}
        names = set(by_name)
        assert {"planner", "researcher", "synthesizer"} <= names
        assert "coordinator" not in names  # compose 专用原语，不在可编排列表
        assert by_name["synthesizer"]["produces_report"] is True
        assert by_name["my-writer"]["produces_report"] is True
        assert by_name["my-critic"]["produces_report"] is False
        assert "disabled-writer" not in names
        # 构建器角色选择器的展示文案：内置角色须有中文 label 与职责描述,
        # 自定义卡片的 description 必须透传(而非被丢弃)
        assert by_name["planner"]["label"] == "规划师"
        for builtin_name in ("planner", "researcher", "reflector", "synthesizer", "critic"):
            assert by_name[builtin_name]["description"]
        assert by_name["my-writer"]["description"] == "把发现写成终稿报告"

        custom_terminal = await c.post(
            "/api/workflows/custom",
            json={
                "name": "custom-terminal",
                "steps": [
                    {"kind": "agent", "agent": "researcher"},
                    {"kind": "agent", "agent": "my-writer"},
                ],
            },
        )
        assert custom_terminal.status_code == 201, custom_terminal.text


@pytest.mark.asyncio
async def test_builtin_name_override_uses_effective_terminal_behavior(cat_app):
    async with _client() as c:
        override = await c.post(
            "/api/agents",
            json={"name": "synthesizer", "behavior": "research"},
        )
        assert override.status_code == 201

        roles = {role["name"]: role for role in (await c.get("/api/roles")).json()}
        assert roles["synthesizer"]["builtin"] is False
        assert roles["synthesizer"]["produces_report"] is False

        invalid = await c.post(
            "/api/workflows/custom",
            json={"name": "overridden-terminal", "steps": _VALID},
        )
        assert invalid.status_code == 422


@pytest.mark.asyncio
async def test_partial_graph_updates_are_merged_validated_and_normalized(cat_app):
    nodes = [
        {"id": "p", "position": {"x": 10, "y": 20}, "step": _VALID[0]},
        {"id": "r", "position": {"x": 30, "y": 40}, "step": _VALID[1]},
        {"id": "s", "position": {"x": 50, "y": 60}, "step": _VALID[2]},
    ]
    original_edges = [
        {"id": "pr", "source": "p", "target": "r"},
        {"id": "rs", "source": "r", "target": "s"},
    ]
    viewport = {"x": 125, "y": 75, "zoom": 1.5}
    async with _client() as c:
        created = await c.post(
            "/api/workflows/custom",
            json={
                "name": "partial-graph",
                "nodes": nodes,
                "edges": original_edges,
                "viewport": viewport,
            },
        )
        assert created.status_code == 201, created.text
        workflow_id = created.json()["id"]

        invalid_edges = [
            {"id": "ps", "source": "p", "target": "s"},
            {"id": "sr", "source": "s", "target": "r"},
        ]
        rejected = await c.put(
            f"/api/workflows/custom/{workflow_id}", json={"edges": invalid_edges}
        )
        assert rejected.status_code == 422
        stored = next(
            item
            for item in (await c.get("/api/workflows/custom")).json()
            if item["id"] == workflow_id
        )
        assert [(edge["source"], edge["target"]) for edge in stored["edges"]] == [
            ("p", "r"),
            ("r", "s"),
        ]

        reordered_edges = [
            {"id": "rp", "source": "r", "target": "p"},
            {"id": "ps", "source": "p", "target": "s"},
        ]
        reordered = await c.put(
            f"/api/workflows/custom/{workflow_id}", json={"edges": reordered_edges}
        )
        assert reordered.status_code == 200, reordered.text
        body = reordered.json()
        assert [node["id"] for node in body["nodes"]] == ["p", "r", "s"]
        assert [step["agent"] for step in body["steps"]] == [
            "researcher",
            "planner",
            "synthesizer",
        ]
        assert {key: body["viewport"][key] for key in ("x", "y", "zoom")} == viewport

        moved_nodes = [
            {**node, "position": {"x": node["position"]["x"] + 100, "y": 80}} for node in nodes
        ]
        moved = await c.put(f"/api/workflows/custom/{workflow_id}", json={"nodes": moved_nodes})
        assert moved.status_code == 200, moved.text
        moved_body = moved.json()
        assert [(edge["source"], edge["target"]) for edge in moved_body["edges"]] == [
            ("r", "p"),
            ("p", "s"),
        ]
        assert [step["agent"] for step in moved_body["steps"]] == [
            "researcher",
            "planner",
            "synthesizer",
        ]
        assert {key: moved_body["viewport"][key] for key in ("x", "y", "zoom")} == viewport

        invalid_viewport = await c.put(
            f"/api/workflows/custom/{workflow_id}", json={"viewport": {"zoom": 0}}
        )
        assert invalid_viewport.status_code == 422

        next_viewport = {"x": 200, "y": 100, "zoom": 2}
        viewport_only = await c.put(
            f"/api/workflows/custom/{workflow_id}", json={"viewport": next_viewport}
        )
        assert viewport_only.status_code == 200, viewport_only.text
        steps_only = await c.put(f"/api/workflows/custom/{workflow_id}", json={"steps": _VALID})
        assert steps_only.status_code == 200, steps_only.text
        assert {
            key: steps_only.json()["viewport"][key] for key in ("x", "y", "zoom")
        } == next_viewport


@pytest.mark.asyncio
async def test_edges_only_update_upgrades_legacy_steps_definition(cat_app):
    legacy = await api.app.state.catalog.create_workflow_def(
        WorkflowDefCreate(name="legacy-graph-patch", steps=_VALID)
    )
    async with _client() as c:
        response = await c.put(
            f"/api/workflows/custom/{legacy.id}",
            json={
                "edges": [
                    {"id": "rp", "source": "node-2", "target": "node-1"},
                    {"id": "ps", "source": "node-1", "target": "node-3"},
                ]
            },
        )
    assert response.status_code == 200, response.text
    assert [node["id"] for node in response.json()["nodes"]] == [
        "node-1",
        "node-2",
        "node-3",
    ]
    assert [step["agent"] for step in response.json()["steps"]] == [
        "researcher",
        "planner",
        "synthesizer",
    ]


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


@pytest.mark.asyncio
async def test_update_rejects_stale_workflow_version(cat_app):
    async with _client() as c:
        created = (
            await c.post("/api/workflows/custom", json={"name": "cas", "steps": _VALID})
        ).json()
        first = await c.put(
            f"/api/workflows/custom/{created['id']}",
            json={"description": "first", "version": created["version"]},
        )
        stale = await c.put(
            f"/api/workflows/custom/{created['id']}",
            json={"description": "stale", "version": created["version"]},
        )

        assert first.status_code == 200
        assert first.json()["version"] == created["version"] + 1
        assert stale.status_code == 409
        stored = next(
            item
            for item in (await c.get("/api/workflows/custom")).json()
            if item["id"] == created["id"]
        )
        assert stored["description"] == "first"


@pytest.mark.asyncio
async def test_duplicate_workflow_name_returns_409(cat_app):
    """重名自定义工作流：唯一约束冲突应翻译为 409 而非 500。"""
    async with _client() as c:
        first = await c.post("/api/workflows/custom", json={"name": "dup-flow", "steps": _VALID})
        assert first.status_code == 201, first.text
        conflict = await c.post("/api/workflows/custom", json={"name": "dup-flow", "steps": _VALID})
        assert conflict.status_code == 409
        assert "已存在" in conflict.json()["detail"]
