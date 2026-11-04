"""剩余问题分级：可修订问题完成并标注待核，硬门问题仍停在 needs_review。"""

from __future__ import annotations

import pytest

from deep_research.models import Report
from deep_research.persistence.repository import RunDetail
from deep_research.report.validation import finalize_report, validate_body
from deep_research.workbench.completion import assess_completion
from deep_research.workbench.completion_repair import _worth_revising_flagged
from deep_research.workbench.flag_markers import mark_paragraphs, short_reason
from deep_research.workbench.gate_classification import blocks_formal_export, classify_gates
from deep_research.workbench.gates import GateResult
from deep_research.workbench.publish import build_bundle
from tests.test_completion_auto_repair import BAD, GOOD, run_fixture
from tests.test_completion_classification import subject
from tests.test_prose_review import findings

FORMAL = {"html", "docx", "pdf"}


def _prose_gate(*rows, bound=True, method="model_assessment"):
    return GateResult(
        "prose_evidence",
        "fail",
        [row["message"] for row in rows],
        {"review_bound": bound, "method": method, "findings": list(rows)},
    )


def _row(code, message, unit="p-1"):
    return {"code": code, "message": message, "unit": unit}


# ---------------------------------------------------------------- (a) 可修订 → 完成 + 待核


async def test_revisable_leftovers_finish_done_with_markers_and_formal_files(
    settings, cooperative_render
):
    repo, run_id, model, _ = await run_fixture(
        settings, max_revisions=0, formats=["md", "html", "docx", "pdf"]
    )
    detail = await repo.get_run(run_id)
    completion = detail.orchestration.checkpoint["scratch"]["_completion"]
    assert detail.status == "done", completion["issues"]
    assert completion["issues"] == []
    assert completion["flagged"] and all(
        set(item) == {"gate", "issue"} for item in completion["flagged"]
    )
    assert any(item["gate"] == "prose_evidence" for item in completion["flagged"])
    # 写作者原文保留在定稿中，没有被删去或替换成素材摘录。
    assert BAD.rstrip("。") in detail.report.markdown
    assert "已验证素材摘要" not in detail.report.markdown
    assert "本段结论待进一步核验" not in detail.report.markdown

    bundle = build_bundle(detail)
    formats = {file.format for file in bundle.files}
    assert FORMAL <= formats
    source = next(f for f in bundle.files if f.format == "md").data.decode()
    html = next(f for f in bundle.files if f.format == "html").data.decode()
    # 不支持的段落原文仍在，段末追加了待核标注；已支持的段落不加标注。
    assert "变量已经证明因果关系" in source and "〔待核：" in source
    assert source.count("〔待核：") == 1
    assert "## 待核事项" in source
    assert "〔待核：" in html
    assert not any(gate.blocking_issues for gate in bundle.gates)


def test_uncited_paragraph_is_kept_and_flagged_only_when_its_numbers_have_evidence():
    results = findings()
    mapping = {"https://a.com": 1}
    cited = "变量存在相关关系，不能据此证明因果关系 [1]。"
    uncited = "这一结论适用于观察性研究。"
    check = validate_body(
        f"{cited}\n\n{uncited}", results, mapping, preserve_valid=True, keep_revisable=True
    )
    assert check.issues == ()
    assert uncited in check.body
    assert check.flagged == (("uncited_paragraph", uncited, "这一段没有任何引用角标"),)

    # 未引用段落中的数字在任何素材中都找不到：仍按无依据数值处理，不保留。
    invented = "样本量达到 4821 例。"
    check = validate_body(
        f"{cited}\n\n{invented}", results, mapping, preserve_valid=True, keep_revisable=True
    )
    assert "unsupported_number" in check.issues and invented not in check.body
    assert check.flagged == ()

    # 默认（未开启 keep_revisable）的终态复核行为不变。
    legacy = validate_body(f"{cited}\n\n{uncited}", results, mapping, preserve_valid=True)
    assert "uncited_paragraph" in legacy.issues


def test_finalize_report_keeps_revisable_paragraph_alongside_hard_rejections():
    report = Report(
        query="q",
        markdown=(
            "变量存在相关关系，不能据此证明因果关系 [1]。\n\n这一结论适用于观察性研究。\n\n"
            "变量相关系数为 0.93 [1]。"
        ),
        citations=["https://a.com"],
    )
    final, check = finalize_report(
        report, findings(), preserve_valid=True, keep_revisable=True
    )
    # 无依据数值的段落照旧删去（硬门），未引用段落保留并记入待核。
    assert "unsupported_number" in check.issues and check.partial
    assert "0.93" not in final.markdown
    assert "这一结论适用于观察性研究。" in final.markdown
    assert [row[0] for row in check.flagged] == ["uncited_paragraph"]


