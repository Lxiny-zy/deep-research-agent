"""Resolve a task's frozen source scope for paper and open research conversations."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

from ..models import Finding, Source
from ..persistence.repository import RunDetail
from .contract import contract_from_scratch
from .reader import paper_sources


@dataclass
class TaskQaScope:
    kind: Literal["paper", "research"]
    sources: list[Source]
    findings: list[Finding]
    query: str


def task_qa_scope(detail: RunDetail) -> TaskQaScope:
    scratch = detail.orchestration.checkpoint.get("scratch", {}) if detail.orchestration else {}
    contract = contract_from_scratch(scratch)
    fixed = paper_sources(detail)
    kind: Literal["paper", "research"] = (
        "paper"
        if (contract and contract.template in {"paperRead", "peerReview"})
        or (contract is None and fixed)
        else "research"
    )
    findings = [finding for result in detail.results for finding in result.findings]
    sources = list(fixed)
    if kind == "research":
        sources.extend(detail.sources)
        sources.extend(
            source
            for result in detail.results
            if result.extraction_audit
            for source in result.extraction_audit.sources
        )
    snapshots: dict[str, dict[str, Source]] = {}
    for source in sources:
        if source.content.strip():
            key = hashlib.sha256(source.content.encode("utf-8")).hexdigest()
            versions = snapshots.setdefault(source.url, {})
            if key not in versions or (source.scholarly and source.scholarly.retracted is True):
                versions[key] = source
    selected = []
    for url, versions in snapshots.items():
        if len(versions) == 1:
            selected.append(next(iter(versions.values())))
            continue
        hashes = {
            finding.verification.source_content_hash
            for finding in findings
            if finding.source_url == url and finding.verification.source_content_hash in versions
        }
        # A source number cannot mean two versions. Prefer the uniquely bound
        # snapshot; ambiguous versions remain unavailable rather than being mixed.
        if len(hashes) == 1:
            selected.append(versions[next(iter(hashes))])
    return TaskQaScope(kind, selected, findings, detail.query)
