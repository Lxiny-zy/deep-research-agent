"""论文精读工作区回归：原文件保存、原版 PDF 接口与归属校验、论文范围的对话。"""

from __future__ import annotations

import asyncio
import base64
from unittest.mock import AsyncMock

import httpx
import pytest

from deep_research import api
from deep_research.access import ApiCredential, Principal
from deep_research.config import Settings
from deep_research.models import FindingList
from deep_research.orchestrator import create_initial_execution
from deep_research.persistence.memory_repository import InMemoryRepository
from deep_research.workbench.attachments import (
    ATTACHMENT_URL_PREFIX,
    ATTACHMENTS_SCRATCH_KEY,
    Attachment,
    AttachmentChunk,
    AttachmentError,
    load_original,
    save_original,
)
from deep_research.workbench.contract import CONTRACT_SCRATCH_KEY, build_contract
from deep_research.workbench.templates import get_template
from tests.fakes import FakeLLM, FakeSearch

ADMIN = "test-administrator-credential"
ALICE = "test-alice-research-credential"
BOB = "test-bob-research-credential"


def _pdf(text: str = "Reconstruction PSNR reaches 38.4 dB on CAVE.") -> bytes:
    import pymupdf

    document = pymupdf.open()
    document.new_page().insert_text((72, 72), text)
    return document.tobytes()


def _headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


@pytest.fixture
async def reader_client(monkeypatch, tmp_path):  # type: ignore[no-untyped-def]
    from deep_research.workbench.qa_store import InMemoryQaStore

    monkeypatch.setattr(api, "_run_limiter", api._RateLimiter(max_calls=10, window_seconds=60))
    repo = InMemoryRepository()
    settings = Settings(
        api_key=ADMIN,
        api_credentials=(
            ApiCredential(Principal("alice", "researcher"), ALICE),
            ApiCredential(Principal("bob", "researcher"), BOB),
        ),
        execution_mode="worker",
        artifact_root=str(tmp_path / "artifacts"),
    )
    for name, value in {
        "settings": settings,
        "repo": repo,
        "catalog": None,
        "live": {},
        "tasks": set(),
        "run_tasks": {},
        "cancellation_requested": set(),
        "config_lock": asyncio.Lock(),
        "qa_store": InMemoryQaStore(),
    }.items():
        monkeypatch.setattr(api.app.state, name, value, raising=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test"
    ) as client:
        yield client, repo, settings


async def _paper_run(repo, settings, *, attachments=(), query="重点看实验", owner="alice"):  # type: ignore[no-untyped-def]
    template = get_template("paperRead")
    assert template is not None
    execution = create_initial_execution(query, template.workflow, settings)
    scratch = execution.checkpoint.setdefault("scratch", {})
    scratch[CONTRACT_SCRATCH_KEY] = build_contract(template, query).model_dump(mode="json")
    scratch[ATTACHMENTS_SCRATCH_KEY] = [item.model_dump(mode="json") for item in attachments]
    run_id, _ = await repo.create_run_once(
        query, request_hash="", owner_id=owner, execution=execution
    )
    return run_id


def test_original_pdf_round_trip_and_tamper_detection(tmp_path) -> None:
    settings = Settings(artifact_root=str(tmp_path))
    raw = _pdf()
    import hashlib

    attachment_id = hashlib.sha256(raw).hexdigest()[:24]
    save_original(settings, attachment_id, raw)
    save_original(settings, attachment_id, raw)  # 同一内容重复上传只保存一次
    assert load_original(settings, attachment_id) == raw
    assert load_original(settings, "0" * 24) is None
    with pytest.raises(AttachmentError):
        load_original(settings, "../../etc/passwd")


