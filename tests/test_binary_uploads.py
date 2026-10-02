from __future__ import annotations

import asyncio
import base64

import httpx
import pytest

from deep_research import api
from deep_research.access import ApiCredential, Principal
from deep_research.config import Settings
from deep_research.workbench.attachments import load_original
from tests.test_attachments import _pdf


@pytest.fixture
async def client(monkeypatch, tmp_path):
    settings = Settings(
        api_key="upload-test-admin",
        api_credentials=(
            ApiCredential(Principal("researcher", "researcher"), "upload-test-researcher"),
            ApiCredential(Principal("viewer", "reader"), "upload-test-reader"),
        ),
        artifact_root=str(tmp_path),
    )
    monkeypatch.setattr(api.app.state, "settings", settings, raising=False)
    monkeypatch.setattr(api.app.state, "config_lock", asyncio.Lock(), raising=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app),
        base_url="http://test",
        headers={"Authorization": "Bearer upload-test-researcher"},
    ) as connection:
        yield connection


async def test_binary_and_legacy_uploads_parse_identically_and_keep_original_pdf(client):
    raw = _pdf()
    binary = await client.post(
        "/api/attachments/file",
        params={"filename": "原版论文.pdf"},
        content=raw,
        headers={"Content-Type": "application/pdf"},
    )
    legacy = await client.post(
        "/api/attachments",
        json={
            "filename": "原版论文.pdf",
            "mime_type": "application/pdf",
            "data_base64": base64.b64encode(raw).decode(),
        },
    )
    assert binary.status_code == legacy.status_code == 201
    assert binary.json() == legacy.json()
    attachment = binary.json()["attachment"]
    assert attachment["stored"] and load_original(api.app.state.settings, attachment["id"]) == raw


async def test_pdf_larger_than_previous_limit_is_fully_stored_and_parsed(client):
    raw = _pdf() + b"\n%padding\n" + b" " * (17 * 1024 * 1024)
    response = await client.post(
        "/api/attachments/file",
        params={"filename": "large.pdf"},
        content=raw,
        headers={"Content-Type": "application/pdf"},
    )
    assert response.status_code == 201, response.text
    attachment = response.json()["attachment"]
    assert attachment["size"] == len(raw) and not attachment["truncated"]
    assert load_original(api.app.state.settings, attachment["id"]) == raw


async def test_declared_and_chunked_oversize_fail_before_parsing(client, monkeypatch):
    from deep_research.workbench import api as workbench_api

    monkeypatch.setattr(workbench_api, "DOCUMENT_MAX_BYTES", 8)
    calls = []

    async def forbidden(*args):
        calls.append(args)
        raise AssertionError("Oversized requests must not reach parsing/storage")

    monkeypatch.setattr(workbench_api, "_store_attachment", forbidden)
    declared = await client.post(
        "/api/attachments/file?filename=x.txt",
        content=b"small",
        headers={"Content-Length": "9"},
    )

    async def chunks():
        yield b"123456"
        yield b"789"

    streamed = await client.post("/api/attachments/file?filename=x.txt", content=chunks())
    assert declared.status_code == streamed.status_code == 413
    assert not calls


async def test_binary_upload_retains_researcher_authorization_boundary(client):
    denied = await client.post(
        "/api/attachments/file?filename=x.txt",
        content=b"document",
        headers={"Authorization": "Bearer upload-test-reader"},
    )
    assert denied.status_code == 403
