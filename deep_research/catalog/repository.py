"""catalog 仓储：模型档案 / 角色卡片 / 搜索 key 的增删改查（async SQLAlchemy 2.0）。

与 SqlRepository 同构：每个写方法独立事务，读方法预取关系。密钥在视图层脱敏。
"""

from __future__ import annotations

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from ..config_service import ConfigStore
from ..persistence import orm
from ..persistence.coordination import transaction_lock
from ..security import SecretCipher
from .dto import (
    AgentCardCreate,
    AgentCardUpdate,
    AgentCardView,
    ModelProfileFull,
    ModelProfileView,
    SearchKeyView,
    SearchProfileInput,
    SearchProfileView,
    WorkflowDefCreate,
    WorkflowDefUpdate,
    WorkflowDefView,
)


class WorkflowVersionConflictError(RuntimeError):
    """Raised when a workflow update is based on a stale version."""


async def _lock_search_resources(session: AsyncSession) -> None:
    # JSON references cannot use SQL foreign keys. Serialize reference changes
    # and deletes in PostgreSQL, then validate within this same transaction.
    if session.get_bind().dialect.name == "postgresql":
        await session.execute(text("SELECT pg_advisory_xact_lock(73104920260909)"))


async def _validate_role_search(session: AsyncSession, ids: list[str] | None) -> None:
    from .search import BUILTIN_NAMES

    for profile_id in ids or []:
        if profile_id in {f"builtin:{p}" for p in BUILTIN_NAMES}:
            continue
        profile = await session.get(orm.SearchProfileRow, profile_id)
        if profile is None or not profile.enabled:
            raise ValueError(f"检索档案不存在或已停用：{profile_id}")


def mask(secret: str) -> str:
    """密钥脱敏：只露尾 4 位（与 api.py 的 _mask_secret 同语义）。"""
    if not secret:
        return ""
    tail = secret[-4:] if len(secret) >= 4 else secret
    return f"…{tail}"


def _profile_view(r: orm.ModelProfileRow, cipher: SecretCipher) -> ModelProfileView:
    secret = cipher.decrypt(r.api_key)
    return ModelProfileView(
        id=r.id,
        name=r.name,
        base_url=r.base_url,
        model=r.model,
        temperature=r.temperature,
        parameter_mode=r.parameter_mode,
        reasoning_effort=r.reasoning_effort,
        is_default=bool(r.is_default),
        api_key_set=bool(secret),
        api_key_hint=mask(secret),
    )


def _profile_full(r: orm.ModelProfileRow, cipher: SecretCipher) -> ModelProfileFull:
    return ModelProfileFull(
        id=r.id,
        name=r.name,
        base_url=r.base_url,
        api_key=cipher.decrypt(r.api_key),
        model=r.model,
        temperature=r.temperature,
        parameter_mode=r.parameter_mode,
        reasoning_effort=r.reasoning_effort,
        is_default=bool(r.is_default),
    )


def _agent_view(r: orm.AgentCardRow) -> AgentCardView:
    return AgentCardView(
        id=r.id,
        name=r.name,
        display_name=r.display_name,
        description=r.description,
        behavior=r.behavior,
        system_prompt=r.system_prompt,
        prompt_mode=r.prompt_mode,
        search_profile_ids=r.search_profile_ids,
        icon=r.icon,
        enabled=bool(r.enabled),
        model_profile_id=r.model_profile_id,
        model_profile_name=r.model_profile.name if r.model_profile else None,
    )


def _key_view(r: orm.SearchKeyRow, cipher: SecretCipher) -> SearchKeyView:
    return SearchKeyView(
        id=r.id,
        provider=r.provider,
        label=r.label,
        priority=r.priority,
        enabled=bool(r.enabled),
        api_key_hint=mask(cipher.decrypt(r.api_key)),
    )


def _workflow_view(r: orm.WorkflowDefRow) -> WorkflowDefView:
    return WorkflowDefView(
        id=r.id,
        name=r.name,
        display_name=r.display_name,
        description=r.description,
        steps=list(r.steps or []),
        nodes=list(r.nodes or []),
        edges=list(r.edges or []),
        viewport=dict(r.viewport or {}),
        version=r.version,
        enabled=bool(r.enabled),
    )


