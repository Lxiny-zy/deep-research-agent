from __future__ import annotations

from pathlib import Path

import pytest

from deep_research.orchestration.compiler import PlanCompileError, PlanCompiler
from deep_research.skills import SkillResolver


def _plan(*steps: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "slug": "compiler-demo",
        "title": "Compiler demo",
        "steps": list(steps),
    }


def _step(step_id: str, **overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "id": step_id,
        "name": step_id.replace("-", " "),
        "prompt": f"Run {step_id}.",
    }
    value.update(overrides)
    return value


def _resolver(tmp_path: Path) -> SkillResolver:
    skill = tmp_path / ".claude" / "skills" / "academic-search"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: academic-search\ndescription: Search papers\n---\nUse trusted sources.\n",
        encoding="utf-8",
    )
    return SkillResolver((tmp_path / ".claude" / "skills",))


def test_compiler_maps_linear_steps_and_preserves_artifact_metadata(tmp_path: Path) -> None:
    resolver = _resolver(tmp_path)
    plan = _plan(
        _step(
            "collect-evidence",
            skills=["academic-search"],
            prompt="Read `.claude/skills/academic-search/SKILL.md` and collect evidence.",
            artifacts=["work/compiler-demo/explore/sources.md"],
        ),
        _step("write-report", artifacts=["output/compiler-demo/final/report.md"]),
    )

    compiled = PlanCompiler(
        available_agents={"planner", "researcher", "synthesizer", "operation_runner"},
        skill_resolver=resolver,
    ).compile(plan)

    assert compiled.workflow.name == "plan-compiler-demo"
    assert [step.agent for step in compiled.workflow.steps] == ["planner", "synthesizer"]
    assert [(edge["source"], edge["target"]) for edge in compiled.workflow.edges] == [
        ("node-collect-evidence", "node-write-report")
    ]
    assert compiled.step_mapping == {
        "collect-evidence": "node-collect-evidence",
        "write-report": "node-write-report",
    }
    assert compiled.workflow.steps[0].metadata["expected_outputs"] == [
        "work/compiler-demo/explore/sources.md"
    ]


def test_compiler_uses_explicit_agents_and_operation_runner() -> None:
    compiled = PlanCompiler(
        available_agents={"planner", "researcher", "synthesizer", "operation_runner"}
    ).compile(
        _plan(
            _step("prepare", operation={"kind": "agent", "agent": "researcher"}),
            _step("extract", operation={"kind": "pdf.extract"}),
            _step("finish"),
        )
    )

    assert [step.agent for step in compiled.workflow.steps] == [
        "researcher",
        "operation_runner",
        "synthesizer",
    ]
    assert "operations" not in compiled.workflow.steps[0].metadata
    assert compiled.workflow.steps[1].metadata["operations"][0]["kind"] == "pdf.extract"


def test_compiler_builds_explicit_dependency_dag() -> None:
    compiled = PlanCompiler(available_agents={"planner", "researcher", "synthesizer"}).compile(
        _plan(
            _step("collect-a"),
            _step("collect-b", depends_on=[]),
            _step("synth", depends_on=["collect-a", "collect-b"]),
        )
    )

    assert {(edge["source"], edge["target"]) for edge in compiled.workflow.edges} == {
        ("node-collect-a", "node-synth"),
        ("node-collect-b", "node-synth"),
    }


def test_compiler_rejects_missing_or_implicit_skill_references(tmp_path: Path) -> None:
    resolver = _resolver(tmp_path)
    compiler = PlanCompiler(
        available_agents={"planner", "researcher", "synthesizer"},
        skill_resolver=resolver,
    )

    with pytest.raises(PlanCompileError, match="invalid skill references"):
        compiler.compile(_plan(_step("search", skills=["missing-skill"])))

    with pytest.raises(PlanCompileError, match="explicitly reference"):
        compiler.compile(_plan(_step("search", skills=["academic-search"])))


def test_compiler_rejects_command_attached_to_agent_selector() -> None:
    compiler = PlanCompiler(
        available_agents={"planner", "researcher", "synthesizer", "operation_runner"}
    )

    with pytest.raises(PlanCompileError, match="agent selector"):
        compiler.compile(
            _plan(
                _step(
                    "unsafe",
                    operation={
                        "kind": "agent",
                        "agent": "researcher",
                        "command": "rm -rf work",
                    },
                )
            )
        )
