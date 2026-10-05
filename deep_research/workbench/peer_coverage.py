"""Method-section coverage and focused, recoverable reading for peer review."""

from __future__ import annotations

import hashlib
from typing import Any

from ..agents.base import Blackboard, RunContext
from ..agents.researcher import Researcher
from ..artifacts import ArtifactStore
from ..blocking import run_blocking
from ..guardrails import EvidenceVerifier, verify_claim_consistency
from ..models import Finding, ResearchResult
from ..persistence.repository import LeaseLostError
from ..research_progress import ResearchProgress, ResearchProgressError
from .attachment_reader import source_batches
from .contract import contract_from_scratch
from .intake import _FixedSources
from .paper_evidence import EvidencePlan, plan_findings
from .peer_sections import MethodUnit, method_units
from .quality import coerce_policy
from .review_coverage import REVIEW_COVERAGE_KEY, _groups
from .support import digest

VERSION = 1


def _key(finding: Finding) -> str:
    return digest(finding.model_dump(mode="json"))


def _units(scratch: dict[str, Any]) -> dict[str, list[MethodUnit]]:
    return {key: method_units(key, sources) for key, sources in _groups(scratch).items()}


def _input(scratch: dict[str, Any], results: list[ResearchResult]) -> str:
    contract = contract_from_scratch(scratch)
    return digest([
        VERSION, contract.model_dump(mode="json") if contract else None,
        {key: [(s.model_dump(mode="json", exclude={"content"}),
                hashlib.sha256(s.content.encode()).hexdigest()) for s in sources]
         for key, sources in _groups(scratch).items()},
        {key: [(u.id, [p.binding() for p in u.parts], [_key(f) for f in u.findings(results)])
               for u in units] for key, units in _units(scratch).items()},
    ])


def coverage_issues(scratch: dict[str, Any], results: list[ResearchResult]) -> list[str]:
    groups = _units(scratch)
    if not groups:
        return ["同行评审没有可检查的方法或论证原文，需要先导入论文"]
    record = scratch.get(REVIEW_COVERAGE_KEY)
    if (not isinstance(record, dict) or record.get("kind") != "peer_methods"
            or record.get("version") != VERSION
            or record.get("input_hash") != _input(scratch, results)):
        return ["同行评审的方法章节证据尚未逐节核对，或原文/证据已变更"]
    documents = record.get("documents", [])
    if not isinstance(documents, list) or len(documents) != len(groups):
        return ["方法覆盖记录未包含全部论文"]
    by_id = {d["id"]: d for d in documents if isinstance(d, dict) and isinstance(d.get("id"), str)}
    if set(by_id) != set(groups):
        return ["方法覆盖记录的论文范围不一致"]
    issues = []
    for key, units in groups.items():
        if not units:
            issues.append("论文未取得可核对的方法或论证章节，不能只凭摘要/引言完成评审")
            continue
        sections = by_id[key].get("sections", [])
        if not isinstance(sections, list):
            issues.append("方法覆盖章节记录格式无效")
            continue
        entries = {
            s["id"]: s for s in sections if isinstance(s, dict) and isinstance(s.get("id"), str)
        }
        if len(sections) != len(units) or set(entries) != {u.id for u in units}:
            issues.append("方法覆盖记录遗漏、重复或变更了章节范围")
            continue
        for unit in units:
            entry = entries[unit.id]
            checks = entry.get("checks") or []
            if not isinstance(checks, list):
                checks = []
            last = checks[-1] if checks and isinstance(checks[-1], dict) else {}
            keys = last.get("findings", [])
            current = {_key(f) for f in unit.findings(results)}
            if (entry.get("status") != "pass" or last.get("sufficient") is not True
                    or not isinstance(keys, list) or not keys
                    or not all(isinstance(k, str) for k in keys)
                    or not set(keys).issubset(current)):
                missing = entry.get("missing_topics") or ["尚无足够的本章节已核验证据"]
                issues.append(f"方法章节「{unit.title}」覆盖不足：" + "；".join(map(str, missing)))
    return issues


def _question(request: str, unit: MethodUnit) -> str:
    return (
        f"同行评审任务：{request}\n当前仅检查章节「{unit.title}」。"
        "判断来自本章节的已核验证据能否说明其核心方法、组成部分、操作或论证步骤及适用条件。"
        "不能以引言贡献口号、其他章节的证据或基线数字代替。"
        "只抽取当前展示范围中的信息，保持引句连续完整；未抽取到不等于原文没有。"
    )


