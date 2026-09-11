"""Shared, versioned runtime configuration; credentials never enter JSON checkpoints."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .config import Settings
from .persistence.coordination import transaction_lock
from .persistence.orm import RuntimeConfigRow
from .runtime_config import EDITABLE_FIELDS, SECRET_FIELDS, apply_overrides
from .security import SecretCipher


class ConfigConflictError(ValueError):
    pass


class ConfigStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], cipher: SecretCipher) -> None:
        self.sessions = sessions
        self.cipher = cipher

    async def load(self, base: Settings) -> Settings:
        async with self.sessions() as session:
            row = await session.scalar(
                select(RuntimeConfigRow).order_by(RuntimeConfigRow.version.desc()).limit(1)
            )
            if row is None:
                return base
            values = dict(row.values)
            for name in SECRET_FIELDS:
                if name in values:
                    values[name] = self.cipher.decrypt(values[name])
            return replace(apply_overrides(base, values), runtime_config_version=row.version)

    async def save(
        self, settings: Settings, *, expected_version: int, secrets_changed: bool = False
    ) -> Settings:
        if secrets_changed and not self.cipher.enabled:
            raise ValueError(
                "保存服务凭据需要配置 CATALOG_ENCRYPTION_KEY；也可在 API 与 worker 环境中配置凭据"
            )
        values: dict[str, Any] = {
            name: getattr(settings, name) for name in EDITABLE_FIELDS if name not in SECRET_FIELDS
        }
        if self.cipher.enabled:
            values.update(
                {name: self.cipher.encrypt(getattr(settings, name)) for name in SECRET_FIELDS}
            )
        async with self.sessions() as session, session.begin():
            await transaction_lock(session, "runtime-config")
            current = (
                await session.scalar(
                    select(RuntimeConfigRow.version)
                    .order_by(RuntimeConfigRow.version.desc())
                    .limit(1)
                )
                or 0
            )
            if current != expected_version:
                raise ConfigConflictError("配置已被其他会话更新，请重新载入后保存")
            version = current + 1
            session.add(RuntimeConfigRow(version=version, values=values))
        return replace(settings, runtime_config_version=version)


async def effective_settings(base: Settings, catalog: Any) -> Settings:
    store = getattr(catalog, "config_store", None)
    return await store.load(base) if isinstance(store, ConfigStore) else base
