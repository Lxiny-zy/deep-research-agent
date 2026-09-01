from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from deep_research.agents.base import Blackboard, RunContext
from deep_research.agents.operation_runner import OperationRunnerAgent
from deep_research.artifacts import ArtifactStore
from deep_research.config import Settings
from deep_research.observability import Tracer
from deep_research.runner import CommandPolicyError
from tests.fakes import FakeLLM, FakeSearch


@dataclass
class _Result:
    operation: str = "fixture.operation"
    status: str = "succeeded"
    exit_code: int = 0
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


class _Runner:
    def __init__(self, root: Path, *, reported_outputs: tuple[str, ...] = ()) -> None:
        self.workspace_root = root
        self.reported_outputs = reported_outputs
        self.calls: list[dict[str, Any]] = []

    async def run(self, operation: str, **kwargs: Any) -> _Result:
        self.calls.append({"operation": operation, **kwargs})
        return _Result(operation=operation, outputs=self.reported_outputs)


def _context(
    tmp_path: Path,
    runner: _Runner,
    *,
    artifact_slug: str | None = "current-run",
    store: ArtifactStore | None = None,
) -> RunContext:
    settings = Settings(artifact_root=str(tmp_path), runner_enabled=True)
    return RunContext(
        llm=FakeLLM(),
        search_tool=FakeSearch(),
        tracer=Tracer(),
        settings=settings,
        artifact_store=store,
        command_runner=runner,
        artifact_slug=artifact_slug,
    )


def _blackboard(operation: dict[str, Any]) -> Blackboard:
    return Blackboard(
        query="scope test",
        scratch={"_active_step_metadata": {"operations": [operation]}},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["inputs", "outputs"])
async def test_operation_paths_cannot_cross_current_artifact_slug(
    tmp_path: Path, field: str
) -> None:
    runner = _Runner(tmp_path)
    path = "work/other-run/convert/result.txt"
    operation = {"operation": "fixture.operation", field: [path]}

    with pytest.raises(CommandPolicyError, match="does not match current run"):
        await OperationRunnerAgent().step(_blackboard(operation), _context(tmp_path, runner))

    assert runner.calls == []


@pytest.mark.asyncio
async def test_runner_reported_outputs_are_checked_at_operation_boundary(tmp_path: Path) -> None:
    runner = _Runner(
        tmp_path,
        reported_outputs=("output/other-run/convert/result.txt",),
    )
    operation = {
        "operation": "fixture.operation",
        "outputs": ["work/current-run/convert/input.txt"],
    }

    with pytest.raises(CommandPolicyError, match="result output artifact slug"):
        await OperationRunnerAgent().step(_blackboard(operation), _context(tmp_path, runner))

    assert len(runner.calls) == 1


def test_register_output_rechecks_artifact_slug_before_manifest_update(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    store.write_text("other-run", "convert", "result.txt", "result\n")

    with pytest.raises(CommandPolicyError, match="does not match current run"):
        OperationRunnerAgent._register_output(
            store,
            "work/other-run/convert/result.txt",
            artifact_slug="current-run",
        )

    assert not store.manifest_path("current-run").exists()
