"""Compatibility facade for the canonical execution-plan contract.

``deep_research.planning`` is the single source of truth for the planner wire
schema.  The orchestration package historically exposed plan symbols from
this submodule, so imports are kept stable here while avoiding a second set of
Pydantic models with subtly different validation rules.

New code should import from :mod:`deep_research.planning` directly.  The
aliases below intentionally retain the names used by early planner adapters
(``GpuKind``, ``PlanStep``, ``parse_plan`` and friends).
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any, TypeAlias

from ..planning import (
    SCHEMA_VERSION,
    Artifact,
    ArtifactSpec,
    ExecutionPlan,
    ExecutionPlanStatus,
    ExecutionResource,
    ExecutionStep,
    ExecutionStepStatus,
    GPUKind,
    Operation,
    OperationSpec,
    PlanStatus,
    PlanStepStatus,
    ResourcePlan,
    ResourceSpec,
    StepSpec,
    StepStatus,
    can_transition_step_status,
    load_execution_plan,
    make_slug,
    normalize_plan_payload,
    parse_execution_plan,
    parse_legacy_plan,
    plan_to_json,
    slugify,
    stable_slug,
    transition_step_status,
    validate_execution_plan,
    validate_identifier,
    validate_plan,
    validate_relative_path,
)

# Compatibility spellings used by the first planner adapter.
GpuKind = GPUKind
PlanStep = ExecutionStep
ArtifactRef: TypeAlias = str | ArtifactSpec


class NetworkProfile(StrEnum):
    """Legacy network-profile spelling retained for import compatibility.

    Network policy is enforced by ``CommandRunner`` and is not part of the
    canonical plan operation payload.  Keeping this enum here lets older
    callers validate configuration without reintroducing a second schema.
    """

    NONE = "none"
    RESTRICTED = "restricted"
    FULL = "full"


class PlanValidationError(ValueError):
    """Stable facade error for malformed JSON or convenience validation."""


def parse_plan(
    value: ExecutionPlan | Mapping[str, Any] | str,
) -> ExecutionPlan:
    """Parse a plan through the canonical validator.

    ``validate_execution_plan`` deliberately preserves Pydantic's detailed
    ``ValidationError`` for mappings and JSON.  This facade only translates
    malformed JSON decoding failures to the stable ``PlanValidationError``
    used by the old orchestration API.
    """

    return parse_execution_plan(value)


def validate_plan_json(value: str, *, initial: bool = False) -> ExecutionPlan:
    """Validate JSON text using the canonical schema."""

    return validate_execution_plan(value, initial=initial)


def load_plan(path: str) -> ExecutionPlan:
    """Compatibility alias for :func:`deep_research.planning.load_execution_plan`."""

    return load_execution_plan(path)


__all__ = [
    "SCHEMA_VERSION",
    "ArtifactRef",
    "ArtifactSpec",
    "Artifact",
    "ExecutionPlan",
    "ExecutionPlanStatus",
    "ExecutionResource",
    "ExecutionStep",
    "ExecutionStepStatus",
    "GPUKind",
    "GpuKind",
    "NetworkProfile",
    "Operation",
    "OperationSpec",
    "PlanStatus",
    "PlanStep",
    "PlanStepStatus",
    "PlanValidationError",
    "ResourcePlan",
    "ResourceSpec",
    "StepSpec",
    "StepStatus",
    "can_transition_step_status",
    "load_execution_plan",
    "load_plan",
    "make_slug",
    "normalize_plan_payload",
    "parse_execution_plan",
    "parse_legacy_plan",
    "parse_plan",
    "plan_to_json",
    "slugify",
    "stable_slug",
    "transition_step_status",
    "validate_execution_plan",
    "validate_identifier",
    "validate_plan",
    "validate_plan_json",
    "validate_relative_path",
]
