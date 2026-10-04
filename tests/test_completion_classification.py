"""Advisory delivery issues never waive facts, scope, or file requirements."""

import pytest

from deep_research.models import Report
from deep_research.persistence.repository import RunDetail
from deep_research.workbench.completion import assess_completion, validate_bundle_files
from deep_research.workbench.gates import GateResult, length_gate, scholarly_gate
from deep_research.workbench.quality import QualityPolicy
from deep_research.workbench.templates import get_template
from tests.test_task_completion import bundle, execution


def subject(settings, *gates):
    detail = RunDetail(
        id="classified",
        query="Q",
        status="running",
        orchestration=execution(settings),
        report=Report(query="Q", markdown="Verified body"),
    )
    value = bundle(detail)
    names = {gate.name for gate in gates}
    value.gates = [gate for gate in value.gates if gate.name not in names] + list(gates)
    validate_bundle_files(value)
    return detail, value


@pytest.mark.parametrize(
    "code",
    [
        "paired-frame",
        "colloquial",
        "production-narration",
        "overlong-sentence",
        "cjk-ascii-punctuation",
        "citation-cluster",
        "abstract-citation",
    ],
)
def test_style_warnings_and_matching_revision_notes_do_not_block_completion(settings, code):
    message = "一项已定位的文体建议"
    detail, value = subject(
        settings,
        GateResult(
            "scholarly", "warn", [message], {"findings": [{"code": code, "message": message}]}
        ),
        GateResult("revision", "warn", [message]),
    )
    value.status = "warn"
    value.files[0].status = "warn"
    value.files[0].issues = [message]
    record = assess_completion(detail, value)
    assert record["status"] == "done" and record["issues"] == []
    assert message in record["advisories"]
    assert record["policy_version"] >= 2


def test_minor_shortfall_is_advisory_but_substantially_incomplete_body_blocks(settings):
    template = get_template("autoResearch")
    near = length_gate("字" * int(template.min_length * 0.9), template)
    detail, value = subject(settings, near)
    value.status = "warn"
    record = assess_completion(detail, value)
    assert record["status"] == "done" and record["advisories"]
    severe = length_gate("字" * 20, template)
    detail, value = subject(settings, severe)
    assert assess_completion(detail, value)["status"] == "needs_review"


@pytest.mark.parametrize(
    "name",
    [
        "prose_evidence",
        "node_evidence",
        "user_requirements",
        "table_evidence",
        "table_scope",
        "structure",
        "citation",
        "source_processing",
        "unrecognized_check",
    ],
)
def test_factual_scope_and_unknown_warnings_still_block(settings, name):
    detail, value = subject(settings, GateResult(name, "warn", ["尚未验证必要内容"]))
    value.status = "pass"
    assert assess_completion(detail, value)["status"] == "needs_review"


def test_mixed_scholarly_gate_never_hides_a_blocker_beyond_display_limit(settings):
    findings = [{"code": "colloquial", "message": f"风格建议 {i}"} for i in range(25)]
    findings.append({"code": "recency-gap", "message": "用户点名的年份范围未覆盖"})
    detail, value = subject(
        settings,
        GateResult(
            "scholarly", "warn", [row["message"] for row in findings[:20]], {"findings": findings}
        ),
    )
    record = assess_completion(detail, value)
    assert record["status"] == "needs_review"
    assert "用户点名的年份范围未覆盖" in record["issues"]
    assert "风格建议 0" in record["advisories"]


def test_real_scholarly_gate_emits_classifiable_findings():
    gate = scholarly_gate(
        "说白了，这项方法值得后续研究。",
        template=get_template("autoResearch"),
        query="q",
        citations=[],
        min_citations=0,
        policy=QualityPolicy(require_limitations=False),
    )
    assert any(row["code"] == "colloquial" for row in gate.metrics["findings"])


def test_missing_or_corrupt_files_still_block_an_advisory_only_draft(settings):
    detail, value = subject(settings, GateResult("scholarly", "pass", ["建议进一步扩展讨论"]))
    value.files = []
    assert assess_completion(detail, value)["status"] == "needs_review"


def test_legacy_or_unclassified_revision_warning_is_not_assumed_stylistic(settings):
    detail, value = subject(settings, GateResult("revision", "warn", ["修订未完成"]))
    assert assess_completion(detail, value)["status"] == "needs_review"


@pytest.mark.parametrize(
    "revision_metrics",
    [{"remaining_issues": "broken"}, {"remaining": 2}, {"advisory_issues": "broken"}],
)
def test_truncated_or_malformed_revision_record_cannot_hide_behind_a_style_message(
    settings, revision_metrics
):
    message = "文体建议"
    detail, value = subject(
        settings,
        GateResult(
            "scholarly",
            "warn",
            [message],
            {"findings": [{"code": "colloquial", "message": message}]},
        ),
        GateResult("revision", "warn", [message], revision_metrics),
    )
    assert assess_completion(detail, value)["status"] == "needs_review"


def test_prelabelled_advisory_does_not_override_a_fact_warning(settings):
    detail, value = subject(
        settings,
        GateResult(
            "prose_evidence",
            "warn",
            ["缺少事实依据"],
            blocking_issues=[],
            advisories=["缺少事实依据"],
        ),
    )
    assert assess_completion(detail, value)["status"] == "needs_review"


def test_current_contract_requires_requested_content_and_table_proof(settings):
    from deep_research.workbench.publish import delivery_fingerprint

    detail, value = subject(settings)
    detail.orchestration.checkpoint["scratch"]["task_contract"]["requirements_version"] = 1
    value.input_version = delivery_fingerprint(detail)
    record = assess_completion(detail, value)
    assert record["status"] == "needs_review"
    assert any(
        "user_requirements" in issue and "table_evidence" in issue for issue in record["issues"]
    )


def test_renderer_does_not_mark_files_failed_for_style_advice_in_strict_mode(settings):
    from deep_research.workbench.delivery_render import render_bundle

    message = "文体建议"
    detail, value = subject(
        settings,
        GateResult(
            "scholarly",
            "warn",
            [message],
            {
                "findings": [{"code": "colloquial", "message": message}],
            },
        ),
    )
    rendered = render_bundle(
        {
            "title": "Q",
            "markdown": "Verified body",
            "stem": "q",
            "extras": {},
            "citations": [],
            "base_gates": [gate.to_dict() for gate in value.gates],
            "wants": ["md"],
            "blocked": False,
            "template": "autoResearch",
            "fail_on_quality": True,
            "generated_at": "2026-10-04T00:00:00Z",
        },
        [],
    )
    assert rendered.status == "pass"
    assert all(file.status == "pass" for file in rendered.files)
    rendered.input_version = value.input_version
    record = assess_completion(detail, rendered)
    assert record["status"] == "done" and message in record["advisories"]
