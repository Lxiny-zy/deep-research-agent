import json
from copy import deepcopy
from dataclasses import asdict

import pytest

from deep_research.bibliography import build_bibliography
from deep_research.models import ResearchResult
from deep_research.workbench.citation_binding import bind_review
from deep_research.workbench.prose_review import ProseReviewer
from deep_research.workbench.support import (
    SupportDecision,
    SupportDecisions,
    SupportReviewer,
    SupportUnit,
    digest,
)
from deep_research.workbench.support_alignment import numeric_fact
from tests.fakes import verified_finding


class SelectedJudge:
    def __init__(self, ids):
        self.ids = ids

    async def parse(self, system, user, schema, **kwargs):
        data = json.loads(user)
        return SupportDecisions(decisions=[
            SupportDecision(
                unit_id=u["id"], verdict="supported", evidence_ids=self.ids, reason="fake"
            )
            for u in data["units"]
        ])


def item(key, quote, citation=1, statement=""):
    return dict(
        id=key, citation=citation, quote=quote, statement=statement, source=f"url-{citation}"
    )


@pytest.mark.parametrize("text, evidence, selected", [
    ("Alpha 得分为 95 [1]。", [item("a", "Alpha score 90"), item("b", "Alpha score 95")], ["a"]),
    ("Alpha 得分为 95 [1]。", [item("a", "Alpha score 90", statement="Alpha 得分为95")], ["a"]),
    (
        "Alpha 得分为 95 [1]。",
        [item("a", "Alpha score 95"), item("b", "Beta latency 30")], ["a", "b"],
    ),
    ("Alpha 使用稀疏编码 [1]。", [item("a", "Beta uses convolution")], ["a"]),
    ("Alpha 得分95 [1]。", [item("a", "Beta score 95")], ["a"]),
    ("Alpha 得分95。 [1]", [item("a", "Beta score 95")], ["a"]),
    (
        "Alpha scored 95 [1]. The result is discussed [1].",
        [item("a", "Beta score 95")], ["a"],
    ),
    (
        "Alpha uses spectral attention. [1][2]",
        [item("a", "Alpha uses spectral attention."), item("b", "Beta uses spatial filters.", 2)],
        ["a", "b"],
    ),
    (
        "Alpha 得分为95 [1]。Beta 得分为90 [2]。",
        [item("a", "Alpha score 90"), item("b", "Beta score 95", 2)], ["a", "b"],
    ),
    (
        "Alpha 得分为95 [1]，Beta 得分为90 [2]。",
        [item("a", "Alpha score 90"), item("b", "Beta score 95", 2)], ["a", "b"],
    ),
    (
        "Alpha scored 95 [1] while Beta scored 90 [2].",
        [item("a", "Alpha score 90"), item("b", "Beta score 95", 2)], ["a", "b"],
    ),
    (
        "Alpha 得分95 [1]而Beta 得分90 [2]。",
        [item("a", "Alpha score 90"), item("b", "Beta score 95", 2)], ["a", "b"],
    ),
])
async def test_optimistic_verdict_cannot_bind_unrelated_evidence(text, evidence, selected):
    reviewer = SupportReviewer(SelectedJudge(selected), evidence, 50000)
    decisions = await reviewer.review([
        SupportUnit("u", text, citations=[1, 2] if "[2]" in text else [1])
    ])
    assert decisions[0].verdict == "unsupported"
    assert not decisions[0].evidence_ids


@pytest.mark.parametrize("text, quote", [
    ("Alpha 得分95 [1]。", "Alpha score 95.0"),
    ("Alpha 增量为 95-90=5 [1]。", "Alpha scores 95 and 90"),
    ("Alpha 误差为1e-3 [1]。", "Alpha error 0.001"),
    ("如表2所示，Alpha 使用稀疏编码 [1]。", "Alpha uses sparse coding"),
    ("变量相关 [1]。", "The variables are correlated."),
    ("Performance improved [1].", "The results improve."),
    ("Accuracy improved [1].", "Classification performance improved."),
    ("Training remains stable [1].", "Optimization remains stable."),
    ("## 3. Alpha 的结果 [1]", "Alpha results"),
    ("Paragraph 20", "Some original text."),
    ("Alpha error is $10^{-3}$ [1].", "Alpha error is 0.001."),
    ("Alpha error is $1 \\times 10^{-3}$ [1].", "Alpha error is 1e-3."),
])
async def test_alignment_preserves_supported_values_arithmetic_and_translated_prose(text, quote):
    reviewer = SupportReviewer(SelectedJudge(["a"]), [item("a", quote)], 50000)
    decision = (await reviewer.review([SupportUnit("u", text, citations=[1])]))[0]
    assert decision.verdict == "supported"


