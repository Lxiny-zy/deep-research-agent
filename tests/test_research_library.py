from __future__ import annotations

import asyncio

import httpx
import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool

from deep_research import api
from deep_research.access import ApiCredential, Principal
from deep_research.config import Settings
from deep_research.library.ingestion import SourceImportError, normalize_doi, prepare_source
from deep_research.library.models import SearchChunk
from deep_research.library.repository import (
    InMemoryLibraryRepository,
    LibraryConflictError,
    SqlLibraryRepository,
)
from deep_research.library.search import ProjectCorpusSearch
from deep_research.persistence.db import create_all, make_sessionmaker
from deep_research.persistence.memory_repository import InMemoryRepository

ADMIN = "library-test-administrator"
ALICE = "library-test-alice-credential"
BOB = "library-test-bob-credential"
READER = "library-test-reader-credential"


def _headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


@pytest.fixture(params=["memory", "sqlite"])
async def library_repo(request):  # type: ignore[no-untyped-def]
    if request.param == "memory":
        yield InMemoryLibraryRepository()
        return
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    await create_all(engine)
    yield SqlLibraryRepository(make_sessionmaker(engine))
    await engine.dispose()


@pytest.mark.asyncio
async def test_library_roundtrip_review_and_project_search(library_repo) -> None:
    project = await library_repo.create_project("alice", "Evidence review", "Reusable sources")
    corpora = await library_repo.list_corpora(project.id)
    assert len(corpora) == 1
    prepared = await prepare_source(
        kind="markdown",
        title="Benchmark notes",
        text="# Results\n\nAlpha reaches 91.2 percent accuracy on Dataset Z. " * 80,
    )
    source = await library_repo.add_source(
        project_id=project.id,
        corpus_id=corpora[0].id,
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
    assert source.chunk_count >= 2
    assert (await library_repo.list_chunks(source.id))[0].locator == "片段 1"

    search = ProjectCorpusSearch(library_repo, project.id, "alice")
    results = await search.search("Dataset Z accuracy", max_results=3)
    assert results
    assert results[0].locator.startswith("片段")
    assert "91.2 percent" in results[0].content

    await library_repo.set_source_status(source.id, "excluded")
    assert await search.search("Dataset Z accuracy", max_results=3) == []
    assert (
        await ProjectCorpusSearch(library_repo, project.id, "bob").search(
            "Dataset Z", max_results=3
        )
        == []
    )


class _ChunkSearchRepository:
    def __init__(self, chunks: list[SearchChunk]) -> None:
        self.chunks = chunks

    async def search_chunks(
        self, project_id: str, *, owner_id: str | None = None, limit: int = 5000
    ) -> list[SearchChunk]:
        if project_id != "project" or owner_id != "alice":
            return []
        return self.chunks[:limit]


def _search_chunk(
    chunk_id: str,
    source_id: str,
    title: str,
    content: str,
    *,
    ordinal: int = 0,
    section: str = "",
) -> SearchChunk:
    return SearchChunk(
        id=chunk_id,
        source_id=source_id,
        project_id="project",
        source_title=title,
        source_kind="text",
        ordinal=ordinal,
        content=content,
        content_hash=f"hash-{chunk_id}",
        locator=f"片段 {ordinal + 1}",
        start_char=0,
        end_char=len(content),
        section=section,
    )


@pytest.mark.asyncio
async def test_project_search_fuses_bm25_phrase_fields_and_source_diversity() -> None:
    chunks = [
        _search_chunk(
            "a-1",
            "source-a",
            "Quantum sensor calibration guide",
            "The quantum sensor calibration procedure uses a traceable reference.",
        ),
        _search_chunk(
            "a-2",
            "source-a",
            "Quantum sensor calibration guide",
            "Quantum readings and sensor logs are reviewed before calibration.",
            ordinal=1,
        ),
        _search_chunk(
            "b-1",
            "source-b",
            "Independent validation",
            "An independent quantum sensor calibration reproduced the result.",
        ),
        _search_chunk(
            "noise",
            "source-c",
            "General model notes",
            "model model model with unrelated calibration records",
        ),
    ]
    search = ProjectCorpusSearch(_ChunkSearchRepository(chunks), "project", "alice")  # type: ignore[arg-type]

    results = await search.search("quantum sensor calibration", max_results=3)

    assert results[0].title == "Quantum sensor calibration guide"
    assert {result.title for result in results[:2]} == {
        "Quantum sensor calibration guide",
        "Independent validation",
    }
    assert len({result.url for result in results}) == len(results)


@pytest.mark.asyncio
async def test_project_search_uses_rare_terms_and_bounds_empty_or_large_requests() -> None:
    chunks = [
        _search_chunk("rare", "rare-source", "ZXQV evaluation", "ZXQV model benchmark evidence."),
        *[
            _search_chunk(
                f"common-{index}",
                f"common-source-{index}",
                "General model review",
                "model architecture and model evaluation",
            )
            for index in range(25)
        ],
    ]
    search = ProjectCorpusSearch(_ChunkSearchRepository(chunks), "project", "alice")  # type: ignore[arg-type]

    assert (await search.search("model ZXQV", max_results=5))[0].title == "ZXQV evaluation"
    assert await search.search("   ", max_results=5) == []
    assert len(await search.search("model", max_results=100)) == 20


@pytest.mark.asyncio
async def test_duplicate_source_snapshot_is_rejected(library_repo) -> None:
    project = await library_repo.create_project("alice", "Duplicate control")
    corpus = (await library_repo.list_corpora(project.id))[0]
    prepared = await prepare_source(kind="text", title="Same", text="same evidence")
    kwargs = {
        "project_id": project.id,
        "corpus_id": corpus.id,
        "title": prepared.title,
        "kind": prepared.kind,
        "status": "included",
        "origin_url": prepared.origin_url,
        "mime_type": prepared.mime_type,
        "content_hash": prepared.content_hash,
        "char_count": prepared.char_count,
        "metadata": prepared.metadata,
        "chunks": prepared.chunks,
    }
    await library_repo.add_source(**kwargs)
    with pytest.raises(LibraryConflictError):
        await library_repo.add_source(**kwargs)


@pytest.mark.asyncio
async def test_ingestion_extracts_html_and_validates_doi() -> None:
    prepared = await prepare_source(
        kind="url",
        title="",
        text=(
            "<html><head><title>Study</title></head><body>"
            "<script>ignore me</script><h1>Finding</h1>"
            "<p>Measured value is 42.</p></body></html>"
        ),
        mime_type="text/html",
        origin_url="https://example.org/study",
    )
    assert prepared.title == "Study"
    assert "ignore me" not in prepared.chunks[0]["content"]
    assert normalize_doi("https://doi.org/10.1234/ABC.9") == "10.1234/ABC.9"
    with pytest.raises(SourceImportError):
        normalize_doi("not-a-doi")


@pytest.fixture
async def library_client(monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(api, "_run_limiter", api._RateLimiter(max_calls=10, window_seconds=60))
    run_repo = InMemoryRepository()
    library = InMemoryLibraryRepository()
    settings = Settings(
        api_key=ADMIN,
        api_credentials=(
            ApiCredential(Principal("alice", "researcher"), ALICE),
            ApiCredential(Principal("bob", "researcher"), BOB),
            ApiCredential(Principal("viewer", "reader"), READER),
        ),
        execution_mode="worker",
    )
    for name, value in {
        "settings": settings,
        "repo": run_repo,
        "library": library,
        "catalog": None,
        "live": {},
        "tasks": set(),
        "run_tasks": {},
        "cancellation_requested": set(),
        "config_lock": asyncio.Lock(),
    }.items():
        monkeypatch.setattr(api.app.state, name, value, raising=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test"
    ) as client:
        yield client, run_repo


@pytest.mark.asyncio
async def test_project_api_enforces_ownership_and_binds_run(library_client) -> None:
    client, run_repo = library_client
    created = await client.post(
        "/api/projects",
        headers=_headers(ALICE),
        json={"name": "Private corpus", "description": "Alice only"},
    )
    assert created.status_code == 201
    project_id = created.json()["id"]
    corpora = await client.get(f"/api/projects/{project_id}/corpora", headers=_headers(ALICE))
    corpus_id = corpora.json()[0]["id"]

    imported = await client.post(
        f"/api/projects/{project_id}/sources/import",
        headers=_headers(ALICE),
        json={
            "corpus_id": corpus_id,
            "kind": "text",
            "title": "Internal finding",
            "text": "The controlled experiment measured a 17 percent improvement.",
        },
    )
    assert imported.status_code == 201
    source_id = imported.json()["id"]
    chunks = await client.get(
        f"/api/projects/{project_id}/sources/{source_id}/chunks",
        headers=_headers(ALICE),
    )
    assert chunks.json()[0]["locator"] == "片段 1"

    assert (
        await client.get(f"/api/projects/{project_id}", headers=_headers(BOB))
    ).status_code == 404
    assert (
        await client.post("/api/projects", headers=_headers(READER), json={"name": "Denied"})
    ).status_code == 403

    run = await client.post(
        "/api/runs",
        headers=_headers(ALICE),
        json={
            "query": "Analyze the controlled experiment",
            "project_id": project_id,
            "clarified": True,
        },
    )
    assert run.status_code == 202
    detail = await run_repo.get_run(run.json()["run_id"])
    assert detail is not None and detail.project_id == project_id
    assert detail.orchestration is not None
    assert detail.orchestration.checkpoint["scratch"]["project_id"] == project_id

    for template, query, extra in (
        ("paperRead", "https://arxiv.org/abs/2205.10102", {}),
        ("dataAnalysis", "compare the uploaded measurements", {"demo_data": True}),
    ):
        unsupported = await client.post(
            "/api/runs",
            headers=_headers(ALICE),
            json={
                "query": query,
                "template": template,
                "project_id": project_id,
                "clarified": True,
                **extra,
            },
        )
        assert unsupported.status_code == 422
        assert unsupported.json()["detail"]["code"] == "library_unsupported"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("steps", "nodes", "consumes_library"),
    [
        ([{"agent": "synthesizer"}], [], False),
        ([{"agent": "researcher"}, {"agent": "synthesizer"}], [], True),
        ([{"kind": "reflect_loop"}, {"agent": "synthesizer"}], [], True),
        ([{"kind": "team_fanout"}], [], True),
        ([{"kind": "compose", "agent": "coordinator"}], [], True),
        ([], [{"id": "search", "step": {"agent": "researcher"}}], True),
        (
            [{"agent": "researcher"}],
            [{"id": "write", "step": {"agent": "synthesizer"}}],
            False,
        ),
    ],
)
async def test_library_binding_checks_executable_workflow_steps(
    library_client, monkeypatch, steps, nodes, consumes_library
) -> None:
    from deep_research.workflow import Workflow

    client, run_repo = library_client
    project = await client.post(
        "/api/projects", headers=_headers(ALICE), json={"name": "Library input"}
    )
    # Serialize through the real model: ordinary steps also carry the default
    # researcher field, but only reflect_loop executes that field.
    workflow = Workflow(name="deep", steps=steps, nodes=nodes)
    monkeypatch.setattr("deep_research.orchestrator.get_workflow", lambda _: workflow)
    response = await client.post(
        "/api/runs",
        headers=_headers(ALICE),
        json={
            "query": "Analyze the controlled experiment",
            "workflow": "deep",
            "project_id": project.json()["id"],
            "clarified": True,
        },
    )
    if consumes_library:
        assert response.status_code == 202, response.text
        detail = await run_repo.get_run(response.json()["run_id"])
        assert detail is not None and detail.project_id == project.json()["id"]
    else:
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["code"] == "library_unsupported"
        assert not await run_repo.list_runs()


@pytest.mark.asyncio
async def test_project_api_review_delete_and_error_paths(library_client) -> None:
    client, _ = library_client
    alice = _headers(ALICE)
    blank = await client.post("/api/projects", headers=alice, json={"name": "   "})
    assert blank.status_code == 422
    project = (await client.post("/api/projects", headers=alice, json={"name": "Review"})).json()
    pid = project["id"]
    other = (await client.post("/api/projects", headers=alice, json={"name": "Other"})).json()

    listed = await client.get("/api/projects", headers=alice)
    assert {item["id"] for item in listed.json()} >= {pid, other["id"]}
    assert (await client.get("/api/projects", headers=_headers(BOB))).json() == []

    corpus = await client.post(f"/api/projects/{pid}/corpora", headers=alice, json={"name": "B"})
    assert corpus.status_code == 201
    assert (
        await client.post(f"/api/projects/{pid}/corpora", headers=alice, json={"name": " "})
    ).status_code == 422
    corpus_id = corpus.json()["id"]

    async def _import(**body):  # type: ignore[no-untyped-def]
        return await client.post(f"/api/projects/{pid}/sources/import", headers=alice, json=body)

    assert (await _import(corpus_id="missing", kind="text", text="x y z")).status_code == 404
    assert (await _import(corpus_id=corpus_id, kind="text", text="")).status_code == 422
    first = await _import(corpus_id=corpus_id, kind="text", text="Measured gain was 17 percent.")
    assert first.status_code == 201
    dup = await _import(corpus_id=corpus_id, kind="text", text="Measured gain was 17 percent.")
    assert dup.status_code == 409
    sid = first.json()["id"]

    filtered = await client.get(
        f"/api/projects/{pid}/sources", headers=alice, params={"corpus_id": corpus_id}
    )
    assert [item["id"] for item in filtered.json()] == [sid]
    assert (
        await client.get(f"/api/projects/{pid}/sources", headers=alice, params={"corpus_id": "no"})
    ).status_code == 404

    # 来源必须属于路径里的项目：跨项目访问一律 404，不泄露存在性
    wrong = f"/api/projects/{other['id']}/sources/{sid}"
    assert (await client.get(wrong + "/chunks", headers=alice)).status_code == 404
    assert (
        await client.patch(wrong, headers=alice, json={"status": "excluded"})
    ).status_code == 404
    assert (await client.delete(wrong, headers=alice)).status_code == 404
    assert (
        await client.patch(
            f"/api/projects/{pid}/sources/{sid}", headers=_headers(BOB), json={"status": "excluded"}
        )
    ).status_code == 404

    excluded = await client.patch(
        f"/api/projects/{pid}/sources/{sid}", headers=alice, json={"status": "excluded"}
    )
    assert excluded.status_code == 200 and excluded.json()["status"] == "excluded"
    assert (
        await client.delete(f"/api/projects/{pid}/sources/{sid}", headers=alice)
    ).status_code == 204
    assert (
        await client.delete(f"/api/projects/{pid}/sources/{sid}", headers=alice)
    ).status_code == 404


@pytest.mark.asyncio
async def test_project_api_reports_uninitialized_library(library_client, monkeypatch) -> None:
    client, _ = library_client
    monkeypatch.setattr(api.app.state, "library", None, raising=False)
    response = await client.get("/api/projects", headers=_headers(ALICE))
    assert response.status_code == 503
