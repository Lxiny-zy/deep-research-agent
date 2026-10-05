"""Check each supplied paper's task evidence before writing a closed review."""

from __future__ import annotations

import hashlib
from typing import Any

from ..agents.base import Blackboard, RunContext
from ..agents.researcher import Researcher
from ..bibliography import document_identity
from ..guardrails import report_eligible, verify_claim_consistency
from ..models import Finding, ResearchResult, Source
from ..persistence.repository import LeaseLostError
from ..registry import register
from .attachment_reader import source_batches
from .contract import contract_from_scratch, provided_review
from .intake import _FixedSources
from .paper_evidence import EvidencePlan, plan_findings
from .quality import coerce_policy
from .reader import paper_sources_from_scratch
from .support import digest

REVIEW_COVERAGE_KEY = "review_coverage"
REVIEW_COVERAGE_VERSION = 1


def _groups(scratch: dict[str, Any]) -> dict[str, list[Source]]:
    groups: dict[str, list[Source]] = {}
    seen = set()
    for source in paper_sources_from_scratch(scratch):
        key = (source.url, hashlib.sha256(source.content.encode()).hexdigest())
        if key not in seen:
            groups.setdefault(document_identity(source.url)[0], []).append(source)
            seen.add(key)
    return groups


def _findings(results: list[ResearchResult], sources: list[Source]) -> list[Finding]:
    snapshots = {
        (source.url, hashlib.sha256(source.content.encode()).hexdigest()) for source in sources
    }
    return [
        f
        for result in results
        for f in result.findings
        if (f.source_url, f.verification.source_content_hash) in snapshots and report_eligible(f)
    ]


def _material(findings: list[Finding]) -> list[dict[str, Any]]:
    return [
        f.model_dump(
            mode="json",
            include={
                "statement",
                "source_url",
                "evidence_quote",
                "entity",
                "quantity",
                "conditions",
            },
        )
        for f in findings
    ]


def coverage_fingerprint(scratch: dict[str, Any], results: list[ResearchResult]) -> str:
    contract = contract_from_scratch(scratch)
    groups = _groups(scratch)
    return digest(
        [
            REVIEW_COVERAGE_VERSION,
            contract.original_request if contract else "",
            {
                key: [
                    (s.url, s.title, s.locator, hashlib.sha256(s.content.encode()).hexdigest())
                    for s in sources
                ]
                for key, sources in groups.items()
            },
            {key: _material(_findings(results, sources)) for key, sources in groups.items()},
        ]
    )


def coverage_issues(scratch: dict[str, Any], results: list[ResearchResult]) -> list[str]:
    contract = contract_from_scratch(scratch)
    if contract is not None and contract.template == "peerReview":
        from .peer_coverage import coverage_issues as peer_issues

        return peer_issues(scratch, results)
    if not provided_review(contract_from_scratch(scratch)):
        return []
    record = scratch.get(REVIEW_COVERAGE_KEY)
    if (
        not isinstance(record, dict)
        or record.get("version") != REVIEW_COVERAGE_VERSION
        or record.get("input_hash") != coverage_fingerprint(scratch, results)
    ):
        return ["指定文献的任务证据覆盖尚未核对，或材料已变更，需要先完成覆盖检查"]
    documents = record.get("documents")
    expected = set(_groups(scratch))
    if (
        not isinstance(documents, list)
        or not expected
        or len(documents) != len(expected)
        or {d.get("id") for d in documents if isinstance(d, dict) and isinstance(d.get("id"), str)}
        != expected
    ):
        return ["任务证据覆盖记录未包含全部已导入文献"]
    issues = []
    for document in documents:
        if document.get("status") != "pass":
            missing = document.get("missing_topics") or ["关键方法或任务所需证据尚未确认"]
            if not isinstance(missing, list):
                missing = [missing]
            issues.append(
                f"「{document.get('title') or document['id']}」证据覆盖不足："
                + "；".join(str(x) for x in missing)
            )
    return issues


def _question(request: str, title: str) -> str:
    return (
        f"为以下综述核对当前文献「{title}」的证据充分性。只检查当前文献，不要求它回答其他论文的内容。\n"
        f"综述任务：{request}\n"
        "判断现有发现能否说明用户比较所需的输入或研究对象、核心方法或论证步骤、"
        "关键组成部分、适用的实验或研究条件、结果与有依据的局限。"
        "重点核对方法如何运作以及各部分如何相互作用；涉及算法或模型时核对计算步骤与参数的作用，"
        "不能仅有引言贡献口号和大量基线数字就认为核心方法已覆盖。"
        "不要求用户未问的完整复现细节，不因论文没有使用某个范式术语就认为其结构无法比较。"
        "未抽取内容不等于原文没有；不足时从目录选择最相关的完整片段补读。"
    )


