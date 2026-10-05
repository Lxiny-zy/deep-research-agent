"""Citation fixes cannot consume the separate claim-revision allowance."""

import json

import pytest

from deep_research.agents.base import RunContext
from deep_research.observability import Tracer
from deep_research.workbench.prose_edit import ProseEdits
from deep_research.workbench.qa import answer_question
from tests.test_prose_review import CorrelationSearch, Judge

BAD = "变量已经证明因果关系 [1]。"
GOOD = "不能从相关关系证明因果关系 [1]。"


class BudgetWriter(Judge):
    def __init__(self, *, missing_citation=True, repair_on=2):
        super().__init__()
        self.missing_citation = missing_citation
        self.repair_on = repair_on
        self.local_edits = 0
        self.citation_edits = 0

    async def stream(self, *args, **kwargs):
        self.stream_calls += 1
        yield BAD.replace(" [1]", "") if self.missing_citation and self.stream_calls == 1 else BAD

    async def parse(self, system, user, schema, **kwargs):
        if schema is ProseEdits:
            payload = json.loads(user.split("【只修订以下段落】\n", 1)[1])
            missing = any("[1]" not in part["text"] for part in payload["paragraphs"])
            if missing:
                self.citation_edits += 1
            else:
                self.local_edits += 1
            return ProseEdits(
                edits=[
                    {
                        "unit_id": part["unit_id"],
                        "replacement": BAD
                        if missing
                        else (GOOD if self.local_edits >= self.repair_on else "现有" + BAD),
                    }
                    for part in payload["paragraphs"]
                ]
            )
        return await super().parse(system, user, schema, **kwargs)


async def ask(settings, model):
    ctx = RunContext(llm=model, search_tool=CorrelationSearch(), tracer=Tracer(), settings=settings)
    return await answer_question("解释变量关系", history=[], ctx=ctx, include_web=True)


async def test_citation_repair_leaves_the_full_claim_budget_available(settings):
    settings.quality = {"max_revisions": 1, "qa_claim_max_revisions": 2}
    model = BudgetWriter()
    result = await ask(settings, model)
    assert not result.fallback and result.answer == GOOD
    assert model.stream_calls == 1 and model.local_edits == 2 and model.citation_edits == 1
    attempts = [row for row in result.thoughts if row["tool"] == "answer_revision"]
    assert [row["category"] for row in attempts] == ["mechanical", "claim", "claim"]
    assert [row["attempt"] for row in attempts] == [1, 1, 2]


@pytest.mark.parametrize("mechanical,claims", [(0, 1), (3, 0), (0, 0)])
async def test_each_revision_allowance_can_be_disabled_independently(settings, mechanical, claims):
    settings.quality = {"max_revisions": mechanical, "qa_claim_max_revisions": claims}
    model = BudgetWriter(missing_citation=False, repair_on=1)
    result = await ask(settings, model)
    assert model.stream_calls == 1 and model.local_edits == claims
    assert result.fallback is (claims == 0)


async def test_claim_budget_does_not_extend_repeated_citation_repairs(settings):
    class Uncited(BudgetWriter):
        async def stream(self, *args, **kwargs):
            self.stream_calls += 1
            yield BAD.replace(" [1]", "")

        async def parse(self, system, user, schema, **kwargs):
            if schema is ProseEdits:
                raise ValueError("local repair unavailable")
            return await super().parse(system, user, schema, **kwargs)

    settings.quality = {"max_revisions": 1, "qa_claim_max_revisions": 4}
    model = Uncited()
    result = await ask(settings, model)
    assert result.fallback and model.stream_calls == 2 and model.local_edits == 0


async def test_exhausted_total_budget_does_not_start_an_extra_full_generation(
    settings, monkeypatch
):
    from deep_research.token_budget import TokenBudgetExceeded
    from deep_research.workbench import prose_edit

    async def exhausted(*args, **kwargs):
        raise TokenBudgetExceeded("budget exhausted")

    monkeypatch.setattr(prose_edit, "repair_paragraphs", exhausted)
    settings.quality = {"max_revisions": 4, "qa_claim_max_revisions": 4}
    model = BudgetWriter(missing_citation=False)
    result = await ask(settings, model)
    assert result.fallback and model.stream_calls == 1


@pytest.mark.parametrize("local", [True, False])
async def test_unchanged_revision_stops_without_spending_remaining_allowances(settings, local):
    class NoProgress(BudgetWriter):
        async def parse(self, system, user, schema, **kwargs):
            if schema is ProseEdits:
                self.local_edits += 1
                if not local:
                    raise ValueError("local repair unavailable")
                payload = json.loads(user.split("【只修订以下段落】\n", 1)[1])
                return ProseEdits(edits=[
                    {"unit_id": part["unit_id"], "replacement": part["text"]}
                    for part in payload["paragraphs"]
                ])
            return await super().parse(system, user, schema, **kwargs)

    settings.quality = {"max_revisions": 4, "qa_claim_max_revisions": 4}
    model = NoProgress(missing_citation=False)
    result = await ask(settings, model)
    assert result.fallback and model.local_edits == 1
    assert model.stream_calls == (1 if local else 2)
    assert any("未产生新正文" in row["observation"] for row in result.thoughts)


async def test_revision_cycle_stops_when_returning_to_a_checked_draft(settings):
    class CyclicWriter(BudgetWriter):
        async def parse(self, system, user, schema, **kwargs):
            if schema is ProseEdits:
                self.local_edits += 1
                payload = json.loads(user.split("【只修订以下段落】\n", 1)[1])
                return ProseEdits(edits=[
                    {"unit_id": part["unit_id"],
                     "replacement": "现有" + BAD if self.local_edits % 2 else BAD}
                    for part in payload["paragraphs"]
                ])
            return await super().parse(system, user, schema, **kwargs)

    settings.quality = {"max_revisions": 4, "qa_claim_max_revisions": 4}
    model = CyclicWriter(missing_citation=False)
    result = await ask(settings, model)
    assert result.fallback and model.local_edits == 2 and model.stream_calls == 1
    assert any("未产生新正文" in row["observation"] for row in result.thoughts)