def test_revisable_prose_findings_complete_with_flagged_items(settings):
    gate = _prose_gate(
        _row("unsupported", "第 3 行：引用只支持相关，没有因果证据"),
        _row("alignment", "第 5 行：所选摘录缺少对应句中的数值或专名", "p-2"),
        _row("record_issue", "第 7 行：公式与原文不一致", "p-3"),
        _row("unsupported", "第 9 行：核验调用失败，本段未完成核验", "p-4"),
    )
    detail, value = subject(settings, gate)
    record = assess_completion(detail, value)
    assert record["status"] == "done" and record["issues"] == []
    assert [item["issue"] for item in record["flagged"]] == gate.issues
    assert not blocks_formal_export(gate, "autoResearch")
    assert not blocks_formal_export(gate, "dataAnalysis")


@pytest.mark.parametrize(
    "gate",
    [
        GateResult(
            "node_evidence",
            "fail",
            ["节点 0.1：来源只提到两者并列"],
            {
                "review_bound": True,
                "findings": [_row("node_unsupported", "节点 0.1：来源只提到两者并列", "0.1")],
            },
        ),
        GateResult("review_coverage", "fail", ["方法部分尚未覆盖"], {"scope": "peer_review"}),
        GateResult("review", "fail", ["评审没有给出 1–10 的整数评分（格式：评分：N/10）"]),
        GateResult(
            "revision",
            "warn",
            ["第 3 行：引用只支持相关"],
            {"remaining": 1, "remaining_issues": ["第 3 行：引用只支持相关"]},
        ),
        GateResult("structured_content", "warn", ["幻灯片结构与审核正文不一致"]),
        GateResult(
            "research_sufficiency", "warn", ["检索证据不足：已核验 1 个来源，任务要求 3 个"]
        ),
    ],
    ids=lambda gate: gate.name,
)
def test_each_revisable_family_completes_and_never_refuses_formal_export(settings, gate):
    detail, value = subject(settings, gate)
    record = assess_completion(detail, value)
    assert record["status"] == "done", record["issues"]
    assert {item["issue"] for item in record["flagged"]} == set(gate.issues)
    assert not blocks_formal_export(gate, "mindmap")


def test_strict_quality_mode_keeps_revisable_items_blocking():
    gate = _prose_gate(_row("unsupported", "第 3 行：引用只支持相关"))
    classify_gates([gate], strict=True)
    assert gate.blocking_issues == ["第 3 行：引用只支持相关"] and gate.flagged == []


# ---------------------------------------------------------------- (b) 硬门 → needs_review


@pytest.mark.parametrize(
    ("gate", "template"),
    [
        (GateResult("citation", "fail", ["正文引用了不存在的来源编号 [9]"]), "autoResearch"),
        (
            GateResult("task_content", "fail", ["深度研究正文有段落未通过引用或数值复核"]),
            "autoResearch",
        ),
        (
            _prose_gate(_row("statistic_binding", "统计数值绑定记录缺失或与当前正文/台账不一致")),
            "dataAnalysis",
        ),
        (_prose_gate(_row("statistic_scope", "相关分析被写成因果结论")), "dataAnalysis"),
        (
            _prose_gate(
                _row("record_stale", "正文、证据或核验规则已变更，原终稿核验记录不再适用"),
                bound=False,
            ),
            "dataAnalysis",
        ),
        (
            GateResult(
                "node_evidence",
                "fail",
                ["「方法」使用了不存在的引用编号"],
                {
                    "review_bound": True,
                    "findings": [_row("invalid_citation", "「方法」使用了不存在的引用编号", None)],
                },
            ),
            "mindmap",
        ),
        (GateResult("table_evidence", "fail", ["表格数值在所引素材中找不到"]), "autoResearch"),
    ],
    ids=["citation", "task_content", "statistic_binding", "statistic_scope", "version",
         "node_citation", "table_number"],
)
def test_hard_families_stay_needs_review_and_refuse_formal_export(settings, gate, template):
    detail, value = subject(settings, gate)
    record = assess_completion(detail, value)
    assert record["status"] == "needs_review"
    assert set(gate.issues) <= set(record["issues"])
    assert not any(item["issue"] in gate.issues for item in record["flagged"])
    assert blocks_formal_export(gate, template)