async def test_multiple_sentences_can_each_use_their_own_selected_excerpt():
    reviewer = SupportReviewer(SelectedJudge(["a", "b"]), [
        item("a", "Alpha uses spectral attention."),
        item("b", "Beta uses spatial filters.", 2),
    ], 50000)
    unit = SupportUnit(
        "slide", "Alpha uses spectral attention. Beta uses spatial filters.", citations=[1, 2]
    )
    decision = (await reviewer.review([unit]))[0]
    assert decision.verdict == "supported"
    assert decision.evidence_ids == ["a", "b"]


async def test_forged_stored_pass_cannot_seed_cache_or_bind_export():
    url = "https://example.org/paper"
    results = [ResearchResult(sub_question="q", findings=[
        verified_finding("Alpha 得分90", url, "Alpha score 90"),
        verified_finding("Alpha 得分95", url, "Alpha score 95"),
    ])]
    checker = ProseReviewer.research(None, results, {url: 1}, 50000)
    good_id, bad_id = checker.evidence[1]["id"], checker.evidence[0]["id"]
    checker.reviewer.llm = SelectedJudge([good_id])
    body = "Alpha 得分95 [1]。"
    record = await checker.review(body)
    assert record["status"] == "pass"
    forged = deepcopy(record)
    for d in forged["decisions"]:
        d["evidence_ids"] = [bad_id]
    assert checker.check(body, forged)[1]
    checker.reviewer.cache.clear()
    checker.prime(body, forged)
    assert not checker.reviewer.cache
    catalog = build_bibliography(body, [url], results[0].findings)
    assert not bind_review(catalog, checker, body, forged)
    assert not catalog.occurrences


@pytest.mark.parametrize("body", [
    "Alpha 得分95 [1]。",
    "Alpha 得分95 [1]，建议复测。",
    "建议复测，Alpha 得分95 [1]。",
    "Alpha 得分95，建议复测 [1]。",
    "We recommend replication, Alpha scored 95 [1].",
    "Alpha scored 95. [1]",
])
async def test_numeric_fact_cannot_be_relabelled_non_factual_in_old_record(body):
    url = "https://example.org/paper"
    results = [ResearchResult(sub_question="q", findings=[
        verified_finding("Alpha 得分95", url, "Alpha score 95"),
    ])]
    checker = ProseReviewer.research(None, results, {url: 1}, 50000)
    checker.reviewer.llm = SelectedJudge([checker.evidence[0]["id"]])
    record = await checker.review(body)
    assert record["status"] == "pass"
    for decision in record["decisions"]:
        decision.update(verdict="non_factual", evidence_ids=[])
    assert checker.check(body, record)[1]
    checker.reviewer.cache.clear()
    checker.prime(body, record)
    assert not checker.reviewer.cache
    catalog = build_bibliography(body, [url], results[0].findings)
    assert not bind_review(catalog, checker, body, record)


@pytest.mark.parametrize("body", [
    "建议复测95次 [1]。",
    "是否达到95分 [1]？",
    "如果样本数为95 [1]。",
    "主观评分：95 [1]。",
    "We recommend 95 trials [1].",
    "Should Alpha reach 95 [1]?",
])
def test_proposals_and_questions_are_not_forced_into_numeric_facts(body):
    assert not numeric_fact(body)


@pytest.mark.parametrize("ids", [[], ["missing"], ["outside"]])
async def test_cached_support_with_invalid_evidence_is_rejudged(ids):
    evidence = [item("a", "The variables correlate"), item("outside", "The variables correlate", 2)]
    reviewer = SupportReviewer(SelectedJudge(["a"]), evidence, 50000)
    unit = SupportUnit("u", "变量相关 [1]。", citations=[1])
    key = digest([asdict(unit), evidence[:1]])
    reviewer.cache[key] = SupportDecision(
        unit_id="u", verdict="supported", evidence_ids=ids, reason="historical"
    )
    decision = (await reviewer.review([unit]))[0]
    assert decision.verdict == "supported"
    assert decision.evidence_ids == ["a"]
    assert decision.reason != "historical"
