"""Regression coverage for task completion, checkpointing and bounded dispatch."""

from dataclasses import replace

import pytest

from deep_research.agents.base import Blackboard, RunContext
from deep_research.agents.reflector import Reflector
from deep_research.artifacts import ArtifactStore
from deep_research.execution_policy import TaskYield, resource_wait_checkpoint, start_window
from deep_research.models import Reflection, SubQuestion
from deep_research.observability import Tracer
from deep_research.orchestrator import create_initial_execution
from deep_research.provider_limits import ProviderBusyError
from deep_research.scheduling import schedule_for_execution
from deep_research.workbench.contract import build_contract
from deep_research.workbench.coverage import CoverageGaps
from deep_research.workbench.templates import get_template
from deep_research.workbench.tiers import default_tier, tier_overrides
from deep_research.workflow import Step, Workflow, WorkflowEngine
from tests.fakes import FakeLLM, FakeSearch
from tests.test_content_revision import failed_parent
from tests.test_content_revision import service as service
from tests.test_research_progress import result


def workbench(settings, key, *, strategy="none", text="Q", dataset=""):
    template = get_template(key)
    settings = replace(settings, research_tier="light", max_run_seconds=0,
                       **tier_overrides("light", explicit={}, ceilings={}))
    execution = create_initial_execution(text, template.workflow_for(strategy), settings)
    execution.checkpoint["scratch"]["task_contract"] = build_contract(
        template, text, tier="light", strategy=strategy, attachments_csv=dataset
    ).model_dump(mode="json")
    return execution


def test_measured_small_jobs_get_light_capacity_without_allowing_plan_spoofing(settings):
    small = workbench(settings, "dataAnalysis", dataset="x,y\n1,2\n3,4")
    assert schedule_for_execution(small).kind == "light"
    large = workbench(settings, "dataAnalysis", dataset="x,y\n" + "1,2\n" * 1000)
    assert schedule_for_execution(large).kind == "heavy"
    small.definition["steps"].append(Step(agent="researcher").model_dump(mode="json"))
    assert schedule_for_execution(small).kind == "heavy"
    external = workbench(settings, "mindmap", strategy="quick")
    external.checkpoint["scratch"]["_execution_plan"] = {}
    assert schedule_for_execution(external).kind == "heavy"
    assert schedule_for_execution(workbench(
        settings, "autoResearch", strategy="quick", text="解释一种方法"
    )).kind == "light"
    assert schedule_for_execution(workbench(
        settings, "paperRead", text="https://arxiv.org/abs/2501.12345"
    )).kind == "heavy"


def test_retrieval_defaults_agree_with_actual_rounds():
    assert default_tier("light", "deep") == "deep"
    assert tier_overrides(default_tier("light", "deep"), explicit={}, ceilings={})[
        "max_rounds"
    ] == 2
    assert default_tier("deep", "quick") == "light"
    assert default_tier("standard", "none") == "standard"


async def test_deterministic_gap_overrules_empty_sufficient_response(settings):
    class Model(FakeLLM):
        async def parse(self, *args, **kwargs):
            return Reflection(is_sufficient=True)

    response = await Reflector(Model(), Tracer(), settings).run(
        "Q", [], coverage=CoverageGaps(gaps=["缺少独立来源"])
    )
    assert not response.is_sufficient
    assert response.new_sub_questions and response.gaps == ["缺少独立来源"]


@pytest.mark.parametrize("failure", [ProviderBusyError(15), TimeoutError("network")])
async def test_reflection_recovery_reuses_completed_round_and_exact_pending_plan(
    settings, tmp_path, failure
):
    calls = []

    class Reflect:
        async def step(self, bb, ctx):
            question = f"gap-{len(bb.reflections) + 1}"
            calls.append("reflect:" + question)
            bb.reflections.append(Reflection(is_sufficient=False, new_sub_questions=[question]))
            return bb

    class Research:
        failed = False

        async def step(self, bb, ctx):
            question = SubQuestion.model_validate(
                bb.scratch.pop("pending_sub_questions")[0]
            ).question
            calls.append("research:" + question)
            if question == "gap-2" and not self.failed:
                self.failed = True
                raise failure
            bb.results.append(result(question))
            return bb

    roles = {"reflector": Reflect(), "researcher": Research()}
    ctx = RunContext(llm=FakeLLM(), search_tool=FakeSearch(), tracer=Tracer(), settings=settings,
                     artifact_store=ArtifactStore(tmp_path), run_id="reflection-run")
    wf = Workflow(name="test", steps=[Step(kind="reflect_loop", max_rounds=2)])
    with pytest.raises(type(failure)):
        await WorkflowEngine(ctx, resolver=roles.__getitem__, recover_transient=True).run(
            wf, Blackboard(query="Q")
        )
    restored = Blackboard(query="Q")
    await WorkflowEngine(ctx, resolver=roles.__getitem__, recover_transient=True).run(wf, restored)
    assert calls == ["reflect:gap-1", "research:gap-1", "reflect:gap-2",
                     "research:gap-2", "research:gap-2"]
    assert [r.sub_question for r in restored.results] == ["gap-1", "gap-2"]


