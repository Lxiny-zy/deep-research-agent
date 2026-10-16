"""Figure edits converge, preserve the best candidate, and consume durable slots."""

import asyncio
import json
from copy import deepcopy

import pytest

from deep_research.agents.base import Blackboard, RunContext
from deep_research.models import ResearchResult
from deep_research.observability import Tracer
from deep_research.persistence.repository import LeaseLostError
from deep_research.workbench.figure_review import check_figure
from deep_research.workbench.figure_revision import review_figure_with_revisions
from deep_research.workbench.figures import ConceptFigure
from deep_research.workbench.prose_review import ProseReviewer
from deep_research.workbench.support import SupportDecisions, SupportReviewer
from deep_research.workbench.writers import TemplateWriter
from deep_research.workbench.writing_progress import WritingProgressError
from tests.fakes import FakeLLM, FakeSearch, verified_finding


def invalid_figure():
    return ConceptFigure(
        title="流程", evidence_mode="scoped",
        nodes=[{"id": "a", "label": "输入"}, {"id": "a", "label": "输出"}],
    )


def graph(errors):
    return ConceptFigure(title="主题", evidence_mode="scoped", nodes=[
        {"id": label, "label": "BAD" if index < errors else label}
        for index, label in enumerate(("输入", "处理", "输出"))
    ])


class Judge(FakeLLM):
    async def parse(self, system, user, schema, **kwargs):
        assert schema is SupportDecisions
        return schema(decisions=[
            {
                "unit_id": unit["id"],
                "verdict": "unsupported" if "BAD" in unit["text"] else "non_factual",
                "reason": "需要改正" if "BAD" in unit["text"] else "概念编排",
                "evidence_ids": [],
            }
            for unit in json.loads(user)["units"]
        ])


class Ledger:
    def __init__(self, state=None):
        self.state = state or {}
        self.saved = None

    async def save(self):
        # Only serialized state crosses the simulated process boundary.
        self.saved = json.loads(json.dumps(self.state))


async def converge(ledger, revise, figure=None, limit=4):
    return await review_figure_with_revisions(
        figure or invalid_figure(), SupportReviewer(Judge(), [], 20000), revise,
        max_revisions=limit, state=ledger.state, save=ledger.save, on_progress=lambda _: None,
    )


@pytest.mark.parametrize("same_body", [True, False])
async def test_unchanged_figure_or_issues_stop_and_stay_stopped_after_recovery(same_body):
    calls = []

    async def edit(current, record):
        calls.append(True)
        return current if same_body else current.model_copy(update={"title": "另一个标题"})

    ledger = Ledger()
    _, _, log = await converge(ledger, edit)
    assert calls == [True]
    assert log["stop_reason"] == "no_progress" and log["revisions_used"] == 1
    recovered = Ledger(ledger.saved)
    _, _, resumed = await converge(recovered, edit)
    assert resumed == log and calls == [True]


async def test_figure_review_keeps_the_better_earlier_graph_with_its_matching_record():
    async def edit(current, record):
        return graph(3)

    chosen, record, log = await converge(Ledger(), edit, figure=graph(1), limit=1)
    assert chosen == graph(1) and log["chosen"] == 1
    assert len(record["issues"]) == 1
    assert check_figure(chosen, [], record) == ["需要改正"]


async def test_improving_graphs_continue_until_passed():
    versions = iter([graph(2), graph(1), graph(0)])

    async def edit(current, record):
        return next(versions)

    chosen, record, log = await converge(Ledger(), edit, figure=graph(3))
    assert chosen == graph(0) and record["status"] == "pass"
    assert log["revisions_used"] == 3 and log["stop_reason"] == "passed"
    assert check_figure(chosen, [], record) == []


async def test_zero_figure_revision_allowance_never_calls_the_editor():
    async def edit(current, record):
        raise AssertionError("No automatic edits permitted")

    _, _, log = await converge(Ledger(), edit, limit=0)
    assert log["revisions_used"] == 0 and log["stop_reason"] == "revision_limit"


async def test_failed_figure_edit_consumes_a_slot_and_is_not_repeated_after_recovery():
    calls = []

    async def edit(current, record):
        calls.append(True)
        raise ValueError("controlled failure")

    ledger = Ledger()
    _, _, log = await converge(ledger, edit)
    assert calls == [True]
    assert log["revisions_used"] == 1 and log["stop_reason"] == "revision_failed"
    await converge(Ledger(ledger.saved), edit)
    assert calls == [True]


async def test_cancelled_edit_keeps_its_reserved_slot_on_recovery():
    entered = asyncio.Event()
    calls = []

    async def edit(current, record):
        calls.append(True)
        entered.set()
        await asyncio.Future()

    ledger = Ledger()
    task = asyncio.create_task(converge(ledger, edit, limit=1))
    await asyncio.wait_for(entered.wait(), 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert ledger.saved["revisions_used"] == 1 and not ledger.saved.get("complete")
    _, _, log = await converge(Ledger(ledger.saved), edit, limit=1)
    assert calls == [True] and log["stop_reason"] == "revision_limit"


@pytest.mark.parametrize("error", [LeaseLostError("lost"), WritingProgressError("storage")])
async def test_figure_edit_does_not_swallow_execution_or_storage_failures(error):
    async def edit(current, record):
        raise error

    with pytest.raises(type(error)):
        await converge(Ledger(), edit)


async def test_completed_figure_record_cannot_be_reused_with_changed_content():
    async def edit(current, record):
        return current

    ledger = Ledger()
    await converge(ledger, edit)
    corrupted = deepcopy(ledger.saved)
    corrupted["versions"][0]["model"]["title"] = "changed"
    with pytest.raises(WritingProgressError, match="不一致"):
        await converge(Ledger(corrupted), edit)


async def test_writer_stops_regenerating_an_identical_invalid_figure(settings, monkeypatch):
    class Model(FakeLLM):
        async def parse(self, system, user, schema, **kwargs):
            assert schema is ConceptFigure
            self.parse_calls += 1
            return invalid_figure()

    class Writer(TemplateWriter):
        name = "research_writer"

        async def _write_checked(self, *args, **kwargs):
            return "## 结论\n\n发现X [1]。", None

    async def approved(self, body):
        return {"status": "pass", "issues": []}

    monkeypatch.setattr(ProseReviewer, "review", approved)
    settings.quality = {"max_revisions": 4, "adaptive_review": False}
    model = Model()
    bb = Blackboard(
        query="研究结果并生成配套概念图",
        results=[ResearchResult(sub_question="q", findings=[verified_finding()])],
    )
    await Writer().step(
        bb, RunContext(llm=model, search_tool=FakeSearch(), tracer=Tracer(), settings=settings),
    )
    assert model.parse_calls == 2
    extras = bb.scratch["workbench"]["extras"]
    assert extras["figure_revision"]["stop_reason"] == "no_progress"
    assert extras["figure_review"]["status"] == "fail"
