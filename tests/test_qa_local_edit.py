from __future__ import annotations

import json

import pytest

from deep_research.agents.base import RunContext
from deep_research.observability import Tracer
from deep_research.persistence.repository import LeaseLostError
from deep_research.workbench.prose_edit import ProseEdits
from deep_research.workbench.qa import answer_question
from deep_research.workbench.support import SupportDecisions
from tests.test_prose_review import CorrelationSearch, Judge


class LocalAnswer(Judge):
    lose_lease = False

    def __init__(self):
        super().__init__()
        self.edits = []
        self.checked = []

    async def stream(self, *args, **kwargs):
        self.stream_calls += 1
        assert self.stream_calls == 1, "A local defect must not rewrite the complete answer"
        yield "变量存在相关关系 [1]。\n\n变量已经证明因果关系 [1]。"

    async def parse(self, system, user, schema, **kwargs):
        if schema is ProseEdits:
            if self.lose_lease:
                raise LeaseLostError("taken over")
            data = json.loads(user.split("【只修订以下段落】\n", 1)[1])
            self.edits.extend(data["paragraphs"])
            assert len(data["paragraphs"]) == 1
            paragraph = data["paragraphs"][0]
            assert "已经证明因果" in paragraph["text"]
            return ProseEdits(
                edits=[
                    {
                        "unit_id": paragraph["unit_id"],
                        "replacement": "不能从相关关系证明因果关系 [1]。",
                    }
                ]
            )
        if schema is SupportDecisions:
            self.checked.append([u["text"] for u in json.loads(user)["units"]])
        return await super().parse(system, user, schema, **kwargs)


async def test_answer_repairs_only_failed_paragraph_and_reuses_passed_review(settings):
    model, events = LocalAnswer(), []
    ctx = RunContext(llm=model, search_tool=CorrelationSearch(), tracer=Tracer(), settings=settings)
    result = await answer_question(
        "解释变量关系",
        history=[],
        ctx=ctx,
        include_web=True,
        on_event=lambda event: events.append(event),
        on_delta=lambda text: events.append({"type": "delta", "text": text}),
    )
    assert not result.fallback and model.stream_calls == 1 and len(model.edits) == 1
    assert result.answer == "变量存在相关关系 [1]。\n\n不能从相关关系证明因果关系 [1]。"
    assert model.checked[-1] == ["不能从相关关系证明因果关系 [1]。"]
    reset = next(i for i, event in enumerate(events) if event["type"] == "reset")
    assert events[reset + 1] == {"type": "delta", "text": result.answer}


async def test_answer_local_repair_propagates_lost_lease(settings):
    model = LocalAnswer()
    model.lose_lease = True
    ctx = RunContext(llm=model, search_tool=CorrelationSearch(), tracer=Tracer(), settings=settings)
    with pytest.raises(LeaseLostError):
        await answer_question("解释变量关系", history=[], ctx=ctx, include_web=True)
    assert model.stream_calls == 1


@pytest.mark.parametrize("failure", ["exception", "missing_edits", "unavailable"])
async def test_failed_local_edit_keeps_checked_paragraph_without_full_rewrite(
    settings, monkeypatch, failure
):
    from deep_research.workbench import prose_edit

    attempted = []

    class FailedEdit(LocalAnswer):
        async def parse(self, system, user, schema, **kwargs):
            if schema is ProseEdits:
                data = json.loads(user.split("【只修订以下段落】\n", 1)[1])
                attempted.extend(data["paragraphs"])
                if failure == "exception":
                    raise ValueError("local repair failed")
                return ProseEdits(edits=[])
            return await super().parse(system, user, schema, **kwargs)

    if failure == "unavailable":
        async def unavailable(_model, _reviewer, body, _record, **_kwargs):
            attempted.append(body)
            return None

        monkeypatch.setattr(prose_edit, "repair_paragraphs", unavailable)

    # Failure is terminal even when an operator explicitly allows more edits.
    settings.quality = {"max_revisions": 4, "qa_claim_max_revisions": 4, "qa_max_revisions": 4}
    model, events = FailedEdit(), []
    ctx = RunContext(llm=model, search_tool=CorrelationSearch(), tracer=Tracer(), settings=settings)
    result = await answer_question(
        "解释变量关系", history=[], ctx=ctx, include_web=True,
        on_event=events.append,
        on_delta=lambda text: events.append({"type": "delta", "text": text}),
    )
    assert len(attempted) == 1 and model.stream_calls == 1
    assert result.fallback
    assert "变量存在相关关系 [1]。" in result.answer
    assert "变量已经证明因果关系" not in result.answer
    assert any("未能安全完成局部修订" in row["observation"] for row in result.thoughts)
    assert not any(event.get("message") == "局部修订完成，继续核对依据…" for event in events)
