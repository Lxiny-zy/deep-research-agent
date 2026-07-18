"""Lifecycle recorder used by the current workflow engine.

This deliberately owns no scheduling policy.  It gives the existing engine a
single run/step state model now, and becomes the persistence/checkpoint seam in
the next migration phase.
"""

from __future__ import annotations

from collections.abc import Callable

from .types import RunStatus, StepRun, StepStatus, WorkflowRun

EventEmitter = Callable[[str, dict], None]


class OrchestrationRuntime:
    def __init__(self, emit: EventEmitter | None = None) -> None:
        self.run: WorkflowRun | None = None
        self._emit = emit
        self._sequence = 0

    def restore(self, run: WorkflowRun) -> None:
        self.run = run.model_copy(deep=True)
        self.run.status = RunStatus.RUNNING
        self.run.finished_at = None
        self._sequence = len(self.run.steps)
        self._publish("workflow.resumed", {"workflow": self.run.workflow_name})

    def start(self, workflow_name: str, input_: dict) -> WorkflowRun:
        if self.run is not None and self.run.status == RunStatus.RUNNING:
            return self.run
        self.run = WorkflowRun(workflow_name=workflow_name, input=input_)
        self.run.start()
        self._publish("workflow.started", {"workflow": workflow_name})
        return self.run

    def create_step(
        self, *, label: str, kind: str, agent: str = "", node_id: str | None = None
    ) -> StepRun:
        if self.run is None or self.run.status != RunStatus.RUNNING:
            raise RuntimeError("workflow run has not started")
        self._sequence += 1
        step = StepRun(
            node_id=node_id or f"step-{self._sequence}", label=label, kind=kind, agent=agent
        )
        step.transition(StepStatus.READY)
        self.run.steps.append(step)
        self._publish("step.ready", self._step_data(step))
        return step

    def start_step(self, step: StepRun) -> None:
        step.transition(StepStatus.RUNNING)
        self._publish("step.started", self._step_data(step))

    def complete_step(self, step: StepRun) -> None:
        step.transition(StepStatus.SUCCEEDED)
        self._publish("step.completed", self._step_data(step))

    def retry_step(self, step: StepRun, error: Exception, delay: float) -> None:
        step.transition(StepStatus.RETRYING, error=str(error))
        self._publish("step.retrying", {**self._step_data(step), "delay": delay})

    def fail_step(self, step: StepRun, error: Exception) -> None:
        step.transition(StepStatus.FAILED, error=str(error))
        self._publish("step.failed", self._step_data(step))

    def skip_step(self, step: StepRun, reason: str) -> None:
        step.transition(StepStatus.SKIPPED, error=reason)
        self._publish("step.skipped", self._step_data(step))

    def cancel_step(self, step: StepRun) -> None:
        step.transition(StepStatus.CANCELLED, error="cancelled")
        self._publish("step.cancelled", self._step_data(step))

    def finish(self, status: RunStatus, output: dict | None = None) -> WorkflowRun:
        if self.run is None:
            raise RuntimeError("workflow run has not started")
        self.run.finish(status, output=output)
        self._publish(f"workflow.{status.value}", {"workflow": self.run.workflow_name})
        return self.run

    def save_checkpoint(self, checkpoint: dict, definition: dict | None = None) -> WorkflowRun:
        if self.run is None:
            raise RuntimeError("workflow run has not started")
        self.run.checkpoint = checkpoint
        if definition is not None:
            self.run.definition = definition
        self._publish("checkpoint.saved", {"step_count": len(self.run.steps)})
        return self.run

    def _publish(self, name: str, data: dict) -> None:
        if self._emit is not None:
            self._emit(name, data)

    @staticmethod
    def _step_data(step: StepRun) -> dict:
        return {
            "step_run_id": step.id,
            "node_id": step.node_id,
            "label": step.label,
            "kind": step.kind,
            "agent": step.agent,
            "status": step.status.value,
            "attempt": step.attempt,
            "error": step.error,
        }
