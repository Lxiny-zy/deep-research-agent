"""自定义工作流仓储 CRUD：创建/列出/取/改/删，steps JSON 往返；缺失返回 None/False。"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from deep_research.catalog.dto import WorkflowDefCreate, WorkflowDefUpdate
from deep_research.catalog.repository import CatalogRepository, WorkflowVersionConflictError
from deep_research.persistence.db import create_all


@pytest.fixture
async def cat():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    await create_all(engine)
    yield CatalogRepository(async_sessionmaker(engine, expire_on_commit=False))
    await engine.dispose()


_STEPS = [
    {"kind": "agent", "agent": "planner"},
    {"kind": "agent", "agent": "researcher"},
    {"kind": "agent", "agent": "synthesizer"},
]


@pytest.mark.asyncio
async def test_workflow_def_crud_roundtrip(cat):
    v = await cat.create_workflow_def(
        WorkflowDefCreate(name="my-deep", display_name="我的深度", description="d", steps=_STEPS)
    )
    assert v.name == "my-deep" and len(v.steps) == 3 and v.enabled

    got = await cat.get_workflow_def("my-deep")
    assert got is not None and got.steps[0]["agent"] == "planner"  # JSON 往返保真
    by_id = await cat.get_workflow_def_by_id(v.id)
    assert by_id is not None and by_id.name == "my-deep"

    listed = await cat.list_workflow_defs()
    assert any(w.name == "my-deep" for w in listed)

    upd = await cat.update_workflow_def(
        v.id, WorkflowDefUpdate(description="d2", enabled=False, version=v.version)
    )
    assert upd is not None and upd.description == "d2" and not upd.enabled
    assert upd.version == 2
    with pytest.raises(WorkflowVersionConflictError):
        await cat.update_workflow_def(
            v.id, WorkflowDefUpdate(description="stale", version=v.version)
        )

    assert await cat.delete_workflow_def(v.id) is True
    assert await cat.get_workflow_def("my-deep") is None


@pytest.mark.asyncio
async def test_missing_returns_none_and_false(cat):
    assert await cat.get_workflow_def_by_id("nope") is None
    assert await cat.update_workflow_def("nope", WorkflowDefUpdate(description="x")) is None
    assert await cat.delete_workflow_def("nope") is False
