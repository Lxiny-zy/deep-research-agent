"""Research/survey evidence context, rendered from existing frozen records only."""

from __future__ import annotations

from typing import Any

from ..document_corpus import content_hash, corpus_from_inputs
from ..models import ExperimentConditions, Quantity
from ..persistence.repository import RunDetail
from ..report.document import TableBlock, TableCell, TableColumn, TableRow
from ..report.markdown import _table
from ..report.pivot import _display
from .delivery.math_markdown import replace_citations
from .extraction import processing_failures
from .fulltext_review import located_passages
from .prose_review import reviewer_for_report, stored_review
from .support import digest, evidence_records

RESEARCH_CONTEXT_VERSION = 1


def _text(value: str) -> str:
    # Source-paper reference numbers are literal metadata, not run citations.
    value = replace_citations(value, lambda match: r"\[" + match[1] + r"\]")
    return value.replace("\r", " ").replace("\n", " ").replace("|", "\\|")


def research_context(detail: RunDetail) -> dict[str, Any]:
    from ..report.service import requires_corroboration

    scratch = detail.orchestration.checkpoint.get("scratch", {}) if detail.orchestration else {}
    review = stored_review(scratch) or {}
    review = review if isinstance(review, dict) else {}
    mapping = {
        url: index for index, url in enumerate(detail.report.citations if detail.report else [], 1)
    }
    corroboration = requires_corroboration(detail)
    records = evidence_records(detail.results, mapping, corroboration=corroboration)
    unique: dict[str, dict[str, Any]] = {}
    variants: dict[str, set[str]] = {}
    for record in records:
        metadata = record.get("measurement_context")
        if metadata is None:
            continue
        key = digest([record["id"], metadata])
        variants.setdefault(record["id"], set()).add(key)
        conditions = metadata.get("conditions") or {}
        known = {k: value for k, value in conditions.items() if value not in (None, "")}
        unique[key] = {
            "id": key,
            "evidence_id": record["id"],
            "citation": record["citation"],
            "entity": record.get("entity", ""),
            "statement": record["statement"],
            "source_hash": record["source_hash"],
            "measurement_context": metadata,
            "recorded_conditions": known,
            "missing_fields": [
                field
                for field in ("dataset", "split", "train_data", "protocol", "acquisition")
                if not conditions.get(field)
            ],
            "scope_key": digest(known) if known else "unknown-" + key,
            "comparability": "not_established_by_metadata",
        }
    for record in unique.values():
        record["metadata_conflict"] = len(variants[record["evidence_id"]]) > 1
    corpus = corpus_from_inputs(detail.results, mapping, scratch, detail.sources)
    sources = [
        {
            "id": key,
            "title": document.title,
            "complete_manifest": document.complete,
            "citation_positions": sorted(
                index for index, document_key in corpus.citations.items() if document_key == key
            ),
            "parts": [
                {
                    "source": source.url,
                    "locator": source.locator,
                    "content_hash": content_hash(source.content),
                    "document_content_hash": source.document_content_hash,
                }
                for source in document.sources
            ],
        }
        for key, document in corpus.documents.items()
    ]
    query_terms = sorted(
        {
            selection.search_query
            for result in detail.results
            if result.extraction_audit
            for selection in result.extraction_audit.source_selections
            if selection.search_query
        }
    )
    subquestions = [
        {
            "id": digest(result.sub_question),
            "question": result.sub_question,
            "finding_ids": list(
                dict.fromkeys(
                    record["id"]
                    for record in evidence_records(
                        [result],
                        mapping,
                        corroboration=corroboration,
                    )
                )
            ),
            "processing_status": "failed"
            if processing_failures([result])
            else "recorded"
            if result.extraction_audit
            else "not_recorded",
        }
        for result in detail.results
    ]
    passages: list[dict[str, Any]] = []
    bound = False
    if detail.report:
        checker = reviewer_for_report(
            None,
            detail.query,
            detail.results,
            detail.report.citations,
            scratch,
            0,
            corroboration=corroboration,
            sources=detail.sources,
        )
        if checker and review:
            bound, _ = checker.check(detail.report.markdown, review)
            if bound:
                by_id = {unit.id: unit for unit in checker.units(detail.report.markdown)[0]}
                for decision in review.get("decisions", []):
                    unit = by_id.get(decision["unit_id"])
                    if unit:
                        passages.extend(
                            {"unit_id": unit.id, **passage}
                            for passage in located_passages(
                                unit,
                                decision.get("fulltext_review"),
                                checker.reviewer.fulltext_corpus,
                            )
                        )
    return {
        "version": RESEARCH_CONTEXT_VERSION,
        "input_hash": digest(
            [
                detail.report.model_dump(mode="json") if detail.report else None,
                [result.material_data() for result in detail.results],
                corpus.fingerprint,
            ]
        ),
        "measurements": list(unique.values()),
        "subquestions": subquestions,
        "source_coverage": sources,
        "prose_review_bound": bound,
        "located_fulltext_passages": passages,
        "search_scope": {
            "queries_with_recorded_sources": query_terms,
            "search_completed_at": None,
            "run_started_at": detail.created_at.isoformat() if detail.created_at else None,
            "scope": "saved_source_snapshots_only",
            "domain_absence_established": False,
        },
    }


