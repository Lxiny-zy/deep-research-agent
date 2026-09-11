"""Small database locks shared by SQLite and PostgreSQL deployments."""

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .orm import CoordinationRow


async def transaction_lock(session: AsyncSession, name: str) -> None:
    insert = pg_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert
    await session.execute(insert(CoordinationRow).values(name=name).on_conflict_do_nothing())
    # UPDATE forces SQLite to acquire its writer lock too; SELECT FOR UPDATE alone does not.
    await session.execute(
        update(CoordinationRow).where(CoordinationRow.name == name).values(name=name)
    )
