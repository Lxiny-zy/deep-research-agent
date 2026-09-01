"""受控的本机命令执行边界。

Agent 只能请求一个已登记的 ``operation``，不能把模型生成的 shell 字符串
直接交给操作系统。这个模块故意不依赖 FastAPI 或数据库，因此同一个接口
可以由本地开发进程和未来的 Docker runner worker 复用。
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

CommandStatus = Literal["succeeded", "failed", "timed_out", "cancelled"]


class CommandPolicyError(ValueError):
    """请求不符合 runner 安全策略。"""


class OperationNotAllowed(CommandPolicyError):
    """operation 不在白名单中。"""


@dataclass(frozen=True)
class OperationRequest:
    """传给 operation builder 的结构化请求。"""

    operation: str
    inputs: tuple[Path, ...] = ()
    outputs: tuple[Path, ...] = ()
    workspace: Path = Path(".")
    options: Mapping[str, Any] = field(default_factory=dict)


CommandBuilder = Callable[[OperationRequest], Sequence[str]]


@dataclass(frozen=True)
class OperationDefinition:
    """一个可执行 operation 的声明。

    ``builder`` 必须返回参数数组，数组第一个元素是可执行文件。禁止在
    builder 中拼接 shell；runner 还会拒绝 ``shell`` 元字符和换行。
    """

    name: str
    builder: CommandBuilder
    network_profile: Literal["none", "restricted", "full"] = "none"
    max_timeout_seconds: float = 3600.0
    description: str = ""


@dataclass(frozen=True)
class CommandResult:
    operation: str
    status: CommandStatus
    exit_code: int | None
    argv: tuple[str, ...]
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = False
    truncated: bool = False
    outputs: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status == "succeeded"


@dataclass
class _OutputCapture:
    """Bounded byte capture for one child-process stream.

    ``asyncio``'s :meth:`Process.communicate` drains pipes safely, but keeps
    the complete output in memory.  Operations can be supplied by third-party
    tools, so retaining only the configured prefix is an important resource
    boundary as well as an audit detail.
    """

    limit: int
    data: bytearray = field(default_factory=bytearray)
    seen: int = 0
    truncated: bool = False

    def append(self, chunk: bytes) -> None:
        remaining = self.limit - len(self.data)
        if remaining > 0:
            self.data.extend(chunk[:remaining])
        self.seen += len(chunk)
        if self.seen > self.limit:
            self.truncated = True

    def text(self) -> str:
        # Ignoring an incomplete UTF-8 tail keeps the encoded representation
        # within ``limit`` even when the boundary falls inside a code point.
        return bytes(self.data).decode("utf-8", errors="ignore")


async def _capture_stream(stream: asyncio.StreamReader, capture: _OutputCapture) -> None:
    """Drain a subprocess stream while retaining only a bounded prefix."""

    while True:
        chunk = await stream.read(64 * 1024)
        if not chunk:
            return
        capture.append(chunk)


def _default_operations() -> dict[str, OperationDefinition]:
    """Return conservative built-ins.

    They are intentionally opt-in at deployment time: a missing executable is a
    normal operation failure, not a reason to fall back to arbitrary shell.
    """

    def _static(executable: str, *prefix: str) -> CommandBuilder:
        def build(request: OperationRequest) -> Sequence[str]:
            # Paths are passed as individual argv entries.  The operation
            # adapter, rather than an LLM, decides their positional meaning.
            return (
                executable,
                *prefix,
                *(str(p) for p in request.inputs),
                *(str(p) for p in request.outputs),
            )

        return build

    return {
        # These definitions are useful when the corresponding utility is
        # installed in the runner image.  They do not enable arbitrary flags.
        "archive.unpack": OperationDefinition(
            "archive.unpack", _static("bsdtar", "-xf"), description="Extract an archive"
        ),
        "pdf.convert": OperationDefinition(
            "pdf.convert",
            _static("libreoffice", "--headless", "--convert-to", "pdf"),
            description="Convert a document to PDF",
        ),
        "latex.compile": OperationDefinition(
            "latex.compile",
            _static("latexmk", "-pdf", "-interaction=nonstopmode"),
            description="Compile LaTeX",
        ),
    }


class CommandRunner:
    """Execute registered operations with process and workspace isolation.

    The class is deliberately stateless per invocation.  A deployment may put
    one instance in each worker, while a Docker runner can construct it from
    the same registry and policy.  ``register`` is intended for trusted code
    at startup, never for model output.
    """

    _FORBIDDEN_ARG_CHARS = frozenset({"\x00", "\r", "\n"})

    def __init__(
        self,
        *,
        workspace_root: str | os.PathLike[str],
        operations: Mapping[str, OperationDefinition] | None = None,
        allowed_operations: Sequence[str] | None = None,
        default_timeout_seconds: float = 300.0,
        max_output_bytes: int = 256_000,
        max_processes: int | None = None,
    ) -> None:
        root = Path(workspace_root).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        if default_timeout_seconds <= 0:
            raise ValueError("default_timeout_seconds must be > 0")
        if max_output_bytes < 1024:
            raise ValueError("max_output_bytes must be >= 1024")
        if max_processes is not None and max_processes < 1:
            raise ValueError("max_processes must be >= 1")
        self.workspace_root = root
        self.default_timeout_seconds = float(default_timeout_seconds)
        self.max_output_bytes = int(max_output_bytes)
        self.max_processes = max_processes
        self._operations: dict[str, OperationDefinition] = dict(_default_operations())
        if operations:
            for definition in operations.values():
                self.register(definition)
        self.allowed_operations = (
            frozenset(allowed_operations)
            if allowed_operations is not None
            else frozenset(self._operations)
        )
        unknown = self.allowed_operations.difference(self._operations)
        if unknown:
            raise OperationNotAllowed(f"unknown allowed operations: {sorted(unknown)}")
        self._semaphore = asyncio.Semaphore(max_processes) if max_processes else None

    @property
    def operations(self) -> tuple[str, ...]:
        return tuple(sorted(self._operations))

    def register(self, definition: OperationDefinition) -> None:
        name = definition.name.strip()
        if not name or any(ch.isspace() for ch in name):
            raise ValueError("operation name must be a non-empty token")
        if definition.max_timeout_seconds <= 0:
            raise ValueError("operation max_timeout_seconds must be > 0")
        self._operations[name] = definition

    def _resolve_path(self, value: str | os.PathLike[str], *, workspace: Path) -> Path:
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = workspace / candidate
        # ``strict=False`` allows output paths that do not exist yet.  Resolve
        # existing parents to prevent symlink escapes before the write occurs.
        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(workspace)
        except ValueError as exc:
            raise CommandPolicyError(f"path escapes workspace: {value}") from exc
        return resolved

    def _validate_argv(self, argv: Sequence[str]) -> tuple[str, ...]:
        if not argv or not str(argv[0]).strip():
            raise CommandPolicyError("operation returned an empty argv")
        normalized: list[str] = []
        for raw in argv:
            value = os.fspath(raw)
            if not isinstance(value, str) or not value:
                raise CommandPolicyError("argv entries must be non-empty strings")
            if any(ch in value for ch in self._FORBIDDEN_ARG_CHARS):
                raise CommandPolicyError("argv contains NUL/newline")
            normalized.append(value)
        # A builder should return a direct executable invocation.  Reject the
        # common shell wrappers even when a trusted adapter accidentally emits
        # them; shell execution is never part of this contract.
        executable = Path(normalized[0]).name.casefold()
        if executable in {"sh", "bash", "zsh", "cmd", "cmd.exe", "powershell", "pwsh"}:
            raise CommandPolicyError("shell wrappers are not allowed")
        return tuple(normalized)

    async def run(
        self,
        operation: str,
        *,
        inputs: Sequence[str | os.PathLike[str]] = (),
        outputs: Sequence[str | os.PathLike[str]] = (),
        workspace: str | os.PathLike[str] | None = None,
        options: Mapping[str, Any] | None = None,
        timeout_seconds: float | None = None,
        env: Mapping[str, str] | None = None,
        cwd: str | os.PathLike[str] | None = None,
        dry_run: bool = False,
    ) -> CommandResult:
        """Run one registered operation and return a bounded audit record."""

        name = operation.strip()
        if name not in self._operations or name not in self.allowed_operations:
            raise OperationNotAllowed(f"operation is not allowed: {operation}")
        definition = self._operations[name]
        run_workspace = (
            self.workspace_root
            if workspace is None
            else self._resolve_path(workspace, workspace=self.workspace_root)
        )
        run_workspace.mkdir(parents=True, exist_ok=True)
        input_paths = tuple(self._resolve_path(value, workspace=run_workspace) for value in inputs)
        output_paths = tuple(
            self._resolve_path(value, workspace=run_workspace) for value in outputs
        )
        request = OperationRequest(
            operation=name,
            inputs=input_paths,
            outputs=output_paths,
            workspace=run_workspace,
            options=options or {},
        )
        argv = self._validate_argv(definition.builder(request))
        timeout = (
            self.default_timeout_seconds
            if timeout_seconds is None
            else float(timeout_seconds)
        )
        timeout = min(timeout, definition.max_timeout_seconds)
        if timeout <= 0:
            raise CommandPolicyError("timeout_seconds must be > 0")
        run_cwd = run_workspace if cwd is None else self._resolve_path(cwd, workspace=run_workspace)
        if not run_cwd.is_dir():
            raise CommandPolicyError(f"cwd is not a directory: {run_cwd}")
        output_names = tuple(str(path.relative_to(run_workspace)) for path in output_paths)
        if dry_run:
            return CommandResult(
                operation=name,
                status="succeeded",
                exit_code=0,
                argv=argv,
                stdout="",
                stderr="",
                duration_seconds=0.0,
                outputs=output_names,
            )

        child_env = os.environ.copy()
        if env:
            child_env.update({str(key): str(value) for key, value in env.items()})
        # Do not inherit a caller's proxy/network knobs accidentally.  An
        # operation that needs network must be put in a separately configured
        # runner profile; this local runner defaults to no network hints.
        if definition.network_profile == "none":
            for key in (
                "HTTP_PROXY",
                "HTTPS_PROXY",
                "ALL_PROXY",
                "http_proxy",
                "https_proxy",
                "all_proxy",
            ):
                child_env.pop(key, None)

        async def _invoke() -> CommandResult:
            started = time.perf_counter()
            process: asyncio.subprocess.Process | None = None
            reader_tasks: list[asyncio.Task[None]] = []
            stdout_capture = _OutputCapture(self.max_output_bytes)
            stderr_capture = _OutputCapture(self.max_output_bytes)

            async def _settle_readers(*, timeout_seconds: float | None = None) -> None:
                if not reader_tasks:
                    return
                gathered = asyncio.gather(*reader_tasks, return_exceptions=True)
                try:
                    if timeout_seconds is None:
                        await gathered
                    else:
                        await asyncio.wait_for(gathered, timeout=timeout_seconds)
                except (TimeoutError, asyncio.CancelledError):
                    # A descendant that keeps a pipe open must not hold the
                    # runner forever after the process group has been killed.
                    for task in reader_tasks:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*reader_tasks, return_exceptions=True)
                    stdout_capture.truncated = True
                    stderr_capture.truncated = True

            try:
                kwargs: dict[str, Any] = {
                    "cwd": str(run_cwd),
                    "env": child_env,
                    "stdin": asyncio.subprocess.DEVNULL,
                    "stdout": asyncio.subprocess.PIPE,
                    "stderr": asyncio.subprocess.PIPE,
                }
                if os.name == "nt":
                    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                    kwargs["creationflags"] = creationflags
                else:
                    kwargs["start_new_session"] = True
                process = await asyncio.create_subprocess_exec(*argv, **kwargs)
                # Read both pipes concurrently.  This avoids deadlocks while
                # ensuring untrusted tools cannot force unbounded buffering.
                if process.stdout is None or process.stderr is None:
                    raise OSError("operation pipes were not created")
                reader_tasks = [
                    asyncio.create_task(_capture_stream(process.stdout, stdout_capture)),
                    asyncio.create_task(_capture_stream(process.stderr, stderr_capture)),
                ]
                await asyncio.wait_for(process.wait(), timeout=timeout)
                await _settle_readers(timeout_seconds=2.0)
                stdout = stdout_capture.text()
                stderr = stderr_capture.text()
                truncated = stdout_capture.truncated or stderr_capture.truncated
                status: CommandStatus = "succeeded" if process.returncode == 0 else "failed"
                return CommandResult(
                    name,
                    status,
                    process.returncode,
                    argv,
                    stdout,
                    stderr,
                    time.perf_counter() - started,
                    outputs=output_names,
                    truncated=truncated,
                )
            except TimeoutError:
                if process is not None:
                    await self._terminate(process)
                await _settle_readers(timeout_seconds=2.0)
                timeout_stderr = stderr_capture.text()
                if timeout_stderr:
                    timeout_stderr += "\n"
                timeout_stderr += f"timed out after {timeout:g}s"
                return CommandResult(
                    name,
                    "timed_out",
                    None if process is None else process.returncode,
                    argv,
                    stdout_capture.text(),
                    timeout_stderr,
                    time.perf_counter() - started,
                    timed_out=True,
                    truncated=stdout_capture.truncated or stderr_capture.truncated,
                    outputs=output_names,
                )
            except asyncio.CancelledError:
                if process is not None:
                    await self._terminate(process)
                await _settle_readers(timeout_seconds=2.0)
                raise
            except OSError as exc:
                await _settle_readers(timeout_seconds=2.0)
                return CommandResult(
                    name,
                    "failed",
                    None,
                    argv,
                    stdout_capture.text(),
                    (stderr_capture.text() + "\n" if stderr_capture.text() else "")
                    + str(exc),
                    time.perf_counter() - started,
                    truncated=stdout_capture.truncated or stderr_capture.truncated,
                    outputs=output_names,
                )

        if self._semaphore is None:
            return await _invoke()
        async with self._semaphore:
            return await _invoke()

    async def _terminate(self, process: asyncio.subprocess.Process) -> None:
        """Terminate the whole operation process group where supported."""

        if process.returncode is not None:
            return
        try:
            if os.name == "nt":
                process.terminate()
            else:
                # ``os.killpg``/``SIGKILL`` are POSIX-only at runtime and are
                # hidden from mypy's Windows stubs; resolve them defensively.
                killpg = getattr(os, "killpg", None)
                sigterm = getattr(signal, "SIGTERM", signal.SIGINT)
                if killpg is None:
                    process.terminate()
                else:
                    killpg(process.pid, sigterm)
            await asyncio.wait_for(process.wait(), timeout=2.0)
        except (ProcessLookupError, TimeoutError):
            try:
                if os.name == "nt":
                    process.kill()
                else:
                    killpg = getattr(os, "killpg", None)
                    sigkill = getattr(signal, "SIGKILL", signal.SIGTERM)
                    if killpg is None:
                        process.kill()
                    else:
                        killpg(process.pid, sigkill)
            except ProcessLookupError:
                pass
            try:
                await process.wait()
            except Exception:
                pass


LocalCommandRunner = CommandRunner

__all__ = [
    "CommandBuilder",
    "CommandPolicyError",
    "CommandResult",
    "CommandRunner",
    "CommandStatus",
    "LocalCommandRunner",
    "OperationDefinition",
    "OperationNotAllowed",
    "OperationRequest",
]
