"""Durable execution-plan contract.

The Vela planner contract is deliberately small: a planner emits a list of
stable, independently executable steps and the worker persists their state.
This module contains the data contract and deterministic validation only.  It
does not schedule agents or perform any I/O, so it can be used by the API,
worker, and offline tests without pulling in the rest of the runtime.

``ExecutionPlan`` is a versioned wire model.  A freshly authored plan starts
with ``pending`` statuses; a recovered plan may be loaded with its persisted
``running``/terminal states.  Use :func:`validate_execution_plan` with
``initial=True`` at the planner boundary to enforce the former invariant.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from copy import deepcopy
from enum import StrEnum
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, ClassVar, Literal, TypeAlias, cast

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

SCHEMA_VERSION = 1
"""The only plan version understood by this runtime."""


_IDENTIFIER_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_OPERATION_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,63}$")
_DRIVE_RE = re.compile(r"^[a-zA-Z]:")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_RESERVED_ROOTS = {".vela", ".claude", ".git", "tmp", "temp"}


def stable_slug(topic: str, *, max_stem_length: int = 40) -> str:
    """Return the deterministic slug used to join artefacts across steps.

    The human-readable stem is bounded, while the digest prevents collisions
    between topics that happen to share the same truncated prefix.  The digest
    is based on the original topic (not the normalised stem), so repeated runs
    with the same request address the same workspace intentionally.
    """

    if not isinstance(topic, str) or not topic.strip():
        raise ValueError("topic must be a non-empty string")
    # Slugs are part of the persisted path contract and therefore must be
    # portable across POSIX/Windows workers.  Unicode titles are common in
    # research requests; transliterate what can be represented in ASCII and
    # use the digest as the collision-resistant identity when nothing remains.
    normalised = unicodedata.normalize("NFKD", topic.lower().strip())
    normalised = normalised.encode("ascii", "ignore").decode("ascii")
    normalised = re.sub(r"[^a-z0-9\s-]", "", normalised)
    stem = re.sub(r"[\s_]+", "-", normalised).strip("-")[:max_stem_length].rstrip("-")
    if not stem:
        stem = "run"
    digest = hashlib.sha1(topic.encode("utf-8")).hexdigest()[:8]
    return f"{stem}-{digest}"


# Common spellings used by callers and older integrations.
slugify = stable_slug
make_slug = stable_slug


class PlanStatus(StrEnum):
    """Lifecycle states represented in a planner plan."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"


