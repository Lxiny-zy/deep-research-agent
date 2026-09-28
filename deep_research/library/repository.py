"""Persistence boundary for projects, corpora, sources and text chunks."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from sqlalchemy import case, func, select
from sqlalchemy import delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..persistence import orm
from .models import Corpus, LibrarySource, Project, ProjectSummary, SearchChunk, SourceChunk


class LibraryConflictError(ValueError):
    pass


class LibraryRepository(Protocol):
    async def create_project(self, owner_id: str, name: str, description: str = "") -> Project: ...

    async def list_projects(self, owner_id: str | None = None) -> list[ProjectSummary]: ...

    async def get_project(self, project_id: str) -> Project | None: ...

    async def create_corpus(self, project_id: str, name: str, description: str = "") -> Corpus: ...

    async def list_corpora(self, project_id: str) -> list[Corpus]: ...

    async def add_source(
        self,
        *,
        project_id: str,
        corpus_id: str,
        title: str,
        kind: str,
        status: str,
        origin_url: str,
        mime_type: str,
        content_hash: str,
        char_count: int,
        metadata: dict[str, object],
        chunks: list[dict[str, object]],
    ) -> LibrarySource: ...

    async def list_sources(
        self, project_id: str, corpus_id: str | None = None
    ) -> list[LibrarySource]: ...

    async def get_source(self, source_id: str) -> LibrarySource | None: ...

    async def list_chunks(self, source_id: str) -> list[SourceChunk]: ...

    async def set_source_status(self, source_id: str, status: str) -> LibrarySource | None: ...

    async def delete_source(self, source_id: str) -> bool: ...

    async def search_chunks(
        self, project_id: str, *, owner_id: str | None = None, limit: int = 5000
    ) -> list[SearchChunk]: ...


def _project(row: orm.ResearchProjectRow, counts: tuple[int, int, int] = (0, 0, 0)) -> Project:
    return Project(
        id=row.id,
        name=row.name,
        description=row.description,
        owner_id=row.owner_id,
        corpus_count=counts[0],
        source_count=counts[1],
        included_source_count=counts[2],
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _corpus(row: orm.CorpusRow, source_count: int = 0) -> Corpus:
    return Corpus(
        id=row.id,
        project_id=row.project_id,
        name=row.name,
        description=row.description,
        source_count=source_count,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _source(row: orm.LibrarySourceRow, chunk_count: int = 0) -> LibrarySource:
    return LibrarySource(
        id=row.id,
        project_id=row.project_id,
        corpus_id=row.corpus_id,
        title=row.title,
        kind=row.kind,  # type: ignore[arg-type]
        status=row.status,  # type: ignore[arg-type]
        origin_url=row.origin_url,
        mime_type=row.mime_type,
        content_hash=row.content_hash,
        char_count=row.char_count,
        chunk_count=chunk_count,
        metadata=row.source_metadata or {},
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _chunk(row: orm.SourceChunkRow) -> SourceChunk:
    return SourceChunk(
        id=row.id,
        source_id=row.source_id,
        ordinal=row.ordinal,
        content=row.content,
        content_hash=row.content_hash,
        locator=row.locator,
        start_char=row.start_char,
        end_char=row.end_char,
        page_start=row.page_start,
        page_end=row.page_end,
        section=row.section,
    )


class SqlLibraryRepository:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sessionmaker

    async def create_project(self, owner_id: str, name: str, description: str = "") -> Project:
        project = orm.ResearchProjectRow(
            owner_id=owner_id, name=name.strip(), description=description.strip()
        )
        project.corpora = [orm.CorpusRow(name="主资料库", description="项目默认资料库")]
        async with self._sm() as session, session.begin():
            duplicate = await session.scalar(
                select(orm.ResearchProjectRow.id).where(
                    orm.ResearchProjectRow.owner_id == owner_id,
                    func.lower(orm.ResearchProjectRow.name) == name.strip().lower(),
                )
            )
            if duplicate is not None:
                raise LibraryConflictError("同名研究项目已存在")
            session.add(project)
            await session.flush()
            await session.refresh(project)
        return _project(project, (1, 0, 0))

    async def list_projects(self, owner_id: str | None = None) -> list[ProjectSummary]:
        async with self._sm() as session:
            corpus_counts = (
                select(orm.CorpusRow.project_id, func.count(orm.CorpusRow.id).label("count"))
                .group_by(orm.CorpusRow.project_id)
                .subquery()
            )
            source_counts = (
                select(
                    orm.LibrarySourceRow.project_id,
                    func.count(orm.LibrarySourceRow.id).label("count"),
                    func.sum(case((orm.LibrarySourceRow.status == "included", 1), else_=0)).label(
                        "included"
                    ),
                )
                .group_by(orm.LibrarySourceRow.project_id)
                .subquery()
            )
            stmt = (
                select(
                    orm.ResearchProjectRow,
                    func.coalesce(corpus_counts.c.count, 0),
                    func.coalesce(source_counts.c.count, 0),
                    func.coalesce(source_counts.c.included, 0),
                )
                .outerjoin(corpus_counts, corpus_counts.c.project_id == orm.ResearchProjectRow.id)
                .outerjoin(source_counts, source_counts.c.project_id == orm.ResearchProjectRow.id)
                .order_by(orm.ResearchProjectRow.updated_at.desc())
            )
            if owner_id is not None:
                stmt = stmt.where(orm.ResearchProjectRow.owner_id == owner_id)
            rows = (await session.execute(stmt)).all()
            return [
                _project(row, (int(corpora), int(sources), int(included)))
                for row, corpora, sources, included in rows
            ]

    async def get_project(self, project_id: str) -> Project | None:
        projects = await self.list_projects()
        project = next((item for item in projects if item.id == project_id), None)
        return Project.model_validate(project) if project is not None else None

    async def create_corpus(self, project_id: str, name: str, description: str = "") -> Corpus:
        row = orm.CorpusRow(
            project_id=project_id, name=name.strip(), description=description.strip()
        )
        async with self._sm() as session, session.begin():
            duplicate = await session.scalar(
                select(orm.CorpusRow.id).where(
                    orm.CorpusRow.project_id == project_id,
                    func.lower(orm.CorpusRow.name) == name.strip().lower(),
                )
            )
            if duplicate is not None:
                raise LibraryConflictError("该项目中已存在同名资料库")
            session.add(row)
            project = await session.get(orm.ResearchProjectRow, project_id)
            if project is None:
                raise KeyError(project_id)
            project.updated_at = datetime.now(UTC)
            await session.flush()
            await session.refresh(row)
        return _corpus(row)

    async def list_corpora(self, project_id: str) -> list[Corpus]:
        async with self._sm() as session:
            rows = (
                await session.execute(
                    select(orm.CorpusRow, func.count(orm.LibrarySourceRow.id))
                    .outerjoin(orm.LibrarySourceRow)
                    .where(orm.CorpusRow.project_id == project_id)
                    .group_by(orm.CorpusRow.id)
                    .order_by(orm.CorpusRow.created_at, orm.CorpusRow.id)
                )
            ).all()
            return [_corpus(row, int(count)) for row, count in rows]

    async def add_source(
        self,
        *,
        project_id: str,
        corpus_id: str,
        title: str,
        kind: str,
        status: str,
        origin_url: str,
        mime_type: str,
        content_hash: str,
        char_count: int,
        metadata: dict[str, object],
        chunks: list[dict[str, object]],
    ) -> LibrarySource:
        row = orm.LibrarySourceRow(
            project_id=project_id,
            corpus_id=corpus_id,
            title=title,
            kind=kind,
            status=status,
            origin_url=origin_url,
            mime_type=mime_type,
            content_hash=content_hash,
            char_count=char_count,
            source_metadata=metadata,
        )
        row.chunks = [orm.SourceChunkRow(**chunk) for chunk in chunks]
        async with self._sm() as session, session.begin():
            corpus = await session.get(orm.CorpusRow, corpus_id)
            if corpus is None or corpus.project_id != project_id:
                raise KeyError(corpus_id)
            duplicate = await session.scalar(
                select(orm.LibrarySourceRow.id).where(
                    orm.LibrarySourceRow.corpus_id == corpus_id,
                    orm.LibrarySourceRow.content_hash == content_hash,
                )
            )
            if duplicate is not None:
                raise LibraryConflictError("相同内容已经导入该资料库")
            session.add(row)
            project = await session.get(orm.ResearchProjectRow, project_id)
            assert project is not None
            project.updated_at = datetime.now(UTC)
            await session.flush()
            await session.refresh(row)
        return _source(row, len(chunks))

    async def list_sources(
        self, project_id: str, corpus_id: str | None = None
    ) -> list[LibrarySource]:
        async with self._sm() as session:
            stmt = (
                select(orm.LibrarySourceRow, func.count(orm.SourceChunkRow.id))
                .outerjoin(orm.SourceChunkRow)
                .where(orm.LibrarySourceRow.project_id == project_id)
                .group_by(orm.LibrarySourceRow.id)
                .order_by(orm.LibrarySourceRow.created_at.desc())
            )
            if corpus_id is not None:
                stmt = stmt.where(orm.LibrarySourceRow.corpus_id == corpus_id)
            rows = (await session.execute(stmt)).all()
            return [_source(row, int(count)) for row, count in rows]

    async def get_source(self, source_id: str) -> LibrarySource | None:
        async with self._sm() as session:
            result = (
                await session.execute(
                    select(orm.LibrarySourceRow, func.count(orm.SourceChunkRow.id))
                    .outerjoin(orm.SourceChunkRow)
                    .where(orm.LibrarySourceRow.id == source_id)
                    .group_by(orm.LibrarySourceRow.id)
                )
            ).first()
            return _source(result[0], int(result[1])) if result is not None else None

    async def list_chunks(self, source_id: str) -> list[SourceChunk]:
        async with self._sm() as session:
            rows = (
                await session.scalars(
                    select(orm.SourceChunkRow)
                    .where(orm.SourceChunkRow.source_id == source_id)
                    .order_by(orm.SourceChunkRow.ordinal)
                )
            ).all()
            return [_chunk(row) for row in rows]

    async def set_source_status(self, source_id: str, status: str) -> LibrarySource | None:
        async with self._sm() as session, session.begin():
            row = await session.get(orm.LibrarySourceRow, source_id)
            if row is None:
                return None
            row.status = status
            row.updated_at = datetime.now(UTC)
            project = await session.get(orm.ResearchProjectRow, row.project_id)
            if project is not None:
                project.updated_at = datetime.now(UTC)
        return await self.get_source(source_id)

    async def delete_source(self, source_id: str) -> bool:
        async with self._sm() as session, session.begin():
            row = await session.get(orm.LibrarySourceRow, source_id)
            if row is None:
                return False
            project_id = row.project_id
            await session.execute(
                sa_delete(orm.LibrarySourceRow).where(orm.LibrarySourceRow.id == source_id)
            )
            project = await session.get(orm.ResearchProjectRow, project_id)
            if project is not None:
                project.updated_at = datetime.now(UTC)
            return True

    async def search_chunks(
        self, project_id: str, *, owner_id: str | None = None, limit: int = 5000
    ) -> list[SearchChunk]:
        async with self._sm() as session:
            stmt = (
                select(orm.SourceChunkRow, orm.LibrarySourceRow)
                .join(orm.LibrarySourceRow)
                .join(
                    orm.ResearchProjectRow,
                    orm.ResearchProjectRow.id == orm.LibrarySourceRow.project_id,
                )
                .where(
                    orm.LibrarySourceRow.project_id == project_id,
                    orm.LibrarySourceRow.status == "included",
                )
                .order_by(orm.LibrarySourceRow.created_at.desc(), orm.SourceChunkRow.ordinal)
                .limit(limit)
            )
            if owner_id is not None:
                stmt = stmt.where(orm.ResearchProjectRow.owner_id == owner_id)
            rows = (await session.execute(stmt)).all()
            return [
                SearchChunk(
                    **_chunk(chunk).model_dump(),
                    project_id=source.project_id,
                    source_title=source.title,
                    source_kind=source.kind,
                    origin_url=source.origin_url,
                )
                for chunk, source in rows
            ]


class InMemoryLibraryRepository:
    """Feature-complete in-memory implementation used by HTTP and executor tests."""

    def __init__(self) -> None:
        self.projects: dict[str, Project] = {}
        self.corpora: dict[str, Corpus] = {}
        self.sources: dict[str, LibrarySource] = {}
        self.chunks: dict[str, list[SourceChunk]] = {}

    async def create_project(self, owner_id: str, name: str, description: str = "") -> Project:
        if any(
            p.owner_id == owner_id and p.name.casefold() == name.strip().casefold()
            for p in self.projects.values()
        ):
            raise LibraryConflictError("同名研究项目已存在")
        now = datetime.now(UTC)
        project = Project(
            id=str(uuid4()),
            name=name.strip(),
            description=description.strip(),
            owner_id=owner_id,
            corpus_count=1,
            created_at=now,
            updated_at=now,
        )
        corpus = Corpus(
            id=str(uuid4()),
            project_id=project.id,
            name="主资料库",
            description="项目默认资料库",
            created_at=now,
            updated_at=now,
        )
        self.projects[project.id] = project
        self.corpora[corpus.id] = corpus
        return project.model_copy(deep=True)

    async def list_projects(self, owner_id: str | None = None) -> list[ProjectSummary]:
        result: list[ProjectSummary] = []
        for project in self.projects.values():
            if owner_id is not None and project.owner_id != owner_id:
                continue
            corpora = [c for c in self.corpora.values() if c.project_id == project.id]
            sources = [s for s in self.sources.values() if s.project_id == project.id]
            result.append(
                project.model_copy(
                    update={
                        "corpus_count": len(corpora),
                        "source_count": len(sources),
                        "included_source_count": sum(s.status == "included" for s in sources),
                    }
                )
            )
        return sorted(
            result,
            key=lambda item: item.updated_at or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )

    async def get_project(self, project_id: str) -> Project | None:
        project = self.projects.get(project_id)
        if project is None:
            return None
        summaries = await self.list_projects()
        return Project.model_validate(next(item for item in summaries if item.id == project_id))

    async def create_corpus(self, project_id: str, name: str, description: str = "") -> Corpus:
        if project_id not in self.projects:
            raise KeyError(project_id)
        if any(
            c.project_id == project_id and c.name.casefold() == name.strip().casefold()
            for c in self.corpora.values()
        ):
            raise LibraryConflictError("该项目中已存在同名资料库")
        now = datetime.now(UTC)
        corpus = Corpus(
            id=str(uuid4()),
            project_id=project_id,
            name=name.strip(),
            description=description.strip(),
            created_at=now,
            updated_at=now,
        )
        self.corpora[corpus.id] = corpus
        self.projects[project_id] = self.projects[project_id].model_copy(update={"updated_at": now})
        return corpus.model_copy(deep=True)

    async def list_corpora(self, project_id: str) -> list[Corpus]:
        result = []
        for corpus in self.corpora.values():
            if corpus.project_id == project_id:
                result.append(
                    corpus.model_copy(
                        update={
                            "source_count": sum(
                                s.corpus_id == corpus.id for s in self.sources.values()
                            )
                        }
                    )
                )
        return result

    async def add_source(
        self,
        *,
        project_id: str,
        corpus_id: str,
        title: str,
        kind: str,
        status: str,
        origin_url: str,
        mime_type: str,
        content_hash: str,
        char_count: int,
        metadata: dict[str, object],
        chunks: list[dict[str, object]],
    ) -> LibrarySource:
        corpus = self.corpora.get(corpus_id)
        if corpus is None or corpus.project_id != project_id:
            raise KeyError(corpus_id)
        if any(
            s.corpus_id == corpus_id and s.content_hash == content_hash
            for s in self.sources.values()
        ):
            raise LibraryConflictError("相同内容已经导入该资料库")
        now = datetime.now(UTC)
        source_id = str(uuid4())
        raw_chunks = list(chunks)
        source = LibrarySource(
            id=source_id,
            project_id=project_id,
            corpus_id=corpus_id,
            title=title,
            kind=kind,  # type: ignore[arg-type]
            status=status,  # type: ignore[arg-type]
            origin_url=origin_url,
            mime_type=mime_type,
            content_hash=content_hash,
            char_count=char_count,
            chunk_count=len(raw_chunks),
            metadata=metadata,
            created_at=now,
            updated_at=now,
        )
        self.sources[source_id] = source
        self.chunks[source_id] = [
            SourceChunk(id=str(uuid4()), source_id=source_id, **chunk) for chunk in raw_chunks
        ]
        self.projects[project_id] = self.projects[project_id].model_copy(update={"updated_at": now})
        return source.model_copy(deep=True)

    async def list_sources(
        self, project_id: str, corpus_id: str | None = None
    ) -> list[LibrarySource]:
        return [
            s.model_copy(deep=True)
            for s in self.sources.values()
            if s.project_id == project_id and (corpus_id is None or s.corpus_id == corpus_id)
        ]

    async def get_source(self, source_id: str) -> LibrarySource | None:
        source = self.sources.get(source_id)
        return source.model_copy(deep=True) if source else None

    async def list_chunks(self, source_id: str) -> list[SourceChunk]:
        return [chunk.model_copy(deep=True) for chunk in self.chunks.get(source_id, [])]

    async def set_source_status(self, source_id: str, status: str) -> LibrarySource | None:
        source = self.sources.get(source_id)
        if source is None:
            return None
        updated = source.model_copy(update={"status": status, "updated_at": datetime.now(UTC)})
        self.sources[source_id] = updated
        return updated.model_copy(deep=True)

    async def delete_source(self, source_id: str) -> bool:
        if source_id not in self.sources:
            return False
        del self.sources[source_id]
        self.chunks.pop(source_id, None)
        return True

    async def search_chunks(
        self, project_id: str, *, owner_id: str | None = None, limit: int = 5000
    ) -> list[SearchChunk]:
        project = self.projects.get(project_id)
        if project is None or (owner_id is not None and project.owner_id != owner_id):
            return []
        result: list[SearchChunk] = []
        for source in self.sources.values():
            if source.project_id != project_id or source.status != "included":
                continue
            result.extend(
                SearchChunk(
                    **chunk.model_dump(),
                    project_id=project_id,
                    source_title=source.title,
                    source_kind=source.kind,
                    origin_url=source.origin_url,
                )
                for chunk in self.chunks.get(source.id, [])
            )
        return result[:limit]


def content_digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
