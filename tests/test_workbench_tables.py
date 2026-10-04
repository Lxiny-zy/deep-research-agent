"""Regression fixtures from sc82/sc32, using fixed admitted source excerpts."""

import json
from copy import deepcopy

import pytest

from deep_research.models import (
    EvidenceVerification,
    ExperimentConditions,
    Finding,
    Quantity,
    ResearchResult,
)
from deep_research.workbench.support import evidence_id


def admitted(statement, *, entity="CQR", quantity=None, conditions=None, quote=None):
    return Finding(
        statement=statement,
        evidence_quote=quote or statement,
        source_url="https://example.org/paper",
        entity=entity,
        quantity=quantity,
        conditions=conditions,
        verification=EvidenceVerification(
            status="verified", semantic_status="supported", quantity_status="verified"
        ),
    )


def inputs(*findings):
    return [ResearchResult(sub_question="比较", findings=list(findings))], {
        "https://example.org/paper": 1
    }


def test_sc82_imports_actual_cqr_value_and_all_quartiles_from_verified_quote():
    from deep_research.workbench.tables import TableSpec, render_table

    quote = r"""\caption{Coverage by predicted-fatigue-strength quartile}
\begin{tabular}{lccc}
\toprule
Quartile & SCP & CQR & Unreported \\
\midrule
Q1 & $0.979 \pm 0.032$ & $0.908 \pm 0.085$ & -- \\
Q2 & $0.974 \pm 0.033$ & $0.988 \pm 0.030$ & -- \\
Q3 & $0.966 \pm 0.040$ & $0.971 \pm 0.039$ & -- \\
Q4 & $0.758 \pm 0.109$ & $0.816 \pm 0.089$ & -- \\
\bottomrule
\end{tabular}"""
    finding = admitted("SCP 在 Q1 的覆盖率为 0.979。", entity="SCP", quote=quote)
    table = render_table(
        TableSpec(id="t1", title="表 1 四分位覆盖", source_finding_id=evidence_id(finding)),
        *inputs(finding),
    )
    assert "0.908 ± 0.085" in table.markdown
    assert "Q3" in table.markdown
    assert "未报告" in table.markdown
    assert "[1]" in table.markdown
    assert len(table.units) == 12
    # The same quoted CQR value must not disappear when the model chooses the
    # generic form but omits a separately extracted numeric finding.
    missing = TableSpec.model_validate(
        {
            "id": "t2",
            "title": "表 2 coverage",
            "columns": [
                {"key": "v", "label": "coverage", "field": "quantity", "metric": "coverage"}
            ],
            "rows": [{"label": "CQR", "cells": {"v": []}}],
        }
    )
    with pytest.raises(ValueError, match="原始表格"):
        render_table(missing, *inputs(finding))


def test_quantities_are_resolved_even_when_model_omits_existing_cell_reference():
    from deep_research.workbench.tables import TableSpec, render_table

    finding = admitted(
        "CQR coverage 0.908 ± 0.085",
        quantity=Quantity(metric="coverage", value=0.908, rendered="0.908", uncertainty=0.085),
    )
    spec = TableSpec.model_validate(
        {
            "id": "t1",
            "title": "表 1 覆盖率",
            "columns": [
                {"key": "coverage", "label": "coverage", "field": "quantity", "metric": "coverage"}
            ],
            "rows": [{"label": "CQR", "cells": {"coverage": []}}],
        }
    )
    table = render_table(spec, *inputs(finding))
    assert "0.908 ± 0.085" in table.markdown


def test_sc32_spatial_size_keeps_training_scope_and_cannot_be_rewritten_by_model():
    from deep_research.workbench.tables import TableSpec, render_table

    finding = admitted(
        "MST++ 训练时裁剪 128×128 的 RGB 与 HSI 样本对。",
        entity="MST++",
        conditions=ExperimentConditions(spatial_size="128×128"),
        quote="During the training procedure, 128 × 128 RGB and HSI sample pairs are cropped.",
    )
    spec = TableSpec.model_validate(
        {
            "id": "t1",
            "title": "表 1 实验条件",
            "columns": [{"key": "size", "label": "空间尺寸", "field": "conditions.spatial_size"}],
            "rows": [{"label": "MST++", "cells": {"size": [evidence_id(finding)]}}],
        }
    )
    table = render_table(spec, *inputs(finding))
    assert "128×128" in table.markdown and "训练" in table.markdown
    bad = spec.model_dump()
    bad["rows"][0]["cells"]["size"] = [{"value": "256×256"}]
    with pytest.raises(ValueError):
        TableSpec.model_validate(bad)


