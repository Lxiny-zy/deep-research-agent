import asyncio

import pytest
from sqlalchemy import text

from alembic import command
from deep_research.document_corpus import FullTextCorpus, mark_complete_sources
from deep_research.models import Source
from deep_research.persistence.db import make_engine, make_sessionmaker
from deep_research.persistence.sql_repository import SqlRepository
from tests.test_migrations_pg import (
    _alembic_config,
    _isolated_database,
    _postgres_url,
    _run_migration,
)


async def roundtrip(url):
    await _run_migration(url, "0039")
    engine = make_engine(url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO research_run "
                    "(id,query,status,interpretation,elapsed,total_tokens) "
                    "VALUES ('old','legacy','done','',0,0)"
                )
            )
        await _run_migration(url, "head")
        repo = SqlRepository(make_sessionmaker(engine))
        run_id = await repo.create_run("full text")
        sources = mark_complete_sources(
            [
                Source(
                    url=f"https://example.org/paper.pdf#chunk-{i}",
                    content=body,
                    section_title="Methods",
                    section_start=i == 0,
                    section_end=i == 1,
                )
                for i, body in enumerate(["Method description", "T = 4"])
            ]
        )
        await repo.save_sources(run_id, sources)
        await engine.dispose()
        restored = (await repo.get_run(run_id)).sources
        assert next(iter(FullTextCorpus(restored, {}).documents.values())).complete
        by_url = {source.url: source for source in restored}
        for source in sources:
            assert by_url[source.url].document_content_hash == source.document_content_hash
            assert by_url[source.url].section_title == "Methods"
            assert by_url[source.url].section_start == source.section_start
            assert by_url[source.url].section_end == source.section_end
        # A later copy of the same bytes without metadata must not erase the manifest.
        await repo.save_sources(
            run_id, [Source(url=source.url, content=source.content) for source in sources]
        )
        assert next(
            iter(FullTextCorpus((await repo.get_run(run_id)).sources, {}).documents.values())
        ).complete
        await engine.dispose()
        await asyncio.to_thread(command.downgrade, _alembic_config(url), "0039")
        async with engine.connect() as connection:
            assert (
                await connection.execute(text("SELECT query FROM research_run WHERE id='old'"))
            ).scalar() == "legacy"
        await engine.dispose()
        await _run_migration(url, "head")
        assert not next(
            iter(FullTextCorpus((await repo.get_run(run_id)).sources, {}).documents.values())
        ).complete
    finally:
        await engine.dispose()


async def test_source_context_roundtrip_sqlite(tmp_path):
    await roundtrip(f"sqlite+aiosqlite:///{(tmp_path / 'context.db').as_posix()}")


@pytest.mark.pg
async def test_source_context_roundtrip_postgres():
    async with _isolated_database(_postgres_url()) as url:
        await roundtrip(url)