@register("review_evidence_coverage")
class ReviewEvidenceCoverage:
    name: str

    async def step(self, bb: Blackboard, ctx: RunContext) -> Blackboard:
        contract = contract_from_scratch(bb.scratch)
        if contract is not None and contract.template == "peerReview":
            from .peer_coverage import check_methods

            return await check_methods(bb, ctx)
        if not provided_review(contract):
            return bb
        assert contract is not None
        if not coverage_issues(bb.scratch, bb.results):
            return bb
        groups = _groups(bb.scratch)
        researcher = Researcher(
            llm=ctx.llm_for("researcher"), tracer=ctx.tracer, settings=ctx.settings
        )
        researcher.verification_llm = ctx.llm_for("evidence_verifier")
        researcher.system = ctx.system_prompt(researcher.system)
        rounds = coerce_policy(contract.quality).review_evidence_rounds
        documents: list[dict[str, Any]] = []
        before_consistency: dict[str, str] = {}
        added = False

        async def check(
            sources: list[Source], question: str, read: set[str], record: dict[str, Any]
        ) -> EvidencePlan | None:
            try:
                plan = await plan_findings(
                    _findings(bb.results, sources),
                    question,
                    [],
                    researcher,
                    sources,
                    read_urls=read,
                )
            except LeaseLostError:
                raise
            except Exception as exc:
                record["missing_topics"] = [f"覆盖检查未完成：{type(exc).__name__}"]
                record["status"] = "fail"
                return None
            record["checks"].append(
                {
                    "sufficient": plan.sufficient,
                    "missing_topics": plan.missing_topics,
                    "source_urls": plan.source_urls,
                }
            )
            record["status"] = "pass" if plan.sufficient else "fail"
            record["missing_topics"] = plan.missing_topics
            return plan

        for key, sources in groups.items():
            title = sources[0].title or key
            question = _question(contract.original_request, title)
            record: dict[str, Any] = {
                "id": key,
                "title": title,
                "status": "fail",
                "checks": [],
                "reads": [],
            }
            documents.append(record)
            read: set[str] = set()
            ctx.tracer.emit(
                "RESEARCHER",
                "info",
                f"核对「{title}」是否具备综述所需的关键证据…",
                data={"category": REVIEW_COVERAGE_KEY},
            )
            for attempt in range(rounds + 1):
                plan = await check(sources, question, read, record)
                if plan is None or plan.sufficient or attempt == rounds:
                    break
                chosen = [
                    s
                    for s in sources
                    if s.url not in read and (not plan.source_urls or s.url in plan.source_urls)
                ]
                if not chosen:
                    break
                read.update(s.url for s in chosen)
                focus = question + "\n本次补读重点：" + "；".join(plan.missing_topics)
                ctx.tracer.emit(
                    "RESEARCHER",
                    "info",
                    f"补读「{title}」的 {len(chosen)}/{len(sources)} 个片段，补齐关键证据…",
                    data={"category": REVIEW_COVERAGE_KEY},
                )
                entry: dict[str, Any] = {
                    "source_urls": [s.url for s in chosen],
                    "findings": 0,
                    "calls": 0,
                }
                record["reads"].append(entry)
                try:
                    for batch in source_batches(chosen, researcher, focus):
                        researcher.search = _FixedSources(batch)
                        entry["calls"] += 1
                        result = await researcher.run(focus)
                        if result is not None and (result.findings or result.extraction_audit):
                            bb.results.append(
                                result.model_copy(
                                    update={"sub_question": f"「{title}」任务证据补读"}
                                )
                            )
                            entry["findings"] += sum(report_eligible(f) for f in result.findings)
                            added = True
                except LeaseLostError:
                    raise
                except Exception as exc:
                    record["missing_topics"] = [f"补读未完成：{type(exc).__name__}"]
                    break
            before_consistency[key] = digest(_material(_findings(bb.results, sources)))

        if added:
            await verify_claim_consistency(
                bb.results, researcher.consistency_verifier, researcher.verification_llm, ctx.tracer
            )
            for record in documents:
                sources = groups[record["id"]]
                if before_consistency[record["id"]] != digest(
                    _material(_findings(bb.results, sources))
                ):
                    await check(
                        sources,
                        _question(contract.original_request, record["title"]),
                        set(),
                        record,
                    )
        bb.scratch[REVIEW_COVERAGE_KEY] = {
            "version": REVIEW_COVERAGE_VERSION,
            "input_hash": coverage_fingerprint(bb.scratch, bb.results),
            "documents": documents,
        }
        issues = coverage_issues(bb.scratch, bb.results)
        ctx.tracer.emit(
            "RESEARCHER",
            "info",
            "已导入文献的关键证据覆盖检查通过"
            if not issues
            else "关键证据仍有缺口，正式综述交付将保留为未通过",
            data={
                "category": REVIEW_COVERAGE_KEY,
                "issues": issues,
                "read_calls": sum(
                    read["calls"] for document in documents for read in document["reads"]
                ),
            },
        )
        return bb
