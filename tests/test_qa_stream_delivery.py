"""Prove draft deltas precede completion and a disconnected client cannot cancel saving."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from deep_research.workbench import qa_api


@pytest.mark.parametrize("disconnect", [False, True])
async def test_qa_deltas_arrive_before_final_answer(monkeypatch, disconnect) -> None:
    release = asyncio.Event()
    saved: list[str] = []

    async def answer(cid, body, request, *, on_delta=None):  # type: ignore[no-untyped-def]
        on_delta("provisional answer")
        await release.wait()
        saved.append("validated answer")
        return {"id": "m1", "answer": saved[0], "citations": []}

    monkeypatch.setattr(qa_api, "_answer", answer)
    request = Request({"type": "http", "app": SimpleNamespace(state=SimpleNamespace())})
    response = await qa_api.ask_stream("c1", qa_api.AskRequest(query="question"), request)
    iterator = response.body_iterator
    assert "connected" in await anext(iterator)
    first = await asyncio.wait_for(anext(iterator), 1)
    assert "event: delta" in first and "provisional answer" in first
    assert saved == []
    tasks = list(request.app.state.qa_tasks)
    assert len(tasks) == 1 and not tasks[0].done()
    if disconnect:
        await iterator.aclose()
    release.set()
    if not disconnect:
        final = await asyncio.wait_for(anext(iterator), 1)
        assert "event: complete" in final and "validated answer" in final
        await iterator.aclose()
    await asyncio.wait_for(asyncio.gather(*tasks), 1)
    assert saved == ["validated answer"]