async def test_stage_yield_preserves_success_and_does_not_spend_failure_budget(settings):
    settings = replace(settings, run_slice_seconds=1)
    calls, saved = [], []

    class Role:
        def __init__(self, name):
            self.name = name

        async def step(self, bb, ctx):
            calls.append(self.name)
            bb.scratch[self.name] = True
            return bb

    async def checkpoint(run):
        saved.append(run.model_copy(deep=True))

    ctx = RunContext(llm=FakeLLM(), search_tool=FakeSearch(), tracer=Tracer(), settings=settings)
    wf = Workflow(name="slice", steps=[Step(agent="one"), Step(agent="two")])
    engine = WorkflowEngine(ctx, resolver=Role, checkpoint_sink=checkpoint, recover_transient=True)
    engine._slice_started -= 10  # Deterministic even with Windows coarse clock ticks.
    with pytest.raises(TaskYield):
        await engine.run(wf, Blackboard(query="Q"))
    assert calls == ["one"] and saved[-1].steps[0].status == "succeeded"
    start_window(saved[-1], 100, 200)
    deadline = saved[-1].checkpoint["scratch"]["_deadline_at"]
    queued, reason = resource_wait_checkpoint(
        saved[-1], settings, retry_after=1, elapsed=1, reason="stage_yield"
    )
    assert reason == "scheduled" and "_recovery" not in queued.checkpoint["scratch"]
    assert queued.checkpoint["scratch"]["_deadline_at"] == deadline
    await WorkflowEngine(ctx, resolver=Role, resume_run=queued, recover_transient=True).run(
        wf, Blackboard.model_validate(queued.checkpoint)
    )
    assert calls == ["one", "two"]


async def test_research_followup_reuses_evidence_and_keeps_closed_material_scope(settings):
    from deep_research.models import Report
    from deep_research.persistence.repository import RunDetail
    from deep_research.workbench.content_revision import source_version
    from deep_research.workbench.gates import citation_gate
    from deep_research.workbench.research_continuation import (
        continuation_offer,
        prepare_continuation,
    )

    execution = workbench(settings, "autoResearch", strategy="quick")
    detail = RunDetail(
        id="parent", query="Q", status="needs_review", orchestration=execution,
        report=Report(query="Q", markdown="Result [1]", citations=["https://a.com"]),
        results=[result("known")],
    )
    gate = citation_gate("Result [1]", ["https://a.com"], get_template("autoResearch"), 3)
    offer = continuation_offer(detail, [gate])
    assert offer["available"]
    original = source_version(detail)
    child, current = await prepare_continuation(detail, settings, offer["gaps"])
    assert source_version(detail) == original
    assert child.checkpoint["report"] is None and child.checkpoint["results"]
    assert child.checkpoint["scratch"]["research_continuation"]["parent_run_id"] == "parent"
    assert any(step["kind"] == "reflect_loop" for step in child.definition["steps"])
    assert current.max_rounds == 2
    detail.orchestration = workbench(settings, "paperRead", text="Paper text")
    child, _ = await prepare_continuation(detail, settings, ["补读原文"])
    assert not any(step["agent"] == "researcher" for step in child.definition["steps"])
    assert child.checkpoint["scratch"]["task_contract"]["strategy"] == "none"


async def test_research_followup_http_is_idempotent_and_version_bound(service, monkeypatch):
    from deep_research.workbench import api as workbench_api
    from deep_research.workbench.content_revision import source_version
    from deep_research.workbench.gates import citation_gate
    from tests.test_task_completion import bundle

    client, repo, settings = service
    parent = await failed_parent(repo, settings)
    value = bundle(parent)
    value.gates.append(citation_gate(
        "Result [1]", ["https://a.com"], get_template("autoResearch"), 3
    ))

    async def prepared_bundle(*args, **kwargs):
        return value

    monkeypatch.setattr(workbench_api, "_bundle", prepared_bundle)
    body = {"source_version": source_version(parent), "request_id": "research-intent",
            "mode": "research"}
    url = f"/api/runs/{parent.id}/revise"
    first = await client.post(url, json=body)
    assert first.status_code == 202, first.text
    repeated = await client.post(url, json=body)
    assert repeated.json() == first.json()
    conflict = await client.post(url, json={**body, "mode": "rewrite"})
    assert conflict.status_code == 409
    stale = await client.post(url, json={
        **body, "source_version": "0" * 64, "request_id": "stale-research",
    })
    assert stale.status_code == 409
    denied = await client.post(url, json={**body, "request_id": "reader-research"},
                               headers={"Authorization": "Bearer revision-reader"})
    assert denied.status_code in {403, 404}
    child = await repo.get_run(first.json()["run_id"])
    assert child.orchestration.checkpoint["scratch"]["research_continuation"][
        "parent_run_id"
    ] == parent.id
    assert source_version(await repo.get_run(parent.id)) == source_version(parent)

