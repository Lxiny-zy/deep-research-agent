"""catalog 仓储 CRUD：模型档案 / 角色卡片 / 搜索 key，及密钥脱敏、单一默认、故障转移取 key。"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from deep_research.catalog.dto import AgentCardCreate, AgentCardUpdate
from deep_research.catalog.repository import CatalogRepository
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


@pytest.mark.asyncio
async def test_profile_crud_and_mask(cat):
    v = await cat.create_profile(
        name="gpt", base_url="https://x/v1", api_key="sk-secret9999",
        model="gpt-4o", temperature=0.5, is_default=True,
    )
    assert v.is_default and v.api_key_set and v.api_key_hint == "…9999"
    assert "secret" not in v.api_key_hint  # 脱敏：明文不外泄

    full = await cat.get_default_profile()
    assert full is not None and full.api_key == "sk-secret9999"  # 内部视图保留明文

    # 空 api_key 更新＝保持不变
    await cat.update_profile(v.id, {"api_key": "", "model": "gpt-4o-mini"})
    full2 = await cat.get_profile_full(v.id)
    assert full2.api_key == "sk-secret9999" and full2.model == "gpt-4o-mini"


@pytest.mark.asyncio
async def test_single_default_profile(cat):
    a = await cat.create_profile(
        name="a", base_url=None, api_key="k", model="m", temperature=0.3, is_default=True
    )
    b = await cat.create_profile(
        name="b", base_url=None, api_key="k", model="m", temperature=0.3, is_default=True
    )
    # 设 b 为默认后，a 不再是默认
    profiles = {p.id: p for p in await cat.list_profiles()}
    assert not profiles[a.id].is_default
    assert profiles[b.id].is_default


@pytest.mark.asyncio
async def test_agent_crud_with_profile_binding(cat):
    prof = await cat.create_profile(
        name="cheap", base_url=None, api_key="k", model="deepseek",
        temperature=0.3, is_default=False,
    )
    card = await cat.create_agent(
        AgentCardCreate(
            name="my-critic", display_name="我的评审员", behavior="critique",
            system_prompt="挑刺", icon="🔍", model_profile_id=prof.id,
        )
    )
    assert card.name == "my-critic"
    assert card.model_profile_name == "cheap"  # 视图带出绑定模型名

    updated = await cat.update_agent(card.id, AgentCardUpdate(enabled=False))
    assert updated is not None and not updated.enabled

    assert await cat.delete_agent(card.id) is True
    assert await cat.get_agent("my-critic") is None


@pytest.mark.asyncio
async def test_search_key_pool_priority_order(cat):
    await cat.create_key(label="备", api_key="k-backup", priority=10, enabled=True)
    await cat.create_key(label="主", api_key="k-primary", priority=1, enabled=True)
    await cat.create_key(label="停", api_key="k-disabled", priority=0, enabled=False)

    keys = await cat.list_keys()
    assert [k.label for k in keys] == ["停", "主", "备"]  # 按 priority 升序
    assert all("k-" not in k.api_key_hint for k in keys)  # 脱敏

    # active_keys 只取启用的，按优先级：主(1) 在 备(10) 前；停(禁用)排除
    assert await cat.active_keys() == ["k-primary", "k-backup"]