@pytest.mark.parametrize(
    "gate",
    [
        GateResult("territory", "fail", ["地名表述不符合规范"]),
        GateResult("render", "fail", ["PDF：生成失败：RuntimeError"]),
        GateResult("consistency", "fail", ["PDF 末段缺失"]),
        # 记录未绑定当前正文（版本冲突）时，即便代码本身可修订也不能放行。
        _prose_gate(_row("unsupported", "第 3 行：引用只支持相关"), bound=False),
        # 终稿没有经过任何模型核验，什么都没有被验证过。
        _prose_gate(_row("unsupported", "第 3 行：引用只支持相关"), method="not_reviewed"),
        # 旧记录没有逐条代码：无法分级，按硬门处理。
        GateResult("prose_evidence", "fail", ["第 3 行：引用只支持相关"]),
        GateResult("revision", "warn", ["修订未完成"]),
    ],
    ids=["territory", "render", "consistency", "unbound", "not_reviewed", "legacy_prose",
         "legacy_revision"],
)
def test_unverifiable_or_boundary_issues_stay_hard(settings, gate):
    detail, value = subject(settings, gate)
    assert assess_completion(detail, value)["status"] == "needs_review"


def test_corrupted_file_still_blocks_even_when_only_revisable_content_remains(settings):
    detail, value = subject(settings, _prose_gate(_row("unsupported", "第 3 行：引用只支持相关")))
    value.files[0].data = b""
    record = assess_completion(detail, value)
    assert record["status"] == "needs_review" and record["flagged"]


async def test_bad_citation_end_to_end_still_needs_review_without_formal_files(
    settings, cooperative_render
):
    import tests.test_completion_auto_repair as fixture

    original = fixture.BAD
    fixture.BAD = "变量存在相关关系 [7]。"
    try:
        repo, run_id, _, _ = await run_fixture(
            settings, max_revisions=0, formats=["md", "html", "docx", "pdf"]
        )
    finally:
        fixture.BAD = original
    detail = await repo.get_run(run_id)
    assert detail.status == "needs_review"
    bundle = build_bundle(detail)
    assert not FORMAL & {file.format for file in bundle.files}
    assert any(gate.name == "task_content" and gate.blocking_issues for gate in bundle.gates)


# ---------------------------------------------------------------- (c) 整篇素材摘要


async def test_body_that_is_entirely_the_fallback_summary_stays_needs_review(settings):
    repo, run_id, _, _ = await run_fixture(settings, broken=False)
    detail = await repo.get_run(run_id)
    summary = "## 已验证素材摘要\n\n- " + GOOD + "\n"
    detail.report = detail.report.model_copy(update={"markdown": summary})
    scratch = detail.orchestration.checkpoint["scratch"]
    scratch["_report_validation"] = {
        "scope": "citation_and_numbers", "issues": ["invalid_citation"], "fallback": True,
        "flagged": [{"code": "uncited_paragraph", "paragraph": "x", "message": "m"}],
    }
    bundle = build_bundle(detail)
    task = next(gate for gate in bundle.gates if gate.name == "task_content")
    assert task.status == "fail" and task.blocking_issues
    assert "目前仅保留证据摘录" in task.issues[0]
    assert not any(gate.name == "uncited_paragraphs" for gate in bundle.gates)
    assert not FORMAL & {file.format for file in bundle.files}
    assert assess_completion(detail, bundle)["status"] == "needs_review"


# ---------------------------------------------------------------- 标注与修复轮


def test_markers_keep_tables_and_code_blocks_intact():
    markdown = (
        "## 结果\n\n第一段结论 [1]。\n\n| 指标 | 值 |\n| --- | --- |\n| 精度 | 高 [1] |\n\n"
        "```python\nprint(1)\n```\n\n第一段结论 [1]。\n"
    )
    marked = mark_paragraphs(
        markdown,
        [
            ("第一段结论 [1]。", "第 3 行：引用只支持相关"),
            ("| 精度 | 高 [1] |", "所选摘录缺少对应句中的数值或专名"),
            ("```python\nprint(1)\n```", "代码与原文不一致"),
            ("第一段结论 [1]。", "第二次出现"),
            ("找不到的段落", "不会被硬塞"),
        ],
    )
    assert "第一段结论 [1]。〔待核：引用只支持相关〕" in marked
    assert "| 精度 | 高 [1] 〔待核：所选摘录缺少对应句中的数值或专名〕 |" in marked
    assert "```\n\n〔待核：代码与原文不一致〕" in marked
    assert marked.count("〔待核：") == 4 and "不会被硬塞" not in marked
    assert len(short_reason("第 3 行：" + "很长的原因" * 30)) <= 40


