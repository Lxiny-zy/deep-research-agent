"""Run manifests, source recording, and deterministic quality metrics."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit

from .guardrails import publisher_identity, report_eligible
from .models import QualityMetrics, RunManifest, Source
from .persistence.repository import LeaseLostError, RunDetail
from .tools.base import SearchTool

RUN_MANIFEST_CHECKPOINT_KEY = "run_manifest"


def _stable_hash(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _safe_endpoint(value: str | None) -> str:
    if not value:
        return ""
    parsed = urlsplit(value)
    hostname = parsed.hostname or ""
    if ":" in hostname:
        hostname = f"[{hostname}]"
    if parsed.port is not None:
        hostname = f"{hostname}:{parsed.port}"
    return urlunsplit((parsed.scheme, hostname, parsed.path, "", ""))


def build_run_manifest(
    *,
    query: str,
    workflow_name: str,
    workflow_definition: dict,
    settings: dict[str, bool | int | float | None],
    llm_model: str,
    llm_endpoint: str | None,
    search_backend: str,
    catalog_snapshot: object | None = None,
    catalog_model_profiles: list[dict[str, object]] | None = None,
    created_at: datetime | None = None,
) -> RunManifest:
    return RunManifest(
        created_at=created_at or datetime.now(UTC),
        workflow_name=workflow_name,
        workflow_hash=_stable_hash(workflow_definition),
        query_hash=_stable_hash(query),
        settings=settings,
        llm_model=llm_model,
        llm_endpoint=_safe_endpoint(llm_endpoint),
        search_backend=search_backend,
        catalog_snapshot_hash=_stable_hash(catalog_snapshot) if catalog_snapshot else "",
        catalog_model_profiles=catalog_model_profiles or [],
    )


SourceSink = Callable[[list[Source]], Awaitable[None]]
RecordingErrorSink = Callable[[Exception, list[Source]], None]


class RecordingSearchTool(SearchTool):
    """Persist each unique retrieval snapshot before downstream LLM processing."""

    def __init__(self, delegate: SearchTool) -> None:
        self.delegate = delegate
        self._sink: SourceSink | None = None
        self._error_sink: RecordingErrorSink | None = None
        self._seen: set[tuple[str, str]] = set()

    def set_sink(self, sink: SourceSink | None) -> None:
        self._sink = sink

    def set_error_sink(self, sink: RecordingErrorSink | None) -> None:
        self._error_sink = sink

    async def search(self, query: str, *, max_results: int = 5) -> list[Source]:
        sources = await self.delegate.search(query, max_results=max_results)
        snapshots = [
            source.model_copy(
                update={
                    "content_hash": hashlib.sha256(source.content.encode("utf-8")).hexdigest()
                }
            )
            for source in sources
        ]
        fresh = [
            source
            for source in snapshots
            if (source.url, source.content_hash) not in self._seen
        ]
        if fresh and self._sink is not None:
            try:
                await self._sink(fresh)
            except LeaseLostError:
                raise
            except Exception as exc:
                if self._error_sink is not None:
                    self._error_sink(exc, fresh)
            else:
                self._seen.update((source.url, source.content_hash) for source in fresh)
        return sources

    async def aclose(self) -> None:
        await self.delegate.aclose()


def quality_metrics(detail: RunDetail, *, require_corroboration: bool = False) -> QualityMetrics:
    findings = [finding for result in detail.results for finding in result.findings]
    total = len(findings)
    verified = sum(f.verification.status == "verified" for f in findings)
    supported = sum(f.verification.semantic_status == "supported" for f in findings)
    eligible = sum(
        report_eligible(finding, require_corroboration=require_corroboration)
        for finding in findings
    )
    corroborated = sum(f.verification.corroboration_status == "corroborated" for f in findings)
    conflicted = sum(f.verification.consistency_status == "conflicted" for f in findings)
    disputed = sum(f.verification.corroboration_status == "disputed" for f in findings)
    citations = set(detail.report.citations if detail.report is not None else [])
    snapshot_urls = {source.url for source in detail.sources}
    publishers = {
        publisher_identity(source.url)
        for source in detail.sources
        if publisher_identity(source.url)
    }
    blocked = 0
    for event in detail.events:
        if event.data and event.data.get("category") == "source_policy":
            blocked += int(event.data.get("blocked", 0))

    def rate(value: int) -> float:
        return round(value / total, 4) if total else 0.0

    return QualityMetrics(
        total_findings=total,
        verbatim_verified=verified,
        semantically_supported=supported,
        report_eligible=eligible,
        corroborated=corroborated,
        conflicted=conflicted,
        disputed=disputed,
        source_snapshots=len(detail.sources),
        cited_sources=len(citations),
        cited_source_snapshot_coverage=(
            round(len(citations & snapshot_urls) / len(citations), 4) if citations else 0.0
        ),
        verified_finding_rate=rate(verified),
        supported_finding_rate=rate(supported),
        eligible_finding_rate=rate(eligible),
        independent_publishers=len(publishers),
        blocked_sources=blocked,
        total_tokens=detail.total_tokens,
        elapsed_seconds=round(detail.elapsed, 3),
    )
