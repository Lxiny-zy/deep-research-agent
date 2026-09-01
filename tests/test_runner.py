from __future__ import annotations

import sys
from pathlib import Path

import pytest

from deep_research.runner import (
    CommandPolicyError,
    CommandRunner,
    OperationDefinition,
    OperationNotAllowed,
)


def _python_operation(
    name: str,
    code: str,
    *,
    max_timeout_seconds: float = 30.0,
) -> OperationDefinition:
    """Build a trusted operation backed by the current Python interpreter."""

    def build(request) -> list[str]:
        return [
            sys.executable,
            "-c",
            code,
            *(str(path) for path in request.inputs),
            *(str(path) for path in request.outputs),
        ]

    return OperationDefinition(name, build, max_timeout_seconds=max_timeout_seconds)


def _require_process_spawn() -> None:
    # The managed Windows test sandbox denies creation of overlapped pipe
    # handles.  CI/Linux and normal deployments exercise these subprocess
    # paths; keep policy-only tests runnable on every platform.
    if sys.platform == "win32":
        pytest.skip("asyncio subprocess pipes are unavailable in this sandbox")


@pytest.mark.asyncio
async def test_registered_operation_executes_and_reports_output(tmp_path: Path) -> None:
    _require_process_spawn()
    operation = _python_operation(
        "fixture.write",
        "import pathlib, sys; "
        "pathlib.Path(sys.argv[1]).write_text('done', encoding='utf-8'); print('ok')",
    )
    runner = CommandRunner(
        workspace_root=tmp_path,
        operations={operation.name: operation},
        allowed_operations=[operation.name],
    )

    result = await runner.run(operation.name, outputs=["result.txt"])

    assert result.ok
    assert result.status == "succeeded"
    assert result.exit_code == 0
    assert result.stdout.strip() == "ok"
    assert (tmp_path / "result.txt").read_text(encoding="utf-8") == "done"
    assert result.outputs == ("result.txt",)
    assert result.argv[0] == sys.executable


@pytest.mark.asyncio
async def test_dry_run_validates_but_does_not_spawn_process(tmp_path: Path) -> None:
    operation = _python_operation(
        "fixture.marker",
        "import pathlib, sys; pathlib.Path(sys.argv[1]).write_text('unexpected', encoding='utf-8')",
    )
    runner = CommandRunner(
        workspace_root=tmp_path,
        operations={operation.name: operation},
        allowed_operations=[operation.name],
    )

    result = await runner.run(operation.name, outputs=["marker.txt"], dry_run=True)

    assert result.ok
    assert result.duration_seconds == 0
    assert not (tmp_path / "marker.txt").exists()


@pytest.mark.asyncio
async def test_nonzero_exit_is_a_failed_audited_result(tmp_path: Path) -> None:
    _require_process_spawn()
    operation = _python_operation(
        "fixture.fail",
        "import sys; print('bad', file=sys.stderr); sys.exit(7)",
    )
    runner = CommandRunner(
        workspace_root=tmp_path,
        operations={operation.name: operation},
        allowed_operations=[operation.name],
    )

    result = await runner.run(operation.name)

    assert result.status == "failed"
    assert result.exit_code == 7
    assert result.stderr.strip() == "bad"
    assert not result.ok


@pytest.mark.asyncio
async def test_timeout_terminates_operation_and_marks_result(tmp_path: Path) -> None:
    _require_process_spawn()
    operation = _python_operation("fixture.sleep", "import time; time.sleep(5)")
    runner = CommandRunner(
        workspace_root=tmp_path,
        operations={operation.name: operation},
        allowed_operations=[operation.name],
        default_timeout_seconds=1,
    )

    result = await runner.run(operation.name, timeout_seconds=0.05)

    assert result.status == "timed_out"
    assert result.timed_out
    assert "timed out" in result.stderr


