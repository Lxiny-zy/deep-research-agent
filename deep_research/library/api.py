"""HTTP API for reusable research projects and source review."""

from __future__ import annotations

from typing import Literal, cast

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, field_validator

from ..access import Principal
from ..http.auth import principal_for
from ..upload_limits import DOCUMENT_MAX_BASE64_CHARS
from .ingestion import SourceImportError, prepare_source
from .models import Corpus, LibrarySource, Project, ProjectSummary, SourceChunk
from .repository import LibraryConflictError, LibraryRepository

router = APIRouter(prefix="/api/projects", tags=["research-library"])


class CreateProjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("项目名称不能为空")
        return cleaned


class CreateCorpusRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("资料库名称不能为空")
        return cleaned


class ImportSourceRequest(BaseModel):
    corpus_id: str = Field(min_length=1, max_length=36)
    title: str = Field(default="", max_length=300)
    kind: Literal["text", "markdown", "url", "doi", "pdf"]
    text: str = Field(default="", max_length=1_000_000)
    data_base64: str = Field(default="", max_length=DOCUMENT_MAX_BASE64_CHARS)
    origin_url: str = Field(default="", max_length=4000)
    mime_type: str = Field(default="", max_length=100)


class UpdateSourceRequest(BaseModel):
    status: Literal["included", "excluded"]


def _library(request: Request) -> LibraryRepository:
    repository = getattr(request.app.state, "library", None)
    if repository is None:
        raise HTTPException(503, "研究资料库尚未初始化")
    return cast(LibraryRepository, repository)


def _visible(project: Project, principal: Principal) -> bool:
    return principal.can_manage or project.owner_id == principal.id


async def _owned_project(request: Request, project_id: str) -> Project:
    project = await _library(request).get_project(project_id)
    if project is None or not _visible(project, principal_for(request)):
        raise HTTPException(404, "project not found")
    return project


async def _owned_source(request: Request, source_id: str) -> LibrarySource:
    source = await _library(request).get_source(source_id)
    if source is None:
        raise HTTPException(404, "source not found")
    await _owned_project(request, source.project_id)
    return source


@router.get("", response_model=list[ProjectSummary])
async def list_projects(request: Request) -> list[ProjectSummary]:
    principal = principal_for(request)
    return await _library(request).list_projects(
        owner_id=None if principal.can_manage else principal.id
    )


@router.post("", response_model=Project, status_code=201)
async def create_project(req: CreateProjectRequest, request: Request) -> Project:
    try:
        return await _library(request).create_project(
            principal_for(request).id, req.name, req.description
        )
    except LibraryConflictError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/{project_id}", response_model=Project)
async def get_project(project_id: str, request: Request) -> Project:
    return await _owned_project(request, project_id)


@router.get("/{project_id}/corpora", response_model=list[Corpus])
async def list_corpora(project_id: str, request: Request) -> list[Corpus]:
    await _owned_project(request, project_id)
    return await _library(request).list_corpora(project_id)


@router.post("/{project_id}/corpora", response_model=Corpus, status_code=201)
async def create_corpus(project_id: str, req: CreateCorpusRequest, request: Request) -> Corpus:
    await _owned_project(request, project_id)
    try:
        return await _library(request).create_corpus(project_id, req.name, req.description)
    except LibraryConflictError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/{project_id}/sources", response_model=list[LibrarySource])
async def list_sources(
    project_id: str,
    request: Request,
    corpus_id: str | None = Query(default=None, max_length=36),
) -> list[LibrarySource]:
    await _owned_project(request, project_id)
    if corpus_id is not None:
        corpora = await _library(request).list_corpora(project_id)
        if not any(corpus.id == corpus_id for corpus in corpora):
            raise HTTPException(404, "corpus not found")
    return await _library(request).list_sources(project_id, corpus_id)


@router.post("/{project_id}/sources/import", response_model=LibrarySource, status_code=201)
async def import_source(
    project_id: str, req: ImportSourceRequest, request: Request
) -> LibrarySource:
    await _owned_project(request, project_id)
    try:
        prepared = await prepare_source(
            kind=req.kind,
            title=req.title,
            text=req.text,
            data_base64=req.data_base64,
            origin_url=req.origin_url,
            mime_type=req.mime_type,
        )
        return await _library(request).add_source(
            project_id=project_id,
            corpus_id=req.corpus_id,
            title=prepared.title,
            kind=prepared.kind,
            status="included",
            origin_url=prepared.origin_url,
            mime_type=prepared.mime_type,
            content_hash=prepared.content_hash,
            char_count=prepared.char_count,
            metadata=prepared.metadata,
            chunks=prepared.chunks,
        )
    except KeyError as exc:
        raise HTTPException(404, "corpus not found") from exc
    except LibraryConflictError as exc:
        raise HTTPException(409, str(exc)) from exc
    except SourceImportError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/{project_id}/sources/{source_id}/chunks", response_model=list[SourceChunk])
async def list_source_chunks(
    project_id: str, source_id: str, request: Request
) -> list[SourceChunk]:
    source = await _owned_source(request, source_id)
    if source.project_id != project_id:
        raise HTTPException(404, "source not found")
    return await _library(request).list_chunks(source_id)


@router.patch("/{project_id}/sources/{source_id}", response_model=LibrarySource)
async def update_source(
    project_id: str, source_id: str, req: UpdateSourceRequest, request: Request
) -> LibrarySource:
    source = await _owned_source(request, source_id)
    if source.project_id != project_id:
        raise HTTPException(404, "source not found")
    updated = await _library(request).set_source_status(source_id, req.status)
    if updated is None:
        raise HTTPException(404, "source not found")
    return updated


@router.delete("/{project_id}/sources/{source_id}", status_code=204)
async def delete_source(project_id: str, source_id: str, request: Request) -> Response:
    source = await _owned_source(request, source_id)
    if source.project_id != project_id:
        raise HTTPException(404, "source not found")
    if not await _library(request).delete_source(source_id):
        raise HTTPException(404, "source not found")
    return Response(status_code=204)