def test_raw_numeric_table_without_bound_spec_is_rejected():
    from deep_research.workbench.tables import table_issues

    assert table_issues("| 方法 | 覆盖率 |\n|---|---|\n| CQR | 0.908 [1] |", [], {}, None)


class TableJudge:
    def __init__(self, *, verdict="supported"):
        self.verdict = verdict
        self.calls = []

    async def parse(self, system, user, schema, **kwargs):
        payload = json.loads(user)
        self.calls.append(payload)
        return schema.model_validate(
            {
                "decisions": [
                    {
                        "unit_id": unit["id"],
                        "verdict": self.verdict,
                        "evidence_ids": [item["id"] for item in payload["evidence"]],
                        "reason": "fixed cell judgement",
                    }
                    for unit in payload["units"]
                ]
            }
        )


def quantity_spec(finding):
    return {
        "id": "t1",
        "title": "表 1 coverage",
        "columns": [{"key": "v", "label": "coverage", "field": "quantity", "metric": "coverage"}],
        "rows": [{"label": finding.entity, "cells": {"v": [evidence_id(finding)]}}],
    }


@pytest.mark.asyncio
async def test_bound_table_rechecks_values_citations_scope_and_reviews():
    from deep_research.workbench.tables import render_specs, review_tables, table_issues

    finding = admitted(
        "CQR coverage 0.908", quantity=Quantity(metric="coverage", value=0.908, rendered="0.908")
    )
    results, mapping = inputs(finding)
    raw = "```evidence-table\n" + json.dumps(quantity_spec(finding)) + "\n```\n"
    body, record = render_specs(raw, results, mapping)
    assert table_issues(body, results, mapping, record)
    judge = TableJudge()
    assert not await review_tables(body, record, results, mapping, judge, 50000)
    assert len(judge.calls) == 1
    assert not await review_tables(body, record, results, mapping, judge, 50000)
    assert len(judge.calls) == 1
    for bad_body in [
        body.replace("0.908", "0.988"),
        body.replace("[1]", "[2]"),
        body.replace("口径脚注", "备注"),
    ]:
        assert table_issues(bad_body, results, mapping, record)
    changed = deepcopy(results)
    changed[0].findings[0].quantity.unit = "%"
    assert table_issues(body, changed, mapping, record)
    bad = deepcopy(record)
    bad["decisions"] = []
    assert table_issues(body, results, mapping, bad)
    bad = deepcopy(record)
    bad["decisions"][0]["evidence_ids"] = ["other"]
    assert table_issues(body, results, mapping, bad)
    bad = deepcopy(record)
    bad["decisions"][0]["verdict"] = "non_factual"
    assert table_issues(body, results, mapping, bad)


@pytest.mark.asyncio
async def test_independent_cell_judge_cannot_borrow_sibling_evidence():
    from deep_research.workbench.tables import render_specs, review_tables

    a = admitted(
        "A coverage 0.908",
        entity="A",
        quantity=Quantity(metric="coverage", value=0.908, rendered="0.908"),
    )
    b = admitted(
        "B coverage 0.979",
        entity="B",
        quantity=Quantity(metric="coverage", value=0.979, rendered="0.979"),
    )
    results, mapping = inputs(a, b)
    spec = quantity_spec(a)
    spec["rows"].append({"label": "B", "cells": {"v": [evidence_id(b)]}})
    body, record = render_specs(
        "```evidence-table\n" + json.dumps(spec) + "\n```", results, mapping
    )
    judge = TableJudge()
    assert not await review_tables(body, record, results, mapping, judge, 50000)
    assert [{e["id"] for e in call["evidence"]} for call in judge.calls] == [
        {evidence_id(a)},
        {evidence_id(b)},
    ]
    record.pop("review_hash")
    assert await review_tables(
        body, record, results, mapping, TableJudge(verdict="unsupported"), 50000
    )


