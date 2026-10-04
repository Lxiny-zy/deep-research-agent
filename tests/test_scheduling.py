"""Work-conserving identity credits, priority aging and protected light capacity."""

from collections import Counter

import pytest

from deep_research.orchestration import OrchestrationRuntime
from deep_research.scheduling import (
    ActiveRun,
    IdentityCredit,
    ScheduleCandidate,
    SchedulerState,
    eligible_candidates,
    schedule_for_execution,
    select_next,
    validate_priority,
)


def candidate(run_id, identity, *, cost=1, kind="light", priority=1, ready_at=0):
    return ScheduleCandidate(run_id, identity, cost, kind, priority, ready_at)


def test_cost_fairness_charges_work_instead_of_task_count():
    queued = [candidate(f"heavy-{i:02}", "a", cost=4, kind="heavy") for i in range(20)]
    queued += [candidate(f"light-{i:02}", "b") for i in range(30)]
    state, credits = SchedulerState(), {}
    served = Counter()
    for _ in range(20):
        result = select_next(queued, credits, state, now=0)
        assert result is not None
        state, credits = result.state, result.credits
        served[result.candidate.identity] += result.candidate.cost
        queued = [run for run in queued if run.run_id != result.candidate.run_id]
    assert served == {"a": 16, "b": 16}


def test_priority_aging_promotes_old_work_above_continual_new_urgent_work():
    old = candidate("old", "a", priority=0)
    new = candidate("new", "a", priority=2, ready_at=49)
    result = select_next([old, new], {}, SchedulerState(), now=60)
    assert result.candidate == old
    assert select_next([old, new], {}, SchedulerState(), now=59).candidate == new


def test_lone_maximum_cost_task_can_start_without_waiting_for_poll_ticks():
    task = candidate("heavy", "a", cost=8, kind="heavy")
    credits = {"a": IdentityCredit()}
    state = SchedulerState()
    result = select_next([task], credits, state, now=10)
    assert result.candidate == task
    assert result.credits["a"].deficit == 0
    assert credits == {"a": IdentityCredit()} and state == SchedulerState()


def test_light_capacity_is_independent_of_the_same_identity_heavy_allotment():
    heavy = candidate("next-heavy", "a", kind="heavy", cost=4)
    light = candidate("next-light", "a")
    assert eligible_candidates([heavy, light], [ActiveRun("a", "heavy")], 2) == [light]
    assert eligible_candidates([heavy], [], 1) == [heavy]
    assert not eligible_candidates([light], [ActiveRun("a", "heavy")], 1)


def test_per_identity_heavy_limit_keeps_room_for_another_identity():
    active = [ActiveRun("a", "heavy"), ActiveRun("a", "heavy")]
    own = candidate("own", "a", kind="heavy", cost=4)
    other = candidate("other", "b", kind="heavy", cost=4)
    assert eligible_candidates([own, other], active, 4) == [other]


def test_schedule_estimate_uses_frozen_tier_parallelism_and_explicit_long_window():
    execution = OrchestrationRuntime().start("quick", {"query": "Q"})
    assert schedule_for_execution(execution).kind == "light"
    execution.checkpoint = {
        "scratch": {"_run_settings": {"research_tier": "deep", "max_concurrency": 4}}
    }
    assert schedule_for_execution(execution).cost == 8
    assert schedule_for_execution(execution).kind == "heavy"
    execution.checkpoint = {"scratch": {"_run_settings": {"max_run_seconds": 43200}}}
    assert schedule_for_execution(execution).kind == "heavy"


@pytest.mark.parametrize(
    "workflow",
    [
        "research",
        "research_quick",
        "research_deep",
        "survey_quick",
        "paper_read",
        "custom_quick",
        "custom_qa",
        "QUICK",
    ],
)
@pytest.mark.parametrize("seconds", [0, 1, 1800])
def test_light_tier_and_small_timeout_cannot_disguise_long_or_unknown_workflows(workflow, seconds):
    execution = OrchestrationRuntime().start(workflow, {"query": "Q"})
    execution.checkpoint = {
        "scratch": {"_run_settings": {"research_tier": "light", "max_run_seconds": seconds}}
    }
    schedule = schedule_for_execution(execution)
    assert schedule.kind == "heavy"
    assert schedule.cost == 1
    queued = candidate("queued", "a", cost=schedule.cost, kind=schedule.kind)
    assert eligible_candidates([queued], [ActiveRun("a", "heavy")], 2) == []


