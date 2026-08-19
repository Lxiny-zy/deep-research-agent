"""Smoke-test the installed wheel outside the source checkout."""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path


def _resolved_path(value: str) -> Path:
    return Path(value).resolve()


def _path(value: str) -> Path:
    return Path(value)


async def _run() -> None:
    import deep_research
    from deep_research.api import _FRONTEND_ASSETS, _FRONTEND_DIST, app, lifespan
    from deep_research.intent.model import QUERY_MODEL_PATH
    from deep_research.persistence.db import _migration_root

    source_root = _resolved_path(__file__).parents[1]
    package_path = _resolved_path(str(deep_research.__file__))
    if package_path == source_root / "deep_research" / "__init__.py":
        raise RuntimeError(
            f"smoke imported source checkout instead of installed wheel: {package_path}"
        )
    if not QUERY_MODEL_PATH.is_file():
        raise RuntimeError(f"intent model resource is missing: {QUERY_MODEL_PATH}")
    if not _FRONTEND_DIST.is_file() or not _FRONTEND_ASSETS.is_dir():
        raise RuntimeError(
            f"frontend resources are missing: index={_FRONTEND_DIST}, assets={_FRONTEND_ASSETS}"
        )
    migration_root = _migration_root()
    if not (migration_root / "alembic" / "versions").is_dir():
        raise RuntimeError(f"migration resources are missing: {migration_root}")

    with tempfile.TemporaryDirectory(prefix="deep-research-wheel-") as directory:
        root = _path(directory)
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{root / 'smoke.db'}"
        os.environ["RUNTIME_CONFIG_PATH"] = str(root / "runtime_config.json")
        async with lifespan(app):
            if not await app.state.repo.healthcheck():
                raise RuntimeError("installed package repository healthcheck failed")
        if not (root / "smoke.db").is_file():
            raise RuntimeError("installed package did not initialize SQLite")


def main() -> None:
    asyncio.run(_run())
    print("installed package smoke passed")


if __name__ == "__main__":
    main()
