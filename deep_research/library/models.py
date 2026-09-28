"""Wire and repository models for the reusable research library."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

SourceKind = Literal["text", "markdown", "url", "doi", "pdf"]
SourceStatus = Literal["included", "excluded"]


class ProjectSummary(BaseModel):
    id: str
    name: str
    description: str = ""
    owner_id: str
    corpus_count: int = 0
    source_count: int = 0
    included_source_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


class Project(ProjectSummary):
    pass


class Corpus(BaseModel):
    id: str
    project_id: str
    name: str
    description: str = ""
    source_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


class LibrarySource(BaseModel):
    id: str
    project_id: str
    corpus_id: str
    title: str
    kind: SourceKind
    status: SourceStatus = "included"
    origin_url: str = ""
    mime_type: str = "text/plain"
    content_hash: str
    char_count: int
    chunk_count: int = 0
    metadata: dict[str, object] = Field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class SourceChunk(BaseModel):
    id: str
    source_id: str
    ordinal: int
    content: str
    content_hash: str
    locator: str
    start_char: int = 0
    end_char: int = 0
    page_start: int | None = None
    page_end: int | None = None
    section: str = ""


class SearchChunk(SourceChunk):
    project_id: str
    source_title: str
    source_kind: SourceKind
    origin_url: str = ""
