from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from deep_research.api import CreateRunRequest
from deep_research.config import Settings
from deep_research.orchestration.runtime import OrchestrationRuntime
from deep_research.orchestrator import DeepResearchAgent, create_initial_execution
from deep_research.planner_runtime import coerce_execution_plan, sync_plan_from_workflow
from deep_research.planning import stable_slug
from tests.fakes import FakeLLM, FakeSearch


@dataclass
class _CommandResult:
    operation: str
    status: str
    exit_code: int
    argv: tuple[str, ...] = ("fixture",)
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = 0.0
    timed_out: bool = False
    truncated: bool = False
    outputs: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status == "succeeded"


class _FakeCommandRunner:
    def __init__(self, root: Path, *, succeed: bool = True, write_outputs: bool = True) -> None:
        self.workspace_root = root
        self.succeed = succeed
        self.write_outputs = write_outputs
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def run(self, operation: str, **kwargs: object) -> _CommandResult:
        self.calls.append((operation, kwargs))
        outputs = tuple(str(item) for item in kwargs.get("outputs", ()))
        if self.write_outputs and self.succeed:
            for item in outputs:
                path = self.workspace_root / item
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("fixture output\n", encoding="utf-8")
        return _CommandResult(
            operation=operation,
            status="succeeded" if self.succeed else "failed",
            exit_code=0 if self.succeed else 1,
            stderr="fixture failure" if not self.succeed else "",
            outputs=outputs,
        )


class _FailOnceLLM:
    def __init__(self) -> None:
        self.complete_calls = 0

    async def complete(self, system: str, user: str, *, temperature: float = 0.3) -> str:
        self.complete_calls += 1
        if self.complete_calls == 1:
            raise RuntimeError("model refused this step")
        return "# final result\n"

    async def aclose(self) -> None:
        return None


def test_external_plan_shorthand_gets_slug_and_output_artifacts() -> None:
    slug = stable_slug("A small task")
    plan = coerce_execution_plan(
        {
            "title": "A small task",
            "steps": [
                {
                    "id": "collect",
                    "name": "Collect",
                    "prompt": "Collect data",
                    "output_paths": [f"work/{slug}/explore/data.md"],
                }
            ],
        },
        query="fallback query",
    )
    assert plan.slug.startswith("a-small-task-")
    assert plan.steps[0].artifacts[0].path.startswith("work/")


def test_initial_external_plan_cannot_spoof_internal_source_marker() -> None:
    plan = coerce_execution_plan(
        {
            "slug": "source-spoof",
            "title": "Source spoof",
            "metadata": {"source": "workflow_projection"},
            "steps": [{"id": "one", "name": "One", "prompt": "Do one"}],
        },
        query="source spoof",
        initial=True,
    )
    assert plan.metadata["source"] == "external"


def test_compiler_accepts_legacy_vela_shape_at_its_boundary() -> None:
    from deep_research.orchestration.compiler import compile_plan

    compiled = compile_plan(
        {
            "title": "Legacy direct",
            "steps": [
                {"id": "one", "name": "One", "prompt": "Do one"},
                {"id": "two", "name": "Two", "prompt": "Do two"},
            ],
        },
        available_agents={"planner", "researcher", "synthesizer", "operation_runner"},
    )
    assert compiled.plan.slug.startswith("legacy-direct-")
    assert [step.agent for step in compiled.workflow.steps] == ["planner", "synthesizer"]


def test_create_request_accepts_plan_alias() -> None:
    request = CreateRunRequest(
        query="external",
        plan={
            "title": "External",
            "steps": [{"id": "one", "name": "One", "prompt": "Do one"}],
        },
    )
    assert request.execution_plan is not None
    assert isinstance(request.model_dump(mode="json")["execution_plan"], dict)


def test_initial_execution_persists_external_plan_before_queue() -> None:
    plan = {
        "slug": "queued-plan",
        "title": "Queued",
        "steps": [{"id": "one", "name": "One", "prompt": "Do one"}],
    }
    execution = create_initial_execution(
        "queued query",
        "deep",
        Settings(orchestration_mode="legacy"),
        execution_plan=plan,
    )
    scratch = execution.checkpoint["scratch"]
    assert scratch["_artifact_slug"] == "queued-plan"
    assert scratch["_execution_plan"]["slug"] == "queued-plan"