class CatalogRepository:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        *,
        secret_cipher: SecretCipher | None = None,
    ) -> None:
        self._sm = sessionmaker
        self._cipher = secret_cipher or SecretCipher.from_env()
        self.config_store = ConfigStore(sessionmaker, self._cipher)

    async def encrypt_legacy_secrets(self) -> int:
        """Encrypt legacy plaintext rows in place and verify encrypted rows."""
        if not self._cipher.enabled:
            return 0
        changed = 0
        async with self._sm() as s, s.begin():
            profiles = (await s.scalars(select(orm.ModelProfileRow))).all()
            keys = (await s.scalars(select(orm.SearchKeyRow))).all()
            for profile in profiles:
                if not profile.api_key:
                    continue
                # Decrypt first so a wrong deployment key fails at startup.
                plaintext = self._cipher.decrypt(profile.api_key)
                encrypted = self._cipher.encrypt(plaintext)
                if encrypted != profile.api_key:
                    profile.api_key = encrypted
                    changed += 1
            for key in keys:
                if not key.api_key:
                    continue
                # Decrypt first so a wrong deployment key fails at startup.
                plaintext = self._cipher.decrypt(key.api_key)
                encrypted = self._cipher.encrypt(plaintext)
                if encrypted != key.api_key:
                    key.api_key = encrypted
                    changed += 1
        return changed

    # ── 模型档案 ────────────────────────────────────────────────────────
    async def list_search_profiles(self) -> list[SearchProfileView]:
        async with self._sm() as s:
            rows = (
                await s.scalars(select(orm.SearchProfileRow).order_by(orm.SearchProfileRow.name))
            ).all()
            return [SearchProfileView.model_validate(row, from_attributes=True) for row in rows]

    async def save_search_profile(
        self, payload: SearchProfileInput, profile_id: str | None = None
    ) -> SearchProfileView | None:
        async with self._sm() as s, s.begin():
            await _lock_search_resources(s)
            for key_id in payload.key_ids:
                key = await s.get(orm.SearchKeyRow, key_id)
                if key is None or key.provider != payload.provider:
                    raise ValueError("Key 不存在或与检索档案的渠道不匹配")
            if profile_id:
                row = await s.get(orm.SearchProfileRow, profile_id)
                if row is None:
                    return None
            else:
                row = orm.SearchProfileRow()
                s.add(row)
            for name, value in payload.model_dump().items():
                setattr(row, name, value)
            await s.flush()
            return SearchProfileView.model_validate(row, from_attributes=True)

    async def delete_search_profile(self, profile_id: str) -> bool:
        async with self._sm() as s, s.begin():
            await _lock_search_resources(s)
            row = await s.get(orm.SearchProfileRow, profile_id)
            if row is None:
                return False
            cards = (await s.scalars(select(orm.AgentCardRow))).all()
            if any(profile_id in (card.search_profile_ids or []) for card in cards):
                raise ValueError("检索档案仍被角色引用，请先修改角色绑定")
            await s.delete(row)
            return True

    async def selected_key_secrets(
        self, provider: str, key_ids: list[str], *, preserve_order: bool = False
    ) -> list[str]:
        """Validate every frozen reference; resolve enabled keys in priority order."""
        async with self._sm() as s:
            rows = (
                await s.scalars(
                    select(orm.SearchKeyRow)
                    .where(orm.SearchKeyRow.id.in_(key_ids))
                    .order_by(orm.SearchKeyRow.priority, orm.SearchKeyRow.created_at)
                )
            ).all()
            if len(rows) != len(set(key_ids)) or any(row.provider != provider for row in rows):
                raise ValueError("检索档案引用的 Key 已删除或渠道已改变")
            if preserve_order:
                by_id = {row.id: row for row in rows}
                rows = [by_id[key_id] for key_id in key_ids]
            return [
                self._cipher.decrypt(row.api_key) for row in rows if row.enabled and row.api_key
            ]

    async def list_profiles(self) -> list[ModelProfileView]:
        async with self._sm() as s:
            stmt = select(orm.ModelProfileRow).order_by(orm.ModelProfileRow.name)
            rows = (await s.scalars(stmt)).all()
            return [_profile_view(r, self._cipher) for r in rows]

    async def get_default_profile(self) -> ModelProfileFull | None:
        async with self._sm() as s:
            row = await s.scalar(
                select(orm.ModelProfileRow).where(orm.ModelProfileRow.is_default == 1)
            )
            return _profile_full(row, self._cipher) if row else None

    async def get_profile_full(self, profile_id: str) -> ModelProfileFull | None:
        async with self._sm() as s:
            row = await s.get(orm.ModelProfileRow, profile_id)
            return _profile_full(row, self._cipher) if row else None

    async def create_profile(
        self,
        *,
        name: str,
        base_url: str | None,
        api_key: str,
        model: str,
        temperature: float,
        parameter_mode: str = "temperature",
        reasoning_effort: str = "medium",
        is_default: bool,
    ) -> ModelProfileView:
        async with self._sm() as s, s.begin():
            if is_default:  # 单一默认：先清掉其他默认标记
                await transaction_lock(s, "model-default")
                await s.execute(update(orm.ModelProfileRow).values(is_default=0))
            row = orm.ModelProfileRow(
                name=name,
                base_url=base_url or None,
                api_key=self._cipher.encrypt(api_key),
                model=model,
                temperature=temperature,
                parameter_mode=parameter_mode,
                reasoning_effort=reasoning_effort,
                is_default=1 if is_default else 0,
            )
            s.add(row)
            await s.flush()
            return _profile_view(row, self._cipher)

    async def update_profile(self, profile_id: str, fields: dict) -> ModelProfileView | None:
        async with self._sm() as s, s.begin():
            row = await s.get(orm.ModelProfileRow, profile_id)
            if row is None:
                return None
            if fields.get("is_default"):
                await transaction_lock(s, "model-default")
                await s.execute(update(orm.ModelProfileRow).values(is_default=0))
            for k, v in fields.items():
                if k == "is_default":
                    row.is_default = bool(v)
                elif k == "api_key" and not v:
                    continue  # 空＝保持不变（脱敏表单不清空）
                elif k == "api_key":
                    row.api_key = self._cipher.encrypt(str(v))
                elif k == "base_url":
                    row.base_url = v or None
                else:
                    setattr(row, k, v)
            await s.flush()
            return _profile_view(row, self._cipher)

    async def delete_profile(self, profile_id: str) -> bool:
        async with self._sm() as s, s.begin():
            row = await s.get(orm.ModelProfileRow, profile_id)
            if row is None:
                return False
            await s.delete(row)
            return True

    # ── 角色卡片 ────────────────────────────────────────────────────────
    async def list_agents(self) -> list[AgentCardView]:
        async with self._sm() as s:
            rows = (
                await s.scalars(
                    select(orm.AgentCardRow)
                    .options(selectinload(orm.AgentCardRow.model_profile))
                    .order_by(orm.AgentCardRow.name)
                )
            ).all()
            return [_agent_view(r) for r in rows]

    async def get_agent(self, name: str) -> AgentCardView | None:
        async with self._sm() as s:
            row = await s.scalar(
                select(orm.AgentCardRow)
                .options(selectinload(orm.AgentCardRow.model_profile))
                .where(orm.AgentCardRow.name == name)
            )
            return _agent_view(row) if row else None

    async def create_agent(self, payload: AgentCardCreate) -> AgentCardView:
        async with self._sm() as s, s.begin():
            await _lock_search_resources(s)
            await _validate_role_search(s, payload.search_profile_ids)
            row = orm.AgentCardRow(
                name=payload.name,
                display_name=payload.display_name,
                description=payload.description,
                behavior=payload.behavior,
                system_prompt=payload.system_prompt,
                prompt_mode=payload.prompt_mode,
                search_profile_ids=payload.search_profile_ids,
                icon=payload.icon,
                enabled=1 if payload.enabled else 0,
                model_profile_id=payload.model_profile_id,
            )
            s.add(row)
            await s.flush()
            await s.refresh(row, ["model_profile"])
            return _agent_view(row)

    async def update_agent(self, agent_id: str, payload: AgentCardUpdate) -> AgentCardView | None:
        fields = payload.model_dump(exclude_unset=True)
        async with self._sm() as s, s.begin():
            await _lock_search_resources(s)
            row = await s.get(orm.AgentCardRow, agent_id)
            if row is None:
                return None
            if "search_profile_ids" in fields:
                await _validate_role_search(s, payload.search_profile_ids)
            for k, v in fields.items():
                if k == "enabled":
                    row.enabled = bool(v)
                else:
                    setattr(row, k, v)
            await s.flush()
            await s.refresh(row, ["model_profile"])
            return _agent_view(row)

    async def delete_agent(self, agent_id: str) -> bool:
        async with self._sm() as s, s.begin():
            await _lock_search_resources(s)
            row = await s.get(orm.AgentCardRow, agent_id)
            if row is None:
                return False
            await s.delete(row)
            return True

    # ── 搜索 key 池 ─────────────────────────────────────────────────────
    async def list_keys(self) -> list[SearchKeyView]:
        async with self._sm() as s:
            rows = (
                await s.scalars(
                    select(orm.SearchKeyRow).order_by(
                        orm.SearchKeyRow.priority, orm.SearchKeyRow.created_at
                    )
                )
            ).all()
            return [_key_view(r, self._cipher) for r in rows]

    async def get_key_secret(self, key_id: str) -> tuple[str, str] | None:
        """按 id 取来源与明文 key（供「测试连接」单点验证）。"""
        async with self._sm() as s:
            row = await s.get(orm.SearchKeyRow, key_id)
            return (row.provider, self._cipher.decrypt(row.api_key)) if row else None

    async def active_keys(self, provider: str = "tavily") -> list[str]:
        """按来源和优先级返回启用的明文 key。"""
        async with self._sm() as s:
            rows = (
                await s.scalars(
                    select(orm.SearchKeyRow)
                    .where(orm.SearchKeyRow.enabled == 1)
                    .where(orm.SearchKeyRow.provider == provider)
                    .order_by(orm.SearchKeyRow.priority, orm.SearchKeyRow.created_at)
                )
            ).all()
            return [self._cipher.decrypt(r.api_key) for r in rows if r.api_key]

    async def create_key(
        self,
        *,
        label: str,
        api_key: str,
        priority: int,
        enabled: bool,
        provider: str = "tavily",
    ) -> SearchKeyView:
        async with self._sm() as s, s.begin():
            row = orm.SearchKeyRow(
                provider=provider,
                label=label,
                api_key=self._cipher.encrypt(api_key),
                priority=priority,
                enabled=1 if enabled else 0,
            )
            s.add(row)
            await s.flush()
            return _key_view(row, self._cipher)

    async def update_key(self, key_id: str, fields: dict) -> SearchKeyView | None:
        async with self._sm() as s, s.begin():
            await _lock_search_resources(s)
            row = await s.get(orm.SearchKeyRow, key_id)
            if row is None:
                return None
            if fields.get("provider", row.provider) != row.provider:
                profiles = (await s.scalars(select(orm.SearchProfileRow))).all()
                if any(key_id in profile.key_ids for profile in profiles):
                    raise ValueError("此 Key 被检索档案引用，不能改变渠道")
            for k, v in fields.items():
                if k == "enabled":
                    row.enabled = bool(v)
                elif k == "api_key" and not v:
                    continue  # 空＝保持不变
                elif k == "api_key":
                    row.api_key = self._cipher.encrypt(str(v))
                else:
                    setattr(row, k, v)
            await s.flush()
            return _key_view(row, self._cipher)

    async def delete_key(self, key_id: str) -> bool:
        async with self._sm() as s, s.begin():
            await _lock_search_resources(s)
            row = await s.get(orm.SearchKeyRow, key_id)
            if row is None:
                return False
            profiles = (await s.scalars(select(orm.SearchProfileRow))).all()
            if any(key_id in profile.key_ids for profile in profiles):
                raise ValueError("此 Key 被检索档案引用，请先解除绑定，或停用该 Key")
            await s.delete(row)
            return True

    # ── 自定义工作流 ────────────────────────────────────────────────────
    async def list_workflow_defs(self) -> list[WorkflowDefView]:
        async with self._sm() as s:
            rows = (
                await s.scalars(select(orm.WorkflowDefRow).order_by(orm.WorkflowDefRow.name))
            ).all()
            return [_workflow_view(r) for r in rows]

    async def get_workflow_def(self, name: str) -> WorkflowDefView | None:
        async with self._sm() as s:
            row = await s.scalar(select(orm.WorkflowDefRow).where(orm.WorkflowDefRow.name == name))
            return _workflow_view(row) if row else None

    async def get_workflow_def_by_id(self, workflow_id: str) -> WorkflowDefView | None:
        async with self._sm() as s:
            row = await s.get(orm.WorkflowDefRow, workflow_id)
            return _workflow_view(row) if row else None

    async def create_workflow_def(self, payload: WorkflowDefCreate) -> WorkflowDefView:
        async with self._sm() as s, s.begin():
            row = orm.WorkflowDefRow(
                name=payload.name,
                display_name=payload.display_name,
                description=payload.description,
                steps=payload.steps,
                nodes=payload.nodes,
                edges=payload.edges,
                viewport=payload.viewport,
                version=payload.version,
                enabled=1 if payload.enabled else 0,
            )
            s.add(row)
            await s.flush()
            return _workflow_view(row)

    async def update_workflow_def(
        self, workflow_id: str, payload: WorkflowDefUpdate
    ) -> WorkflowDefView | None:
        fields = payload.model_dump(exclude_unset=True)
        async with self._sm() as s, s.begin():
            row = await s.get(orm.WorkflowDefRow, workflow_id)
            if row is None:
                return None
            expected_version = fields.pop("version", None)
            if expected_version is None:
                expected_version = row.version
            values = {
                key: (bool(value) if key == "enabled" else value) for key, value in fields.items()
            }
            values["version"] = expected_version + 1
            result = await s.execute(
                update(orm.WorkflowDefRow)
                .where(
                    orm.WorkflowDefRow.id == workflow_id,
                    orm.WorkflowDefRow.version == expected_version,
                )
                .values(**values)
            )
            if not getattr(result, "rowcount", 0):
                raise WorkflowVersionConflictError(
                    f"workflow {workflow_id} changed since version {expected_version}"
                )
            await s.flush()
            await s.refresh(row)
            return _workflow_view(row)

    async def delete_workflow_def(self, workflow_id: str) -> bool:
        async with self._sm() as s, s.begin():
            row = await s.get(orm.WorkflowDefRow, workflow_id)
            if row is None:
                return False
            await s.delete(row)
            return True