def test_scopes_must_be_separated_and_missing_values_are_explicit():
    from deep_research.workbench.tables import TableSpec, render_table

    a = admitted(
        "CQR coverage 0.908 in Q1",
        quantity=Quantity(metric="coverage", value=0.908, rendered="0.908"),
        conditions=ExperimentConditions(split="Q1"),
    )
    b = admitted(
        "CQR coverage 0.971 in Q3",
        quantity=Quantity(metric="coverage", value=0.971, rendered="0.971"),
        conditions=ExperimentConditions(split="Q3"),
    )
    spec = quantity_spec(a)
    spec["rows"][0]["cells"]["v"] = []
    with pytest.raises(ValueError, match="不同单位或实验口径"):
        render_table(TableSpec.model_validate(spec), *inputs(a, b))
    spec["columns"][0]["scope"] = {"split": "Q1"}
    table = render_table(TableSpec.model_validate(spec), *inputs(a, b))
    assert "0.908" in table.markdown and "0.971" not in table.markdown
    spec["columns"][0]["scope"] = {"split": "Q2"}
    assert "未报告" in render_table(TableSpec.model_validate(spec), *inputs(a, b)).markdown


def test_unverified_findings_and_ambiguous_source_tables_are_rejected():
    from deep_research.workbench.tables import TableSpec, render_specs, render_table

    finding = admitted(
        "CQR coverage 0.908", quantity=Quantity(metric="coverage", value=0.908, rendered="0.908")
    )
    spec = TableSpec.model_validate(quantity_spec(finding))
    finding.verification.semantic_status = "unsupported"
    with pytest.raises(ValueError, match="未通过核验"):
        render_table(spec, *inputs(finding))
    finding.verification.semantic_status = "supported"
    finding.quantity.value = None
    with pytest.raises(ValueError, match="缺少有效数值"):
        render_table(spec, *inputs(finding))
    body, record = render_specs('```evidence-table\n{"id":"x","value":1}\n```', [], {})
    assert record["errors"] and "evidence-table" in body
    finding = admitted("source table", quote=r"\begin{tabular}{lcc}Row & A & B \\ Q1 & 0.908 \\")
    with pytest.raises(ValueError, match="行列不完整"):
        render_table(
            TableSpec(id="t1", title="表 1", source_finding_id=evidence_id(finding)),
            *inputs(finding),
        )


@pytest.mark.asyncio
async def test_analysis_table_uses_exact_ledger_records_and_invalidates_changes():
    from deep_research.workbench.tables import render_specs, review_tables, table_issues

    ledger = {"describe": [{"variable": "x", "n": 12, "mean": 3.25, "std": None}]}
    raw = '```evidence-table\n{"id":"t1","title":"表 1 描述统计","ledger_path":["describe"]}\n```'
    body, record = render_specs(raw, [], {}, ledger=ledger)
    assert "3.25" in body and "未报告" in body
    assert not await review_tables(body, record, [], {}, TableJudge(), 50000, ledger=ledger)
    changed = {"describe": [{"variable": "x", "n": 12, "mean": 4.25, "std": None}]}
    assert table_issues(body, [], {}, record, ledger=changed)


