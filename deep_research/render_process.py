"""Killable native rendering, with publication authority retained by the parent.

The child receives data only, executes the existing renderer and checkpoints,
and asks the parent for authority at every existing publication guard. No
database credentials or executable payloads are sent over this protocol.
"""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from typing import Any
from uuid import uuid4

from .render_diagnostics import exception_diagnostic, validated_diagnostic
from .render_queue import RenderJob
from .shutdown import cancel, wait_until


class _WindowsJob:
    """Closing this handle terminates the renderer and any native descendants."""

    def __init__(self, pid: int) -> None:
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                ("flags", wintypes.DWORD), ("min_working_set", ctypes.c_size_t),
                ("max_working_set", ctypes.c_size_t), ("active_processes", wintypes.DWORD),
                ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                ("scheduling", wintypes.DWORD),
            ]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("basic", BasicLimits), ("io", ctypes.c_uint64 * 6),
                ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t),
            ]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ]
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel = kernel
        self.handle = kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        process = kernel.OpenProcess(0x0100 | 0x0001, False, pid)
        try:
            limits = ExtendedLimits()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not kernel.SetInformationJobObject(
                self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits),
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            if not process or not kernel.AssignProcessToJobObject(self.handle, process):
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            self.close()
            raise
        finally:
            if process:
                kernel.CloseHandle(process)

    def close(self) -> None:
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


class RenderProcess:
    def __init__(self) -> None:
        self.process: asyncio.subprocess.Process | None = None
        self._job: _WindowsJob | None = None
        self._aborted = False
        self.diagnostic: dict[str, Any] | None = None

    def command(self) -> list[str]:
        return [sys.executable, "-m", "deep_research.render_process"]

    def abort(self) -> None:
        self._aborted = True
        if self._job is not None:
            self._job.close()
            self._job = None
        process = self.process
        if process is None:
            return
        if sys.platform != "win32":
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.kill()

    async def reap(self, deadline: float) -> bool:
        if self.process is None:
            return True
        reaped = asyncio.create_task(self.process.wait())
        pending = await wait_until({reaped}, deadline)
        cancel(pending)
        return not pending

    async def run(
        self, job: RenderJob, root: str, quota: int | None,
        authority: Callable[[], Awaitable[bool]],
    ) -> dict[str, Any]:
        options: dict[str, Any] = (
            {"creationflags": subprocess.CREATE_NO_WINDOW}
            if sys.platform == "win32" else {"start_new_session": True}
        )
        spawn = asyncio.create_task(asyncio.create_subprocess_exec(
            *self.command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, **options,
        ))

        def spawned(task: asyncio.Task) -> None:
            if not task.cancelled() and task.exception() is None:
                self.process = task.result()
                if self._aborted:
                    self.abort()

        spawn.add_done_callback(spawned)
        try:
            process = self.process = await asyncio.shield(spawn)
            if self._aborted:
                raise asyncio.CancelledError
            # The child waits for stdin before importing/rendering: assignment
            # to the job precedes all native work and subprocess creation.
            if sys.platform == "win32":
                self._job = _WindowsJob(process.pid)
            assert process.stdin is not None and process.stdout is not None
            nonce, sequence = uuid4().hex, 0
            request = {
                "job": asdict(job), "root": root, "quota": quota,
                "parent": os.getpid(), "nonce": nonce,
            }
            process.stdin.write((json.dumps(request) + "\n").encode())
            await process.stdin.drain()
            while True:
                line = await process.stdout.readline()
                if not line:
                    raise OSError("渲染子进程异常退出，已保存的格式可恢复")
                message = json.loads(line)
                if message.get("nonce") != nonce:
                    raise ValueError("invalid renderer protocol nonce")
                if message.get("check"):
                    sequence += 1
                    if message.get("sequence") != sequence or sequence > 10000:
                        raise ValueError("invalid renderer publication sequence")
                    valid = await authority()
                    reply = {"nonce": nonce, "sequence": sequence, "valid": valid}
                    process.stdin.write((json.dumps(reply) + "\n").encode())
                    await process.stdin.drain()
                elif "error" in message:
                    from .render_tasks import raise_render_error

                    self.diagnostic = validated_diagnostic(message.get("diagnostic"))
                    raise_render_error(message["error"])
                elif "result" in message:
                    return message["result"]
                else:
                    raise ValueError("invalid renderer protocol")
        finally:
            self.abort()
            await self.reap(time.monotonic() + 1)


def main() -> None:
    request = json.loads(sys.stdin.buffer.readline())
    output = sys.stdout
    # Imports and native libraries cannot mix diagnostic output into the IPC.
    sys.stdout = sys.stderr
    if sys.platform.startswith("linux"):
        # A crashed parent cannot leave an orphan render occupying a slot.
        ctypes.CDLL(None).prctl(1, signal.SIGKILL)
        if os.getppid() != request["parent"]:
            return
    from . import render_tasks
    from .persistence.repository import LeaseLostError
    from .render_capacity import publication_guard, render_run_lock, rendering_capacity

    def send(message: dict[str, Any]) -> None:
        output.write(json.dumps({**message, "nonce": request["nonce"]}) + "\n")
        output.flush()

    sequence = 0

    def check() -> None:
        nonlocal sequence
        sequence += 1
        send({"check": True, "sequence": sequence})
        response = sys.stdin.buffer.readline(65536)
        if not response:
            raise LeaseLostError("渲染执行权已失效")
        value = json.loads(response)
        if value != {"nonce": request["nonce"], "sequence": sequence, "valid": True}:
            raise LeaseLostError("渲染执行权已失效")

    job = RenderJob(**request["job"])
    try:
        with (
            contextlib.redirect_stdout(sys.stderr),
            render_run_lock(request["root"], job.run_id),
            rendering_capacity(request["root"]),
            publication_guard(check),
        ):
            result = render_tasks.execute_render(job, request["root"], request["quota"])
        send({"result": result})
    except Exception as exc:
        send({
            "error": render_tasks.error_record(exc),
            "diagnostic": exception_diagnostic(exc),
        })


if __name__ == "__main__":
    main()
