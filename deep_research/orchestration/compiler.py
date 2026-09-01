"""Compile planner data into the existing workflow engine.

This adapter is intentionally boring: it validates skill references, maps a
plan's explicit dependency graph to workflow nodes, and carries operation
requests as opaque metadata.  It does not interpret arbitrary prompts or
construct shell commands.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..skills import SkillResolver
from .graph import WorkflowEdge, WorkflowNode
from .plan import ExecutionPlan as ArtifactExecutionPlan
from .plan import parse_plan

if TYPE_CHECKING:
    from ..workflow import Workflow


class PlanCompileError(ValueError):
    """Raised when a valid plan cannot be mapped to available runtime roles."""


@dataclass(frozen=True, slots=True)
class CompiledPlan:
    # ``plan`` is intentionally structural: both the legacy artifact plan
    # contract and ``deep_research.planning.ExecutionPlan`` can be compiled.
    plan: Any
    workflow: Workflow
    step_mapping: Mapping[str, str]


def _raw_metadata(step: Any) -> dict[str, Any]:
    raw = getattr(step, "metadata", None)
    return dict(raw) if isinstance(raw, Mapping) else {}


def _operation_payload(step: Any) -> list[dict[str, Any]]:
    """Normalise both planner contract variants to serialisable operations."""

    specs: list[Any] = []
    operation_specs = getattr(step, "operation_specs", None)
    if callable(operation_specs):
        specs = list(operation_specs())
    else:
        # Legacy Vela plans may declare more than one operation.  The
        # parser-boundary normaliser stores that list in step metadata while
        # retaining the first item in the canonical singular ``operation``
        # field for compatibility.  Prefer the complete list when present.
        metadata_operations = _raw_metadata(step).get("operations")
        if isinstance(metadata_operations, Sequence) and not isinstance(
            metadata_operations, (str, bytes, bytearray)
        ) and metadata_operations:
            specs = list(metadata_operations)
        else:
            operation = getattr(step, "operation", None)
            if operation is not None:
                specs = [operation]
    result: list[dict[str, Any]] = []
    for spec in specs:
        if hasattr(spec, "model_dump"):
            payload = spec.model_dump(mode="json")
        elif isinstance(spec, Mapping):
            payload = dict(spec)
        else:
            payload = {"operation": str(spec)}
        # The canonical ``ExecutionStep`` has a default ``OperationSpec`` so
        # that a planner may omit operation metadata.  Do not turn that
        # placeholder into an ``operation_runner`` request: only an explicit
        # operation (or non-default operation options) should cross the runner
        # boundary.
        if (
            str(payload.get("kind", "")).casefold() == "agent"
            and not payload.get("operation")
            and not payload.get("operation_id")
        ):
            # ``kind=agent`` is the canonical role selector.  A populated
            # ``agent`` field is consumed by ``_explicit_agent`` below and
            # must not become an operation-runner request.  An empty/default
            # spec is likewise only a placeholder on the canonical model.
            # Keep a kind=agent declaration as an operation only when it
            # carries an actual operation payload (for example a skill or
            # arguments) that the worker can audit.
            extra_keys = (
                "skill",
                "command",
                "args",
                "inputs",
                "outputs",
                "timeout_seconds",
                "max_attempts",
            )
            if not any(payload.get(key) not in (None, "", {}, [], 1) for key in extra_keys):
                continue
            # A role selector with operation options is ambiguous.  Keep the
            # declaration so the boundary validator below can reject it with
            # an actionable error; silently dropping a model-supplied command
            # would make the accepted plan differ from the audited payload.
        result.append(payload)
    return result


def _explicit_agent(step: Any, available_agents: set[str]) -> str | None:
    metadata = _raw_metadata(step)
    for key in ("agent", "role", "agent_name"):
        value = metadata.get(key)
        if isinstance(value, str) and value in available_agents:
            return value
    operation = getattr(step, "operation", None)
    if isinstance(operation, str) and operation in available_agents:
        return operation
    if operation is not None:
        value = getattr(operation, "agent", "")
        if isinstance(value, str) and value in available_agents:
            return value
        value = getattr(operation, "operation", "")
        if isinstance(value, str) and value in available_agents:
            return value
        value = getattr(operation, "operation_id", "")
        if isinstance(value, str) and value in available_agents:
            return value
        value = getattr(operation, "kind", "")
        if isinstance(value, str) and value in available_agents:
            return value
    return None


def _step_dependencies(step: Any) -> list[str]:
    value = getattr(step, "depends_on", None)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [str(item) for item in value]
    return []


class PlanCompiler:
    """Turn an :class:`ExecutionPlan` into a safe :class:`Workflow`."""

    def __init__(
        self,
        *,
        available_agents: Iterable[str],
        skill_resolver: SkillResolver | None = None,
        require_explicit_skills: bool = True,
        default_agent: str = "researcher",
    ) -> None:
        self.available_agents = set(available_agents)
        self.skill_resolver = skill_resolver
        self.require_explicit_skills = require_explicit_skills
        self.default_agent = default_agent
        if default_agent not in self.available_agents and default_agent != "operation_runner":
            raise PlanCompileError(f"default agent is not available: {default_agent}")

    def compile(self, value: Any) -> CompiledPlan:
        plan = _parse_compatible_plan(value)
        if not plan.steps:
            raise PlanCompileError("execution plan has no steps")
        plan_metadata = getattr(plan, "metadata", {})
        external_plan = isinstance(plan_metadata, Mapping) and (
            str(plan_metadata.get("source", "")).casefold() == "external"
        )
        if self.skill_resolver is not None:
            for step in plan.steps:
                skills = getattr(step, "skills", [])
                if skills:
                    try:
                        self.skill_resolver.validate_references(skills)
                        if self.require_explicit_skills:
                            self.skill_resolver.require_explicit_references(step.prompt, skills)
                    except ValueError as exc:
                        raise PlanCompileError(
                            f"step {step.id!r} has invalid skill references: {exc}"
                        ) from exc

        # Import lazily: ``workflow`` imports the orchestration package for its
        # runtime graph types, so importing it at module load time would create
        # a cycle during package initialisation.
        from ..workflow import Step, Workflow

        has_explicit_dependencies = any(_step_dependencies(step) for step in plan.steps)
        if has_explicit_dependencies:
            depended_on = {
                dependency
                for step in plan.steps
                for dependency in _step_dependencies(step)
            }
            terminal_ids = {
                step.id for step in plan.steps if step.id not in depended_on
            }
        else:
            # The authored list is linear when no dependency declarations are
            # present, so only its final step owns report-terminal semantics.
            terminal_ids = {plan.steps[-1].id}

        compiled: list[Step] = []
        mapping: dict[str, str] = {}
        for index, plan_step in enumerate(plan.steps):
            operations = _operation_payload(plan_step)
            agent = _explicit_agent(plan_step, self.available_agents)
            if agent is not None:
                # An operation-shaped role selector (for example
                # ``operation_id=researcher``) is equivalent to an explicit
                # agent declaration.  Do not attach it to operation_runner,
                # while retaining genuinely declarative payloads.
                operations = [
                    payload for payload in operations if not _is_agent_selector(payload, agent)
                ]
            _reject_untrusted_commands(plan_step, operations)
            if operations and agent is None:
                agent = "operation_runner"
                if agent not in self.available_agents:
                    raise PlanCompileError(
                        f"step {plan_step.id!r} requests an operation but "
                        "operation_runner is unavailable"
                    )
            if agent is None:
                if external_plan and "plan_executor" in self.available_agents:
                    # Caller-authored Vela steps carry their own complete
                    # prompt.  They should not be silently reinterpreted as
                    # the domain-specific planner/researcher roles.
                    agent = "plan_executor"
            if agent is None:
                # A plan authored for the Vela contract has no mandatory agent
                # field.  Use stable role defaults while allowing explicit
                # metadata to override them.
                if index == 0 and "planner" in self.available_agents:
                    agent = "planner"
                elif index == len(plan.steps) - 1 and "synthesizer" in self.available_agents:
                    agent = "synthesizer"
                else:
                    agent = self.default_agent
            metadata: dict[str, Any] = {
                "plan_step_id": plan_step.id,
                "prompt": plan_step.prompt,
                "reset": bool(getattr(plan_step, "reset", True)),
                "plan_step_index": index,
                "total_steps": len(plan.steps),
                "is_terminal": plan_step.id in terminal_ids,
                "workflow_agent": agent,
            }
            if operations:
                metadata["operations"] = operations
            declared_outputs = getattr(plan_step, "produced_paths", None)
            if callable(declared_outputs):
                metadata["expected_outputs"] = list(declared_outputs())
            elif hasattr(plan_step, "artifacts"):
                # Canonical plans attach artifacts to each step.  Preserve
                # their relative paths in workflow metadata for the runner and
                # for checkpoint/SSE consumers.
                artifacts = getattr(plan_step, "artifacts", [])
                metadata["expected_outputs"] = [
                    item.path if hasattr(item, "path") else str(item) for item in artifacts
                ]
                # Keep the required/format hints as well.  The operation
                # runner uses these to distinguish an optional report from a
                # missing required handoff; the path-only list above remains
                # the compact compatibility field consumed by generic agents.
                metadata["expected_output_specs"] = [
                    item.model_dump(mode="json") if hasattr(item, "model_dump") else item
                    for item in artifacts
                ]
            elif hasattr(plan_step, "output_paths"):
                metadata["expected_outputs"] = list(getattr(plan_step, "output_paths", []))
            extra = _raw_metadata(plan_step)
            metadata.update({key: value for key, value in extra.items() if key not in metadata})
            timeout = getattr(plan_step, "timeout_seconds", None)
            if timeout is None:
                resource = getattr(plan_step, "resource", None)
                timeout = getattr(resource, "timeout_seconds", None)
            max_attempts = getattr(plan_step, "max_attempts", None)
            if max_attempts is None:
                resource = getattr(plan_step, "resource", None)
                max_attempts = getattr(resource, "max_attempts", 1)
            compiled_step = Step(
                kind="agent",
                agent=agent,
                timeout_seconds=timeout,
                max_attempts=max(1, min(int(max_attempts or 1), 10)),
                metadata=metadata,
            )
            compiled.append(compiled_step)
            mapping[plan_step.id] = f"node-{plan_step.id}"

        nodes = [
            WorkflowNode(
                id=mapping[plan_step.id],
                step=compiled[index].model_dump(mode="json"),
                join_mode="success_all" if len(_step_dependencies(plan_step)) > 1 else "any",
            )
            for index, plan_step in enumerate(plan.steps)
        ]
        edges: list[WorkflowEdge] = []
        edge_index = 0
        if not has_explicit_dependencies:
            # The original Vela contract executes an authored list in order by
            # default.  Fan-out is opt-in through depends_on.
            for left, right in zip(nodes, nodes[1:], strict=False):
                edge_index += 1
                edges.append(WorkflowEdge(id=f"edge-{edge_index}", source=left.id, target=right.id))
        else:
            for plan_step in plan.steps:
                for dependency in _step_dependencies(plan_step):
                    edge_index += 1
                    edges.append(
                        WorkflowEdge(
                            id=f"edge-{edge_index}",
                            source=mapping[dependency],
                            target=mapping[plan_step.id],
                        )
                    )
        title = plan.title or getattr(plan, "slug", "planner-driven")
        workflow = Workflow(
            name=f"plan-{getattr(plan, 'slug', 'run')}",
            description=f"Planner-driven execution: {title}",
            steps=compiled,
            nodes=[node.model_dump(mode="json") for node in nodes],
            edges=[edge.model_dump(mode="json") for edge in edges],
        )
        return CompiledPlan(plan=plan, workflow=workflow, step_mapping=mapping)


def compile_plan(
    value: Any,
    *,
    available_agents: Iterable[str],
    skill_resolver: SkillResolver | None = None,
    require_explicit_skills: bool = True,
    default_agent: str = "researcher",
) -> CompiledPlan:
    return PlanCompiler(
        available_agents=available_agents,
        skill_resolver=skill_resolver,
        require_explicit_skills=require_explicit_skills,
        default_agent=default_agent,
    ).compile(value)


__all__ = ["CompiledPlan", "PlanCompileError", "PlanCompiler", "compile_plan"]


def _parse_compatible_plan(value: Any) -> Any:
    """Parse either supported wire model without weakening validation.

    The project shipped an artifact-oriented plan model before the stricter
    ``deep_research.planning`` model.  Keeping this conversion at the boundary
    avoids making callers know which version they received, while each model
    still performs its own path/graph checks.
    """

    if isinstance(value, ArtifactExecutionPlan):
        return value
    try:
        return parse_plan(value)
    except Exception as first_error:
        try:
            # The public compiler is also a parser boundary for callers that
            # hand it an extracted Vela document directly (without going
            # through ``coerce_execution_plan``).  Reuse the single legacy
            # normaliser rather than maintaining a second alias table here.
            from ..planning import parse_legacy_plan

            return parse_legacy_plan(value, initial=False)
        except Exception as second_error:
            raise PlanCompileError(str(second_error)) from first_error


def _reject_untrusted_commands(step: Any, operations: Sequence[Mapping[str, Any]]) -> None:
    """Reject legacy free-form command fields at the compiler boundary.

    A command operation is executable only when it is represented by a stable
    operation id and resolved by ``CommandRunner``.  Human-readable ``command``
    text from an LLM is retained for audit/debugging but never compiled into a
    shell-backed step.
    """

    for payload in operations:
        operation = payload.get("operation") or payload.get("operation_id")
        kind = str(payload.get("kind") or payload.get("type") or "").casefold()
        command = payload.get("command")
        selected_agent = payload.get("agent")
        if kind == "agent" and selected_agent and not operation:
            extras = (
                "skill",
                "command",
                "args",
                "inputs",
                "outputs",
                "timeout_seconds",
                "max_attempts",
            )
            if any(payload.get(key) not in (None, "", {}, [], 1) for key in extras):
                raise PlanCompileError(
                    f"step {getattr(step, 'id', '<unknown>')!r} combines an agent selector "
                    "with operation options; declare skills/operations on separate fields"
                )
        if command and not operation:
            raise PlanCompileError(
                f"step {getattr(step, 'id', '<unknown>')!r} contains raw command text; "
                "use a registered operation id"
            )
        if kind in {"command", "shell", "exec"} and not operation:
            raise PlanCompileError(
                f"step {getattr(step, 'id', '<unknown>')!r} uses an unregistered command kind"
            )


def _is_agent_selector(payload: Mapping[str, Any], agent: str) -> bool:
    """Return whether an operation payload only selects the resolved agent."""

    kind = str(payload.get("kind") or payload.get("type") or "").casefold()
    if kind != "agent":
        return False
    selected = payload.get("agent") or payload.get("operation") or payload.get("operation_id")
    if str(selected or "").strip() != agent:
        return False
    # Non-empty options turn this into a real operation declaration rather
    # than a role selector and should remain auditable by operation_runner.
    return not any(
        payload.get(key) not in (None, "", {}, [], 1)
        for key in (
            "skill",
            "command",
            "args",
            "inputs",
            "outputs",
            "timeout_seconds",
            "max_attempts",
        )
    )