@pytest.mark.asyncio
async def test_uploaded_pdf_is_stored_and_served_only_to_the_run_owner(reader_client) -> None:
    client, repo, settings = reader_client
    raw = _pdf()
    uploaded = await client.post(
        "/api/attachments",
        headers=_headers(ALICE),
        json={
            "filename": "paper.pdf",
            "mime_type": "application/pdf",
            "data_base64": base64.b64encode(raw).decode(),
        },
    )
    assert uploaded.status_code == 201, uploaded.text
    attachment = Attachment.model_validate(uploaded.json()["attachment"])
    assert attachment.stored and attachment.chunks[0].page == 1

    run_id = await _paper_run(repo, settings, attachments=[attachment])
    reader = await client.get(f"/api/runs/{run_id}/reader", headers=_headers(ALICE))
    assert reader.status_code == 200
    assert reader.json()["can_ask"] is False
    conversation = await client.post(
        "/api/qa/conversations",
        headers=_headers(ALICE),
        json={"title": "尚未完成的精读", "run_id": run_id},
    )
    blocked = await client.post(
        f"/api/qa/conversations/{conversation.json()['id']}/messages",
        headers=_headers(ALICE),
        json={"query": "现在可以提问吗？"},
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "paper_not_ready"
    document = reader.json()["documents"][0]
    assert document == {**document, "id": f"att-{attachment.id}", "pdf": True}

    pdf = await client.get(
        f"/api/runs/{run_id}/reader/{document['id']}/pdf", headers=_headers(ALICE)
    )
    assert pdf.status_code == 200 and pdf.content == raw
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.headers["vary"] == "Authorization"
    # 别人的任务、未登记的文档、伪造的标识都读不到
    other = await client.get(
        f"/api/runs/{run_id}/reader/{document['id']}/pdf", headers=_headers(BOB)
    )
    stranger = await client.get(
        f"/api/runs/{run_id}/reader/att-{'0' * 24}/pdf", headers=_headers(ALICE)
    )
    bogus = await client.get(f"/api/runs/{run_id}/reader/..%2Fsecret/pdf", headers=_headers(ALICE))
    assert other.status_code == 404
    assert stranger.status_code == 404
    assert bogus.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["pending", "running", "failed", "cancelled", "done"])
