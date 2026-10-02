"""Coverage of every explicitly supplied document in a closed literature review."""

from __future__ import annotations

from typing import Any

from ..bibliography import document_identity
from ..guardrails import report_eligible
from ..models import ResearchResult
from .attachments import attachments_from_scratch
from .contract import contract_from_scratch, provided_review


def corpus_issues(
    scratch: dict[str, Any],
    results: list[ResearchResult],
    cited_urls: list[str] | None = None,
    *,
    writable_only: bool = False,
) -> list[str]:
    """Check supplied evidence and citations; revisions can only fix omitted citations."""
    contract = contract_from_scratch(scratch)
    if not provided_review(contract):
        return []
    assert contract is not None
    intake = scratch.get("intake_sources")
    entries = intake.get("documents", []) if isinstance(intake, dict) else []
    if not isinstance(entries, list):
        entries = []
    documents = {
        item["input_url"]: dict(item)
        for item in entries
        if isinstance(item, dict) and isinstance(item.get("input_url"), str)
    }
    for attachment in attachments_from_scratch(scratch):
        url = f"https://workspace.invalid/attachments/{attachment.id}"
        document = documents.setdefault(
            url,
            {
                "title": attachment.filename,
                "source_urls": [s.url for s in attachment.sources()],
                "truncated": attachment.truncated,
            },
        )
        document["truncated"] = bool(document.get("truncated") or attachment.truncated)
    for paper in contract.papers:
        documents.setdefault(paper.url, {"title": paper.value, "source_urls": [paper.url]})
    if not documents:
        return [] if writable_only else ["没有提供可用于综述的论文链接或上传材料"]
    usable = {f.source_url for r in results for f in r.findings if report_eligible(f)}
    identities = {document_identity(url)[0] for url in usable}
    cited = {document_identity(url)[0] for url in usable.intersection(cited_urls or [])}
    issues = []
    for entry in documents.values():
        title = entry.get("title") or entry.get("input_url") or "指定文献"
        if entry.get("truncated") and not writable_only:
            issues.append(f"指定材料「{title}」未完整导入，不能视为完成全文综述")
        urls = entry.get("source_urls", [])
        expected = {
            document_identity(url)[0]
            for url in (urls if isinstance(urls, list) else [])
            if isinstance(url, str) and url
        }
        if not expected.intersection(identities):
            if not writable_only:
                issues.append(
                    f"指定材料「{title}」未取得可用的已核验证据，不能遗漏或用其他文献替代"
                )
        elif cited_urls is not None and not expected.intersection(cited):
            issues.append(f"指定材料「{title}」正文没有引用其已核验证据，不能遗漏或用其他文献替代")
    return issues