@pytest.mark.asyncio
async def test_stdout_is_bounded_and_truncation_is_recorded(tmp_path: Path) -> None:
    _require_process_spawn()
    operation = _python_operation("fixture.noisy", "print('x' * 10_000)")
    runner = CommandRunner(
        workspace_root=tmp_path,
        operations={operation.name: operation},
        allowed_operations=[operation.name],
        max_output_bytes=1024,
    )

    result = await runner.run(operation.name)

    assert result.status == "succeeded"
    assert len(result.stdout.encode("utf-8")) <= 1024
    assert result.truncated


@pytest.mark.asyncio
async def test_proxy_environment_is_removed_for_no_network_operation(tmp_path: Path) -> None:
    _require_process_spawn()
    operation = _python_operation(
        "fixture.env",
        "import os; "
        "print(os.environ.get('HTTP_PROXY', '')); "
        "print(os.environ.get('http_proxy', ''))",
    )
    runner = CommandRunner(
        workspace_root=tmp_path,
        operations={operation.name: operation},
        allowed_operations=[operation.name],
    )

    result = await runner.run(
        operation.name,
        env={"HTTP_PROXY": "must-not-leak", "http_proxy": "must-not-leak"},
    )

    assert result.ok
    assert result.stdout.splitlines() == ["", ""]


@pytest.mark.asyncio
async def test_unknown_operation_and_escaping_paths_are_rejected(tmp_path: Path) -> None:
    operation = _python_operation("fixture.noop", "print('ok')")
    runner = CommandRunner(
        workspace_root=tmp_path,
        operations={operation.name: operation},
        allowed_operations=[operation.name],
    )

    with pytest.raises(OperationNotAllowed):
        await runner.run("not-registered")
    with pytest.raises(CommandPolicyError, match="escapes workspace"):
        await runner.run(operation.name, inputs=["../outside.txt"])
    with pytest.raises(CommandPolicyError, match="escapes workspace"):
        await runner.run(operation.name, outputs=[str(tmp_path.parent / "outside.txt")])


@pytest.mark.asyncio
async def test_shell_wrappers_and_control_characters_are_never_allowed(tmp_path: Path) -> None:
    shell_operation = OperationDefinition(
        "fixture.shell",
        lambda _request: ["sh", "-c", "echo should-not-run"],
    )
    control_operation = OperationDefinition(
        "fixture.control",
        lambda _request: [sys.executable, "-c", "print('ok')", "bad\narg"],
    )
    runner = CommandRunner(
        workspace_root=tmp_path,
        operations={
            shell_operation.name: shell_operation,
            control_operation.name: control_operation,
        },
        allowed_operations=[shell_operation.name, control_operation.name],
    )

    with pytest.raises(CommandPolicyError, match="shell wrappers"):
        await runner.run(shell_operation.name)
    with pytest.raises(CommandPolicyError, match="NUL/newline"):
        await runner.run(control_operation.name)


@pytest.mark.asyncio
async def test_symlinked_workspace_path_cannot_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"runner-outside-{tmp_path.name}"
    outside.mkdir()
    try:
        link = tmp_path / "link"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (NotImplementedError, OSError):
            pytest.skip("symlinks are unavailable on this platform")
        operation = _python_operation("fixture.noop", "print('ok')")
        runner = CommandRunner(
            workspace_root=tmp_path,
            operations={operation.name: operation},
            allowed_operations=[operation.name],
        )

        with pytest.raises(CommandPolicyError, match="escapes workspace"):
            await runner.run(operation.name, outputs=["link/result.txt"])
    finally:
        for child in outside.iterdir():
            child.unlink()
        outside.rmdir()


def test_runner_rejects_invalid_limits(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="default_timeout_seconds"):
        CommandRunner(workspace_root=tmp_path, default_timeout_seconds=0)
    with pytest.raises(ValueError, match="max_output_bytes"):
        CommandRunner(workspace_root=tmp_path, max_output_bytes=100)
    with pytest.raises(ValueError, match="max_processes"):
        CommandRunner(workspace_root=tmp_path, max_processes=0)
