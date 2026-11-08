import asyncio

from deep_research.render_queue import RenderFailed
from deep_research.workbench import completion as completion_module

STALLED = "渲染连续中断且没有取得进展，已停止自动重试"


class FailingService:
    async def build(self, detail, **kwargs):
        raise RenderFailed("job", {"message": STALLED})


def fake_record(detail, reason):
    return {"status": "needs_review", "content_version": None, "issues": [reason]}


def test_render_failure_keeps_report_as_needs_review(monkeypatch) -> None:
    monkeypatch.setattr(completion_module, "promised_formats", lambda detail: {"md", "pdf"})
    monkeypatch.setattr(
        "deep_research.render_service.service_for", lambda repo, settings: FailingService()
    )
    monkeypatch.setattr(completion_module, "render_failure_record", fake_record)
    record = asyncio.run(completion_module.prepare_completion(object(), settings=None))
    assert record is not None and record["status"] == "needs_review"
    assert STALLED in record["issues"][0]
