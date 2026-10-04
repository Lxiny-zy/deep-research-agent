"""Blocking delivery defects receive one durable repair round before review."""

import pytest

from deep_research.agents.base import Blackboard
from deep_research.orchestrator import DeepResearchAgent, create_initial_execution
from deep_research.persistence.memory_repository import InMemoryRepository
from deep_research.workbench.contract import CONTRACT_SCRATCH_KEY, build_contract
from deep_research.workbench.templates import get_template
from deep_research.workflow import Step, Workflow
from tests.test_prose_review import CorrelationSearch, Judge, findings

GOOD = "变量存在相关关系，不能据此证明因果关系 [1]。"
BAD = "变量已经证明因果关系 [1]。"


class RepairModel(Judge):
    def __init__(self, bodies):
        super().__init__()
        self.bodies = list(bodies)

    async def stream(self, *args, **kwargs):
        self.stream_calls += 1
        yield self.bodies[min(self.stream_calls - 1, len(self.bodies) - 1)]


async def run_fixture(
    settings, *, broken=True, repaired=True, formats=None, short=False, repo=None
):
    settings.quality = {"max_revisions": 0, "register_check": False, "require_limitations": False}
    settings.intent_enabled = False
    query = "研究变量关系，不需要配套图示"
    template = get_template("autoResearch")
    repeats = max(2, 2 * template.min_length // (len(GOOD) * len(template.sections)) + 2)
    complete = "\n\n".join(
        f"## {section.title}\n\n" + GOOD * repeats for section in template.sections
    )
    bad = (
        "\n\n".join(f"## {section.title}\n\n" + GOOD for section in template.sections)
        if short
        else complete + "\n\n" + BAD
        if broken
        else complete
    )
    model = RepairModel([bad, complete if repaired else bad])
    contract = build_contract(template, query, quality=settings.quality)
    contract = contract.model_copy(update={"deliverables": formats or ["md"]})
    workflow = Workflow(name="completion_fixture", steps=[Step(agent="research_writer")])
    execution = create_initial_execution(query, workflow.name, settings)
    execution.definition = workflow.model_dump(mode="json")
    execution.checkpoint = Blackboard(
        query=query,
        results=findings(),
        scratch={CONTRACT_SCRATCH_KEY: contract.model_dump(mode="json")},
    ).model_dump(mode="json")
    repo = repo or InMemoryRepository()
    run_id = await repo.create_run(query, execution=execution)
    agent = DeepResearchAgent(
        settings,
        llm=model,
        search_tool=CorrelationSearch(),
        workflow=workflow.name,
        repo=repo,
        run_id=run_id,
        initial_execution=execution,
    )
    await agent.run(query)
    return repo, run_id, model, agent


async def test_content_is_repaired_before_terminal_needs_review(settings):
    repo, run_id, model, _ = await run_fixture(settings)
    detail = await repo.get_run(run_id)
    assert detail.status == "done", detail.completion
    assert model.stream_calls == 2 and BAD not in detail.report.markdown
    state = detail.orchestration.checkpoint["scratch"]["_completion_repair"]
    assert state["status"] == "complete" and state["content"]["attempts"] == 1
    assert detail.orchestration.workflow_name == "completion_fixture"


async def test_unresolved_content_stops_after_one_additional_round(settings):
    repo, run_id, model, _ = await run_fixture(settings, repaired=False)
    detail = await repo.get_run(run_id)
    assert detail.status == "needs_review"
    assert model.stream_calls == 2
    assert detail.orchestration.checkpoint["scratch"]["_completion_repair"]["status"] == "complete"


async def test_verified_content_does_not_trigger_a_repair_for_advice(settings):
    repo, run_id, model, _ = await run_fixture(settings, broken=False)
    detail = await repo.get_run(run_id)
    assert detail.status == "done", detail.completion
    assert model.stream_calls == 1


async def test_delivery_length_feedback_reaches_the_additional_revision(settings):
    repo, run_id, model, _ = await run_fixture(settings, broken=False, short=True)
    assert (await repo.get_run(run_id)).status == "done"
    assert model.stream_calls == 2


@pytest.mark.parametrize("recovers", [True, False])
async def test_failed_format_gets_exactly_one_retry_without_rewriting(
    settings, monkeypatch, recovers
):
    from deep_research.workbench.delivery import pdf

    calls = []
    original = pdf.render_pdf

    def render(*args, **kwargs):
        calls.append(True)
        if len(calls) == 1 or not recovers:
            raise RuntimeError("temporary PDF failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(pdf, "render_pdf", render)
    repo, run_id, model, _ = await run_fixture(settings, broken=False, formats=["md", "pdf"])
    detail = await repo.get_run(run_id)
    assert detail.status == ("done" if recovers else "needs_review")
    assert len(calls) == 2 and model.stream_calls == 1
    state = detail.orchestration.checkpoint["scratch"]["_completion_repair"]
    assert state["formats"]["pdf"]["status"] == "complete"


class LostSave(InMemoryRepository):
    def __init__(self, phase):
        super().__init__()
        self.phase = phase
        self.failed = False

    async def save_orchestration(self, run_id, execution, **kwargs):
        state = execution.checkpoint.get("scratch", {}).get("_completion_repair", {})
        complete = (
            state.get("content", {}).get("status") == "complete"
            if self.phase == "content"
            else state.get("formats", {}).get("pdf", {}).get("status") == "complete"
        )
        if complete and not self.failed:
            self.failed = True
            raise OSError("lost final repair checkpoint")
        return await super().save_orchestration(run_id, execution, **kwargs)


class NoCalls(RepairModel):
    async def stream(self, *args, **kwargs):
        raise AssertionError("completed writing must not run again")
        yield ""

    async def parse(self, *args, **kwargs):
        raise AssertionError("completed verification must not run again")


@pytest.mark.parametrize("phase", ["content", "format"])
async def test_recovery_reuses_completed_repair_after_lost_checkpoint(settings, monkeypatch, phase):
    from deep_research.workbench.delivery import pdf

    calls = []
    original = pdf.render_pdf

    def render(*args, **kwargs):
        calls.append(True)
        if len(calls) == 1:
            raise RuntimeError("temporary PDF failure")
        return original(*args, **kwargs)

    if phase == "format":
        monkeypatch.setattr(pdf, "render_pdf", render)
    repo = LostSave(phase)
    with pytest.raises(OSError, match="lost final repair checkpoint"):
        await run_fixture(
            settings,
            broken=phase == "content",
            formats=["md", "pdf"] if phase == "format" else ["md"],
            repo=repo,
        )
    run_id = next(iter(repo._runs))
    detail = await repo.get_run(run_id)
    agent = DeepResearchAgent(
        settings,
        llm=NoCalls([]),
        search_tool=CorrelationSearch(),
        repo=repo,
        run_id=run_id,
        resume_execution=detail.orchestration,
    )
    await agent.run(detail.query)
    resumed = await repo.get_run(run_id)
    assert resumed.status == "done", (
        resumed.orchestration.checkpoint["scratch"].get("_completion", {}).get("issues")
    )
    assert resumed.orchestration.checkpoint["scratch"]["_completion_repair"]["status"] == "complete"
    if phase == "format":
        assert len(calls) == 2


async def test_exhausted_budget_does_not_launch_another_writer(settings, monkeypatch):
    from deep_research.token_budget import TokenBudgetExceeded
    from deep_research.workflow import WorkflowEngine

    async def exhausted(*args, **kwargs):
        raise TokenBudgetExceeded("budget exhausted")

    monkeypatch.setattr(WorkflowEngine, "repair_step", exhausted)
    repo, run_id, model, _ = await run_fixture(settings)
    detail = await repo.get_run(run_id)
    assert detail.status == "needs_review" and model.stream_calls == 1
    state = detail.orchestration.checkpoint["scratch"]["_completion_repair"]
    assert state["content"]["error"] == "TokenBudgetExceeded" and state["status"] == "complete"


async def test_unresolved_repair_is_not_repeated_when_the_process_resumes(settings):
    repo, run_id, model, _ = await run_fixture(settings, repaired=False)
    detail = await repo.get_run(run_id)
    resumed = DeepResearchAgent(
        settings,
        llm=NoCalls([]),
        search_tool=CorrelationSearch(),
        repo=repo,
        run_id=run_id,
        resume_execution=detail.orchestration,
    )
    await resumed.run(detail.query)
    assert (await repo.get_run(run_id)).status == "needs_review"
    assert model.stream_calls == 2
