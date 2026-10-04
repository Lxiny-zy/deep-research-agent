"""Delivery identity follows frozen content, not execution/completion bookkeeping."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from deep_research.models import Report
from deep_research.orchestrator import create_initial_execution
from deep_research.persistence.repository import RunDetail
from deep_research.workbench import publish
from deep_research.workbench.contract import CONTRACT_SCRATCH_KEY, build_contract
from deep_research.workbench.delivery_store import build_or_load, load_version
from deep_research.workbench.templates import SectionSpec, get_template


def task(settings, *, title="Frozen topic", formats=("md", "html")):
    template = get_template("autoResearch")
    contract = build_contract(template, "Q", quality={"research_min_citations": 0})
    contract.required_sections = [title]
    contract.deliverables = list(formats)
    execution = create_initial_execution("Q", template.workflow, settings)
    execution.checkpoint["scratch"][CONTRACT_SCRATCH_KEY] = contract.model_dump(mode="json")
    return RunDetail(
        id="snapshot-run",
        query="Q",
        status="done",
        created_at=datetime(2026, 10, 4, tzinfo=UTC),
        report=Report(query="Q", markdown=f"## {title}\n\nA frozen report.", citations=[]),
        orchestration=execution,
    )


def test_bundle_uses_frozen_sections_and_formats_after_template_changes(settings, monkeypatch):
    detail = task(settings)
    current = get_template("autoResearch")
    changed = replace(
        current,
        sections=(SectionSpec("new", "New template requirement", ""),),
        deliverables=("pdf", "docx"),
    )
    monkeypatch.setattr(publish, "get_template", lambda _: changed)
    bundle = publish.build_bundle(detail)
    assert {file.format for file in bundle.files} == {"md", "html"}
    assert bundle.render_context["wants"] == ["md", "html"]
    structure = next(gate for gate in bundle.gates if gate.name == "structure")
    assert structure.status == "pass"
    assert structure.metrics["required"] == 1


def test_frozen_heading_keeps_existing_equivalent_aliases(settings):
    current = get_template("autoResearch")
    section = next(section for section in current.sections if section.aliases)
    detail = task(settings, title=section.title, formats=("md",))
    detail.report.markdown = f"## {section.aliases[0]}\n\nA frozen report."
    resolved = publish.resolve_template(detail)
    assert [section.title for section in resolved.sections] == [section.title]
    gate = next(gate for gate in publish.build_bundle(detail).gates if gate.name == "structure")
    assert gate.status == "pass"


def test_legacy_contract_without_frozen_fields_keeps_template_defaults(settings):
    detail = task(settings)
    raw = detail.orchestration.checkpoint["scratch"][CONTRACT_SCRATCH_KEY]
    raw.pop("required_sections")
    raw.pop("deliverables")
    template = get_template("autoResearch")
    resolved = publish.resolve_template(detail)
    assert resolved.sections == template.sections
    assert resolved.deliverables == template.deliverables


def test_recovery_and_completion_audit_reuses_persisted_delivery_bytes(settings, tmp_path):
    detail = task(settings)
    first = build_or_load(detail, str(tmp_path), None, publish.build_bundle)
    scratch = detail.orchestration.checkpoint["scratch"]
    scratch.update(
        {
            "_completion": {"state": "needs_review", "delivery_version": first.content_version},
            "_runtime_metrics": {"elapsed": 1234, "total_tokens": 9999},
            "_deadline_at": 999999,
            "_task_deadline_at": 9999999,
            "_attempt_elapsed_origin": 1234,
            "_recovery": {"count": 3, "not_before": 8888, "reason": "transport_error"},
            "_orchestration_run": {"attempt": 4, "status": "running"},
            "_committed_research_progress": {"a" * 64: "b" * 64},
        }
    )
    detail.status = "needs_review"
    detail.orchestration.attempt += 1

    def forbidden(_):
        raise AssertionError("execution bookkeeping must not trigger document rendering")

    restored = build_or_load(deepcopy(detail), str(tmp_path), None, forbidden)
    assert restored.content_version == first.content_version
    assert [(file.name, file.sha256) for file in restored.files] == [
        (file.name, file.sha256) for file in first.files
    ]
    assert publish.delivery_fingerprint(detail) == first.input_version
    assert load_version(detail, str(tmp_path), first.content_version).registry() == first.registry()


@pytest.mark.parametrize(
    "key",
    [
        CONTRACT_SCRATCH_KEY,
        "_run_settings",
        "run_manifest",
        "workbench",
        "analysis",
        "paper_sources",
        "paper_abstracts",
        "intake_sources",
        "attachments",
        "prose_review",
        "_report_validation",
        "review_coverage",
        "_future_evidence_input",
    ],
)
def test_evidence_inputs_and_semantic_settings_still_invalidate_delivery(settings, key):
    detail = task(settings)
    before = publish.delivery_fingerprint(detail)
    detail.orchestration.checkpoint["scratch"][key] = {"changed": True}
    assert publish.delivery_fingerprint(detail) != before


def test_real_body_change_creates_new_version_without_overwriting_original(settings, tmp_path):
    detail = task(settings)
    first = build_or_load(detail, str(tmp_path), None, publish.build_bundle)
    detail.report.markdown += "\n\nAdditional reviewed content."
    second = build_or_load(detail, str(tmp_path), None, publish.build_bundle)
    assert first.content_version != second.content_version
    original = load_version(detail, str(tmp_path), first.content_version)
    assert original.files[0].data == first.files[0].data
    assert b"Additional reviewed content" not in original.files[0].data
    assert b"Additional reviewed content" in second.files[0].data


def test_runtime_named_values_inside_real_material_are_not_filtered(settings):
    detail = task(settings)
    scratch = detail.orchestration.checkpoint["scratch"]
    scratch["attachments"] = [{"_runtime_metrics": "scientific input"}]
    original = publish.delivery_fingerprint(detail)
    scratch["attachments"][0]["_runtime_metrics"] = "changed scientific input"
    assert publish.delivery_fingerprint(detail) != original
