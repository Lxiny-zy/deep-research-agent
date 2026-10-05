"""Structured peer-review items must cover the prose and retain grounded judgements."""

from __future__ import annotations

import json
from copy import deepcopy

import pytest

from deep_research.workbench.prose_review import prose_units
from deep_research.workbench.support import SupportDecision

BODY = (
    "## 论文摘要\n本文讨论模型架构。\n\n"
    "## 优点\n- 该架构便于定位增量 [1]。\n\n"
    "## 不足\n- 该架构的增量边界不清 [1]。\n\n"
    "## 详细意见\n建议进一步说明比较范围。\n\n"
    "## 总体推荐\n需要修订。\n\n## 审稿结论\n评分：7/10"
)
EVIDENCE = [
    {
        "id": "e-one",
        "citation": 1,
        "source": "https://example.org/paper",
        "statement": "架构与已有模块的关系",
        "quote": "A module is reused and another is changed.",
    }
]


def material(body=BODY):
    units, locations = prose_units(body, [1])
    decisions = [
        SupportDecision(
            unit_id=u.id,
            verdict="supported" if u.citations else "non_factual",
            evidence_ids=["e-one"] if u.citations else [],
            reason="fixture",
        )
        for u in units
    ]
    return units, locations, decisions


class Judge:
    def __init__(self, *, contradiction=False, critical=False, mode="normal"):
        self.contradiction, self.critical, self.mode = contradiction, critical, mode
        self.classifications = self.comparisons = 0

    async def parse(self, system, user, schema, **kwargs):
        data = json.loads(user)
        if schema.__name__ == "PeerReviewRetentions":
            return schema.model_validate(
                {
                    "items": [
                        {
                            "history_id": h["id"],
                            "unit_id": data["current"][0]["id"],
                            "reason": "同一关键缺陷仅调整措辞",
                        }
                        for h in data["history"]
                    ]
                }
            )
        if schema.__name__ == "PeerReviewClassifications":
            self.classifications += 1
            items = []
            for unit in data["items"]:
                role = unit["role"]
                kind = {
                    "strength": "strength",
                    "weakness": "weakness",
                    "recommendation": "recommendation",
                }.get(role, "suggestion")
                items.append(
                    {
                        "unit_id": unit["id"],
                        "kind": kind,
                        "severity": ("critical" if self.critical else "general")
                        if kind == "weakness"
                        else None,
                        "reason": "影响核心结论" if self.critical else "范围说明需要完善",
                    }
                )
            if self.mode == "omit_item":
                items.pop()
            if self.mode == "error":
                raise RuntimeError("review unavailable")
            return schema.model_validate({"items": items})
        self.comparisons += 1
        pairs = [
            {
                "pair_id": pair["id"],
                "verdict": "contradictory" if self.contradiction else "consistent",
                "reason": "同一范围的增量清晰度判断相互矛盾"
                if self.contradiction
                else "不同维度可并存",
            }
            for pair in data["pairs"]
        ]
        if self.mode == "omit_pair":
            pairs.pop()
        return schema.model_validate({"pairs": pairs})


async def evaluate(judge, body=BODY):
    from deep_research.workbench.peer_review_items import PeerReviewChecker

    checker = PeerReviewChecker(judge, EVIDENCE, 50000, query="评审方法的可靠性")
    args = material(body)
    return checker, args, await checker.review(body, *args)


async def test_sc39_positive_and_negative_increment_claims_cannot_both_pass():
    _, _, record = await evaluate(Judge(contradiction=True))
    assert record["status"] == "fail"
    assert any("矛盾" in issue for issue in record["issues"])
    assert record["local_problems"]


async def test_items_include_type_severity_and_verified_evidence_ids():
    checker, args, record = await evaluate(Judge())
    assert record["status"] == "pass"
    weakness = next(i for i in record["items"] if i["type"] == "weakness")
    assert weakness["severity"] == "general" and weakness["evidence_ids"] == ["e-one"]
    assert weakness["text"] in BODY
    assert not checker.check(BODY, *args, record)


@pytest.mark.parametrize("evidence_ids", [[], ["invented-id"]])
async def test_factual_criticism_cannot_use_missing_or_unknown_evidence_ids(evidence_ids):
    from deep_research.workbench.peer_review_items import PeerReviewChecker

    checker = PeerReviewChecker(Judge(), EVIDENCE, 50000, query="评审")
    units, locations, decisions = material()
    weakness = next(u for u in units if "边界不清" in u.text)
    next(d for d in decisions if d.unit_id == weakness.id).evidence_ids = evidence_ids
    record = await checker.review(BODY, units, locations, decisions)
    assert record["status"] == "fail"
    assert any("依据" in issue for issue in record["issues"])


async def test_high_score_with_critical_defect_is_flagged_without_changing_the_score():
    checker, args, record = await evaluate(Judge(critical=True))
    assert record["score"] == 7 and record["critical_count"] == 1
    assert record["status"] == "fail" and any("评分" in issue for issue in record["issues"])
    assert checker.check(BODY, *args, {**record, "status": "pass"})


