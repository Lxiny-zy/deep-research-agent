"""Run Alembic migrations with a cross-instance PostgreSQL lock."""

from __future__ import annotations

import asyncio

from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url

from alembic import command

from .config import Settings
from .persistence.db import _migration_root, make_engine

_POSTGRES_MIGRATION_LOCK = 0x4452414C454D4947


def _upgrade_head(database_url: str) -> None:
    root = _migration_root()
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.attributes["database_url"] = database_url
    command.upgrade(config, "head")


async def _wait_for_migration(task: asyncio.Task[None]) -> None:
    """Wait for a migration thread to finish before allowing cancellation.

    ``asyncio.to_thread`` cannot stop a synchronous Alembic migration once it
    has started.  Shielding the task prevents cancellation from interrupting
    the await, while the loop also handles a second cancellation request that
    arrives while we are draining the worker.  The caller can then safely
    release the database lock and the original cancellation is propagated.
    """
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True

    # Propagate a migration exception (if any) before restoring cancellation.
    task.result()
    if cancelled:
        raise asyncio.CancelledError


async def upgrade_head(database_url: str | None = None) -> None:
    """Upgrade the schema, serializing PostgreSQL deploys across instances."""
    resolved_url = database_url or Settings().database_url
    if make_url(resolved_url).get_backend_name() != "postgresql":
        await asyncio.to_thread(_upgrade_head, resolved_url)
        return

    engine = make_engine(resolved_url)
    try:
        async with engine.connect() as connection:
            await connection.execute(
                text("SELECT pg_advisory_lock(:lock_key)"),
                {"lock_key": _POSTGRES_MIGRATION_LOCK},
            )
            await connection.commit()
            migration_task = asyncio.create_task(asyncio.to_thread(_upgrade_head, resolved_url))
            try:
                await _wait_for_migration(migration_task)
            finally:
                await connection.execute(
                    text("SELECT pg_advisory_unlock(:lock_key)"),
                    {"lock_key": _POSTGRES_MIGRATION_LOCK},
                )
                await connection.commit()
    finally:
        await engine.dispose()


def main() -> None:
    asyncio.run(upgrade_head())
    print("database migration completed")


if __name__ == "__main__":
    main()