@pytest.mark.asyncio
async def test_external_plan_runs_each_prompt_and_writes_declared_outputs(tmp_path: Path) -> None:
    plan = {
        "slug": "external-run",
        "title": "External run",
        "steps": [
            {
                "id": "collect",
                "name": "Collect",
                "prompt": "Collect evidence now",
                "artifacts": ["work/external-run/explore/evidence.md"],
            },
            {
                "id": "deliver",
                "name": "Deliver",
                "prompt": "Write the final report",
                "artifacts": ["output/external-run/final/report.md"],
            },
        ],
    }
    llm = FakeLLM()
    agent = DeepResearchAgent(
        Settings(artifact_root=str(tmp_path), runner_enabled=False),
        llm=llm,
        search_tool=FakeSearch(),
        execution_plan=plan,
    )
    try:
        report = await agent.run("ignored by explicit plan")
    finally:
        await agent.aclose()

    assert report.markdown
    assert llm.complete_calls == 2
    assert (tmp_path / "work/external-run/explore/evidence.md").is_file()
    assert (tmp_path / "output/external-run/final/report.md").is_file()
    persisted = json.loads(
        (tmp_path / ".framework/plans/external-run.json").read_text(encoding="utf-8")
    )
    assert persisted["metadata"]["source"] == "external"
    assert [step["status"] for step in persisted["steps"]] == ["done", "done"]


@pytest.mark.asyncio
async def test_operation_only_terminal_uses_step_outputs_and_writes_report(tmp_path: Path) -> None:
    runner = _FakeCommandRunner(tmp_path)
    plan = {
        "slug": "operation-terminal",
        "title": "Operation terminal",
        "steps": [
            {
                "id": "convert",
                "name": "Convert",
                "prompt": "Convert the input.",
                "operation": "fixture.convert",
                # Vela shorthand puts outputs on the step, not the operation.
                "artifacts": ["work/operation-terminal/convert/result.txt"],
            }
        ],
    }
    llm = FakeLLM()
    agent = DeepResearchAgent(
        Settings(artifact_root=str(tmp_path), runner_enabled=True),
        llm=llm,
        search_tool=FakeSearch(),
        command_runner=runner,
        execution_plan=plan,
    )
    try:
        report = await agent.run("ignored")
    finally:
        await agent.aclose()

    assert llm.complete_calls == 0
    assert report.markdown.startswith("## Operation summary")
    assert runner.calls[0][1]["outputs"] == [
        "work/operation-terminal/convert/result.txt"
    ]
    assert (tmp_path / "work/operation-terminal/convert/result.txt").is_file()
    persisted = json.loads(
        (tmp_path / ".framework/plans/operation-terminal.json").read_text(encoding="utf-8")
    )
    assert persisted["status"] == "done"
    assert persisted["steps"][0]["status"] == "done"


@pytest.mark.asyncio
async def test_operation_rejects_cross_run_artifact_slug(tmp_path: Path) -> None:
    runner = _FakeCommandRunner(tmp_path)
    plan = {
        "slug": "operation-scope",
        "title": "Operation scope",
        "steps": [
            {
                "id": "convert",
                "name": "Convert",
                "prompt": "Convert the input.",
                "operation": "fixture.convert",
                "artifacts": ["work/another-run/convert/result.txt"],
            }
        ],
    }
    agent = DeepResearchAgent(
        Settings(artifact_root=str(tmp_path), runner_enabled=True),
        llm=FakeLLM(),
        search_tool=FakeSearch(),
        command_runner=runner,
        execution_plan=plan,
    )
    try:
        with pytest.raises(RuntimeError):
            await agent.run("ignored")
    finally:
        await agent.aclose()

    assert runner.calls == []
    assert not (tmp_path / "work/another-run").exists()
    persisted = json.loads(
        (tmp_path / ".framework/plans/operation-scope.json").read_text(encoding="utf-8")
    )
    assert "does not match current run" in persisted["steps"][0]["metadata"]["failure"]