async def test_score_revision_does_not_reroll_severity_or_item_consistency():
    judge = Judge(critical=True)
    checker, _, failed = await evaluate(judge)
    assert failed["status"] == "fail"
    body = BODY.replace("评分：7/10", "评分：5/10")
    record = await checker.review(body, *material(body))
    assert record["status"] == "pass" and record["critical_count"] == 1
    assert judge.classifications == judge.comparisons == 1


@pytest.mark.parametrize("mode", ["omit_item", "omit_pair", "error"])
async def test_incomplete_or_failed_assessment_cannot_pass(mode):
    _, _, record = await evaluate(Judge(mode=mode))
    assert record["status"] == "fail" and record["issues"]


async def test_saved_review_is_bound_to_text_score_evidence_and_complete_items():
    checker, args, record = await evaluate(Judge())
    changed = BODY.replace("7/10", "8/10")
    assert checker.check(changed, *material(changed), record)
    incomplete = deepcopy(record)
    incomplete["items"].pop()
    assert checker.check(BODY, *args, incomplete)
    corrupted = {**record, "critical_count": 7}
    assert checker.check(BODY, *args, corrupted)


@pytest.mark.parametrize("score", [True, 7])
def test_numeric_score_alone_is_not_a_complete_review_gate(score):
    from deep_research.workbench.gates import review_gate

    assert review_gate({"score": score}).status == "fail"


def test_a_rating_line_cannot_discard_an_embedded_factual_criticism():
    from deep_research.workbench.writers import peer_factual_body

    body = peer_factual_body("## 不足\n评分：7/10，核心推导错误 [1]。")
    assert "核心推导错误 [1]" in body
    assert "7/10" not in body


async def test_mixed_rating_and_criticism_is_not_exempt_from_item_checks():
    body = BODY.replace("- 该架构的增量边界不清 [1]。", "评分：7/10，核心推导错误 [1]。")
    _, _, record = await evaluate(Judge(critical=True), body)
    assert any("核心推导错误" in item["text"] for item in record["items"])
    assert record["status"] == "fail"


@pytest.mark.parametrize("restore", [False, True])
@pytest.mark.parametrize("revision", ["delete", "downgrade"])
async def test_confirmed_critical_defect_cannot_be_removed_or_downgraded(restore, revision):
    from deep_research.workbench.peer_review_items import PeerReviewChecker

    judge = Judge(critical=True)
    checker, args, record = await evaluate(judge)
    if restore:
        checker = PeerReviewChecker(judge, EVIDENCE, 50000, query="评审方法的可靠性")
        checker.prime(BODY, *args, record)
    judge.critical = False
    replacement = "" if revision == "delete" else "- 增量范围仍需说明 [1]。"
    body = BODY.replace("- 该架构的增量边界不清 [1]。", replacement)
    revised = await checker.review(body, *material(body))
    assert revised["status"] == "fail"
    assert any("关键缺陷" in issue for issue in revised["issues"])
    assert revised["requires_full_revision"] is True
    assert checker.bound_check(body, *material(body), revised)[0]


@pytest.mark.parametrize("change", ["evidence", "query"])
async def test_new_evidence_or_review_purpose_invalidates_critical_history(change):
    judge = Judge(critical=True)
    checker, _, _ = await evaluate(judge)
    judge.critical = False
    if change == "evidence":
        checker.evidence = [{**EVIDENCE[0], "quote": "New proof resolves the core defect."}]
    else:
        checker.query = "仅评审行文表达"
    revised = await checker.review(BODY, *material())
    assert revised["status"] == "pass"
    assert revised["critical_count"] == 0
    assert judge.classifications == 2


async def test_same_evidence_id_with_new_content_does_not_reuse_judgements():
    judge = Judge()
    checker, _, _ = await evaluate(judge)
    checker.evidence = [{**EVIDENCE[0], "quote": "Changed evidence under the same ID."}]
    await checker.review(BODY, *material())
    assert judge.classifications == judge.comparisons == 2


async def test_fulltext_support_does_not_legitimize_unknown_explicit_evidence():
    from deep_research.workbench.peer_review_items import PeerReviewChecker

    checker = PeerReviewChecker(
        Judge(),
        EVIDENCE,
        50000,
        query="评审",
        fulltext_support=lambda *_: True,
    )
    units, locations, decisions = material()
    for d in decisions:
        if d.evidence_ids:
            d.evidence_ids = ["invented-id"]
    record = await checker.review(BODY, units, locations, decisions)
    assert record["status"] == "fail"
    assert any("依据" in issue for issue in record["issues"])


@pytest.mark.parametrize(
    "field,value",
    [
        ("local_problems", []),
        ("can_revise", False),
        ("requires_full_revision", True),
    ],
)
async def test_revision_control_fields_are_revalidated(field, value):
    checker, args, record = await evaluate(Judge(critical=True))
    assert not checker.bound_check(BODY, *args, {**record, field: value})[0]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("评分：7/10，**核心推导错误 [1]。**", "**核心推导错误 [1]。**"),
        ("**评分：7/10**，核心推导错误 [1]。", "核心推导错误 [1]。"),
        ("评分：**7/10**；*核心推导错误 [1]。*", "*核心推导错误 [1]。*"),
    ],
)
def test_removing_score_preserves_factual_punctuation_and_markdown(text, expected):
    from deep_research.workbench.writers import strip_review_scores

    assert strip_review_scores(text) == expected


