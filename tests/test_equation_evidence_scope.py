from __future__ import annotations

import json

import pytest

from deep_research.models import ResearchResult
from deep_research.report.validation import validate_body
from deep_research.workbench.delivery.math_markdown import equation_prose_spans
from deep_research.workbench.prose_review import ProseReviewer, prose_units
from deep_research.workbench.support import SupportDecisions
from tests.fakes import FakeLLM, verified_finding


def results():
    return [
        ResearchResult(
            sub_question="训练",
            findings=[
                verified_finding(
                    "该方法最小化损失 L，系数为 1。",
                    evidence_quote="训练通过最小化 L = 1 进行优化。",
                )
            ],
        )
    ]


def sentence(lead="该方法通过最小化损失", equation="L = 1", tail="进行优化，系数为 1[1]。"):
    return f"{lead}\n\n$${equation}$$\n\n{tail}"


def test_continued_equation_is_one_cited_unit_without_changing_original_text():
    body = sentence()
    units, locations = prose_units(body, [1, 2])
    assert len(units) == 1
    assert units[0].text == body and units[0].citations == [1]
    assert units[0].kind == "prose"
    assert (locations[0]["start_line"], locations[0]["end_line"]) == (1, 5)
    assert not validate_body(body, results(), {"https://a.com": 1}, fallback=False).issues


@pytest.mark.parametrize(
    "body",
    [sentence(equation="L = 999"), sentence(lead="该方法通过最小化 999 项损失")],
)
def test_equation_and_introduction_numbers_still_require_the_cited_source(body):
    check = validate_body(body, results(), {"https://a.com": 1}, fallback=False)
    assert "unsupported_number" in check.issues
    assert "999" in str(check.problems)


@pytest.mark.parametrize(
    "body",
    [
        sentence(lead="独立事实。"),
        sentence(lead="Independent fact."),
        sentence(lead="另一观点！"),
        sentence(tail="另一项结果为 1[1]。"),
        sentence(tail="其中系数为 1。"),
        sentence(tail="其中范围为 [0,1]。"),
        sentence().replace("$$L = 1$$", "```python\nL = 1\n```"),
        sentence().replace("$$L = 1$$", "## 新章节\n\n$$L = 1$$"),
        sentence().replace("$$L = 1$$", "|值|\n|---|\n|1|\n\n$$L = 1$$"),
        sentence().replace("$$L = 1$$", "- 列表内容\n\n$$L = 1$$"),
    ],
)
def test_independent_or_structurally_separated_paragraph_cannot_borrow_later_citation(body):
    assert equation_prose_spans(body) == {}
    units, _ = prose_units(body, [1])
    assert not units[0].citations
    assert (
        "uncited_paragraph"
        in validate_body(body, results(), {"https://a.com": 1}, fallback=False).issues
    )


def test_continuation_does_not_cover_the_next_independent_paragraph():
    body = sentence() + "\n\n随后补充的独立事实。"
    units, _ = prose_units(body, [1])
    assert len(units) == 2 and not units[-1].citations
    assert (
        "uncited_paragraph"
        in validate_body(body, results(), {"https://a.com": 1}, fallback=False).issues
    )


@pytest.mark.parametrize("lead", ["已完成的句子。[1]", "**Completed sentence.** [1]"])
def test_citations_and_emphasis_do_not_hide_the_end_of_a_sentence(lead):
    assert equation_prose_spans(sentence(lead=lead)) == {}


def test_formula_does_not_borrow_numbers_from_an_unrelated_source():
    extra = ResearchResult(
        sub_question="别的方法",
        findings=[
            verified_finding("系数为 999", source_url="https://b.com", evidence_quote="系数为 999")
        ],
    )
    check = validate_body(
        sentence(equation="L = 999"),
        [*results(), extra],
        {"https://a.com": 1, "https://b.com": 2},
        fallback=False,
    )
    assert "unsupported_number" in check.issues


async def test_merged_sentence_still_sends_all_facts_to_semantic_review():
    class Judge(FakeLLM):
        async def parse(self, system, user, schema, **kwargs):
            data = json.loads(user)
            assert len(data["units"]) == 1
            unit = data["units"][0]
            assert "无需训练" in unit["text"] and "L = 1" in unit["text"]
            assert unit["citations"] == [1]
            return SupportDecisions(
                decisions=[
                    {
                        "unit_id": unit["id"],
                        "verdict": "unsupported",
                        "reason": "原文需要训练，不支持无需训练",
                    }
                ]
            )

    checker = ProseReviewer.research(Judge(), results(), {"https://a.com": 1}, 50000)
    body = sentence(lead="该方法无需训练，通过最小化损失")
    record = await checker.review(body)
    assert record["status"] == "fail" and "无需训练" in record["issues"][0]
    assert checker.check(body, record)[0]
    assert not checker.check(body.replace("L = 1", "L = 999"), record)[0]


def test_english_continuation_crlf_and_multiline_equation_keep_source_locations():
    body = "The loss is\r\n\r\n$$\r\nL = 1\r\n\r\n$$\r\n\r\nwhere L is the loss [1]."
    units, locations = prose_units(body, [1])
    assert len(units) == 1 and units[0].citations == [1]
    assert "L = 1" in units[0].text
    assert locations[0]["end_line"] == 8
