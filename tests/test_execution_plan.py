from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from deep_research.orchestration.plan import (
    ArtifactSpec,
    ExecutionPlan,
    GPUKind,
    PlanStatus,
    StepStatus,
    parse_plan,
    validate_execution_plan,
    validate_relative_path,
)
from deep_research.planning import ExecutionPlan as CanonicalExecutionPlan


def _step(step_id: str, **overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "id": step_id,
        "name": step_id.replace("-", " "),
        "prompt": f"run {step_id}",
    }
    value.update(overrides)
    return value


def _plan(*steps: dict[str, object], **overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": 1,
        "slug": "demo-run",
        "title": "Demo",
        "steps": list(steps),
    }
    value.update(overrides)
    return value


def test_orchestration_module_is_a_facade_for_canonical_models() -> None:
    assert ExecutionPlan is CanonicalExecutionPlan

    plan = validate_execution_plan(
        _plan(
            _step(
                "collect-evidence",
                skills=["academic-search"],
                artifacts=["work/demo-run/explore/sources.md"],
            ),
            _step("write-report", depends_on=["collect-evidence"]),
        )
    )
    assert plan.step_ids == ("collect-evidence", "write-report")
    assert plan.topological_order() == ["collect-evidence", "write-report"]
    assert plan.steps[0].artifacts[0].path == "work/demo-run/explore/sources.md"
    assert plan.status == PlanStatus.PENDING
    assert all(step.status == StepStatus.PENDING for step in plan.steps)


@pytest.mark.parametrize(
    "bad_id", ["Collect-Evidence", "collect_evidence", "-leading", "trailing-"]
)
def test_plan_rejects_non_kebab_step_ids(bad_id: str) -> None:
    with pytest.raises(ValidationError, match="kebab-case"):
        ExecutionPlan.model_validate(_plan(_step(bad_id)))


def test_plan_rejects_duplicate_ids_unknown_dependencies_and_cycles() -> None:
    with pytest.raises(ValidationError, match="unique"):
        ExecutionPlan.model_validate(_plan(_step("same"), _step("same")))

    with pytest.raises(ValidationError, match="unknown"):
        ExecutionPlan.model_validate(_plan(_step("a", depends_on=["missing"])))

    with pytest.raises(ValidationError, match="cycle"):
        ExecutionPlan.model_validate(
            _plan(_step("a", depends_on=["b"]), _step("b", depends_on=["a"]))
        )


def test_initial_validator_requires_pending_states_but_recovery_allows_persisted_state() -> None:
    raw = _plan(_step("a", status="done"), status="running")
    recovered = validate_execution_plan(raw, initial=False)
    assert recovered.status == PlanStatus.RUNNING
    assert recovered.steps[0].status == StepStatus.DONE

    with pytest.raises(ValueError, match="pending"):
        validate_execution_plan(raw)

    with pytest.raises(ValidationError, match="initial plan"):
        ExecutionPlan.model_validate(_plan(_step("a", status="done")))


def test_resource_gpu_profile_and_operation_contract_are_strict() -> None:
    from deep_research.planning import OperationSpec, ResourceSpec

    assert ResourceSpec(gpu=GPUKind.T4, gpu_count=1).gpu == GPUKind.T4

    with pytest.raises(ValidationError, match="gpu_count"):
        ResourceSpec(gpu="t4", gpu_count=0)
    with pytest.raises(ValidationError, match="requires command"):
        ExecutionPlan.model_validate(_plan(_step("run", operation={"kind": "command"})))

    operation = OperationSpec(kind="pdf.extract")
    assert operation.kind == "pdf.extract"


def test_operation_id_alias_is_reexported_by_orchestration_facade() -> None:
    plan = ExecutionPlan.model_validate(_plan(_step("convert", operation="pdf.convert")))
    assert plan.steps[0].operation.operation_id == "pdf.convert"


@pytest.mark.parametrize(
    "unsafe",
    [
        "../escape.txt",
        "work/../escape.txt",
        "/tmp/result.md",
        "C:\\tmp\\result.md",
        "\\\\server\\share",
        ".vela/plan.json",
        "tmp/result",
    ],
)
def test_artifact_paths_are_workspace_relative_and_not_control_paths(unsafe: str) -> None:
    with pytest.raises((ValueError, ValidationError)):
        validate_relative_path(unsafe)
    with pytest.raises(ValidationError):
        ArtifactSpec(path=unsafe)


def test_artifact_string_shorthand_normalizes_windows_separators() -> None:
    artifact = ArtifactSpec(path="work\\demo-run\\final\\report.md")
    assert artifact.path == "work/demo-run/final/report.md"


def test_unknown_fields_and_bad_schema_version_are_rejected() -> None:
    with pytest.raises(ValidationError):
        ExecutionPlan.model_validate(_plan(_step("a"), unexpected=True))
    with pytest.raises(ValidationError, match="schema_version"):
        ExecutionPlan.model_validate(_plan(_step("a"), schema_version=2))


def test_json_round_trip_accepts_mapping_and_json_string() -> None:
    raw = _plan(_step("a"))
    plan = parse_plan(json.dumps(raw))
    assert plan.model_dump(mode="json")["slug"] == "demo-run"
