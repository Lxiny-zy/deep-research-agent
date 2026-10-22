"""Inspect and explicitly preserve orphan task conversations before migration.

Read-only: python -m deep_research.migration_preflight check
Preserve:  python -m deep_research.migration_preflight preserve \
    --conversation-id ID --services-stopped --apply

DATABASE_URL is read from the environment; credentials are never printed.
The preserve command must run while API and worker services are stopped.
It archives the original binding and all child rows in the same database
transaction before making the selected conversation standalone. It does not
delete messages, change their owner, or submit model work.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import sqlalchemy as sa
from fastapi.encoders import jsonable_encoder
from sqlalchemy.engine import Connection, CursorResult, RowMapping, make_url
from sqlalchemy.ext.asyncio import create_async_engine

# Same advisory lock as the normal PostgreSQL migration entry point.
_MIGRATION_LOCK = 0x4452414C454D4947


def _tables(connection: Connection) -> dict[str, sa.Table]:
    metadata = sa.MetaData()
    available = set(sa.inspect(connection).get_table_names())
    names = {"research_run", "qa_conversation", "qa_message", "qa_stream_event"}
    for name in sorted(names & available):
        sa.Table(name, metadata, autoload_with=connection)
    return dict(metadata.tables)


async def preflight(
    database_url: str, *, conversation_ids: list[str] | None = None,
) -> dict[str, Any]:
    """No IDs means inspection; IDs mean explicit transactional preservation."""
    try:
        url = make_url(database_url)
    except Exception as exc:
        raise ValueError("DATABASE_URL is invalid") from exc
    if url.get_backend_name() == "sqlite":
        database = url.database
        if (
            not database or database == ":memory:"
            or not await asyncio.to_thread(Path(database).is_file)
        ):
            raise ValueError("Select an existing SQLite database; no database will be created")
    # Do not use make_engine: its SQLite startup PRAGMAs can mutate the DB.
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            if not conversation_ids:
                if url.get_backend_name() == "sqlite":
                    await connection.execute(sa.text("PRAGMA query_only=ON"))
                elif url.get_backend_name() == "postgresql":
                    await connection.execute(sa.text("SET TRANSACTION READ ONLY"))
            if conversation_ids and url.get_backend_name() == "postgresql":
                await connection.execute(
                    sa.text("SELECT pg_advisory_xact_lock(:key)"), {"key": _MIGRATION_LOCK},
                )
            tables = await connection.run_sync(_tables)
            if "qa_conversation" not in tables:
                return {"orphan_count": 0, "conversations": [], "preserved": []}
            if "research_run" not in tables or "run_id" not in tables["qa_conversation"].c:
                raise ValueError("Schema does not support task-bound conversations")
            chats, runs = tables["qa_conversation"], tables["research_run"]
            orphan = sa.and_(
                chats.c.run_id.is_not(None),
                ~sa.exists(sa.select(runs.c.id).where(runs.c.id == chats.c.run_id)),
            )
            if not conversation_ids:
                count = await connection.scalar(
                    sa.select(sa.func.count()).select_from(chats).where(orphan)
                )
                rows = (await connection.execute(
                    sa.select(chats.c.id, chats.c.run_id)
                    .where(orphan).order_by(chats.c.id).limit(200),
                )).mappings().all()
                return {
                    "orphan_count": count, "conversations": [dict(row) for row in rows],
                    "listed_limit": 200, "preserved": [],
                }

            # Match the application admission lock as a second line
            # of defence; maintenance still requires stopped services.
            await connection.execute(sa.text(
                "INSERT INTO coordination (name) VALUES ('qa:admission') ON CONFLICT DO NOTHING"
            ))
            await connection.execute(sa.text(
                "UPDATE coordination SET name=name WHERE name='qa:admission'"
            ))
            selected = sorted(set(conversation_ids))
            if not selected or any(not cid or len(cid) > 64 for cid in selected):
                raise ValueError("Invalid conversation selection")
            snapshots: list[dict[str, Any]] = []
            messages = tables.get("qa_message")
            events = tables.get("qa_stream_event")
            for cid in selected:
                lock_name = f"qa:{cid}"
                await connection.execute(sa.text(
                    "INSERT INTO coordination (name) VALUES (:name) ON CONFLICT DO NOTHING"
                ), {"name": lock_name})
                await connection.execute(sa.text(
                    "UPDATE coordination SET name=name WHERE name=:name"
                ), {"name": lock_name})
                row = (await connection.execute(
                    sa.select(chats).where(orphan, chats.c.id == cid).with_for_update(),
                )).mappings().one_or_none()
                if row is None:
                    raise ValueError("A selected conversation is missing or no longer orphaned")
                child_rows = [] if messages is None else (await connection.execute(
                    sa.select(messages).where(messages.c.conversation_id == cid),
                )).mappings().all()
                if any(item.get("status") in {"pending", "running"} for item in child_rows):
                    raise ValueError(
                        "Selected conversation has active requests; resolve them first"
                    )
                event_rows: Sequence[RowMapping] = []
                if events is not None and messages is not None:
                    event_rows = (await connection.execute(sa.select(events).where(
                        events.c.message_id.in_(sa.select(messages.c.id).where(
                            messages.c.conversation_id == cid,
                        )),
                    ))).mappings().all()
                snapshots.append({
                    "conversation": dict(row), "messages": [dict(item) for item in child_rows],
                    "events": [dict(item) for item in event_rows],
                })

            # An archival table is intentionally outside runtime ORM metadata;
            # migration autogeneration ignores it explicitly in alembic/env.py.
            archive = sa.Table(
                "qa_orphan_archive", sa.MetaData(),
                sa.Column("id", sa.String(36), primary_key=True),
                sa.Column("conversation_id", sa.String(64), nullable=False),
                sa.Column("original_run_id", sa.String(64), nullable=False),
                sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
                sa.Column("snapshot", sa.JSON(), nullable=False),
            )
            await connection.run_sync(lambda conn: archive.create(conn, checkfirst=True))
            for snapshot in snapshots:
                chat = snapshot["conversation"]
                await connection.execute(archive.insert().values(
                    id=str(uuid4()), conversation_id=chat["id"], original_run_id=chat["run_id"],
                    created_at=datetime.now(UTC), snapshot=jsonable_encoder(snapshot),
                ))
                result = await connection.execute(chats.update().where(
                    chats.c.id == chat["id"], orphan,
                ).values(run_id=None))
                if cast(CursorResult[Any], result).rowcount != 1:
                    raise ValueError("Conversation binding changed; preservation rolled back")
            return {"preserved": selected, "archive_table": archive.name}
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "preserve"))
    parser.add_argument("--conversation-id", action="append", default=[])
    parser.add_argument("--services-stopped", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.action == "preserve" and not (
        args.conversation_id and args.services_stopped and args.apply
    ):
        parser.error("preserve requires explicit IDs, --services-stopped and --apply")
    if args.action == "check" and (args.conversation_id or args.apply):
        parser.error("check does not accept mutation options")
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        parser.error("Set DATABASE_URL for the database to inspect")
    try:
        result = asyncio.run(preflight(
            database_url,
            conversation_ids=args.conversation_id if args.action == "preserve" else None,
        ))
    except ValueError as exc:
        parser.exit(2, f"{exc}\n")
    except Exception as exc:
        # SQLAlchemy exceptions can contain credentials or research payloads.
        parser.exit(2, f"Preflight failed ({type(exc).__name__}); no success is claimed.\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("orphan_count", 0):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