@pytest.mark.parametrize("workflow", ["qa", "paper_qa", "quick"])
def test_only_explicit_short_workflows_get_the_light_resource_class(workflow):
    execution = OrchestrationRuntime().start(workflow, {"query": "Q"})
    execution.checkpoint = {"scratch": {"_run_settings": {"research_tier": "light"}}}
    assert schedule_for_execution(execution).kind == "light"


def test_light_tier_reduces_research_cost_without_reducing_its_resource_class():
    execution = OrchestrationRuntime().start("research_quick", {"query": "Q"})
    execution.checkpoint = {"scratch": {"_run_settings": {"research_tier": "deep"}}}
    deep = schedule_for_execution(execution)
    execution.checkpoint["scratch"]["_run_settings"]["research_tier"] = "light"
    light = schedule_for_execution(execution)
    assert deep.kind == light.kind == "heavy"
    assert light.cost < deep.cost


def test_canonical_builtin_quick_without_external_contract_stays_light():
    from deep_research.config import Settings
    from deep_research.orchestrator import create_initial_execution
    from deep_research.workflows import get_workflow

    execution = create_initial_execution(
        "Q",
        "quick",
        Settings(orchestration_mode="legacy", research_tier="light", max_run_seconds=1),
    )
    assert execution.definition == get_workflow("quick").model_dump(mode="json")
    assert schedule_for_execution(execution).kind == "light"


def test_quick_name_with_a_real_supplied_execution_plan_is_heavy():
    from deep_research.config import Settings
    from deep_research.orchestrator import create_initial_execution
    from deep_research.planner_runtime import PLAN_SCRATCH_KEY, build_execution_plan
    from deep_research.workflows import get_workflow

    plan = build_execution_plan("Full external study", get_workflow("deep"))
    execution = create_initial_execution(
        "Q", "quick", Settings(research_tier="light", max_run_seconds=1), execution_plan=plan
    )
    assert PLAN_SCRATCH_KEY in execution.checkpoint["scratch"]
    assert schedule_for_execution(execution).kind == "heavy"


def test_quick_name_with_a_frozen_workbench_contract_is_heavy():
    from deep_research.config import Settings
    from deep_research.orchestrator import create_initial_execution
    from deep_research.workbench.contract import CONTRACT_SCRATCH_KEY, build_contract
    from deep_research.workbench.templates import AUTO_RESEARCH

    execution = create_initial_execution(
        "Q", "quick", Settings(research_tier="light", max_run_seconds=1)
    )
    execution.checkpoint["scratch"][CONTRACT_SCRATCH_KEY] = build_contract(
        AUTO_RESEARCH, "Q", strategy="quick"
    ).model_dump(mode="json")
    assert schedule_for_execution(execution).kind == "heavy"


@pytest.mark.parametrize("custom", ["steps", "dag"])
def test_quick_name_cannot_hide_a_custom_workflow_definition(custom):
    from deep_research.config import Settings
    from deep_research.orchestrator import create_initial_execution
    from deep_research.workflow import Workflow
    from deep_research.workflows import get_workflow

    execution = create_initial_execution(
        "Q", "quick", Settings(research_tier="light", max_run_seconds=1)
    )
    if custom == "steps":
        definition = get_workflow("deep").model_copy(update={"name": "quick"}, deep=True)
    else:
        definition = Workflow(
            name="quick",
            nodes=[
                {"id": "research", "type": "agent", "data": {"agent": "researcher"}},
                {"id": "report", "type": "agent", "data": {"agent": "synthesizer"}},
            ],
            edges=[{"source": "research", "target": "report"}],
        )
    execution.definition = definition.model_dump(mode="json")
    assert schedule_for_execution(execution).kind == "heavy"


@pytest.mark.parametrize("value", [None, {}])
def test_even_empty_external_plan_marker_cannot_grant_light_capacity(value):
    from deep_research.planner_runtime import PLAN_SCRATCH_KEY

    execution = OrchestrationRuntime().start("quick", {"query": "Q"})
    execution.checkpoint = {"scratch": {PLAN_SCRATCH_KEY: value}}
    assert schedule_for_execution(execution).kind == "heavy"


@pytest.mark.parametrize("priority", [-1, 3, True, 1.5, "1"])
def test_invalid_priority_cannot_enter_the_queue(priority):
    with pytest.raises(ValueError, match="schedule_priority"):
        validate_priority(priority)