async def check_methods(
    bb: Blackboard, ctx: RunContext, *, reuse_checked: bool = False,
) -> Blackboard:
    if not coverage_issues(bb.scratch, bb.results):
        return bb
    contract = contract_from_scratch(bb.scratch)
    request = contract.original_request if contract else bb.query
    policy = coerce_policy(contract.quality if contract else ctx.settings.quality)
    rounds = policy.review_evidence_rounds
    initial = _input(bb.scratch, bb.results)
    previous = bb.scratch.get(REVIEW_COVERAGE_KEY)
    if (reuse_checked and isinstance(previous, dict) and previous.get("kind") == "peer_methods"
            and previous.get("version") == VERSION and previous.get("input_hash") == initial
            and previous.get("run_id") == ctx.run_id):
        return bb
    progress = ResearchProgress(ctx.artifact_store, {
        "run_id": ctx.run_id, "role": "peer_methods", "input": initial, "version": VERSION,
    }) if isinstance(ctx.artifact_store, ArtifactStore) and ctx.run_id else None
    researcher = Researcher(
        llm=ctx.llm_for("researcher"), tracer=ctx.tracer, settings=ctx.settings,
        evidence_verifier=EvidenceVerifier(max_quote_chars=policy.max_evidence_quote_chars),
    )
    researcher.verification_llm = ctx.llm_for("evidence_verifier")
    researcher.system = ctx.system_prompt(researcher.system)
    documents: list[dict[str, Any]] = []
    pending: list[tuple[MethodUnit, dict[str, Any]]] = []
    added = False

    async def check(
        unit: MethodUnit, entry: dict[str, Any], read: set[str],
    ) -> EvidencePlan | None:
        findings = unit.findings(bb.results)
        try:
            plan = await plan_findings(
                findings, _question(request, unit), [], researcher, unit.sources(), read_urls=read,
            )
        except LeaseLostError:
            raise
        except Exception as exc:
            entry.update(
                status="fail", missing_topics=[f"章节覆盖核对未完成：{type(exc).__name__}"],
            )
            return None
        sufficient = bool(findings and plan.sufficient and plan.findings)
        entry["checks"].append({
            "sufficient": sufficient, "findings": [_key(f) for f in plan.findings],
        })
        entry.update(
            status="pass" if sufficient else "fail",
            missing_topics=[] if sufficient else plan.missing_topics or ["缺少本章节已核验证据"],
        )
        return plan

    for document, units in _units(bb.scratch).items():
        doc: dict[str, Any] = {"id": document, "sections": []}
        documents.append(doc)
        for unit in units:
            entry: dict[str, Any] = {"id": unit.id, "title": unit.title, "checks": [], "reads": [],
                                     "status": "fail"}
            doc["sections"].append(entry)
            pending.append((unit, entry))
            read: set[str] = set()
            researcher.source_context = unit.context
            ranges: dict[tuple[str, str], list[tuple[int, int]]] = {}
            for part in unit.parts:
                ranges.setdefault((part.source.url, part.binding()[1]), []).append(
                    (part.start, part.end),
                )
            researcher.evidence_verifier = EvidenceVerifier(
                max_quote_chars=policy.max_evidence_quote_chars, quote_ranges=ranges,
            )
            for attempt in range(rounds + 1):
                plan = await check(unit, entry, read)
                if plan is None or entry["status"] == "pass" or attempt == rounds:
                    break
                chosen = [s for s in unit.sources() if s.url not in read
                          and (not plan.source_urls or s.url in plan.source_urls)]
                if not chosen:
                    break
                ctx.tracer.emit("RESEARCHER", "info", f"补读方法章节「{unit.title}」…",
                                data={"category": REVIEW_COVERAGE_KEY, "section": unit.id})
                focus = _question(request, unit)
                try:
                    for batch in source_batches(chosen, researcher, focus):
                        key = progress.key(
                            digest([unit.id, [s.url for s in batch]]), None,
                        ) if progress else ""
                        result = await run_blocking(progress.load, key, focus) if progress else None
                        reused = result is not None
                        if result is None:
                            researcher.search = _FixedSources(batch)
                            result = await researcher.run(focus)
                            if result is not None and progress and unit.findings([result]):
                                await run_blocking(progress.save, key, result)
                        if result is not None and result.extraction_audit is not None:
                            bb.results.append(result)
                            added = True
                        entry["reads"].append({
                            "source_urls": [s.url for s in batch], "reused": reused,
                        })
                        read.update(s.url for s in batch)
                except (LeaseLostError, ResearchProgressError):
                    raise
                except Exception as exc:
                    entry.update(
                        status="fail", missing_topics=[f"章节补读未完成：{type(exc).__name__}"],
                    )
                    break
    if added:
        before = {unit.id: [_key(f) for f in unit.findings(bb.results)] for unit, _ in pending}
        await verify_claim_consistency(
            bb.results, researcher.consistency_verifier, researcher.verification_llm, ctx.tracer,
        )
        for unit, entry in pending:
            if before[unit.id] != [_key(f) for f in unit.findings(bb.results)]:
                await check(unit, entry, set())
    bb.scratch[REVIEW_COVERAGE_KEY] = {
        "version": VERSION, "kind": "peer_methods", "input_hash": _input(bb.scratch, bb.results),
        "documents": documents, "run_id": ctx.run_id,
    }
    issues = coverage_issues(bb.scratch, bb.results)
    ctx.tracer.emit(
        "RESEARCHER", "info", "方法章节证据覆盖通过" if not issues else "方法证据仍需补齐",
        data={"category": REVIEW_COVERAGE_KEY, "issues": issues},
    )
    return bb