@pytest.mark.asyncio
async def test_operation_failure_is_failed_not_partial(tmp_path: Path) -> None:
    runner = _FakeCommandRunner(tmp_path, succeed=False)
    plan = {
        "slug": "operation-failure",
        "title": "Operation failure",
        "steps": [
            {
                "id": "convert",
                "name": "Convert",
                "prompt": "Convert the input.",
                "operation": "fixture.convert",
            }
        ],
    }
    agent = DeepResearchAgent(
        Settings(artifact_root=str(tmp_path), runner_enabled=True),
        llm=FakeLLM(),
        search_tool=FakeSearch(),
        command_runner=runner,
        execution_plan=plan,
    )
    try:
        with pytest.raises(RuntimeError):
            await agent.run("ignored")
    finally:
        await agent.aclose()

    persisted = json.loads(
        (tmp_path / ".framework/plans/operation-failure.json").read_text(encoding="utf-8")
    )
    assert persisted["status"] == "failed"
    assert persisted["steps"][0]["status"] == "failed"
    assert "failure" in persisted["steps"][0]["metadata"]
    assert "gap_note" not in persisted["steps"][0]["metadata"]


@pytest.mark.asyncio
async def test_generic_step_gap_is_partial_when_later_report_exists(tmp_path: Path) -> None:
    llm = _FailOnceLLM()
    plan = {
        "slug": "generic-partial",
        "title": "Generic partial",
        "steps": [
            {"id": "research", "name": "Research", "prompt": "Research."},
            {"id": "deliver", "name": "Deliver", "prompt": "Deliver."},
        ],
    }
    agent = DeepResearchAgent(
        Settings(artifact_root=str(tmp_path), runner_enabled=False),
        llm=llm,
        search_tool=FakeSearch(),
        execution_plan=plan,
    )
    try:
        report = await agent.run("ignored")
    finally:
        await agent.aclose()

    assert report.markdown == "# final result"
    persisted = json.loads(
        (tmp_path / ".framework/plans/generic-partial.json").read_text(encoding="utf-8")
    )
    assert persisted["status"] == "partial"
    assert persisted["steps"][0]["status"] == "partial"
    assert persisted["steps"][1]["status"] == "done"
    assert persisted["steps"][0]["metadata"]["gap_note"] == "model refused this step"


def test_external_dag_status_projection_uses_node_identity() -> None:
    plan = coerce_execution_plan(
        {
            "slug": "dag-status",
            "title": "DAG status",
            # Deliberately author the dependent node first. Runtime executes
            # its prerequisites first, so index-only projection is incorrect.
            "steps": [
                {
                    "id": "final",
                    "name": "Final",
                    "prompt": "final",
                    "depends_on": ["collect-a", "collect-b"],
                },
                {"id": "collect-a", "name": "Collect A", "prompt": "a"},
                {"id": "collect-b", "name": "Collect B", "prompt": "b"},
            ],
        },
        query="dag-status",
    )
    from deep_research.orchestration.compiler import compile_plan
    from deep_research.registry import available

    compiled = compile_plan(plan, available_agents=set(available()))
    runtime = OrchestrationRuntime()
    runtime.start("dag-status", {})
    for node_id, status in (
        ("node-collect-a", "failed"),
        ("node-collect-b", "succeeded"),
        ("node-final", "skipped"),
    ):
        step = runtime.create_step(node_id=node_id, label=node_id, kind="agent", agent="researcher")
        if status == "skipped":
            runtime.skip_step(step, "incoming conditions not matched")
        else:
            runtime.start_step(step)
        if status == "failed":
            runtime.fail_step(step, RuntimeError("partial evidence"))
        elif status == "succeeded":
            runtime.complete_step(step)

    sync_plan_from_workflow(
        plan,
        runtime.run,
        step_mapping=compiled.step_mapping,
        partial_step_ids={"collect-a"},
    )
    by_id = {step.id: step for step in plan.steps}
    assert by_id["collect-a"].status.value == "partial"
    assert by_id["collect-b"].status.value == "done"
    assert by_id["final"].status.value == "skipped"


def test_external_dag_marks_actual_terminal_operation_not_last_authored_step() -> None:
    from deep_research.orchestration.compiler import compile_plan

    plan = coerce_execution_plan(
        {
            "slug": "dag-terminal",
            "title": "DAG terminal",
            "steps": [
                {
                    "id": "finish",
                    "name": "Finish",
                    "prompt": "finish",
                    "operation": "fixture.finish",
                    "depends_on": ["prepare"],
                },
                {"id": "prepare", "name": "Prepare", "prompt": "prepare"},
            ],
        },
        query="dag-terminal",
    )
    compiled = compile_plan(
        plan,
        available_agents={"planner", "researcher", "synthesizer", "operation_runner"},
    )
    metadata = {step.metadata["plan_step_id"]: step.metadata for step in compiled.workflow.steps}
    assert metadata["finish"]["is_terminal"] is True
    assert metadata["prepare"]["is_terminal"] is False