class StepStatus(StrEnum):
    """Lifecycle state of an individual execution step."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"


# Names used by callers that prefer the more explicit spelling.
ExecutionPlanStatus = PlanStatus
ExecutionStepStatus = StepStatus
PlanStepStatus = StepStatus


class GPUKind(StrEnum):
    """GPU profiles from the original Vela resource envelope."""

    NONE = "none"
    T4 = "t4"


def _strict_config() -> ConfigDict:
    # Keep unknown fields out of the wire contract.  ``populate_by_name`` is
    # useful for accepting the small ``type`` aliases used by older plans.
    return ConfigDict(
        extra="forbid",
        populate_by_name=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )


def validate_identifier(value: str, *, field_name: str = "id", max_length: int = 80) -> str:
    """Validate and return a stable kebab-case identifier.

    IDs are intentionally ASCII-only.  Besides making references portable
    across filesystems, this prevents visually confusable path components.
    """

    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    value = value.strip()
    if not value:
        raise ValueError(f"{field_name} must not be empty")
    if len(value) > max_length:
        raise ValueError(f"{field_name} is too long (max {max_length})")
    if _IDENTIFIER_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be kebab-case")
    return value


def validate_relative_path(value: str, *, field_name: str = "path") -> str:
    """Validate a workspace-relative artifact path.

    Both POSIX and Windows spellings are checked because a plan may be created
    on one machine and executed on another.  The returned representation uses
    forward slashes, making hashes and comparisons deterministic.

    The function deliberately does not resolve against the current process
    directory.  Resolution would make validation dependent on deployment
    state and could turn a previously safe plan into a different target after
    a restart.
    """

    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    raw = value.strip()
    if not raw:
        raise ValueError(f"{field_name} must not be empty")
    if len(raw) > 512:
        raise ValueError(f"{field_name} is too long (max 512 characters)")
    if "\x00" in raw or _CONTROL_RE.search(raw):
        raise ValueError(f"{field_name} contains control characters")

    # Check both path grammars before normalising separators.  PureWindowsPath
    # catches drive letters and UNC paths that PurePosixPath would treat as
    # ordinary relative strings.
    posix = PurePosixPath(raw)
    windows = PureWindowsPath(raw)
    if posix.is_absolute() or windows.is_absolute() or windows.drive:
        raise ValueError(f"{field_name} must be workspace-relative")
    if raw.startswith(("/", "\\", "~")):
        raise ValueError(f"{field_name} must be workspace-relative")

    normalised = raw.replace("\\", "/")
    parts = normalised.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"{field_name} contains an unsafe path segment")
    if any(":" in part for part in parts):
        # Colons are not needed in workspace filenames and permit Windows ADS
        # or drive-like spellings on some hosts.
        raise ValueError(f"{field_name} contains an unsafe ':' segment")
    if parts[0].lower() in _RESERVED_ROOTS:
        raise ValueError(f"{field_name} targets a reserved control directory")
    return "/".join(parts)


class ArtifactSpec(BaseModel):
    """A file produced or consumed by a step."""

    model_config = _strict_config()

    path: str = Field(..., min_length=1, max_length=512)
    kind: str = Field(
        "output",
        min_length=1,
        max_length=32,
        validation_alias=AliasChoices("kind", "type"),
    )
    required: bool = True
    format: str = Field("", max_length=32)
    description: str = Field("", max_length=500)
    sha256: str = Field("", max_length=128)

    @model_validator(mode="before")
    @classmethod
    def _coerce_path_string(cls, value: Any) -> Any:
        # A short path-only form is convenient in planner output and remains
        # unambiguous: ``artifacts: ["work/run/report.md"]``.
        if isinstance(value, str):
            return {"path": value}
        return value

    @field_validator("path")
    @classmethod
    def _safe_path(cls, value: str) -> str:
        return validate_relative_path(value)

    @field_validator("kind")
    @classmethod
    def _kind_name(cls, value: str) -> str:
        value = value.strip().lower()
        if _OPERATION_RE.fullmatch(value) is None:
            raise ValueError("artifact kind must be a simple identifier")
        return value

    @field_validator("sha256")
    @classmethod
    def _hash_shape(cls, value: str) -> str:
        if value and re.fullmatch(r"[0-9a-fA-F]{64}", value) is None:
            raise ValueError("sha256 must be a 64-character hexadecimal digest")
        return value.lower()


class ResourceSpec(BaseModel):
    """Per-step execution resource request.

    ``gpu_count`` is coupled to ``gpu`` to prevent plans that silently ask a
    scheduler for an unsupported machine.  The optional CPU/memory fields are
    advisory and let deployments add capacity hints without changing the
    original ``none``/``t4`` contract.
    """

    model_config = _strict_config()

    gpu: GPUKind = GPUKind.NONE
    gpu_count: int = Field(0, ge=0, le=1)
    cpu: int | None = Field(None, ge=1, le=256)
    memory_mb: int | None = Field(None, ge=128, le=1_048_576)
    timeout_seconds: float | None = Field(None, gt=0, le=172_800)
    max_attempts: int = Field(1, ge=1, le=10)
    needs_confirmation: bool = False

    @model_validator(mode="before")
    @classmethod
    def _coerce_none(cls, value: Any) -> Any:
        return {} if value is None else value

    @model_validator(mode="after")
    def _gpu_count_matches_profile(self) -> ResourceSpec:
        expected = 1 if self.gpu == GPUKind.T4 else 0
        if self.gpu_count != expected:
            raise ValueError(f"gpu_count must be {expected} when gpu={self.gpu.value!r}")
        if self.gpu == GPUKind.NONE and self.needs_confirmation:
            # A CPU step never needs a GPU confirmation.  Keeping this
            # deterministic avoids a plan that advertises contradictory UI.
            raise ValueError("needs_confirmation requires a GPU resource")
        return self


class ResourcePlan(BaseModel):
    """Top-level resource envelope declared by the planner."""

    model_config = _strict_config()

    needs_confirmation: bool = False
    max_gpu: GPUKind = GPUKind.NONE
    max_gpu_count: int = Field(0, ge=0, le=1)
    note: str = Field("", max_length=500)

    @model_validator(mode="after")
    def _max_gpu_count_matches_profile(self) -> ResourcePlan:
        expected = 1 if self.max_gpu == GPUKind.T4 else 0
        if self.max_gpu_count != expected:
            raise ValueError(
                f"max_gpu_count must be {expected} when max_gpu={self.max_gpu.value!r}"
            )
        if self.max_gpu == GPUKind.NONE and self.needs_confirmation:
            raise ValueError("needs_confirmation requires a GPU resource")
        return self


class OperationSpec(BaseModel):
    """Declarative action a worker should perform for one step.

    The worker owns the actual implementation.  Keeping this as data makes a
    plan inspectable and lets deployments map ``kind`` to an agent, skill, or
    command without importing a particular scheduler here.
    """

    model_config = _strict_config()

    kind: str = Field(
        "agent",
        min_length=1,
        max_length=64,
        validation_alias=AliasChoices("kind", "type"),
    )
    # ``operation_id`` is the stable registry key understood by
    # ``CommandRunner``.  Older clients called the same field ``operation``;
    # accept that spelling at the wire boundary while emitting one canonical
    # name.  Keeping this separate from ``kind`` lets a plan retain a
    # human-readable operation kind alongside the executable registry id.
    operation_id: str = Field(
        "",
        max_length=64,
        validation_alias=AliasChoices("operation_id", "operation"),
    )
    agent: str = Field("", max_length=100)
    skill: str = Field("", max_length=100)
    command: str = Field("", max_length=2_000)
    args: dict[str, Any] = Field(default_factory=dict)
    # Declarative operations may consume/produce workspace artefacts.  These
    # fields are intentionally typed with ``ArtifactSpec`` so the same path
    # traversal and reserved-directory checks apply before a worker starts.
    # ``expected_outputs``/``input_paths`` aliases are normalised below for
    # payloads emitted by the original Vela adapter.
    inputs: list[ArtifactSpec] = Field(default_factory=list, max_length=100)
    outputs: list[ArtifactSpec] = Field(default_factory=list, max_length=100)
    timeout_seconds: float | None = Field(None, gt=0, le=172_800)
    max_attempts: int = Field(1, ge=1, le=10)

    @model_validator(mode="before")
    @classmethod
    def _coerce_kind_string(cls, value: Any) -> Any:
        if value is None:
            return {}
        if isinstance(value, str):
            # ``step.operation`` historically accepted a bare registry key.
            # Treating it as ``operation_id`` keeps that shorthand executable
            # without conflating it with the descriptive ``kind`` field.
            return {"operation_id": value}
        return value

    @model_validator(mode="before")
    @classmethod
    def _coerce_io_aliases(cls, value: Any) -> Any:
        """Accept Vela's plural/path aliases without accepting shell input.

        The normalised values remain ordinary artifact declarations and are
        validated by ``ArtifactSpec``.  Unknown operation keys are still
        rejected by the strict model configuration.
        """

        if not isinstance(value, Mapping):
            return value
        normalized = dict(value)
        for canonical, aliases in (
            ("inputs", ("input_paths", "input_artifacts")),
            ("outputs", ("output_paths", "expected_outputs", "output_artifacts")),
        ):
            if canonical not in normalized:
                for alias in aliases:
                    if alias in normalized:
                        normalized[canonical] = normalized.pop(alias)
                        break
            else:
                # Do not silently discard a second declaration.  Preserve it
                # as an extra key so the strict model reports the ambiguity.
                for alias in aliases:
                    if alias in normalized:
                        raise ValueError(f"provide either {canonical} or {alias}, not both")
        return normalized

    @field_validator("kind")
    @classmethod
    def _operation_name(cls, value: str) -> str:
        value = value.strip().lower()
        if _OPERATION_RE.fullmatch(value) is None:
            raise ValueError("operation kind must be a simple identifier")
        return value

    @field_validator("operation_id")
    @classmethod
    def _operation_id_name(cls, value: str) -> str:
        value = value.strip().lower()
        if value and _OPERATION_RE.fullmatch(value) is None:
            raise ValueError("operation_id must be a simple identifier")
        return value

    @field_validator("command")
    @classmethod
    def _command_shape(cls, value: str) -> str:
        if "\x00" in value or _CONTROL_RE.search(value):
            raise ValueError("operation command contains control characters")
        return value

    @model_validator(mode="after")
    def _command_required_for_command_kind(self) -> OperationSpec:
        if self.kind in {"command", "shell", "exec"} and not self.command.strip():
            raise ValueError(f"operation {self.kind!r} requires command")
        return self


class ExecutionStep(BaseModel):
    """One independently checkable plan step."""

    model_config = _strict_config()

    id: str = Field(..., min_length=1, max_length=80)
    name: str = Field(..., min_length=1, max_length=20)
    prompt: str = Field(..., min_length=1, max_length=200_000)
    operation: OperationSpec = Field(default_factory=OperationSpec)
    # Skills are explicit planner inputs.  The worker/SkillResolver validates
    # that each declared skill exists and is named in the step prompt; no
    # keyword-based auto-discovery is performed.
    skills: list[str] = Field(default_factory=list, max_length=32)
    depends_on: list[str] = Field(default_factory=list, max_length=30)
    resource: ResourceSpec = Field(default_factory=ResourceSpec)
    artifacts: list[ArtifactSpec] = Field(default_factory=list, max_length=100)
    reset: bool = True
    status: StepStatus = StepStatus.PENDING
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _coerce_optional_specs(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        # ``None`` is a common result of a partially generated planner JSON;
        # treat it like an omitted optional spec while retaining strict checks
        # for malformed non-object values.
        normalized = dict(value)
        # A few generic plan producers put the registry key directly on the
        # step instead of nesting an OperationSpec.  Normalize that shorthand
        # before strict field validation, and reject ambiguous declarations.
        if "operation_id" in normalized:
            if normalized.get("operation") not in (None, "", {}):
                raise ValueError("provide either operation or operation_id, not both")
            normalized["operation"] = {"operation_id": normalized.pop("operation_id")}
        if normalized.get("operation") is None:
            normalized.pop("operation", None)
        if normalized.get("resource") is None:
            normalized.pop("resource", None)
        return normalized

    @field_validator("id")
    @classmethod
    def _step_id(cls, value: str) -> str:
        return validate_identifier(value, field_name="step id")

    @field_validator("depends_on")
    @classmethod
    def _dependency_ids(cls, value: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for dependency in value:
            dependency = validate_identifier(dependency, field_name="dependency id")
            if dependency in seen:
                raise ValueError(f"duplicate dependency {dependency!r}")
            seen.add(dependency)
            result.append(dependency)
        return result

    @field_validator("skills")
    @classmethod
    def _skill_names(cls, value: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for skill in value:
            if not isinstance(skill, str):
                raise TypeError("skill name must be a string")
            skill = skill.strip().lower()
            if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", skill):
                raise ValueError("skill name must be a simple identifier")
            if skill in seen:
                raise ValueError(f"duplicate skill {skill!r}")
            seen.add(skill)
            result.append(skill)
        return result


class ExecutionPlan(BaseModel):
    """Versioned planner output and persisted execution state."""

    model_config = _strict_config()

    schema_version: Literal[1] = cast(Literal[1], SCHEMA_VERSION)
    slug: str = Field(..., min_length=1, max_length=80)
    title: str = Field("", max_length=200)
    steps: list[ExecutionStep] = Field(..., min_length=1, max_length=30)
    status: PlanStatus = PlanStatus.PENDING
    resource_plan: ResourcePlan = Field(default_factory=ResourcePlan)
    artifacts: list[ArtifactSpec] = Field(default_factory=list, max_length=200)
    metadata: dict[str, Any] = Field(default_factory=dict)

    # Kept as a class constant so callers can discover the graph limit without
    # importing a scheduler implementation.
    MAX_STEPS: ClassVar[int] = 30

    @model_validator(mode="before")
    @classmethod
    def _infer_resource_envelope(cls, value: Any) -> Any:
        """Fill the optional top-level envelope from per-step GPU requests.

        The original planner extension emits ``resource_plan`` explicitly,
        while older/basic plans only carry a step-level ``resource``.  Inferring
        the envelope in the latter case keeps those plans valid without
        weakening validation when an envelope was supplied by the planner.
        """

        if not isinstance(value, Mapping) or "resource_plan" in value:
            return value
        raw_steps = value.get("steps")
        if not isinstance(raw_steps, (list, tuple)):
            return value
        needs_gpu = False
        for raw_step in raw_steps:
            if isinstance(raw_step, ExecutionStep):
                raw_resource: Any = raw_step.resource
            elif isinstance(raw_step, Mapping):
                raw_resource = raw_step.get("resource")
            else:
                continue
            if isinstance(raw_resource, ResourceSpec):
                needs_gpu = raw_resource.gpu == GPUKind.T4
            elif isinstance(raw_resource, Mapping):
                needs_gpu = raw_resource.get("gpu") == GPUKind.T4.value
            if needs_gpu:
                break
        if not needs_gpu:
            return value
        enriched = dict(value)
        enriched["resource_plan"] = {
            "needs_confirmation": True,
            "max_gpu": GPUKind.T4.value,
            "max_gpu_count": 1,
            "note": "inferred from a step-level GPU request",
        }
        return enriched

    @field_validator("schema_version")
    @classmethod
    def _schema_version(cls, value: int) -> Literal[1]:
        if value != SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version {value!r}; expected {SCHEMA_VERSION}")
        return cast(Literal[1], value)

    @field_validator("slug")
    @classmethod
    def _slug(cls, value: str) -> str:
        return validate_identifier(value, field_name="slug")

    @model_validator(mode="after")
    def _validate_graph_and_initial_state(self) -> ExecutionPlan:
        ids = [step.id for step in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("step ids must be unique")
        known = set(ids)
        for step in self.steps:
            unknown = [dep for dep in step.depends_on if dep not in known]
            if unknown:
                raise ValueError(
                    f"step {step.id!r} depends on unknown step(s): {', '.join(unknown)}"
                )
            if step.id in step.depends_on:
                raise ValueError(f"step {step.id!r} cannot depend on itself")

        # Deterministic DFS cycle check.  A dependency graph is small (the
        # planner hard cap is 30), so avoiding a scheduler dependency keeps this
        # model lightweight and easy to validate in API processes.
        dependencies = {step.id: tuple(step.depends_on) for step in self.steps}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> None:
            if node in visiting:
                raise ValueError("step dependency graph contains a cycle")
            if node in visited:
                return
            visiting.add(node)
            for dependency in dependencies[node]:
                visit(dependency)
            visiting.remove(node)
            visited.add(node)

        for node in ids:
            visit(node)

        # The top-level envelope must be able to satisfy every step request.
        # It may be wider than the current plan (for a reusable template), but
        # it must never understate a GPU requirement.
        if any(step.resource.gpu == GPUKind.T4 for step in self.steps):
            if self.resource_plan.max_gpu != GPUKind.T4 or self.resource_plan.max_gpu_count < 1:
                raise ValueError("resource_plan does not cover a t4 step")

        for scope_name, artifacts in (("plan", self.artifacts),):
            paths = [artifact.path for artifact in artifacts]
            if len(paths) != len(set(paths)):
                raise ValueError(f"duplicate artifact paths in {scope_name} scope")
        for step in self.steps:
            paths = [artifact.path for artifact in step.artifacts]
            if len(paths) != len(set(paths)):
                raise ValueError(f"duplicate artifact paths in step {step.id!r}")

        # A newly authored plan is the only state allowed to have a pending
        # plan status.  Persisted non-pending plans remain valid for recovery,
        # while accidental ``status=done`` in a fresh planner response fails
        # fast at the boundary.
        if self.status == PlanStatus.PENDING and any(
            step.status != StepStatus.PENDING for step in self.steps
        ):
            raise ValueError("initial plan requires every step status to be pending")
        return self

    @property
    def step_ids(self) -> tuple[str, ...]:
        """Stable step IDs in authored order."""

        return tuple(step.id for step in self.steps)

    def ready_steps(self) -> list[ExecutionStep]:
        """Return pending steps whose dependencies are terminally successful.

        This is a small convenience for workers; it intentionally treats
        ``partial`` as terminal but *not* successful, so a caller can decide
        whether to continue dependent work explicitly.
        """

        by_id = {step.id: step for step in self.steps}
        ready: list[ExecutionStep] = []
        for step in self.steps:
            if step.status != StepStatus.PENDING:
                continue
            if all(by_id[dep].status == StepStatus.DONE for dep in step.depends_on):
                ready.append(step)
        return ready

    def topological_order(self) -> list[str]:
        """Return a deterministic topological ordering of step IDs."""

        remaining = {step.id: set(step.depends_on) for step in self.steps}
        order: list[str] = []
        while remaining:
            layer = [
                step.id for step in self.steps if step.id in remaining and not remaining[step.id]
            ]
            if not layer:
                # Construction already checks this; retain a defensive error
                # if a caller mutates a model with validation disabled.
                raise ValueError("step dependency graph contains a cycle")
            order.extend(layer)
            for node in layer:
                remaining.pop(node, None)
            for deps in remaining.values():
                deps.difference_update(layer)
        return order


ExecutionResource: TypeAlias = ResourceSpec
PlanStep: TypeAlias = ExecutionStep
StepSpec: TypeAlias = ExecutionStep
Artifact: TypeAlias = ArtifactSpec
Operation: TypeAlias = OperationSpec


# ---------------------------------------------------------------------------
# Legacy Vela boundary
# ---------------------------------------------------------------------------


def _legacy_ascii_identifier(value: Any, *, fallback: str) -> str:
    """Return a deterministic kebab-case identifier for an old Vela value.

    The extracted Vela examples contain IDs such as ``__planner__`` and, in
    some deployments, display-language IDs.  Canonical IDs intentionally stay
    ASCII-only; this helper is used *only* by the explicit legacy parser so a
    strict ``ExecutionPlan.model_validate`` call remains strict.
    """

    raw = str(value).strip()
    folded = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode("ascii")
    candidate = re.sub(r"[^a-zA-Z0-9]+", "-", folded.casefold()).strip("-")
    if not candidate:
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
        candidate = f"{fallback}-{digest}"
    # ``validate_identifier`` permits a leading digit, and capping here keeps
    # the result within the canonical field limit before suffixes are added.
    return candidate[:80].rstrip("-") or fallback


def _legacy_unique_identifier(value: Any, *, fallback: str, used: set[str]) -> str:
    base = _legacy_ascii_identifier(value, fallback=fallback)
    candidate = base
    suffix = 2
    while candidate in used:
        tail = f"-{suffix}"
        candidate = f"{base[: max(1, 80 - len(tail))].rstrip('-')}{tail}"
        suffix += 1
    used.add(candidate)
    return candidate


def _legacy_artifact_values(value: Any) -> list[Any]:
    """Flatten Vela output declarations to canonical artifact entries."""

    if value is None:
        return []
    if isinstance(value, ArtifactSpec):
        return [value]
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        # Preserve the complete artifact object when it already has a path;
        # accept the common aliases used by the old executor otherwise.
        if "path" in value:
            return [dict(value)]
        for alias in ("output_path", "input_path", "file", "filename", "location"):
            if alias in value:
                item = dict(value)
                item["path"] = item.pop(alias)
                return [item]
        # A mapping of labels to paths (``{"report": "work/..."}``) was
        # accepted by the original adapter.  Flatten only scalar/list values;
        # arbitrary nested objects remain invalid at the canonical boundary.
        flattened: list[Any] = []
        for item in value.values():
            flattened.extend(_legacy_artifact_values(item))
        return flattened
    if isinstance(value, (list, tuple)):
        flattened = []
        for item in value:
            flattened.extend(_legacy_artifact_values(item))
        return flattened
    return [value]


def _legacy_operation(value: Any) -> Any:
    """Normalise one old operation declaration without executing it."""

    if isinstance(value, str):
        return {"operation_id": value}
    if not isinstance(value, Mapping):
        return value
    operation = dict(value)
    # ``id``/``name`` were used for registry keys by early adapters.  Keep an
    # explicit operation/kind untouched when present so ambiguous payloads
    # still receive a useful strict-model error.
    if "operation_id" not in operation and "operation" not in operation:
        for alias in ("id", "registry_id", "name"):
            if alias in operation and isinstance(operation[alias], str):
                if alias == "name" and str(operation.get("kind", "")).casefold() == "agent":
                    operation["agent"] = operation[alias]
                else:
                    operation["operation_id"] = operation[alias]
                operation.pop(alias, None)
                break
    if "type" in operation and "kind" not in operation:
        operation["kind"] = operation.pop("type")
    for canonical, aliases in (
        ("inputs", ("input_paths", "input_artifacts")),
        ("outputs", ("output_paths", "expected_outputs", "output_artifacts")),
    ):
        if canonical not in operation:
            for alias in aliases:
                if alias in operation:
                    operation[canonical] = _legacy_artifact_values(operation.pop(alias))
                    break
        else:
            operation[canonical] = _legacy_artifact_values(operation[canonical])
    return operation


def _legacy_dependencies(value: Any, id_map: Mapping[str, str]) -> Any:
    if isinstance(value, str):
        values: list[Any] = [value]
    elif isinstance(value, (list, tuple)):
        values = list(value)
    else:
        return value
    result: list[Any] = []
    for item in values:
        if isinstance(item, str):
            result.append(id_map.get(item, id_map.get(item.casefold(), item)))
        else:
            result.append(item)
    return result


def normalize_plan_payload(
    value: Mapping[str, Any] | str,
    *,
    query: str = "",
) -> dict[str, Any]:
    """Convert the extracted Vela plan shape to a canonical payload.

    This is deliberately a parser-boundary operation, not a permissive model
    configuration.  ``ExecutionPlan`` remains strict for new callers; use
    :func:`parse_legacy_plan` when accepting an old ``.vela/plan.json``.

    Supported compatibility is intentionally limited to documented Vela
    spellings: missing ``schema_version``/``slug``, legacy IDs and dependency
    aliases, ``outputs``/``output_paths``/``artifacts``, and declarative
    ``operations`` with ``inputs``/``outputs``.  Unknown fields are left in the
    payload so canonical validation can reject them rather than silently
    dropping user data.
    """

    raw: Any = value
    if isinstance(value, str):
        try:
            raw = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("execution plan must be valid JSON") from exc
    if not isinstance(raw, Mapping):
        raise TypeError("execution plan must be an object or JSON string")

    payload: dict[str, Any] = deepcopy(dict(raw))
    # A small number of HTTP clients wrap the document.  Do not merge
    # arbitrary outer keys into the plan; the wrapper itself is the boundary.
    for wrapper in ("execution_plan", "plan"):
        nested = payload.get(wrapper)
        if isinstance(nested, Mapping) and len(payload) == 1:
            payload = deepcopy(dict(nested))
            break

    legacy = (
        "schema_version" not in payload
        or "slug" not in payload
        or any(key in payload for key in ("version", "outputs", "output_paths", "operations"))
    )
    if "schema_version" not in payload and "version" in payload:
        payload["schema_version"] = payload.pop("version")
    payload.setdefault("schema_version", SCHEMA_VERSION)

    if "title" not in payload:
        for alias in ("name", "query", "objective"):
            if alias in payload:
                payload["title"] = payload[alias]
                break
    title = payload.get("title")
    title_text = title.strip() if isinstance(title, str) else ""
    if not title_text:
        title_text = query.strip() or "Planner execution"
        payload["title"] = title_text

    slug = payload.get("slug")
    if not isinstance(slug, str) or not slug.strip():
        payload["slug"] = stable_slug(title_text)
    elif legacy and _IDENTIFIER_RE.fullmatch(slug.strip().casefold()) is None:
        normalized_slug = _legacy_ascii_identifier(slug, fallback="run")
        if normalized_slug == "run" and not re.search(r"[a-zA-Z0-9]", slug):
            normalized_slug = f"run-{hashlib.sha1(slug.encode('utf-8')).hexdigest()[:8]}"
        payload["slug"] = normalized_slug

    if "resource_plan" not in payload and "resources" in payload:
        payload["resource_plan"] = payload.pop("resources")
    if isinstance(payload.get("status"), str) and payload["status"].casefold() in {
        "queued",
        "ready",
    }:
        payload["status"] = "pending"

    # Top-level aliases from the original executor.
    top_artifacts: list[Any] = []
    if "artifacts" in payload:
        top_artifacts.extend(_legacy_artifact_values(payload["artifacts"]))
    for alias in ("outputs", "output_paths"):
        if alias in payload:
            top_artifacts.extend(_legacy_artifact_values(payload.pop(alias)))
    if top_artifacts:
        payload["artifacts"] = top_artifacts

    metadata_raw = payload.get("metadata")
    metadata: dict[str, Any] = dict(metadata_raw) if isinstance(metadata_raw, Mapping) else {}
    for key in ("inputs", "input_paths", "input_artifacts"):
        if key in payload:
            metadata.setdefault("inputs", deepcopy(payload.pop(key)))
    if "operations" in payload:
        raw_operations = payload.pop("operations")
        if isinstance(raw_operations, Mapping):
            raw_operations = [raw_operations]
        elif isinstance(raw_operations, str):
            raw_operations = [raw_operations]
        if isinstance(raw_operations, (list, tuple)):
            metadata.setdefault("operations", [_legacy_operation(item) for item in raw_operations])
        else:
            metadata.setdefault("operations", raw_operations)
    if metadata:
        payload["metadata"] = metadata

    raw_steps = payload.get("steps")
    if not isinstance(raw_steps, (list, tuple)):
        return payload

    # First pass assigns IDs, allowing dependencies to be rewritten even when
    # the old plan used punctuation, ``__planner__`` or display-language IDs.
    used: set[str] = set()
    id_map: dict[str, str] = {}
    prepared: list[tuple[Any, Any, str]] = []
    for index, item in enumerate(raw_steps):
        if not isinstance(item, Mapping):
            prepared.append((item, item, f"step-{index + 1}"))
            continue
        raw_id = item.get("id", item.get("step_id", item.get("name", f"step-{index + 1}")))
        normalized_id = _legacy_unique_identifier(
            raw_id,
            fallback=f"step-{index + 1}",
            used=used,
        )
        if isinstance(raw_id, str):
            id_map.setdefault(raw_id, normalized_id)
            id_map.setdefault(raw_id.casefold(), normalized_id)
        id_map.setdefault(normalized_id, normalized_id)
        prepared.append((item, raw_id, normalized_id))

    normalized_steps: list[Any] = []
    for _index, (item, _raw_id, normalized_id) in enumerate(prepared):
        if not isinstance(item, Mapping):
            normalized_steps.append(item)
            continue
        step = deepcopy(dict(item))
        step["id"] = normalized_id
        step.pop("step_id", None)

        if "name" not in step:
            step["name"] = str(_raw_id or normalized_id)
        if isinstance(step.get("status"), str) and step["status"].casefold() in {
            "queued",
            "ready",
        }:
            step["status"] = "pending"
        for alias in ("instruction", "task", "description"):
            if "prompt" not in step and alias in step:
                step["prompt"] = step.pop(alias)
                break

        for alias in ("dependencies", "depends", "after", "requires_steps"):
            if "depends_on" not in step and alias in step:
                step["depends_on"] = step.pop(alias)
                break
        if "depends_on" in step:
            step["depends_on"] = _legacy_dependencies(step["depends_on"], id_map)

        # Legacy output paths become canonical artifacts.  Keep explicitly
        # declared artifacts and append aliases in authored order.
        artifacts: list[Any] = []
        if "artifacts" in step:
            artifacts.extend(_legacy_artifact_values(step["artifacts"]))
        for alias in ("outputs", "output_paths", "produced_outputs", "produces"):
            if alias in step:
                artifacts.extend(_legacy_artifact_values(step.pop(alias)))
        if artifacts:
            step["artifacts"] = artifacts

        if "resources" in step and "resource" not in step:
            step["resource"] = step.pop("resources")
        if "resource" not in step and any(key in step for key in ("gpu", "gpu_count")):
            step["resource"] = {key: step.pop(key) for key in ("gpu", "gpu_count") if key in step}

        # Skills and role selectors were occasionally singular/renamed.
        if "skills" not in step and "skill" in step:
            step["skills"] = [step.pop("skill")]
        role = None
        for alias in ("role", "agent_name"):
            if "agent" not in step and alias in step:
                role = step.pop(alias)
                break
        if role is not None:
            raw_metadata = step.get("metadata")
            step_metadata: dict[str, Any] = (
                dict(raw_metadata) if isinstance(raw_metadata, Mapping) else {}
            )
            step_metadata.setdefault("agent", role)
            step["metadata"] = step_metadata

        # Normalise singular/plural operation declarations.  Multiple
        # operations are retained in metadata for the compiler/runner while a
        # single operation can use the canonical field directly.
        operations: list[Any] = []
        if "operation" in step:
            raw_operation = step["operation"]
            if isinstance(raw_operation, (list, tuple)):
                operations.extend(raw_operation)
            else:
                operations.append(raw_operation)
        if "operation_id" in step:
            if operations:
                # Leave both declarations for the strict validator to explain
                # the ambiguity rather than silently choosing one.
                pass
            else:
                operations.append({"operation_id": step.pop("operation_id")})
        if "operations" in step:
            raw_operations = step.pop("operations")
            if isinstance(raw_operations, Mapping | str):
                raw_operations = [raw_operations]
            if isinstance(raw_operations, (list, tuple)):
                operations.extend(raw_operations)
            else:
                operations.append(raw_operations)
        normalized_operations = [_legacy_operation(operation) for operation in operations]
        if normalized_operations:
            step["operation"] = normalized_operations[0]
            if len(normalized_operations) > 1:
                raw_metadata = step.get("metadata")
                step_metadata = dict(raw_metadata) if isinstance(raw_metadata, Mapping) else {}
                step_metadata.setdefault("operations", normalized_operations)
                step["metadata"] = step_metadata

        if "inputs" in step or "input_paths" in step:
            raw_metadata = step.get("metadata")
            step_metadata = dict(raw_metadata) if isinstance(raw_metadata, Mapping) else {}
            source = step.pop("inputs", step.pop("input_paths", []))
            step_metadata.setdefault("inputs", _legacy_artifact_values(source))
            step["metadata"] = step_metadata

        normalized_steps.append(step)

    payload["steps"] = normalized_steps
    return payload


def parse_legacy_plan(
    value: ExecutionPlan | Mapping[str, Any] | str,
    *,
    query: str = "",
    initial: bool = True,
) -> ExecutionPlan:
    """Parse an extracted Vela plan through explicit legacy normalisation."""

    if isinstance(value, ExecutionPlan):
        return validate_execution_plan(value, initial=initial)
    return validate_execution_plan(normalize_plan_payload(value, query=query), initial=initial)


def validate_execution_plan(
    value: ExecutionPlan | Mapping[str, Any] | str,
    *,
    initial: bool = True,
) -> ExecutionPlan:
    """Parse and validate a plan.

    ``value`` may be an already parsed model, a mapping, or a JSON string.  By
    default this is the planner-boundary validator and requires a fresh
    pending plan.  Set ``initial=False`` when loading a persisted checkpoint.
    Pydantic's native :class:`ValidationError` is intentionally preserved so
    API callers get field-level diagnostics.
    """

    if isinstance(value, ExecutionPlan):
        plan = value
    elif isinstance(value, str):
        try:
            plan = ExecutionPlan.model_validate_json(value)
        except ValidationError:
            raise
    else:
        plan = ExecutionPlan.model_validate(value)

    if initial:
        if plan.status != PlanStatus.PENDING:
            raise ValueError("initial execution plan status must be 'pending'")
        if any(step.status != StepStatus.PENDING for step in plan.steps):
            raise ValueError("initial execution plan requires pending step statuses")
    return plan


def parse_execution_plan(value: ExecutionPlan | Mapping[str, Any] | str) -> ExecutionPlan:
    """Compatibility alias for :func:`validate_execution_plan`."""

    return validate_execution_plan(value)


def validate_plan(
    value: ExecutionPlan | Mapping[str, Any] | str,
    *,
    initial: bool = True,
) -> ExecutionPlan:
    """Backward-compatible short name for :func:`validate_execution_plan`."""

    return validate_execution_plan(value, initial=initial)


_STEP_TRANSITIONS: dict[StepStatus, frozenset[StepStatus]] = {
    StepStatus.PENDING: frozenset({StepStatus.RUNNING, StepStatus.SKIPPED}),
    StepStatus.RUNNING: frozenset(
        {StepStatus.DONE, StepStatus.PARTIAL, StepStatus.FAILED, StepStatus.SKIPPED}
    ),
    StepStatus.DONE: frozenset(),
    StepStatus.PARTIAL: frozenset(),
    StepStatus.FAILED: frozenset(),
    StepStatus.SKIPPED: frozenset(),
}


def can_transition_step_status(current: StepStatus | str, target: StepStatus | str) -> bool:
    """Return whether a worker may perform a status transition."""

    try:
        source = StepStatus(current)
        destination = StepStatus(target)
    except ValueError:
        return False
    return destination in _STEP_TRANSITIONS[source]


def transition_step_status(step: ExecutionStep, target: StepStatus | str) -> ExecutionStep:
    """Apply a legal status transition in place and return ``step``."""

    destination = StepStatus(target)
    if not can_transition_step_status(step.status, destination):
        raise ValueError(f"invalid step transition: {step.status.value} -> {destination.value}")
    step.status = destination
    return step


def plan_to_json(plan: ExecutionPlan, *, indent: int | None = None) -> str:
    """Serialize a validated plan without introducing non-JSON objects."""

    return plan.model_dump_json(indent=indent)


def load_execution_plan(path: str) -> ExecutionPlan:
    """Load a plan from a UTF-8 JSON file after validating its path."""

    safe_path = validate_relative_path(path, field_name="plan path")
    # Deliberately use the caller's current workspace only after path checks;
    # this helper is for local tooling, not an unrestricted file reader.
    from pathlib import Path

    return validate_execution_plan(Path(safe_path).read_text(encoding="utf-8"))


__all__ = [
    "SCHEMA_VERSION",
    "stable_slug",
    "slugify",
    "make_slug",
    "PlanStatus",
    "StepStatus",
    "ExecutionPlanStatus",
    "ExecutionStepStatus",
    "PlanStepStatus",
    "GPUKind",
    "ArtifactSpec",
    "Artifact",
    "ResourceSpec",
    "ExecutionResource",
    "ResourcePlan",
    "OperationSpec",
    "Operation",
    "ExecutionStep",
    "PlanStep",
    "StepSpec",
    "ExecutionPlan",
    "normalize_plan_payload",
    "parse_legacy_plan",
    "validate_identifier",
    "validate_relative_path",
    "validate_execution_plan",
    "parse_execution_plan",
    "validate_plan",
    "can_transition_step_status",
    "transition_step_status",
    "plan_to_json",
    "load_execution_plan",
    "ValidationError",
]
