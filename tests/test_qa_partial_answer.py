"""A failed answer revision must preserve its already verified paragraphs."""

import json

import pytest

from deep_research.workbench.prose_edit import ProseEdits
from deep_research.workbench.support import SupportDecisions
from tests.test_prose_review import Judge
from tests.test_qa_early_review import CAUSAL, ask

GOOD = "变量存在相关关系 [1]。"


class Versions(Judge):
    def __init__(self, bodies):
        super().__init__()
        self.bodies = iter(bodies)

    async def stream(self, *args, **kwargs):
        self.stream_calls += 1
        yield next(self.bodies)

    async def parse(self, system, user, schema, **kwargs):
        if schema is ProseEdits:
            raise ValueError("fixture requires full revision")
        return await super().parse(system, user, schema, **kwargs)


@pytest.mark.parametrize("bad", [CAUSAL, "错误数值 999 [1]。", "变量存在相关关系 [999]。"])
async def test_only_failed_paragraph_is_replaced_when_revisions_are_disabled(settings, bad):
    settings.quality = {"max_revisions": 0, "qa_claim_max_revisions": 0}
    original = GOOD + "\n\n" + bad
    result = await ask(settings, Versions([original]))
    assert result.fallback and result.answer.startswith(GOOD + "\n\n")
    assert "此段内容未通过核验" in result.answer and bad not in result.answer
    assert "已验证素材摘要" not in result.answer
    record = next(row for row in result.thoughts if row["tool"] == "claim_check")
    assert record["unapproved_draft"] == original


async def test_worse_later_revision_does_not_replace_the_best_draft(settings):
    settings.quality = {"max_revisions": 1, "qa_claim_max_revisions": 1}
    first = GOOD + "\n\n" + CAUSAL
    worse = CAUSAL + "\n\n另一个变量已经证明因果关系 [1]。"
    result = await ask(settings, Versions([first, worse]))
    assert result.fallback and result.answer.startswith(GOOD)
    record = next(row for row in result.thoughts if row["tool"] == "claim_check")
    assert record["unapproved_draft"] == first


async def test_empty_revision_cannot_win_by_deleting_the_verified_content(settings):
    settings.quality = {"max_revisions": 0, "qa_claim_max_revisions": 1}
    first = GOOD + "\n\n" + CAUSAL + "\n\n另一个变量已经证明因果关系 [1]。"
    result = await ask(settings, Versions([first, ""]))
    assert result.fallback and result.answer.startswith(GOOD)


async def test_uncertain_paragraph_does_not_erase_a_completed_positive_verdict(settings):
    class Uncertain(Versions):
        async def parse(self, system, user, schema, **kwargs):
            response = await super().parse(system, user, schema, **kwargs)
            if schema is SupportDecisions:
                units = {unit["id"]: unit for unit in json.loads(user)["units"]}
                for decision in response.decisions:
                    if units[decision.unit_id]["text"] == CAUSAL:
                        decision.verdict = "uncertain"
                        decision.reason = "核验调用失败：TimeoutError"
            return response

    result = await ask(settings, Uncertain([GOOD + "\n\n" + CAUSAL]))
    assert result.fallback and result.answer.startswith(GOOD)
    assert CAUSAL not in result.answer


async def test_unapproved_table_does_not_leave_a_broken_partial_markdown_table(settings):
    settings.quality = {"max_revisions": 0, "qa_claim_max_revisions": 0}
    table = "| 项目 | 结论 |\n|---|---|\n| 关系 | 变量已经证明因果关系 [1]。 |"
    result = await ask(settings, Versions([GOOD + "\n\n" + table]))
    assert result.fallback and result.answer.startswith(GOOD)
    assert "|" not in result.answer and "此段内容未通过核验" in result.answer
