"""Read-only artifact inventory. Suspected orphans always require review."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from .artifact_lifecycle import disk_usage
from .blocking import run_blocking
from .config import Settings
from .persistence.orm import ResearchRun, WorkflowRunRow


async def inspect_artifacts(database_url: str, root: Path, *, grace_days: float = 7) -> dict:
    url = make_url(database_url)
    if url.get_backend_name() == "sqlite":
        database = await run_blocking(Path(url.database or "").resolve)
        if not await run_blocking(database.is_file):
            raise ValueError("inspection requires an existing SQLite database")
        url = url.set(database=f"file:{database.as_posix()}", query={"mode": "ro", "uri": "true"})
    engine = create_async_engine(url)
    run_ids: set[str] = set()
    slugs: set[str] = set()
    try:
        async with engine.connect() as connection:
            if url.get_backend_name() == "postgresql":
                await connection.execute(text("SET TRANSACTION READ ONLY"))
            result = await connection.stream(
                select(ResearchRun.id, WorkflowRunRow.checkpoint).outerjoin(
                    WorkflowRunRow, ResearchRun.id == WorkflowRunRow.research_run_id
                )
            )
            async for run_id, checkpoint in result:
                run_ids.add(run_id)
                scratch = checkpoint.get("scratch", {}) if isinstance(checkpoint, dict) else {}
                if isinstance(scratch, dict) and isinstance(scratch.get("_artifact_slug"), str):
                    slugs.add(scratch["_artifact_slug"])
    finally:
        await engine.dispose()

    def inventory() -> dict:
        resolved = root.resolve()
        candidates = []
        for area, references in (("runs", run_ids), ("work", slugs), ("output", slugs)):
            parent = resolved / area
            if not parent.is_dir() or parent.is_symlink():
                continue
            for child in parent.iterdir():
                if child.is_symlink() or not child.is_dir() or child.name in references:
                    continue
                age_days = (time.time() - child.stat().st_mtime) / 86400
                if age_days >= grace_days:
                    candidates.append(
                        {
                            "path": child.relative_to(resolved).as_posix(),
                            "bytes": disk_usage(child),
                            "directory_age_days": round(age_days, 2),
                        }
                    )
        return {
            "root": str(resolved),
            "used_bytes": disk_usage(resolved),
            "referenced_runs": len(run_ids),
            "unreferenced_directories": candidates,
            "read_only": True,
            "review_note": (
                "Unreferenced folders may belong to standalone CLI runs or backups. "
                "No files were removed."
            ),
        }

    return await run_blocking(inventory)


def main() -> None:
    settings = Settings()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=settings.database_url)
    parser.add_argument("--artifact-root", type=Path, default=Path(settings.artifact_root))
    parser.add_argument("--grace-days", type=float, default=7)
    args = parser.parse_args()
    if args.grace_days < 0:
        parser.error("--grace-days must be non-negative")
    report = asyncio.run(
        inspect_artifacts(args.database_url, args.artifact_root, grace_days=args.grace_days)
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
