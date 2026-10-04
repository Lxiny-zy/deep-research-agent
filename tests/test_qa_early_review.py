"""Review cited paragraphs while other paragraphs still need mechanical repair."""

import json

from deep_research.agents.base import RunContext
from deep_research.observability import Tracer
from deep_research.workbench.prose_edit import ProseEdits
from deep_research.workbench.support import SupportDecisions
from tests.test_prose_review import CorrelationSearch, Judge, reviewer

UNCITED = "变量存在相关关系。"
CAUSAL = "变量已经证明因果关系 [1]。"
FIXED = "不能从相关关系证明因果关系 [1]。"


class MixedAnswer(Judge):
    def __init__(self):
        super().__init__()
        self.edits = []
        self.reviews = []

    async def stream(self, *args, **kwargs):
        self.stream_calls += 1
        assert self.stream_calls == 1, "mixed defects should use one local revision"
        yield UNCITED + "\n\n" + CAUSAL

    async def parse(self, system, user, schema, **kwargs):
        if schema is SupportDecisions:
            self.reviews.append([unit["text"] for unit in json.loads(user)["units"]])
        if schema is ProseEdits:
            payload = json.loads(user.split("【只修订以下段落】\n", 1)[1])
            self.edits.append(payload["paragraphs"])
            return ProseEdits(
                edits=[
                    {
                        "unit_id": part["unit_id"],
                        "replacement": "变量存在相关关系 [1]。"
                        if part["text"] == UNCITED
                        else FIXED,
                    }
                    for part in payload["paragraphs"]
                ]
            )
        return await super().parse(system, user, schema, **kwargs)


async def ask(settings, model):
    from deep_research.workbench.qa import answer_question

    ctx = RunContext(llm=model, search_tool=CorrelationSearch(), tracer=Tracer(), settings=settings)
    return await answer_question("解释变量关系", history=[], ctx=ctx, include_web=True)


async def test_cited_claim_is_reviewed_before_missing_citation_is_repaired(settings):
    settings.quality = {"max_revisions": 1, "qa_claim_max_revisions": 1}
    model = MixedAnswer()
    result = await ask(settings, model)
    assert not result.fallback
    assert result.answer == "变量存在相关关系 [1]。\n\n" + FIXED
    assert model.reviews[0] == [CAUSAL]
    assert len(model.edits) == 1 and len(model.edits[0]) == 2
    attempt = next(row for row in result.thoughts if row["tool"] == "answer_revision")
    assert attempt["counts"] == {"mechanical": 1, "claim": 1}


async def test_disabled_mechanical_budget_does_not_block_review_or_local_claim_repair(settings):
    settings.quality = {"max_revisions": 0, "qa_claim_max_revisions": 1}
    model = MixedAnswer()
    result = await ask(settings, model)
    assert result.fallback and model.reviews[0] == [CAUSAL]
    assert len(model.edits) == 1 and [part["text"] for part in model.edits[0]] == [CAUSAL]
    record = next(row for row in result.thoughts if row["tool"] == "claim_check")
    assert record["unapproved_draft"] == UNCITED + "\n\n" + FIXED


async def test_deferred_units_are_never_cached_as_a_semantic_verdict():
    checker = reviewer(Judge())
    body = "变量存在相关关系 [1]。"
    uid = checker.units(body)[0][0].id
    deferred = await checker.review(body, deferred={uid: "等待机械修订"})
    assert deferred["status"] == "fail" and checker.check(body, deferred)[0]
    assert checker.prime(body, deferred)
    checked = await checker.review(body)
    assert checked["status"] == "pass"
    forged = {**checked, "deferred_units": [uid]}
    assert checker.check(body, forged)[1]
    assert not checker.check(body, {**checked, "deferred_units": [[]]})[0]


async def test_early_review_preserves_duplicate_paragraph_locations():
    from deep_research.report.validation import validate_body
    from deep_research.workbench.qa_revision import mechanical_deferrals
    from tests.test_prose_review import findings

    checker = reviewer(Judge())
    body = UNCITED + "\n\n" + CAUSAL + "\n\n" + UNCITED
    check = validate_body(body, findings(), {"https://a.com": 1}, fallback=False)
    units, locations = checker.units(body)
    deferred = mechanical_deferrals(check, units, locations)
    assert set(deferred) == {unit.id for unit in units if unit.text == UNCITED}


async def test_mechanical_locations_cover_a_complete_multiline_equation_paragraph():
    from deep_research.report.validation import validate_body
    from deep_research.workbench.qa_revision import mechanical_deferrals
    from tests.test_prose_review import findings

    checker = reviewer(Judge())
    equation = "目标为\n\n$$ L = 999 $$\n\n其中变量的条件见原文 [1]。"
    body = equation + "\n\n" + CAUSAL
    check = validate_body(body, findings(), {"https://a.com": 1}, fallback=False)
    units, locations = checker.units(body)
    deferred = mechanical_deferrals(check, units, locations)
    assert set(deferred) == {unit.id for unit in units if "999" in unit.text}