@pytest.mark.parametrize("text", ["评分：7.5/10", "评分：7/100", "评分：7/10\n评分：8/10"])
def test_invalid_or_conflicting_scores_are_not_rounded_or_silently_selected(text):
    from deep_research.workbench.writers import extract_review_score

    assert extract_review_score(text) is None


@pytest.mark.parametrize("items", ["nonempty", {"id": "one"}, [True], [{}]])
def test_review_gate_rejects_malformed_items(items):
    from deep_research.workbench.gates import review_gate

    extras = {
        "score": 7,
        "prose_review": {
            "peer_review": {
                "score": 7,
                "status": "pass",
                "items": items,
            }
        },
    }
    assert review_gate(extras).status == "fail"


async def test_rephrased_critical_defect_can_pass_after_lowering_the_score():
    checker, _, _ = await evaluate(Judge(critical=True))
    body = BODY.replace("该架构的增量边界不清", "该架构无法界定核心增量")
    body = body.replace("评分：7/10", "评分：5/10")
    revised = await checker.review(body, *material(body))
    assert revised["status"] == "pass" and revised["critical_count"] == 1
    assert checker.bound_check(body, *material(body), revised) == (True, [])


async def test_failure_recovery_preserves_previously_confirmed_critical_defects():
    from deep_research.workbench.peer_review_items import PeerReviewChecker

    judge = Judge(critical=True)
    checker, _, _ = await evaluate(judge)
    body = BODY.replace("该架构的增量边界不清", "该架构的核心推导错误")
    judge.mode = "error"
    failed = await checker.review(body, *material(body))
    restored = PeerReviewChecker(judge, EVIDENCE, 50000, query="评审方法的可靠性")
    restored.prime(body, *material(body), failed)
    judge.mode, judge.critical = "normal", False
    deleted = body.replace("- 该架构的核心推导错误 [1]。", "")
    result = await restored.review(deleted, *material(deleted))
    assert result["status"] == "fail"
    assert any("关键缺陷" in issue for issue in result["issues"])


async def test_identical_critical_defect_in_two_sections_is_one_issue():
    body = BODY.replace("建议进一步说明比较范围。", "- 该架构的增量边界不清 [1]。")

    class RepeatedJudge(Judge):
        async def parse(self, system, user, schema, **kwargs):
            value = await super().parse(system, user, schema, **kwargs)
            if schema.__name__ == "PeerReviewClassifications":
                by_id = {u["id"]: u for u in json.loads(user)["items"]}
                for item in value.items:
                    if "边界不清" in by_id[item.unit_id]["text"]:
                        item.kind, item.severity = "weakness", "critical"
            return value

    _, _, record = await evaluate(RepeatedJudge(critical=True), body)
    assert "error" not in record
    assert record["critical_count"] == 1


async def test_real_paragraph_editor_accepts_a_structural_score_problem():
    from deep_research.workbench.peer_review_items import PeerReviewChecker
    from deep_research.workbench.prose_edit import repair_paragraphs
    from deep_research.workbench.prose_review import ProseReviewer
    from tests.fakes import FakeLLM

    class Model(FakeLLM):
        async def parse(self, system, user, schema, **kwargs):
            if schema.__name__.startswith("PeerReview"):
                value = await Judge(critical=True).parse(system, user, schema, **kwargs)
                if schema.__name__ == "PeerReviewClassifications":
                    for item in value.items:
                        item.kind, item.severity = "comment", "critical"
                return value
            if schema.__name__ == "ProseEdits":
                data = json.loads(str(user).split("【只修订以下段落】\n", 1)[1])
                return schema.model_validate(
                    {
                        "edits": [
                            {
                                "unit_id": p["unit_id"],
                                "replacement": p["text"].replace("7/10", "5/10"),
                            }
                            for p in data["paragraphs"]
                        ]
                    }
                )
            return await super().parse(system, user, schema, **kwargs)

    model = Model()
    body = "## 详细意见\n\n发现X [1]。\n\n## 审稿结论\n\n评分：7/10"
    reviewer = ProseReviewer(model, EVIDENCE, 50000, source_version="test", query="q")
    reviewer.peer = PeerReviewChecker(model, EVIDENCE, 50000, query="q")
    record = await reviewer.review(body)
    peer = record["peer_review"]
    assert peer["critical_count"] == 1 and peer["local_problems"], record
    repaired = await repair_paragraphs(
        model,
        reviewer,
        body,
        record,
        local_problems=[tuple(p) for p in peer["local_problems"]],
    )
    assert repaired == body.replace("7/10", "5/10")
    assert (await reviewer.review(repaired))["peer_review"]["status"] == "pass"
