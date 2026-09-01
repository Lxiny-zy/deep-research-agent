from __future__ import annotations

import re

from deep_research.planner_runtime import build_execution_plan
from deep_research.workflow import Step, Workflow


def test_projection_keeps_declared_skills_in_canonical_field() -> None:
    workflow = Workflow(
        name="skill projection",
        steps=[Step(agent="researcher", metadata={"prompt": "search"})],
    )

    plan = build_execution_plan(
        "skill projection",
        workflow,
        skills_by_step={"step-1-researcher": ["academic-search"]},
    )

    assert plan.steps[0].skills == ["academic-search"]
    assert plan.steps[0].metadata["skills"] == ["academic-search"]


def test_projection_sanitizes_non_ascii_role_names_for_step_ids() -> None:
    workflow = Workflow(name="custom role", steps=[Step(agent="研究员")])

    plan = build_execution_plan("custom role", workflow)

    assert re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", plan.steps[0].id)
    assert plan.steps[0].id == "step-1-step"
