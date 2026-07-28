from __future__ import annotations

from datetime import UTC, datetime

import pytest

from deep_research.models import (
    EvidenceVerification,
    Finding,
    Report,
    ResearchResult,
    Source,
)
from deep_research.observability import Event
from deep_research.persistence.repository import RunDetail
from deep_research.reproducibility import (
    RecordingSearchTool,
    build_run_manifest,
    quality_metrics,
)
from deep_research.tools.base import SearchTool


class DuplicateSearch(SearchTool):
    async def search(self, query: str, *, max_results: int = 5) -> list[Source]:
        return [Source(title="A", url="https://a.example/source", content="snapshot")]


class ChangingSearch(SearchTool):
    def __init__(self) -> None:
        self.calls = 0

    async def search(self, query: str, *, max_results: int = 5) -> list[Source]:
        self.calls += 1
        return [
            Source(
                title="A",
                url="https://a.example/source",
                content=f"snapshot-{self.calls}",
            )
        ]


@pytest.mark.asyncio
async def test_recording_search_persists_each_url_once() -> None:
    recorded: list[Source] = []
    tool = RecordingSearchTool(DuplicateSearch())

    async def sink(sources: list[Source]) -> None:
        recorded.extend(sources)

    tool.set_sink(sink)
    await tool.search("first")
    await tool.search("second")

    assert [source.url for source in recorded] == ["https://a.example/source"]


@pytest.mark.asyncio
async def test_recording_search_records_changed_content_for_same_url() -> None:
    recorded: list[Source] = []
    tool = RecordingSearchTool(ChangingSearch())

    async def sink(sources: list[Source]) -> None:
        recorded.extend(sources)

    tool.set_sink(sink)
    await tool.search("first")
    await tool.search("second")

    assert [source.content for source in recorded] == ["snapshot-1", "snapshot-2"]
    assert all(len(source.content_hash) == 64 for source in recorded)


@pytest.mark.asyncio
async def test_recording_failure_does_not_hide_search_and_retries_snapshot() -> None:
    recorded: list[Source] = []
    errors: list[Exception] = []
    calls = 0
    tool = RecordingSearchTool(DuplicateSearch())

    async def sink(sources: list[Source]) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("database temporarily unavailable")
        recorded.extend(sources)

    tool.set_sink(sink)
    tool.set_error_sink(lambda error, _sources: errors.append(error))
    first = await tool.search("first")
    second = await tool.search("second")

    assert first[0].content == "snapshot"
    assert second[0].content == "snapshot"
    assert len(errors) == 1
    assert len(recorded) == 1


@pytest.mark.asyncio
async def test_recording_search_does_not_swallow_lease_loss() -> None:
    from deep_research.persistence.repository import LeaseLostError

    tool = RecordingSearchTool(DuplicateSearch())

    async def sink(_sources: list[Source]) -> None:
        raise LeaseLostError("lost")

    tool.set_sink(sink)
    with pytest.raises(LeaseLostError):
        await tool.search("first")


def test_manifest_is_stable_and_redacts_endpoint_credentials() -> None:
    kwargs = {
        "query": "Q",
        "workflow_name": "deep",
        "workflow_definition": {"name": "deep", "steps": []},
        "settings": {"max_rounds": 2},
        "llm_model": "model",
        "llm_endpoint": "https://user:secret@example.com/v1?token=hidden",
        "search_backend": "FakeSearch",
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    first = build_run_manifest(**kwargs)
    second = build_run_manifest(**kwargs)

    assert first.workflow_hash == second.workflow_hash
    assert first.query_hash == second.query_hash
    assert first.llm_endpoint == "https://example.com/v1"
    assert first.catalog_model_profiles == []


def test_manifest_preserves_ipv6_endpoint_brackets() -> None:
    manifest = build_run_manifest(
        query="Q",
        workflow_name="deep",
        workflow_definition={"name": "deep"},
        settings={},
        llm_model="model",
        llm_endpoint="http://user:secret@[::1]:8000/v1?token=hidden",
        search_backend="FakeSearch",
    )
    assert manifest.llm_endpoint == "http://[::1]:8000/v1"


@pytest.mark.asyncio
async def test_recording_search_ignores_untrusted_supplied_content_hash() -> None:
    recorded: list[Source] = []

    class HashingSearch(SearchTool):
        async def search(self, query: str, *, max_results: int = 5) -> list[Source]:
            return [Source(url="https://a.example", content="actual", content_hash="stale")]

    tool = RecordingSearchTool(HashingSearch())

    async def sink(sources: list[Source]) -> None:
        recorded.extend(sources)

    tool.set_sink(sink)
    await tool.search("Q")
    assert recorded[0].content_hash != "stale"
    assert len(recorded[0].content_hash) == 64


def test_quality_metrics_are_deterministic() -> None:
    finding = Finding(
        statement="Claim",
        source_url="https://a.example/source",
        evidence_quote="Evidence",
        verification=EvidenceVerification(
            status="verified",
            method="normalized_quote",
            semantic_status="supported",
            consistency_status="clear",
            corroboration_status="corroborated",
            independent_source_count=2,
            corroborates_claim_ids=["peer-claim"],
        ),
    )
    detail = RunDetail(
        id="run",
        query="Q",
        status="done",
        results=[ResearchResult(sub_question="sq", findings=[finding])],
        report=Report(query="Q", markdown="Claim [1]", citations=[finding.source_url]),
        sources=[Source(url=finding.source_url, content="Evidence")],
        events=[
            Event(
                stage="RESEARCHER",
                type="info",
                data={"category": "source_policy", "allowed": 1, "blocked": 2},
            )
        ],
        total_tokens=100,
        elapsed=1.25,
    )

    metrics = quality_metrics(detail, require_corroboration=True)

    assert metrics.report_eligible == 1
    assert metrics.verified_finding_rate == 1.0
    assert metrics.cited_source_snapshot_coverage == 1.0
    assert metrics.independent_publishers == 1
    assert metrics.blocked_sources == 2


def test_quality_metrics_count_registrable_publishers() -> None:
    detail = RunDetail(
        id="run",
        query="Q",
        status="done",
        sources=[
            Source(url="https://www.example.com/a"),
            Source(url="https://docs.example.com/b"),
            Source(url="https://other.example.org/c"),
        ],
    )

    assert quality_metrics(detail).independent_publishers == 2
