"""Preserve scientific gates while giving local repair actionable diagnostics."""

import json

import pytest

from deep_research.models import ResearchResult
from deep_research.workbench.prose_edit import ProseEdit, ProseEdits, repair_paragraphs
from deep_research.workbench.prose_review import ProseReviewer
from deep_research.workbench.support import (
    SupportDecision,
    SupportDecisions,
    SupportReviewer,
    SupportUnit,
)
from deep_research.workbench.support_alignment import alignment_diagnostics
from eval.scientific_support_cases import CASES, evaluate, evidence_for
from tests.fakes import verified_finding
from tests.test_support_alignment import SelectedJudge, item

# Exact, short source excerpts from the read-only phase-one acceptance record.
# Original discarded evidence_ids were not persisted. The three-record selection
# below is a transparent reproduction candidate, not a reconstructed model trace.
REAL_QUOTES = (
    "First, a cross\nspectral disparity estimation network is introduced, which is\n"
    "trained on a popular stereo database using pseudo spectral data\naugmentation.",
    "We introduce a cross spectral disparity estimation network\nwhich is trained using "
    "a standard RGB stereo database by\naugmenting the RGB in a novel way to generate "
    "pseudo\nspectral data.",
    "Pseudo spectral data augmentation plays a crucial role in\ntraining the cross spectral "
    "disparity estimation as well as\nthe cross spectral reconstruction.",
)
REAL_DRAFT = (
    "1. **伪光谱数据增强驱动的视差估计**：提出伪光谱数据增强方法以扩充标准 RGB 立体数据集，"
    "缓解了多光谱训练数据有限的问题，有效支撑了跨光谱视差估计网络的训练 [1][3][5]。"
)
ACCURATE_REVISION = (
    "1. **伪光谱数据增强驱动的视差估计**：跨光谱视差估计网络使用伪光谱数据增强训练 [1]。"
    "标准 RGB 立体数据被增强以生成伪光谱训练数据 [3]。"
    "伪光谱数据增强对跨光谱视差估计和重建训练起重要作用 [5]。"
)


def test_numeric_name_and_citation_alignment_benchmark():
    assert evaluate()["mechanical_passed"]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
async def test_semantic_decision_is_still_required_for_conditions_and_causality(case):
    evidence = evidence_for(case)

    class LabelledJudge:
        async def parse(self, system, user, schema, **kwargs):
            assert "因果" in system and "条件" in system
            units = json.loads(user)["units"]
            return SupportDecisions(
                decisions=[
                    SupportDecision(
                        unit_id=unit["id"],
                        verdict=case.semantic,
                        evidence_ids=[record["id"] for record in evidence],
                        reason="offline labelled fixture",
                    )
                    for unit in units
                ]
            )

    reviewer = SupportReviewer(LabelledJudge(), evidence, 50000)
    decision = (
        await reviewer.review(
            [
                SupportUnit(
                    "unit",
                    case.text,
                    citations=sorted({x[0] for x in case.quotes}),
                )
            ]
        )
    )[0]
    assert decision.verdict == case.semantic


async def test_real_candidate_rejection_preserves_specific_selected_excerpts():
    evidence = [
        item(f"e{i}", quote, citation)
        for i, (quote, citation) in enumerate(zip(REAL_QUOTES, (1, 3, 5), strict=True))
    ]
    ids = [record["id"] for record in evidence]
    reviewer = SupportReviewer(SelectedJudge(ids), evidence, 50000)
    decision = (await reviewer.review([SupportUnit("real", REAL_DRAFT, citations=[1, 3, 5])]))[0]
    assert decision.verdict == "unsupported" and not decision.evidence_ids
    diagnostic = decision.alignment_review
    assert diagnostic["selected_evidence_ids"] == ids
    assert diagnostic["unanchored_evidence_ids"] == ["e0", "e2"]
    assert diagnostic["code"] == "selected_excerpt_missing_anchor"
    assert any("rgb" in sentence["required_names"] for sentence in diagnostic["sentences"])
    assert alignment_diagnostics(ACCURATE_REVISION, [1, 3, 5], ids, evidence)["status"] == "pass"


async def test_repair_receives_binding_diagnostics_then_rechecks_accurate_revision():
    results = [
        ResearchResult(
            sub_question="q",
            findings=[
                verified_finding("摘要创新", "https://example.org/a", REAL_QUOTES[0]),
                verified_finding("RGB增强", "https://example.org/b", REAL_QUOTES[1]),
                verified_finding("训练用途", "https://example.org/c", REAL_QUOTES[2]),
            ],
        )
    ]

    class RepairJudge:
        seen = None

        async def parse(self, system, user, schema, **kwargs):
            if schema is ProseEdits:
                payload = json.loads(user.split("【只修订以下段落】\n", 1)[1])
                self.seen = payload["paragraphs"][0]["evidence_diagnostic"]
                assert self.seen["unanchored_evidence_ids"]
                assert "不得自行编造或扩展摘录" in system
                return ProseEdits(
                    edits=[
                        ProseEdit(
                            unit_id=payload["paragraphs"][0]["unit_id"],
                            replacement=ACCURATE_REVISION,
                        )
                    ]
                )
            payload = json.loads(user)
            return SupportDecisions(
                decisions=[
                    SupportDecision(
                        unit_id=unit["id"],
                        verdict="supported",
                        evidence_ids=[record["id"] for record in payload["evidence"]],
                        reason="fixture",
                    )
                    for unit in payload["units"]
                ]
            )

    judge = RepairJudge()
    reviewer = ProseReviewer.research(
        judge,
        results,
        {
            "https://example.org/a": 1,
            "https://example.org/b": 3,
            "https://example.org/c": 5,
        },
        50000,
    )
    initial = await reviewer.review(REAL_DRAFT)
    assert initial["status"] == "fail"
    revised = await repair_paragraphs(judge, reviewer, REAL_DRAFT, initial)
    assert revised == ACCURATE_REVISION
    assert judge.seen["selected_evidence_ids"] == [record["id"] for record in reviewer.evidence]
    final = await reviewer.review(revised)
    assert final["status"] == "pass"


def test_diagnostic_field_is_not_a_model_generated_gate_bypass():
    assert "alignment_review" not in SupportDecision.model_json_schema()["properties"]
