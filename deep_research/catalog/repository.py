"""catalog 仓储：模型档案 / 角色卡片 / 搜索 key 的增删改查（async SQLAlchemy 2.0）。

与 SqlRepository 同构：每个写方法独立事务，读方法预取关系。密钥在视图层脱敏。
"""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from ..persistence import orm
from ..security import SecretCipher
from .dto import (
    AgentCardCreate,
    AgentCardUpdate,
    AgentCardView,
    ModelProfileFull,
    ModelProfileView,
    SearchKeyView,
    WorkflowDefCreate,
    WorkflowDefUpdate,
    WorkflowDefView,
)


class WorkflowVersionConflictError(RuntimeError):
    """Raised when a workflow update is based on a stale version."""


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
        icon=r.icon,
        enabled=bool(r.enabled),
        model_profile_id=r.model_profile_id,
        model_profile_name=r.model_profile.name if r.model_profile else None,
    )


def _key_view(r: orm.SearchKeyRow, cipher: SecretCipher) -> SearchKeyView:
    return SearchKeyView(
        id=r.id,
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
            row = orm.AgentCardRow(
                name=payload.name,
                display_name=payload.display_name,
                description=payload.description,
                behavior=payload.behavior,
                system_prompt=payload.system_prompt,
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
            row = await s.get(orm.AgentCardRow, agent_id)
            if row is None:
                return None
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

    async def get_key_secret(self, key_id: str) -> str | None:
        """按 id 取明文 key（供「测试连接」单点验证）。不存在返回 None。"""
        async with self._sm() as s:
            row = await s.get(orm.SearchKeyRow, key_id)
            return self._cipher.decrypt(row.api_key) if row else None

    async def active_keys(self) -> list[str]:
        """按优先级升序返回启用的明文 key（供检索池故障转移）。"""
        async with self._sm() as s:
            rows = (
                await s.scalars(
                    select(orm.SearchKeyRow)
                    .where(orm.SearchKeyRow.enabled == 1)
                    .order_by(orm.SearchKeyRow.priority, orm.SearchKeyRow.created_at)
                )
            ).all()
            return [self._cipher.decrypt(r.api_key) for r in rows if r.api_key]

    async def create_key(
        self, *, label: str, api_key: str, priority: int, enabled: bool
    ) -> SearchKeyView:
        async with self._sm() as s, s.begin():
            row = orm.SearchKeyRow(
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
            row = await s.get(orm.SearchKeyRow, key_id)
            if row is None:
                return None
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
            row = await s.get(orm.SearchKeyRow, key_id)
            if row is None:
                return False
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
