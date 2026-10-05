"""Writing work survives a lost enclosing step without repeating paid calls."""

import asyncio

import pytest

from deep_research.artifacts import ArtifactStore
from deep_research.persistence.repository import LeaseLostError
from deep_research.workbench.revision import Assessment, write_with_revisions


def progress(tmp_path, scope=None, **kwargs):
    from deep_research.workbench.writing_progress import WritingProgress

    return WritingProgress(
        ArtifactStore(tmp_path), scope or {"run_id": "r", "role": "writer"}, **kwargs
    )


async def test_completed_draft_is_reused_after_interruption_during_review(tmp_path):
    writes = []

    async def write(revision):
        writes.append(revision)
        return "Completed draft"

    async def interrupted(body):
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await write_with_revisions(write, interrupted, max_revisions=2, progress=progress(tmp_path))
    body, log = await write_with_revisions(
        write, lambda _: Assessment(), max_revisions=2, progress=progress(tmp_path)
    )
    assert body == "Completed draft" and writes == [None]
    assert log.attempts == 1


async def test_verified_draft_and_revision_budget_survive_lost_write(tmp_path):
    writes, reviews = [], []

    async def write(revision):
        writes.append(revision)
        if revision is not None:
            raise asyncio.CancelledError()
        return "First draft"

    async def assess(body):
        reviews.append(body)
        return (
            Assessment(hard=["Missing required content"]) if body == "First draft" else Assessment()
        )

    with pytest.raises(asyncio.CancelledError):
        await write_with_revisions(write, assess, max_revisions=1, progress=progress(tmp_path))

    async def resumed(revision):
        writes.append(revision)
        assert revision is not None and "First draft" in revision
        return "Repaired draft"

    body, log = await write_with_revisions(
        resumed, assess, max_revisions=1, progress=progress(tmp_path)
    )
    assert body == "Repaired draft" and log.attempts == 2 and log.chosen == 2
    assert reviews == ["First draft", "Repaired draft"]
    assert len(writes) == 3


async def test_finished_loop_restores_best_body_and_matching_side_outputs(tmp_path):
    side, restored = {"value": "first"}, []
    count = 0

    async def write(revision):
        nonlocal count
        count += 1
        side["value"] = "first" if revision is None else "second"
        return side["value"]

    def assess(body):
        return Assessment(hard=["one"] if body == "first" else ["one", "two"])

    kwargs = {"capture": lambda: dict(side), "restore": lambda value: restored.append(value)}
    body, log = await write_with_revisions(
        write, assess, max_revisions=1, progress=progress(tmp_path, **kwargs)
    )
    assert body == "first" and log.chosen == 1
    body, log = await write_with_revisions(
        write, assess, max_revisions=1, progress=progress(tmp_path, **kwargs)
    )
    assert count == 2 and body == "first" and log.attempts == 2
    assert restored[-1] == {"value": "first"}


async def test_progress_isolated_by_run_input_and_budget(tmp_path):
    calls = []

    async def write(revision):
        calls.append(revision)
        return "Draft"

    for scope, budget in [
        ({"run_id": "a", "input": 1}, 1),
        ({"run_id": "b", "input": 1}, 1),
        ({"run_id": "a", "input": 2}, 1),
        ({"run_id": "a", "input": 1}, 2),
    ]:
        await write_with_revisions(
            write, lambda _: Assessment(), max_revisions=budget, progress=progress(tmp_path, scope)
        )
    assert len(calls) == 4


@pytest.mark.parametrize("role,expected", [("mindmap_writer", 2), ("research_writer", 1)])
async def test_mindmap_rule_upgrade_cannot_reuse_the_completed_old_writer(
    settings, tmp_path, monkeypatch, role, expected,
):
    from deep_research.agents.base import Blackboard, RunContext
    from deep_research.observability import Tracer
    from deep_research.workbench import mindmap_contract
    from deep_research.workbench.writing_progress import for_writer
    from tests.fakes import FakeLLM, FakeSearch

    bb = Blackboard(query="关系导图")
    ctx = RunContext(
        llm=FakeLLM(), search_tool=FakeSearch(), tracer=Tracer(), settings=settings,
        artifact_store=ArtifactStore(tmp_path), run_id="mindmap-upgrade",
    )
    writes = []

    async def write(revision):
        writes.append(revision)
        return "Completed map"

    for version in (2, 3, 3):
        monkeypatch.setattr(mindmap_contract, "MINDMAP_POLICY_VERSION", version, raising=False)
        progress = for_writer(bb, ctx, role, "stable system", inputs={})
        await write_with_revisions(
            write, lambda _: Assessment(), max_revisions=0, progress=progress,
        )
    assert len(writes) == expected