def research_notes(detail: RunDetail) -> str:
    packet = research_context(detail)
    if (
        not packet["measurements"]
        and not packet["source_coverage"]
        and not packet["located_fulltext_passages"]
        and not packet["subquestions"]
    ):
        return ""
    lines = [
        "## 证据、比较口径与检索范围记录",
        "",
        "以下为本任务保存的素材与字段记录，便于回查；字段相同不自动证明实验可比，"
        "数值核验状态也不代表全部条件已独立核验。",
        "",
    ]
    if packet["subquestions"]:
        lines += ["### 子问题处理记录", ""]
        statuses = {
            "failed": "存在来源处理失败，需结合读取范围检查缺口",
            "recorded": "保存了来源处理记录",
            "not_recorded": "没有保存来源处理诊断，处理范围未知",
        }
        for question in packet["subquestions"]:
            evidence_count = len(question["finding_ids"])
            lines.append(
                f"- {_text(question['question'])}：{statuses[question['processing_status']]}；"
                f"当前准入发现 {evidence_count} 条。发现数量不代表已回答或证据充分。"
            )
        lines.append("")
    if packet["measurements"]:
        table = TableBlock(
            id="research_measurement_context",
            title="已记录的测量与适用条件",
            columns=[
                TableColumn(key="source", label="素材位置"),
                TableColumn(key="metric", label="指标 / 单位"),
                TableColumn(key="value", label="原始数值写法"),
                TableColumn(key="state", label="原数值检查状态"),
            ],
        )
        for record in packet["measurements"]:
            metadata = record["measurement_context"]
            quantity = (
                Quantity.model_validate(metadata["quantity"]) if metadata["quantity"] else None
            )
            conditions = (
                ExperimentConditions.model_validate(metadata["conditions"])
                if metadata["conditions"]
                else None
            )
            index = len(table.notes) + 1
            condition_note = (
                conditions.describe()
                if conditions and not conditions.is_empty()
                else "实验条件没有记录，暂不建立跨行可比性"
            )
            table.notes.append(
                f"记录 {record['id'][:12]}：{_text(condition_note)}；这些是提取字段，需对照原文；"
                + ("同一证据存在不同字段版本，尚未合并。" if record["metadata_conflict"] else "")
            )
            table.rows.append(
                TableRow(
                    label=_text(record["entity"] or "对象未标注"),
                    cells={
                        "source": TableCell(
                            value=str(record["citation"])
                            if record["citation"]
                            else "未编入引文目录"
                        ),
                        "metric": TableCell(
                            value=_text(
                                f"{quantity.metric} / {quantity.unit or '单位未标注'}"
                                if quantity
                                else "数值未结构化"
                            )
                        ),
                        "value": TableCell(
                            value=_text(_display(quantity))
                            if quantity and quantity.value is not None
                            else "未形成点值",
                            note_ref=index,
                        ),
                        "state": TableCell(
                            value={
                                "verified": "已核验",
                                "unsupported": "未获支持",
                                "not_applicable": "不适用或未记录数值核验",
                            }.get(metadata["quantity_status"], "状态未知")
                        ),
                    },
                )
            )
        table.caption = "仅列保存的原始字段；未列字段不等于原文未报告，也不自动建立方法排名。"
        lines += [_table(table, heading_level=3), ""]
    if packet["source_coverage"]:
        lines += ["### 来源读取范围", ""]
        for source in packet["source_coverage"]:
            scope = (
                "完整文档清单校验通过"
                if source["complete_manifest"]
                else "当前只有片段或清单不完整，不据此断言全文缺失"
            )
            positions = (
                "、".join(str(index) for index in source["citation_positions"]) or "未编入引文目录"
            )
            locators = "；".join(
                _text(part["locator"] or part["source"]) for part in source["parts"]
            )
            lines.append(
                f"- 素材位置 {positions} · {_text(source['title'])}：{scope}；"
                f"{len(source['parts'])} 个来源片段；{locators}。"
            )
    lines += [
        "",
        "### 检索范围边界",
        "",
        "本附录只覆盖本任务保存的来源快照，不证明整个领域不存在其他研究。"
        "来源选择记录中的检索式并非完整检索日志；独立检索完成时间没有记录时保持未知，不以运行开始时间代替。",
    ]
    if packet["search_scope"]["queries_with_recorded_sources"]:
        lines.append(
            "已记录且取得来源的检索式："
            + "；".join(_text(q) for q in packet["search_scope"]["queries_with_recorded_sources"])
        )
    if packet["located_fulltext_passages"]:
        lines += [
            "",
            "### 全文回查定位的互补片段",
            "",
            "这些片段保留其原文位置和回查结论，不自动批准其他新断言。",
        ]
        for passage in packet["located_fulltext_passages"]:
            verdict = "反驳原判断" if passage["verdict"] == "refutes" else "支持原判断"
            lines += [
                f"- 单元 {passage['unit_id']}；{_text(passage['locator'])}；"
                f"字符 {passage['start']}–{passage['end']}；回查：{verdict}。",
                "> " + _text(passage["quote"]),
            ]
    return "\n".join(lines).strip() + "\n"