@pytest.mark.asyncio
@pytest.mark.parametrize("revisions", [0, 1])
async def test_writer_rebuilds_raw_table_and_delivery_blocks_unfixed_table(settings, revisions):
    from dataclasses import replace

    from deep_research.agents.base import Blackboard, RunContext
    from deep_research.models import Report
    from deep_research.observability import Tracer
    from deep_research.orchestrator import create_initial_execution
    from deep_research.persistence.repository import RunDetail
    from deep_research.workbench.publish import build_bundle
    from deep_research.workbench.quality import QualityPolicy
    from deep_research.workbench.tables import TABLES_KEY
    from deep_research.workbench.templates import get_template
    from deep_research.workbench.writers import ResearchWriter
    from tests.fakes import FakeSearch

    finding = admitted(
        "CQR coverage 0.908", quantity=Quantity(metric="coverage", value=0.908, rendered="0.908")
    )
    results, mapping = inputs(finding)
    raw = "| 方法 | coverage |\n|---|---|\n| CQR | 0.908 [1] |"
    corrected = "```evidence-table\n" + json.dumps(quantity_spec(finding)) + "\n```"

    class Writer(ResearchWriter):
        calls = []

        async def write(self, bb, ctx, template, contract, material, revision=None):
            self.calls.append(revision)
            return corrected if revision else raw

    class ProseStub:
        async def review(self, body):
            return {"status": "pass", "issues": [], "can_revise": True}

    bb = Blackboard(query="coverage", results=results)
    writer = Writer()
    ctx = RunContext(llm=TableJudge(), search_tool=FakeSearch(), tracer=Tracer(), settings=settings)
    body, log = await writer._write_checked(
        bb,
        ctx,
        replace(get_template("autoResearch"), sections=(), min_length=0),
        None,
        "",
        mapping,
        policy=QualityPolicy(
            max_revisions=revisions, register_check=False, require_limitations=False
        ),
        min_citations=1,
        require_corroboration=False,
        reviewer=ProseStub(),
    )
    assert len(writer.calls) == revisions + 1
    if revisions:
        assert "表格" in writer.calls[1]
        assert not log.remaining, log.remaining
        assert bb.scratch[TABLES_KEY]["decisions"]
    execution = create_initial_execution(bb.query, "research_quick", settings)
    execution.checkpoint = bb.model_dump(mode="json")
    detail = RunDetail(
        id="table-test",
        query=bb.query,
        status="done",
        results=results,
        report=Report(query=bb.query, markdown=body, citations=list(mapping)),
        orchestration=execution,
    )
    bundle = build_bundle(detail)
    gate = next(g for g in bundle.gates if g.name == "table_evidence")
    assert (gate.status == "pass") == bool(revisions), gate.issues
    if not revisions:
        assert {f.format for f in bundle.files} == {"md"}


@pytest.mark.asyncio
async def test_sc32_mislabelled_training_size_fails_cell_review():
    from deep_research.workbench.tables import render_specs, review_tables

    finding = admitted(
        "MST++ 训练时裁剪 128×128 图像块。",
        entity="MST++",
        conditions=ExperimentConditions(spatial_size="128×128"),
    )
    spec = {
        "id": "t1",
        "title": "表 1 实验输入",
        "columns": [{"key": "size", "label": "仿真输入尺寸", "field": "conditions.spatial_size"}],
        "rows": [{"label": "MST++", "cells": {"size": [evidence_id(finding)]}}],
    }

    class ScopeJudge(TableJudge):
        async def parse(self, system, user, schema, **kwargs):
            assert "训练" in user and "仿真输入尺寸" in user
            return await super().parse(system, user, schema, **kwargs)

    results, mapping = inputs(finding)
    body, record = render_specs(
        "```evidence-table\n" + json.dumps(spec) + "\n```", results, mapping
    )
    assert await review_tables(
        body, record, results, mapping, ScopeJudge(verdict="unsupported"), 50000
    )


def test_rendered_values_are_shared_by_document_formats():
    from deep_research.workbench.delivery.docx import docx_stats, render_docx
    from deep_research.workbench.delivery.html import render_html
    from deep_research.workbench.delivery.pdf import pdf_text, render_pdf
    from deep_research.workbench.tables import TableSpec, render_table

    finding = admitted(
        "CQR coverage 0.908 ± 0.085",
        quantity=Quantity(metric="coverage", value=0.908, rendered="0.908", uncertainty=0.085),
    )
    body = render_table(TableSpec.model_validate(quantity_spec(finding)), *inputs(finding)).markdown
    assert "0.908" in render_html(body, title="t")
    assert docx_stats(render_docx(body, title="t"))["tables"] == 1
    _, text = pdf_text(render_pdf(body, title="t"))
    assert "0.908" in text and "0.085" in text


def test_live_preview_hides_partial_and_complete_model_table_specs():
    from deep_research.workbench.tables import table_preview

    raw = '正文\n\n```evidence-table\n{"finding_id":"private-spec"}\n```\n后文'
    for end in range(raw.index("{") + 1, len(raw) + 1):
        preview = table_preview(raw[:end])
        assert "finding_id" not in preview and "private-spec" not in preview
        assert "正文" in preview
    assert "后文" in table_preview(raw)