async def test_save_failure_stops_before_another_model_call(tmp_path, monkeypatch):
    from deep_research.workbench.writing_progress import WritingProgressError

    checkpoint = progress(tmp_path)
    calls = []

    def full(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(checkpoint.store, "write_control_json", full)

    async def write(revision):
        calls.append(revision)
        return "Draft"

    with pytest.raises(WritingProgressError):
        await write_with_revisions(
            write, lambda _: Assessment(hard=["missing"]), max_revisions=2, progress=checkpoint
        )
    assert len(calls) == 1


async def test_lost_lease_never_falls_back_to_a_previous_draft(tmp_path):
    async def write(revision):
        if revision:
            raise LeaseLostError("lost")
        return "Draft"

    with pytest.raises(LeaseLostError):
        await write_with_revisions(
            write,
            lambda _: Assessment(hard=["missing"]),
            max_revisions=2,
            progress=progress(tmp_path),
        )


async def test_corrupt_progress_is_not_silently_replaced(tmp_path):
    from deep_research.workbench.writing_progress import WritingProgressError

    checkpoint = progress(tmp_path)
    checkpoint.store.write_control_text(checkpoint.path(1), "{broken")
    calls = []

    async def write(revision):
        calls.append(revision)
        return "Draft"

    with pytest.raises(WritingProgressError):
        await write_with_revisions(
            write, lambda _: Assessment(), max_revisions=1, progress=checkpoint
        )
    assert calls == []


async def test_cumulative_metrics_survive_a_completed_loop(tmp_path):
    from deep_research.observability import Tracer

    first = Tracer()
    first.restore_metrics(total_tokens=120, elapsed=30)

    async def write(revision):
        first.add_tokens(40)
        return "Draft"

    await write_with_revisions(
        write, lambda _: Assessment(), max_revisions=0, progress=progress(tmp_path, tracer=first)
    )
    second = Tracer()
    await write_with_revisions(
        write, lambda _: Assessment(), max_revisions=0, progress=progress(tmp_path, tracer=second)
    )
    assert first.total_tokens == second.total_tokens == 160
    assert second.elapsed >= 30


async def test_real_prose_writer_reuses_draft_when_review_was_interrupted(
    tmp_path, settings, monkeypatch
):
    import json

    from deep_research.agents.base import Blackboard, RunContext
    from deep_research.models import ResearchResult
    from deep_research.observability import Tracer
    from deep_research.workbench.prose_review import ProseReviewer
    from deep_research.workbench.support import evidence_id
    from deep_research.workbench.templates import get_template
    from deep_research.workbench.writers import ResearchWriter
    from deep_research.workflow import Step, Workflow, WorkflowEngine
    from tests.fakes import FakeSearch, verified_finding
    from tests.test_workbench import WorkbenchLLM

    settings.quality = {
        "max_revisions": 0,
        "register_check": False,
        "require_limitations": False,
        "forbid_abstract_citations": False,
    }
    finding = verified_finding()
    body = "\n\n".join(
        f"## {section.title}\n发现X [1]。" for section in get_template("autoResearch").sections
    )
    body += (
        "\n\n```evidence-table\n"
        + json.dumps(
            {
                "id": "t1",
                "title": "表 1 已知结果",
                "columns": [{"key": "fact", "label": "结果", "field": "statement"}],
                "rows": [{"label": "方案", "cells": {"fact": [evidence_id(finding)]}}],
            }
        )
        + "\n```"
    )
    writes, checks, table_checks = [], [], []

    class Model(WorkbenchLLM):
        async def stream(self, system, user, **kwargs):
            writes.append(user)
            yield body

        async def parse(self, system, user, schema, **kwargs):
            checks.append(schema.__name__)
            if "逐格核对表格" in system:
                table_checks.append(user)
            return await super().parse(system, user, schema, **kwargs)

    initial = Blackboard(
        query="只需正文，不需要图示", results=[ResearchResult(sub_question="q", findings=[finding])]
    )

    def context():
        return RunContext(
            llm=Model(body),
            search_tool=FakeSearch(),
            tracer=Tracer(),
            settings=settings,
            artifact_store=ArtifactStore(tmp_path),
            run_id="prose",
        )

    original = ProseReviewer.review

    async def interrupted(self, markdown):
        raise asyncio.CancelledError()

    monkeypatch.setattr(ProseReviewer, "review", interrupted)
    workflow = Workflow(name="writer-recovery", steps=[Step(agent="research_writer")])
    engine = WorkflowEngine(context(), resolver=lambda _: ResearchWriter())
    with pytest.raises(asyncio.CancelledError):
        await engine.run(workflow, initial.model_copy(deep=True))
    assert len(table_checks) == 1
    execution = engine.runtime.run.model_copy(deep=True)
    saved_input = Blackboard.model_validate(execution.checkpoint)
    assert saved_input.report is None
    monkeypatch.setattr(ProseReviewer, "review", original)
    resumed_engine = WorkflowEngine(
        context(), resolver=lambda _: ResearchWriter(), resume_run=execution
    )
    result = await resumed_engine.run(workflow, saved_input)
    assert len(writes) == 1 and result.report
    assert len(table_checks) == 1
    check_count = len(checks)
    resumed = await WorkflowEngine(context(), resolver=lambda _: ResearchWriter()).run(
        workflow, initial.model_copy(deep=True)
    )
    assert len(writes) == 1 and resumed.report == result.report
    assert len(checks) == check_count
    assert resumed.scratch["workbench"] == result.scratch["workbench"]


@pytest.mark.parametrize("kind", ["mindmap", "dataAnalysis"])
async def test_finished_specialized_writer_survives_a_discarded_step(tmp_path, settings, kind):
    from deep_research.agents.base import Blackboard, RunContext
    from deep_research.models import ResearchResult
    from deep_research.observability import Tracer
    from deep_research.workbench.analysis import DataAnalyst, analyse, fallback_report
    from deep_research.workbench.contract import build_contract
    from deep_research.workbench.templates import get_template
    from deep_research.workbench.writers import MindmapWriter
    from tests.fakes import FakeSearch, verified_finding
    from tests.test_workbench import WorkbenchLLM

    settings.quality = {"max_revisions": 0, "register_check": False, "require_limitations": False}
    calls = []
    query = (
        "描述数据\nmethod,value\nA,1\nA,2\nB,3\nB,4\n"
        if kind == "dataAnalysis"
        else "整理已核验结果"
    )
    initial = Blackboard(query=query)
    if kind == "dataAnalysis":
        contract = build_contract(get_template(kind), query, quality=settings.quality)
        initial.scratch["task_contract"] = contract.model_dump(mode="json")
        draft = fallback_report(analyse(contract.dataset_csv, contract.focus))
        writer = DataAnalyst
    else:
        initial.results = [ResearchResult(sub_question="q", findings=[verified_finding()])]
        draft = ""
        writer = MindmapWriter

    class Model(WorkbenchLLM):
        async def stream(self, system, user, **kwargs):
            calls.append("write")
            yield draft

        async def parse(self, system, user, schema, **kwargs):
            calls.append(schema.__name__)
            return await super().parse(system, user, schema, **kwargs)

    def context():
        return RunContext(
            llm=Model(draft),
            search_tool=FakeSearch(),
            tracer=Tracer(),
            settings=settings,
            artifact_store=ArtifactStore(tmp_path),
            run_id=kind,
        )

    result = await writer().step(initial.model_copy(deep=True), context())
    count = len(calls)
    resumed = await writer().step(initial.model_copy(deep=True), context())
    assert len(calls) == count
    assert resumed.report == result.report
    assert resumed.scratch["workbench"] == result.scratch["workbench"]


async def test_generated_figure_survives_interruption_before_its_review(
    tmp_path, settings, monkeypatch
):
    from deep_research.agents.base import Blackboard, RunContext
    from deep_research.models import ResearchResult
    from deep_research.observability import Tracer
    from deep_research.workbench import figure_review
    from deep_research.workbench.figures import ConceptFigure
    from deep_research.workbench.templates import get_template
    from deep_research.workbench.writers import ResearchWriter
    from tests.fakes import FakeSearch, verified_finding
    from tests.test_workbench import WorkbenchLLM

    settings.quality = {
        "max_revisions": 0,
        "register_check": False,
        "require_limitations": False,
        "forbid_abstract_citations": False,
    }
    body = "\n\n".join(
        f"## {section.title}\n发现X [1]。" for section in get_template("autoResearch").sections
    )
    calls = []

    class Model(WorkbenchLLM):
        async def stream(self, system, user, **kwargs):
            calls.append("write")
            yield body

        async def parse(self, system, user, schema, **kwargs):
            calls.append(schema.__name__)
            if schema is ConceptFigure:
                return ConceptFigure(
                    title="结果概览",
                    nodes=[
                        {"id": "a", "label": "结果", "kind": "concept"},
                        {"id": "b", "label": "发现X", "kind": "claim", "citations": [1]},
                    ],
                    edges=[],
                )
            return await super().parse(system, user, schema, **kwargs)

    initial = Blackboard(
        query="解释材料并配图",
        results=[ResearchResult(sub_question="q", findings=[verified_finding()])],
    )

    def context():
        return RunContext(
            llm=Model(body),
            search_tool=FakeSearch(),
            tracer=Tracer(),
            settings=settings,
            artifact_store=ArtifactStore(tmp_path),
            run_id="figure",
        )

    original = figure_review.review_figure

    async def interrupted(*args, **kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(figure_review, "review_figure", interrupted)
    with pytest.raises(asyncio.CancelledError):
        await ResearchWriter().step(initial.model_copy(deep=True), context())
    assert calls.count("write") == calls.count("ConceptFigure") == 1
    monkeypatch.setattr(figure_review, "review_figure", original)
    result = await ResearchWriter().step(initial.model_copy(deep=True), context())
    assert calls.count("write") == calls.count("ConceptFigure") == 1
    assert result.scratch["workbench"]["extras"]["concept_figure"]


async def test_analysis_scope_survives_a_crash_before_statistics(tmp_path, settings, monkeypatch):
    from deep_research.agents.base import Blackboard, RunContext
    from deep_research.observability import Tracer
    from deep_research.workbench import analysis
    from deep_research.workbench.contract import build_contract
    from deep_research.workbench.templates import get_template
    from tests.fakes import FakeSearch
    from tests.test_workbench import WorkbenchLLM

    settings.quality = {"max_revisions": 0, "register_check": False, "require_limitations": False}
    query = "描述数据\nmethod,value\nA,1\nA,2\nB,3\nB,4\n"
    contract = build_contract(get_template("dataAnalysis"), query, quality=settings.quality)
    draft = analysis.fallback_report(analysis.analyse(contract.dataset_csv, contract.focus))
    initial = Blackboard(query=query, scratch={"task_contract": contract.model_dump(mode="json")})
    calls = []

    class Model(WorkbenchLLM):
        async def parse(self, system, user, schema, **kwargs):
            calls.append(schema.__name__)
            return await super().parse(system, user, schema, **kwargs)

    def context():
        return RunContext(
            llm=Model(draft),
            search_tool=FakeSearch(),
            tracer=Tracer(),
            settings=settings,
            artifact_store=ArtifactStore(tmp_path),
            run_id="analysis",
        )

    original = analysis.analyse

    def interrupted(*args, **kwargs):
        raise RuntimeError("process stopped after scope selection")

    monkeypatch.setattr(analysis, "analyse", interrupted)
    with pytest.raises(RuntimeError):
        await analysis.DataAnalyst().step(initial.model_copy(deep=True), context())
    monkeypatch.setattr(analysis, "analyse", original)
    restored = await analysis.DataAnalyst().step(initial.model_copy(deep=True), context())
    assert calls.count("AnalysisScope") == 1 and restored.report


async def test_stricter_intent_evidence_policy_does_not_reuse_weaker_progress(tmp_path, settings):
    from deep_research.agents.base import Blackboard, RunContext
    from deep_research.observability import Tracer
    from deep_research.workbench.writing_progress import for_writer
    from tests.fakes import FakeLLM, FakeSearch

    settings.require_corroboration = False
    bb = Blackboard(query="q")
    ctx = RunContext(
        llm=FakeLLM(),
        search_tool=FakeSearch(),
        tracer=Tracer(),
        settings=settings,
        artifact_store=ArtifactStore(tmp_path),
        run_id="policy",
    )
    calls = []

    async def write(revision):
        calls.append(revision)
        return "Draft"

    first = for_writer(bb, ctx, "research_writer", "system")
    await write_with_revisions(write, lambda _: Assessment(), max_revisions=0, progress=first)
    bb.scratch["intent_execution_policy"] = {"requires_corroboration": True}
    second = for_writer(bb, ctx, "research_writer", "system")
    assert first.scope != second.scope
    await write_with_revisions(write, lambda _: Assessment(), max_revisions=0, progress=second)
    assert len(calls) == 2