@pytest.mark.parametrize("stream", [False, True])
async def test_reader_blocks_unready_or_missing_paper_before_building_agent(
    reader_client, monkeypatch, status, stream
) -> None:
    client, repo, settings = reader_client
    run_id = await _paper_run(repo, settings)
    await repo.set_status(run_id, status)
    build_agent = AsyncMock()
    monkeypatch.setattr(api, "_build_agent", build_agent)
    reader = await client.get(f"/api/runs/{run_id}/reader", headers=_headers(ALICE))
    assert reader.json()["can_ask"] is False
    conversation = await client.post(
        "/api/qa/conversations", headers=_headers(ALICE), json={"run_id": run_id}
    )
    cid = conversation.json()["id"]
    response = await client.post(
        f"/api/qa/conversations/{cid}/messages" + ("/stream" if stream else ""),
        headers=_headers(ALICE),
        json={"query": "原文的实验条件是什么？", "sources": ["web"]},
    )
    assert response.status_code == 409
    payload = response.json()
    expected = "paper_sources_unavailable" if status == "done" else "paper_not_ready"
    assert payload["detail"]["code"] == expected
    build_agent.assert_not_awaited()
    stored = await client.get(f"/api/qa/conversations/{cid}", headers=_headers(ALICE))
    assert stored.json()["messages"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_reader_enables_paper_only_questions_after_run_completion(
    reader_client, monkeypatch, stream
) -> None:
    from deep_research.orchestrator import DeepResearchAgent

    client, repo, settings = reader_client
    attachment = Attachment(
        id="a" * 24,
        filename="paper.txt",
        kind="text",
        size=80,
        char_count=30,
        chunks=[AttachmentChunk(ordinal=0, content="方法在 CAVE 上的重建 PSNR 达到 38.4 dB")],
    )
    run_id = await _paper_run(repo, settings, attachments=[attachment])
    await repo.set_status(run_id, "done")
    search = FakeSearch()
    search_call = AsyncMock(return_value=[])
    monkeypatch.setattr(search, "search", search_call)

    async def fake_build_agent(app, settings, **kwargs):  # type: ignore[no-untyped-def]
        return DeepResearchAgent(settings, llm=_PaperLLM(), search_tool=search), None

    monkeypatch.setattr(api, "_build_agent", fake_build_agent)
    reader = await client.get(f"/api/runs/{run_id}/reader", headers=_headers(ALICE))
    assert reader.json()["can_ask"] is True
    conversation = await client.post(
        "/api/qa/conversations", headers=_headers(ALICE), json={"run_id": run_id}
    )
    cid = conversation.json()["id"]
    response = await client.post(
        f"/api/qa/conversations/{cid}/messages" + ("/stream" if stream else ""),
        headers=_headers(ALICE),
        json={"query": "PSNR 是多少？", "sources": []},
    )
    assert response.status_code == (200 if stream else 201), response.text
    if stream:
        assert "event: complete" in response.text
    stored = await client.get(f"/api/qa/conversations/{cid}", headers=_headers(ALICE))
    assert len(stored.json()["messages"]) == 1
    assert stored.json()["messages"][0]["evidence"][0]["origin"] == "paper"
    search_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_arxiv_pdf_is_fetched_once_then_served_from_cache(reader_client, monkeypatch) -> None:
    from deep_research.library import ingestion

    client, repo, settings = reader_client
    raw = _pdf()
    calls: list[str] = []

    async def fake_fetch(url):  # type: ignore[no-untyped-def]
        calls.append(url)
        return raw, "application/pdf", url

    monkeypatch.setattr(ingestion, "_fetch", fake_fetch)
    run_id = await _paper_run(repo, settings, query="https://arxiv.org/abs/2205.10102 重点看实验")
    first = await client.get(f"/api/runs/{run_id}/reader/paper-0/pdf", headers=_headers(ALICE))
    second = await client.get(f"/api/runs/{run_id}/reader/paper-0/pdf", headers=_headers(ALICE))
    assert first.status_code == 200 and second.content == raw
    assert calls == ["https://arxiv.org/pdf/2205.10102"]


class _PaperLLM(FakeLLM):
    """抽取时，论文片段引用附件 URL；联网检索的片段交给 FakeLLM 默认行为。"""

    async def parse(self, system, user, schema, *, temperature=0.2, retries=2):  # type: ignore[no-untyped-def]
        if schema is FindingList and ATTACHMENT_URL_PREFIX in user:
            start = user.index(ATTACHMENT_URL_PREFIX)
            url = user[start:].split()[0].rstrip("）)]，,")
            return FindingList(
                findings=[
                    {
                        "statement": "方法在 CAVE 上的重建 PSNR 为 38.4 dB",
                        "source_url": url,
                        "evidence_quote": "重建 PSNR 达到 38.4 dB",
                        "confidence": 0.9,
                    }
                ]
            )
        return await super().parse(system, user, schema, temperature=temperature, retries=retries)

    async def stream(self, system, user, *, temperature=0.4):  # type: ignore[no-untyped-def]
        yield "论文报告重建 PSNR 为 38.4 dB [1]。"


def _paper_sources():  # type: ignore[no-untyped-def]
    attachment = Attachment(
        id="a" * 24,
        filename="paper.pdf",
        kind="pdf",
        size=10,
        char_count=40,
        chunks=[
            AttachmentChunk(
                ordinal=0, locator="第 3 页", content="在 CAVE 上，重建 PSNR 达到 38.4 dB。", page=3
            )
        ],
    )
    return attachment.sources()


class _NoSearch(FakeSearch):
    async def search(self, query, *, max_results=5):  # type: ignore[no-untyped-def]
        raise AssertionError("paper-only answers must not call external search")


@pytest.mark.asyncio
async def test_paper_only_answer_never_calls_external_search(tmp_path) -> None:
    from deep_research.agents.base import RunContext
    from deep_research.observability import Tracer
    from deep_research.workbench.qa import answer_question

    settings = Settings(artifact_root=str(tmp_path))
    ctx = RunContext(llm=_PaperLLM(), search_tool=_NoSearch(), tracer=Tracer(), settings=settings)
    result = await answer_question(
        "PSNR 是多少？", history=[], ctx=ctx, paper_sources=_paper_sources(), include_web=False
    )
    assert not result.fallback
    assert result.citations and set(result.origins.values()) == {"paper"}


@pytest.mark.asyncio
async def test_paper_answer_labels_web_sources_separately(tmp_path) -> None:
    from deep_research.agents.base import RunContext
    from deep_research.observability import Tracer
    from deep_research.workbench.qa import answer_question

    settings = Settings(artifact_root=str(tmp_path))
    ctx = RunContext(llm=_PaperLLM(), search_tool=FakeSearch(), tracer=Tracer(), settings=settings)
    result = await answer_question(
        "PSNR 是多少？", history=[], ctx=ctx, paper_sources=_paper_sources(), include_web=True
    )
    assert result.origins[result.citations[0]] == "paper"
    assert result.origins.get("https://a.com") == "web"


@pytest.mark.asyncio
async def test_reader_conversations_are_bound_to_their_run(reader_client) -> None:
    client, repo, settings = reader_client
    run_id = await _paper_run(repo, settings)
    bound = await client.post(
        "/api/qa/conversations", headers=_headers(ALICE), json={"title": "精读", "run_id": run_id}
    )
    free = await client.post(
        "/api/qa/conversations", headers=_headers(ALICE), json={"title": "问答"}
    )
    foreign = await client.post(
        "/api/qa/conversations", headers=_headers(BOB), json={"title": "x", "run_id": run_id}
    )
    in_run = (
        await client.get(f"/api/qa/conversations?run_id={run_id}", headers=_headers(ALICE))
    ).json()
    in_qa = (await client.get("/api/qa/conversations", headers=_headers(ALICE))).json()
    assert bound.status_code == 201 and free.status_code == 201
    assert foreign.status_code == 404
    assert [c["id"] for c in in_run] == [bound.json()["id"]]
    assert [c["id"] for c in in_qa] == [free.json()["id"]]
