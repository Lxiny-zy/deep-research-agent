from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from deep_research.planning import (
    ArtifactSpec,
    ExecutionPlan,
    GPUKind,
    PlanStatus,
    ResourceSpec,
    StepStatus,
    normalize_plan_payload,
    parse_execution_plan,
    parse_legacy_plan,
    stable_slug,
    validate_execution_plan,
    validate_relative_path,
)


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


def test_valid_plan_preserves_stable_ids_and_orders_dependencies() -> None:
    plan = validate_execution_plan(
        _plan(
            _step("collect-evidence", artifacts=["work/demo-run/explore/sources.md"]),
            _step("write-report", depends_on=["collect-evidence"]),
        )
    )

    assert plan.schema_version == 1
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
    assert ResourceSpec(gpu=GPUKind.T4, gpu_count=1).gpu == GPUKind.T4

    with pytest.raises(ValidationError, match="gpu_count"):
        ResourceSpec(gpu="t4", gpu_count=0)
    with pytest.raises(ValidationError, match="requires command"):
        ExecutionPlan.model_validate(_plan(_step("run", operation={"kind": "command"})))

    plan = ExecutionPlan.model_validate(
        _plan(
            _step(
                "run",
                operation={"kind": "command", "command": "python train.py"},
                resource={"gpu": "t4", "gpu_count": 1},
            ),
            resource_plan={
                "max_gpu": "t4",
                "max_gpu_count": 1,
                "needs_confirmation": True,
            },
        )
    )
    assert plan.steps[0].operation.command == "python train.py"


def test_operation_id_aliases_are_accepted_at_operation_and_step_levels() -> None:
    from deep_research.planning import OperationSpec

    nested = OperationSpec.model_validate({"operation": "pdf.convert"})
    assert nested.operation_id == "pdf.convert"
    assert nested.model_dump(mode="json")["operation_id"] == "pdf.convert"

    plan = ExecutionPlan.model_validate(_plan(_step("convert", operation_id="pdf.convert")))
    assert plan.steps[0].operation.operation_id == "pdf.convert"

    with pytest.raises(ValidationError, match="either operation or operation_id"):
        ExecutionPlan.model_validate(
            _plan(
                _step(
                    "ambiguous",
                    operation={"operation_id": "pdf.convert"},
                    operation_id="latex.compile",
                )
            )
        )


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
    plan = parse_execution_plan(json.dumps(raw))
    assert plan_to_dict(plan)["slug"] == "demo-run"


def test_legacy_vela_plan_gets_slug_and_rewritten_ids_and_outputs() -> None:
    legacy = {
        "title": "2026 用户态与内核态通信调研",
        "steps": [
            {"id": "__planner__", "name": "规划", "prompt": "plan"},
            {
                "id": "write_report",
                "name": "报告",
                "prompt": "write",
                "depends_on": ["__planner__"],
                "output_paths": ["work/research/final/report.md"],
            },
        ],
    }

    plan = parse_legacy_plan(legacy)

    assert stable_slug(legacy["title"]).isascii()
    assert plan.slug == stable_slug(legacy["title"])
    assert plan.step_ids == ("planner", "write-report")
    assert plan.steps[1].depends_on == ["planner"]
    assert plan.steps[1].artifacts[0].path == "work/research/final/report.md"


def test_legacy_operation_inputs_outputs_are_normalized_and_validated() -> None:
    plan = parse_legacy_plan(
        {
            "title": "Operation compatibility",
            "steps": [
                {
                    "id": "convert",
                    "name": "Convert",
                    "prompt": "convert",
                    "operations": [
                        {
                            "name": "pdf.convert",
                            "input_paths": ["work/demo/source.pdf"],
                            "expected_outputs": ["output/demo/report.pdf"],
                        },
                        "pdf.validate",
                    ],
                }
            ],
        }
    )

    operation = plan.steps[0].operation
    assert operation.operation_id == "pdf.convert"
    assert operation.inputs[0].path == "work/demo/source.pdf"
    assert operation.outputs[0].path == "output/demo/report.pdf"
    assert plan.steps[0].metadata["operations"][1]["operation_id"] == "pdf.validate"


def test_normalize_plan_payload_does_not_mutate_input_and_supports_wrapper() -> None:
    raw = {
        "plan": {
            "title": "Wrapped",
            "steps": [{"id": "a", "name": "A", "prompt": "run"}],
        }
    }
    normalized = normalize_plan_payload(raw)
    assert raw == {
        "plan": {
            "title": "Wrapped",
            "steps": [{"id": "a", "name": "A", "prompt": "run"}],
        }
    }
    assert normalized["schema_version"] == 1
    assert normalized["slug"] == stable_slug("Wrapped")


def plan_to_dict(plan: ExecutionPlan) -> dict[str, object]:
    """Keep the test independent of Pydantic's enum serialization details."""

    return plan.model_dump(mode="json")