def test_flagged_repair_round_only_when_budget_remains(settings):
    from deep_research.orchestrator import create_initial_execution

    detail = RunDetail(id="r", query="Q", status="running",
                       orchestration=create_initial_execution("Q", "quick", settings))
    scratch = detail.orchestration.checkpoint.setdefault("scratch", {})
    settings.quality = {"max_revisions": 2}
    scratch["workbench"] = {"extras": {"revision": {"revisions_used": 1}}}
    assert _worth_revising_flagged(detail, settings)
    scratch["workbench"]["extras"]["revision"] = {"revisions_used": 2}
    assert not _worth_revising_flagged(detail, settings)
    scratch["workbench"]["extras"]["revision"] = {"revisions_used": 0, "stop_reason": "no_progress"}
    assert not _worth_revising_flagged(detail, settings)


async def test_uncited_paragraph_end_to_end_is_kept_marked_and_completes(settings):
    import tests.test_completion_auto_repair as fixture

    original = fixture.BAD
    fixture.BAD = "这一结论只适用于观察性研究。"
    try:
        repo, run_id, _, _ = await run_fixture(settings, max_revisions=0)
    finally:
        fixture.BAD = original
    detail = await repo.get_run(run_id)
    completion = detail.orchestration.checkpoint["scratch"]["_completion"]
    assert detail.status == "done", completion["issues"]
    assert any(item["gate"] == "uncited_paragraphs" for item in completion["flagged"])
    assert "这一结论只适用于观察性研究。" in detail.report.markdown
    source = next(f for f in build_bundle(detail).files if f.format == "md").data.decode()
    assert "这一结论只适用于观察性研究。〔待核：这一段没有任何引用角标〕" in source


def test_mindmap_nodes_and_slide_notes_carry_markers():
    from deep_research.workbench.flag_markers import (
        mark_mindmap,
        mark_slides,
        node_targets,
        slide_targets,
    )

    nodes = node_targets([
        {"code": "node_unsupported", "message": "节点 0.0：来源只提到并列", "unit": "relation:0.0"},
        {"code": "invalid_citation", "message": "越界", "unit": "0"},
    ])
    assert nodes == {"0.0": "节点 0.0：来源只提到并列"}
    value = mark_mindmap(
        {"root": "主题", "branches": [{"label": "方法", "children": [{"label": "编码"}]}]}, nodes
    )
    assert value["branches"][0]["label"] == "方法"
    assert value["branches"][0]["children"][0]["label"] == "编码〔待核：来源只提到并列〕"

    deck = {"slides": [{"title": "背景", "bullets": ["相关不等于因果 [1]"], "notes": "讲稿"},
                       {"title": "方法", "bullets": ["编码孔径设计 [1]"]}]}
    flagged = slide_targets(deck, [("- 编码孔径设计 [1]", "所选摘录缺少对应句中的数值或专名")])
    assert flagged == {"1": "所选摘录缺少对应句中的数值或专名"}
    marked = mark_slides(deck, flagged)
    assert marked["slides"][1]["notes"] == "〔待核：所选摘录缺少对应句中的数值或专名〕"
    assert marked["slides"][0]["notes"] == "讲稿"
    assert marked["slides"][1]["bullets"] == ["编码孔径设计 [1]"]


def test_narrative_reports_done_with_flagged_count():
    from deep_research.observability import Event
    from deep_research.workbench.narrative import build_narrative

    events = [
        Event(seq=0, stage="RESEARCHER", type="finding", data={"verified_count": 2}),
        Event(
            seq=1,
            stage="ORCHESTRATOR",
            type="done",
            message="完成（附 2 项待核）",
            data={"completion": {"status": "done", "flagged": [{}, {}]}},
        ),
    ]
    narrative = build_narrative(events, run_status="done")
    assert narrative["headline"].startswith("已完成") and "（附 2 项待核）" in narrative["headline"]
    finish = next(section for section in narrative["sections"] if section["key"] == "finish")
    assert finish["status"] == "done" and "附 2 项待核" in finish["lines"][-1]
