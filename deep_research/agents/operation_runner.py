"""Agent adapter for declarative file operations.

The adapter deliberately accepts operation identifiers only.  A legacy plan
may contain a human-readable ``command`` field, but it is rejected instead of
being passed to a shell; deployments must register a named operation with
``CommandRunner`` first.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from ..models import Report
from ..registry import register
from ..runner import CommandPolicyError
from .base import Blackboard, RunContext


def _ref_path(value: Any) -> str:
    raw = str(value)
    parsed = urlsplit(raw)
    if parsed.scheme == "artifact":
        path = parsed.path.lstrip("/")
        if parsed.netloc:
            path = f"{parsed.netloc}/{path}" if path else parsed.netloc
        return path
    return raw


def _operation_id(raw: Mapping[str, Any]) -> str:
    value = raw.get("operation") or raw.get("operation_id")
    if isinstance(value, str) and value.strip():
        return value.strip()
    # The compatibility planning schema calls the operation name ``kind``.
    kind = raw.get("kind") or raw.get("type")
    if isinstance(kind, str) and kind not in {"agent", "command", "exec", "shell"}:
        return kind.strip()
    raise CommandPolicyError("operation step must provide a registered operation id")


def _declared_path(value: Any) -> str:
    """Extract a path from a string or canonical/legacy artifact object."""

    return _ref_path(value.get("path") if isinstance(value, Mapping) else value)


def _declared_required(value: Any) -> bool:
    """Return the Vela default (required) for an output declaration."""

    if isinstance(value, Mapping) and "required" in value:
        return bool(value["required"])
    return True


_ARTIFACT_AREAS = frozenset({"work", "output"})


def _artifact_slug(path: str) -> str | None:
    """Return the slug from a canonical artifact path, if one is present.

    Operation plans historically accepted ordinary workspace-relative paths
    (for example ``result.txt``).  Those paths remain valid for compatibility
    and are deliberately not interpreted as artifact references.  The
    artifact-first contract is unambiguous once a path starts with
    ``work/<slug>/`` or ``output/<slug>/`` and has a stage and filename.
    """

    normalized = _ref_path(path).replace("\\", "/").strip()
    parts = normalized.split("/")
    if len(parts) < 4 or parts[0] not in _ARTIFACT_AREAS:
        return None
    return parts[1]


def _validate_artifact_scope(
    path: str,
    artifact_slug: str | None,
    *,
    label: str,
) -> None:
    """Reject canonical paths that point at another run's artifact tree.

    Legacy/non-canonical paths are left to ``CommandRunner``'s workspace
    containment policy.  A canonical artifact path, however, carries an
    explicit run identity and must never be allowed to cross that boundary.
    """

    path_slug = _artifact_slug(path)
    if path_slug is None or artifact_slug is None:
        return
    if path_slug != artifact_slug:
        raise CommandPolicyError(
            f"{label} artifact slug {path_slug!r} does not match current run "
            f"slug {artifact_slug!r}"
        )


@register("operation_runner")
class OperationRunnerAgent:
    name: str

    async def step(self, bb: Blackboard, ctx: RunContext) -> Blackboard:
        runner = getattr(ctx, "command_runner", None)
        if runner is None:
            raise RuntimeError("command runner is not configured for this run")
        metadata = bb.scratch.get("_active_step_metadata", {})
        if not isinstance(metadata, Mapping):
            raise RuntimeError("operation step metadata is malformed")
        raw_operations = metadata.get("operations", [])
        if not isinstance(raw_operations, list) or not raw_operations:
            raise RuntimeError("operation step has no operations")
        store = getattr(ctx, "artifact_store", None)
        artifact_slug = getattr(ctx, "artifact_slug", None)
        if artifact_slug is not None and not isinstance(artifact_slug, str):
            raise CommandPolicyError("current run artifact slug must be a string")
        audits: list[dict[str, Any]] = []
        # Step-level artifact declarations are the canonical Vela shorthand.
        # Older plans often put ``outputs``/``input_paths`` beside the
        # operation rather than inside it.  Preserve those declarations when
        # dispatching to the registered operation; otherwise the command
        # builder cannot know where to write and the manifest loses the
        # handoff even though compilation succeeded.
        step_inputs = metadata.get("inputs", [])
        step_outputs = metadata.get("expected_output_specs", metadata.get("expected_outputs", []))
        if step_inputs is None:
            step_inputs = []
        if step_outputs is None:
            step_outputs = []
        if not isinstance(step_inputs, list) or not isinstance(step_outputs, list):
            raise CommandPolicyError("operation step inputs/outputs must be lists")
        fallback_inputs = [_declared_path(item) for item in step_inputs]
        fallback_outputs = [_declared_path(item) for item in step_outputs]
        fallback_required = {
            _declared_path(item): _declared_required(item) for item in step_outputs
        }
        for raw in raw_operations:
            if not isinstance(raw, Mapping):
                raise CommandPolicyError("operation declaration must be an object")
            # ``OperationSpec`` serializes the trusted registry key as
            # ``operation_id``.  Older payloads use ``operation``; accept both
            # spellings so an audit-only command description does not cause a
            # valid registered operation to be rejected at execution time.
            if raw.get("command") and not (raw.get("operation") or raw.get("operation_id")):
                raise CommandPolicyError(
                    "raw command text is not executable; register a named operation"
                )
            operation = _operation_id(raw)
            inputs = raw.get("inputs")
            if not inputs:
                inputs = fallback_inputs
            raw_output_declaration = raw.get("expected_outputs")
            if not raw_output_declaration:
                raw_output_declaration = raw.get("outputs")
            used_step_outputs = not raw_output_declaration
            outputs = raw_output_declaration
            if not outputs:
                outputs = raw.get("outputs")
            if not outputs:
                outputs = fallback_outputs
            if not isinstance(inputs, list) or not isinstance(outputs, list):
                raise CommandPolicyError("operation inputs/outputs must be lists")
            input_paths = [_declared_path(item) for item in inputs]
            output_paths = [_declared_path(item) for item in outputs]
            for path in input_paths:
                _validate_artifact_scope(path, artifact_slug, label="operation input")
            for path in output_paths:
                _validate_artifact_scope(path, artifact_slug, label="operation output")
            required_outputs = {
                _declared_path(item): _declared_required(item) for item in outputs
            }
            # If the operation omitted its own output declaration, the
            # step-level contract is authoritative.  This also carries the
            # required flag captured by the compiler above.
            if used_step_outputs and output_paths:
                required_outputs = dict(fallback_required)
            result = await runner.run(
                operation,
                inputs=input_paths,
                outputs=output_paths,
                workspace=getattr(store, "workspace_root", None),
                options=raw.get("args") if isinstance(raw.get("args"), Mapping) else {},
                timeout_seconds=raw.get("timeout_seconds"),
            )
            # A runner implementation may report additional paths in its
            # audit record.  Validate those too so an adapter cannot smuggle
            # a cross-run artifact reference through the result boundary.
            result_outputs = tuple(str(item) for item in (result.outputs or ()))
            for path in result_outputs:
                _validate_artifact_scope(
                    path,
                    artifact_slug,
                    label="operation result output",
                )
            audit = {
                "operation": result.operation,
                "status": result.status,
                "exit_code": result.exit_code,
                "argv": list(result.argv),
                "stdout": result.stdout,
                "stderr": result.stderr,
                "duration_seconds": round(result.duration_seconds, 3),
                "timed_out": result.timed_out,
                "truncated": result.truncated,
                "outputs": list(result_outputs),
            }
            audits.append(audit)
            ctx.tracer.emit(
                "RUNNER",
                "info" if result.ok else "error",
                f"{operation}: {result.status}",
                data={
                    "operation": operation,
                    "status": result.status,
                    "exit_code": result.exit_code,
                },
            )
            if not result.ok:
                raise RuntimeError(
                    f"operation {operation!r} {result.status}: {result.stderr[:500]}"
                )
            if store is not None:
                for output in output_paths:
                    registered = self._register_output(
                        store,
                        output,
                        artifact_slug=artifact_slug,
                    )
                    if not registered and required_outputs.get(output, True):
                        raise RuntimeError(f"required operation output is missing: {output}")
        bb.scratch.setdefault("operation_results", []).extend(audits)
        if bool(metadata.get("is_terminal", False)):
            lines = ["## Operation summary", ""]
            for audit in audits:
                lines.append(
                    f"- `{audit['operation']}`: {audit['status']} "
                    f"(exit code {audit['exit_code']})"
                )
                outputs = audit.get("outputs") or []
                if outputs:
                    lines.append(f"  Outputs: {', '.join(str(item) for item in outputs)}")
            bb.report = Report(query=bb.query, markdown="\n".join(lines) + "\n", citations=[])
        return bb

    @staticmethod
    def _register_output(
        store: Any,
        path: str,
        *,
        artifact_slug: str | None = None,
    ) -> bool:
        """Register a produced workspace path when it follows our convention."""

        _validate_artifact_scope(path, artifact_slug, label="operation output")
        parts = path.replace("\\", "/").split("/")
        if len(parts) < 4 or parts[0] not in {"work", "output"}:
            # Paths outside the artifact convention are intentionally left to
            # the operation implementation; they cannot be represented in the
            # run manifest, so they are not considered required handoffs here.
            return True
        try:
            store.register(
                parts[1],
                parts[2],
                "/".join(parts[3:]),
                area=parts[0],
                update_manifest=True,
            )
        except FileNotFoundError:
            # A command may report an optional output.  The operation result
            # remains auditable; the caller decides whether it was required.
            return False
        return True


__all__ = ["OperationRunnerAgent"]
