"""Runtime helpers for the incremental planner-driven migration.

The helpers deliberately keep the existing ``WorkflowEngine`` as the source
of execution semantics.  They add a durable plan and artifact projection that
is enabled by default (or explicitly with ``DR_ORCHESTRATION_MODE=planner-driven``)
and can be replayed independently of the Blackboard checkpoint.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .artifacts import ArtifactManifest, ArtifactStore, ManifestError
from .planning import (
    ArtifactSpec,
    ExecutionPlan,
    ExecutionStep,
    OperationSpec,
    PlanStatus,
    StepStatus,
    stable_slug,
)
from .workflow import Step, Workflow

PLAN_SCRATCH_KEY = "_execution_plan"
ARTIFACT_SLUG_SCRATCH_KEY = "_artifact_slug"
ARTIFACT_MANIFEST_SCRATCH_KEY = "_artifact_manifest"
PLAN_CONTROL_DIR = "plans"


def coerce_execution_plan(
    value: ExecutionPlan | Mapping[str, Any] | str,
    *,
    query: str = "",
    initial: bool = True,
) -> ExecutionPlan:
    """Normalize an externally supplied Vela plan at the runtime boundary.

    The extracted Vela contract predates the canonical model and omits
    ``schema_version``/``slug``.  Accept that small shorthand while retaining
    the canonical model's strict validation for all other fields.  A caller
    may provide JSON text or a mapping; no filesystem path is interpreted here
    (plans must arrive through a trusted API/checkpoint boundary).
    """

    # Keep one compatibility boundary for API, CLI and worker callers.  The
    # canonical Pydantic model remains strict; only this explicitly named
    # legacy parser accepts the extracted Vela aliases and omitted identity
    # fields.
    from .planning import parse_legacy_plan

    plan = parse_legacy_plan(value, query=query, initial=initial)
    if initial:
        # This marker is consumed by ``PlanCompiler`` to select the generic
        # fresh-context executor.  Recovery calls use ``initial=False`` and
        # therefore preserve the fenced source marker already on disk.  An
        # initial caller cannot smuggle ``workflow_projection`` (or another
        # internal source) through metadata to bypass compilation: the
        # explicit plan itself is the authoritative execution contract.
        plan.metadata["source"] = "external"
    return plan


def _safe_step_id(index: int, step: Step) -> str:
    role = (step.agent or step.kind or "step").lower()
    # Execution-plan identifiers are ASCII kebab-case even when a deployment
    # registers a role with a non-ASCII display name.
    role = (
        "".join(char if ("a" <= char <= "z" or "0" <= char <= "9") else "-" for char in role).strip(
            "-"
        )
        or "step"
    )
    return f"step-{index + 1}-{role}"[:80].rstrip("-")


def _workflow_steps(
    workflow: Workflow,
) -> tuple[list[Step], list[tuple[str, str]], list[str]]:
    """Return normalized steps, graph edges, and stable source node IDs."""

    if workflow.nodes:
        from .orchestration import WorkflowEdge, WorkflowNode

        nodes = [WorkflowNode.model_validate(node) for node in workflow.nodes]
        edges = [WorkflowEdge.model_validate(edge) for edge in workflow.edges]
        return (
            [Step.model_validate(node.step) for node in nodes],
            [(edge.source, edge.target) for edge in edges],
            [node.id for node in nodes],
        )
    steps = list(workflow.steps)
    return steps, [], [f"step-{index + 1}" for index in range(len(steps))]


def build_execution_plan(
    query: str,
    workflow: Workflow,
    *,
    slug: str | None = None,
    title: str | None = None,
    skills_by_step: Mapping[str, Iterable[str]] | None = None,
) -> ExecutionPlan:
    """Project an existing workflow into the versioned planner contract."""

    resolved_slug = slug or stable_slug(query)
    steps, raw_edges, source_node_ids = _workflow_steps(workflow)
    ids = [_safe_step_id(index, step) for index, step in enumerate(steps)]
    # Preserve graph dependencies when available; a linear workflow receives
    # explicit authored-order edges to retain legacy scheduling semantics.
    node_to_plan = {source_node_ids[index]: ids[index] for index in range(len(steps))}
    dependencies: dict[str, list[str]] = {step_id: [] for step_id in ids}
    if raw_edges and workflow.nodes:
        for source, target in raw_edges:
            if source in node_to_plan and target in node_to_plan:
                dependencies[node_to_plan[target]].append(node_to_plan[source])
    else:
        for previous, current in zip(ids, ids[1:], strict=False):
            dependencies[current].append(previous)

    projected: list[ExecutionStep] = []
    for index, step in enumerate(steps):
        step_id = ids[index]
        role = step.agent
        if not role:
            role = {
                "reflect_loop": "reflector",
                "team_fanout": step.aggregator,
                "compose": step.agent or "coordinator",
            }.get(step.kind, step.kind)
        prompt = str(step.metadata.get("prompt") or f"Execute workflow role: {role}")
        operation = OperationSpec(kind="agent", agent=role)
        # These are contract-level handoff hints.  Actual content is written
        # by the projection callback after the existing role completes.
        stage = role.lower().replace(" ", "-") or "step"
        filename = {
            "planner": "plan.json",
            "researcher": "results.json",
            "reflector": "reflection.json",
            "synthesizer": "report.md",
        }.get(role, "state.json")
        area = "output" if role in {"synthesizer", "aggregator"} else "work"
        path = f"{area}/{resolved_slug}/{stage}/{filename}"
        artifact = ArtifactSpec(
            path=path,
            kind="report" if role in {"synthesizer", "aggregator"} else "state",
            required=role in {"synthesizer", "aggregator"},
            format=filename.rsplit(".", 1)[-1] if "." in filename else "",
        )
        skills = list((skills_by_step or {}).get(step_id, ()))
        metadata: dict[str, Any] = {
            "workflow_kind": step.kind,
            "workflow_agent": role,
            "source_node_id": source_node_ids[index],
        }
        if step.kind == "reflect_loop":
            metadata.update(
                {
                    "reflector": step.reflector,
                    "researcher": step.researcher,
                    "max_rounds": step.max_rounds,
                }
            )
        elif step.kind == "team_fanout":
            metadata.update({"aggregator": step.aggregator, "max_teams": step.max_teams})
        projected.append(
            ExecutionStep(
                id=step_id,
                name=(role or step.kind or "step")[:20],
                prompt=prompt,
                operation=operation,
                depends_on=dependencies[step_id],
                artifacts=[artifact],
                skills=skills,
                reset=bool(step.metadata.get("reset", True)),
                metadata=metadata,
                # Keep a role-specific timeout if the workflow declares one.
                resource={
                    "max_attempts": step.max_attempts,
                    **(
                        {"timeout_seconds": step.timeout_seconds}
                        if step.timeout_seconds is not None
                        else {}
                    ),
                },
            )
        )
        if skills:
            projected[-1].metadata["skills"] = skills
    return ExecutionPlan(
        schema_version=1,
        slug=resolved_slug,
        title=title or workflow.name,
        steps=projected,
        status=PlanStatus.PENDING,
        metadata={"query": query, "workflow": workflow.name},
    )


def plan_json(plan: ExecutionPlan) -> dict[str, Any]:
    return plan.model_dump(mode="json")


def persist_plan(store: ArtifactStore, plan: ExecutionPlan) -> Path:
    return store.write_control_json(f"{PLAN_CONTROL_DIR}/{plan.slug}.json", plan_json(plan))


def load_plan_from_blackboard(
    store: ArtifactStore | None,
    bb: Any,
    *,
    slug: str,
    query: str,
    workflow: Workflow,
) -> ExecutionPlan:
    """Load an existing plan or create the first durable projection.

    The Blackboard is checked first because it is part of the fenced
    checkpoint.  The control file is a recovery fallback for workers that
    restarted after the plan write but before the next checkpoint.  A stale or
    malformed control file is never silently adopted for a fresh run.
    """

    raw = getattr(bb, "scratch", {}).get(PLAN_SCRATCH_KEY)
    if isinstance(raw, Mapping):
        from .planning import validate_execution_plan

        return validate_execution_plan(raw, initial=False)
    if store is not None:
        persisted = load_persisted_plan(store, slug)
        if persisted is not None:
            return persisted
    return build_execution_plan(query, workflow, slug=slug)


def store_plan_in_blackboard(bb: Any, plan: ExecutionPlan) -> None:
    """Write a JSON-only plan snapshot into a Blackboard scratch area."""

    scratch = getattr(bb, "scratch", None)
    if not isinstance(scratch, dict):
        raise ValueError("blackboard scratch must be a dictionary")
    scratch[PLAN_SCRATCH_KEY] = plan_json(plan)


def load_persisted_plan(store: ArtifactStore, slug: str) -> ExecutionPlan | None:
    try:
        payload = store.read_control_json(f"{PLAN_CONTROL_DIR}/{slug}.json")
        # A control file is a recovery hint, not the source of truth for a
        # fresh run.  Missing files are wrapped as ``ManifestError`` by the
        # artifact store; malformed/stale files should likewise fall back to
        # projecting a new plan instead of preventing the run from starting.
        from .planning import validate_execution_plan

        return validate_execution_plan(payload, initial=False)
    except (FileNotFoundError, OSError, ManifestError, TypeError, ValueError):
        # A control file is a recovery hint, not the source of truth for a
        # fresh run.  Validation errors are treated like a stale file: the
        # checkpoint/workflow projection remains authoritative.
        return None


def sync_plan_from_workflow(
    plan: ExecutionPlan,
    workflow_run: Any | None,
    *,
    output_paths_by_step: Mapping[str, Iterable[str]] | None = None,
    step_mapping: Mapping[str, str] | None = None,
    partial_step_ids: Iterable[str] = (),
) -> ExecutionPlan:
    """Project runtime step statuses into planner statuses without guessing."""

    if workflow_run is None:
        return plan
    status_map = {
        "succeeded": StepStatus.DONE,
        "failed": StepStatus.FAILED,
        "skipped": StepStatus.SKIPPED,
        "cancelled": StepStatus.FAILED,
        "running": StepStatus.RUNNING,
        "retrying": StepStatus.RUNNING,
    }
    runtime_steps = list(getattr(workflow_run, "steps", []))
    by_node = {
        str(getattr(item, "node_id", "")): item
        for item in runtime_steps
        if getattr(item, "node_id", None)
    }
    by_index = {index: item for index, item in enumerate(runtime_steps)}
    partial_ids = set(partial_step_ids)
    for index, plan_step in enumerate(plan.steps):
        runtime_step = None
        if step_mapping:
            runtime_step = by_node.get(step_mapping.get(plan_step.id, ""))
        if runtime_step is None:
            source_node = plan_step.metadata.get("source_node_id")
            if isinstance(source_node, str):
                runtime_step = by_node.get(source_node)
        if runtime_step is None:
            runtime_step = by_index.get(index)
        if runtime_step is None:
            continue
        target = status_map.get(getattr(runtime_step.status, "value", runtime_step.status))
        if target is None:
            continue
        if target == StepStatus.FAILED and plan_step.id in partial_ids:
            target = StepStatus.PARTIAL
        # Direct assignment is intentional here: the runtime may recover from
        # a prior ``partial``/``running`` snapshot, while transition rules are
        # enforced at plan authoring and worker boundaries.
        plan_step.status = target
        if target == StepStatus.FAILED:
            plan_step.metadata["failure"] = runtime_step.error or "workflow step failed"
        elif target == StepStatus.PARTIAL:
            plan_step.metadata["gap_note"] = (
                runtime_step.error or plan_step.metadata.get("gap_note") or "incomplete output"
            )
        if output_paths_by_step and plan_step.id in output_paths_by_step:
            existing = {artifact.path for artifact in plan_step.artifacts}
            for path in output_paths_by_step[plan_step.id]:
                artifact = ArtifactSpec(path=path, required=False)
                if artifact.path not in existing:
                    plan_step.artifacts.append(artifact)
                    existing.add(artifact.path)
    plan.status = (
        PlanStatus.DONE
        if all(step.status in {StepStatus.DONE, StepStatus.SKIPPED} for step in plan.steps)
        else PlanStatus.FAILED
        if any(step.status == StepStatus.FAILED for step in plan.steps)
        else PlanStatus.PARTIAL
        if any(step.status == StepStatus.PARTIAL for step in plan.steps)
        else PlanStatus.RUNNING
    )
    return plan


def plan_step_for_runtime(
    plan: ExecutionPlan,
    workflow: Workflow,
    runtime_step: Any,
    *,
    index: int | None = None,
) -> Any | None:
    """Resolve a plan step from a runtime ``StepRun`` without relying on IDs.

    Existing workflows use authored node IDs while planner-generated plans use
    their own stable step IDs.  The source-node metadata and authored index
    provide deterministic fallbacks across both forms and during recovery.
    """

    node_id = str(getattr(runtime_step, "node_id", ""))
    for step in plan.steps:
        if step.metadata.get("source_node_id") == node_id:
            return step
    if index is not None and 0 <= index < len(plan.steps):
        return plan.steps[index]
    return None


def _json_artifact_payload(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, default=str) + "\n"


def persist_blackboard_artifacts(
    store: ArtifactStore,
    slug: str,
    bb: Any,
    *,
    attempt: int | None = None,
) -> list[str]:
    """Materialize the stable handoff artifacts for the current Blackboard.

    Agent internals remain in memory for compatibility, but every meaningful
    boundary is mirrored to a file so a fresh worker can resume without hidden
    conversation state.  Writes are atomic through :class:`ArtifactStore`;
    any storage error is deliberately propagated as infrastructure failure.
    """

    written: list[str] = []

    def write_json(stage: str, name: str, value: Any) -> None:
        record = store.write_text(
            slug,
            stage,
            name,
            _json_artifact_payload(value),
            mime_type="application/json",
            attempt=attempt,
        )
        written.append(record.path)

    if getattr(bb, "plan", None) is not None:
        write_json("planner", "plan.json", bb.plan.model_dump(mode="json"))
    if getattr(bb, "results", None):
        write_json(
            "researcher",
            "results.json",
            [item.model_dump(mode="json") for item in bb.results],
        )
    if getattr(bb, "reflections", None):
        write_json(
            "reflector",
            "reflection.json",
            [item.model_dump(mode="json") for item in bb.reflections],
        )
    report = getattr(bb, "report", None)
    if report is not None:
        record = store.write_text(
            slug,
            "final",
            "report.md",
            str(report.markdown),
            area="output",
            mime_type="text/markdown",
            min_size=1,
            attempt=attempt,
        )
        written.append(record.path)
    return written


def persist_manifest_snapshot(store: ArtifactStore, slug: str) -> ArtifactManifest:
    manifest = store.load_manifest(slug)
    return manifest


def project_blackboard(
    store: ArtifactStore,
    slug: str,
    *,
    query: str,
    results_count: int,
    reflection_count: int,
    report_markdown: str | None = None,
    attempt: int | None = None,
) -> ArtifactManifest:
    """Write a compact resumable state snapshot and optional final report."""

    store.write_text(
        slug,
        "state",
        "run.json",
        json.dumps(
            {
                "query": query,
                "results_count": results_count,
                "reflection_count": reflection_count,
                "attempt": attempt,
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n",
        mime_type="application/json",
        attempt=attempt,
    )
    if report_markdown is not None:
        store.write_text(
            slug,
            "final",
            "report.md",
            report_markdown,
            area="output",
            mime_type="text/markdown",
            min_size=1,
            attempt=attempt,
        )
    return store.load_manifest(slug)


__all__ = [
    "ARTIFACT_MANIFEST_SCRATCH_KEY",
    "ARTIFACT_SLUG_SCRATCH_KEY",
    "PLAN_CONTROL_DIR",
    "PLAN_SCRATCH_KEY",
    "build_execution_plan",
    "coerce_execution_plan",
    "load_persisted_plan",
    "load_plan_from_blackboard",
    "persist_manifest_snapshot",
    "persist_blackboard_artifacts",
    "persist_plan",
    "plan_json",
    "project_blackboard",
    "stable_slug",
    "store_plan_in_blackboard",
    "sync_plan_from_workflow",
    "plan_step_for_runtime",
]
