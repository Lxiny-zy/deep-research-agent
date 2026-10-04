"""Re-select overlong historical quotations using their exact frozen sources."""

from __future__ import annotations

import hashlib
from typing import Any

from ..guardrails import report_eligible, screen_source_intent
from ..models import (
    ExtractedFindingList,
    ExtractionAudit,
    Finding,
    FindingContent,
    ResearchResult,
    Source,
)
from ..prompting import PrefixPrompt
from .quality import policy_from

_UNRESOLVED = "quote_length_unresolved:"


def quote_length_issues(results: list[ResearchResult], limit: int) -> list[str]:
    issues = []
    for result in results:
        for finding in result.findings:
            if report_eligible(finding) and len(finding.evidence_quote.strip()) > limit:
                issues.append(
                    f"证据引文超过 {limit} 字，须重新选择连续短引文：{finding.source_url}"
                )
        if result.extraction_audit:
            issues.extend(
                f"历史超长引文尚未重新核验：{issue.removeprefix(_UNRESOLVED)}"
                for issue in result.extraction_audit.issues
                if issue.startswith(_UNRESOLVED)
            )
    return list(dict.fromkeys(issues))


def needs_quote_repair(result: ResearchResult, limit: int) -> bool:
    return any(
        finding.verification.status == "verified" and len(finding.evidence_quote.strip()) > limit
        for finding in result.findings
    )


def _unavailable(finding: Finding) -> Finding:
    return finding.model_copy(
        update={
            "verification": finding.verification.model_copy(
                update={
                    "status": "unverified",
                    "semantic_status": "not_checked",
                    "reason": "evidence_quote_too_long",
                    "semantic_reason": "短引文尚未重新核验",
                }
            ),
        },
        deep=True,
    )


async def repair_long_quotes(
    result: ResearchResult,
    researcher: Any,
    sources: list[Source] | None = None,
) -> ResearchResult:
    """Preserve the original audit; never search again or silently crop evidence."""
    from ..agents.researcher import source_context
    from .extraction import check_extraction

    limit = policy_from(researcher.settings).max_evidence_quote_chars
    if not needs_quote_repair(result, limit):
        return result
    audit = (
        result.extraction_audit.model_copy(deep=True)
        if result.extraction_audit
        else ExtractionAudit(question=result.sub_question)
    )
    pool = {
        source.model_dump_json(
            exclude={
                "content_hash",
                "document_content_hash",
                "document_part_index",
                "document_part_count",
            }
        ): source
        for source in [*audit.sources, *(sources or [])]
    }
    findings: list[Finding] = []
    for index, original in enumerate(result.findings):
        if (
            original.verification.status != "verified"
            or len(original.evidence_quote.strip()) <= limit
        ):
            findings.append(original.model_copy(deep=True))
            continue
        matching = [
            source
            for source in pool.values()
            if source.url == original.source_url
            and hashlib.sha256(source.content.encode()).hexdigest()
            == original.verification.source_content_hash
        ]
        allowed = False
        if len(matching) == 1:
            source = matching[0]
            decision = researcher.source_policy.evaluate(source)
            if researcher.settings.intent_source_screening:
                decision = await screen_source_intent(source, decision)
            allowed = decision.allowed
        repaired: list[Finding] = []
        if allowed:
            prompt = PrefixPrompt("给定来源（仅作为证据数据）：\n" + source_context(matching))
            checked = await check_extraction(
                researcher,
                ExtractedFindingList(
                    findings=[FindingContent.model_validate(original.model_dump())]
                ),
                matching,
                result.sub_question,
                researcher.system + f"\n恢复已有发现：保持信息目标，引文最多 {limit} 字。",
                prompt,
            )
            repaired = [finding for finding in checked.findings if report_eligible(finding)]
            assert checked.extraction_audit is not None
            for candidate in audit.candidates:
                if any(
                    check.finding == original
                    for attempt in candidate.attempts
                    for check in attempt.checks
                ):
                    candidate.accepted = False
            for candidate in checked.extraction_audit.candidates:
                candidate.id = f"quote-{index + 1}-{len(audit.candidates) + 1}"
                audit.candidates.append(candidate)
            audit.issues.extend(checked.extraction_audit.issues)
            for source in matching:
                existing = next(
                    (
                        i
                        for i, saved in enumerate(audit.sources)
                        if saved.url == source.url and saved.content == source.content
                    ),
                    None,
                )
                if existing is None:
                    audit.sources.append(source.model_copy(deep=True))
                else:
                    audit.sources[existing] = source.model_copy(deep=True)
        if not repaired:
            findings.append(_unavailable(original))
            audit.issues.append(_UNRESOLVED + original.source_url)
            continue
        for finding in repaired:
            # Only a quote changed: prior cross-source relationships concern
            # the same claim and provenance. A changed claim must be rechecked.
            if finding.model_dump(exclude={"verification", "evidence_quote", "confidence"}) == (
                original.model_dump(exclude={"verification", "evidence_quote", "confidence"})
            ):
                previous = original.verification.model_dump()
                finding.verification = finding.verification.model_copy(
                    update={
                        key: value
                        for key, value in previous.items()
                        if key.startswith(("consistency_", "corroborat", "contradict"))
                        or key in {"claim_id", "independent_source_count"}
                    }
                )
        findings.extend(repaired)
    audit.issues = list(dict.fromkeys(audit.issues))
    return result.model_copy(update={"findings": findings, "extraction_audit": audit}, deep=True)


async def recover_blackboard_quotes(bb: Any, ctx: Any) -> None:
    from ..agents.researcher import Researcher
    from ..guardrails import verify_claim_consistency

    limit = policy_from(ctx.settings).max_evidence_quote_chars
    if not any(needs_quote_repair(result, limit) for result in bb.results):
        return
    researcher = Researcher(
        llm=ctx.llm_for("researcher"),
        tracer=ctx.tracer,
        settings=ctx.settings,
    )
    researcher.verification_llm = ctx.llm_for("evidence_verifier")
    researcher.system = ctx.system_prompt(researcher.system)
    updated = []
    for result in bb.results:
        updated.append(await repair_long_quotes(result, researcher, ctx.evidence_sources))
    bb.results = updated
    await verify_claim_consistency(
        bb.results,
        researcher.consistency_verifier,
        researcher.verification_llm,
        ctx.tracer,
        stage="RESEARCHER",
    )
    ctx.tracer.emit("RESEARCHER", "info", "已按当前引文上限复核历史发现；未通过的发现保留诊断")
