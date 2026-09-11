"""Exercise the real Linux runner; no provider calls or host files are modified.

Run ``python -m scripts.verify_runner_sandbox`` on a Linux worker with bubblewrap,
prlimit and bsdtar installed. ``--expect-unavailable`` instead verifies a deployment
that must refuse operations because isolation prerequisites are absent or denied.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import socket
import tarfile
import tempfile
from pathlib import Path

from deep_research.runner import CommandPolicyError, CommandRunner, OperationDefinition

PROBE = """import errno, json, os, pathlib, resource, socket, sys
workspace = pathlib.Path('/workspace')
assert not pathlib.Path(sys.argv[1]).exists(), 'private host file is visible'
assert 'DRA_SANDBOX_PRIVATE' not in os.environ, 'service environment leaked'
try:
    pathlib.Path('/usr/dra-probe-write').write_text('unexpected')
except OSError as exc:
    assert exc.errno in (errno.EROFS, errno.EACCES), exc
else:
    raise AssertionError('system directories are writable')
with socket.socket() as client:
    client.settimeout(0.5)
    assert client.connect_ex(('127.0.0.1', int(sys.argv[2]))) != 0, 'host network reachable'
limits = {name: resource.getrlimit(getattr(resource, name))[0]
          for name in ('RLIMIT_AS', 'RLIMIT_CPU', 'RLIMIT_NPROC', 'RLIMIT_FSIZE')}
assert 0 < limits['RLIMIT_AS'] <= 268435456, limits
assert 0 < limits['RLIMIT_CPU'] <= 5, limits
assert 0 < limits['RLIMIT_NPROC'] <= 64, limits
assert 0 < limits['RLIMIT_FSIZE'] <= 104857600, limits
try:
    bytearray(536870912)
except MemoryError:
    pass
else:
    raise AssertionError('address-space limit was not enforced')
(workspace / 'probe-created.txt').write_text('workspace is writable')
print(json.dumps(limits, sort_keys=True))
"""

TIMEOUT_PROBE = """import subprocess, sys, time
subprocess.Popen([sys.executable, '-c',
    "import pathlib,time; time.sleep(2); pathlib.Path('/workspace/orphan.txt').write_text('bad')"])
print('child started', flush=True)
time.sleep(60)
"""


def prepare(root: Path) -> tuple[Path, Path]:
    workspace = root / "workspace"
    workspace.mkdir()
    private = root / "private.txt"
    private.write_text("synthetic private host data", encoding="utf-8")
    (workspace / "probe.py").write_text(PROBE, encoding="utf-8")
    (workspace / "timeout.py").write_text(TIMEOUT_PROBE, encoding="utf-8")
    (workspace / "output.py").write_text("print('x' * 65536)", encoding="utf-8")
    with tarfile.open(workspace / "sample.tar", "w") as archive:
        contents = b"real bsdtar extraction\n"
        entry = tarfile.TarInfo("sample.txt")
        entry.size = len(contents)
        archive.addfile(entry, io.BytesIO(contents))
    with tarfile.open(workspace / "escape.tar", "w") as archive:
        entry = tarfile.TarInfo("../../escape.txt")
        entry.size = 3
        archive.addfile(entry, io.BytesIO(b"bad"))
    return workspace, private


async def verify(root: Path, *, expect_unavailable: bool) -> None:
    workspace, private = prepare(root)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(2)
        port = listener.getsockname()[1]
        # A reachable host service makes the network isolation assertion meaningful.
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            pass
        operations = {
            "probe.environment": OperationDefinition(
                "probe.environment",
                lambda request: ("python3", str(request.inputs[0]), str(private), str(port)),
            ),
            "probe.timeout": OperationDefinition(
                "probe.timeout", lambda request: ("python3", str(request.inputs[0]))
            ),
            "probe.output": OperationDefinition(
                "probe.output", lambda request: ("python3", str(request.inputs[0]))
            ),
        }
        runner = CommandRunner(
            workspace_root=workspace,
            operations=operations,
            allowed_operations=(*operations, "archive.unpack"),
            isolation="required",
            memory_bytes=256 * 1024 * 1024,
            max_processes=1,
            max_output_bytes=2048,
            default_timeout_seconds=5,
        )
        os.environ["DRA_SANDBOX_PRIVATE"] = "synthetic-environment-value"
        try:
            result = await runner.run("probe.environment", inputs=["probe.py"])
        except CommandPolicyError as exc:
            if expect_unavailable and "requires Linux bubblewrap and prlimit" in str(exc):
                assert not (workspace / "probe-created.txt").exists()
                print("Runner correctly refused execution: isolation tools unavailable")
                return
            raise
        finally:
            os.environ.pop("DRA_SANDBOX_PRIVATE", None)
        if expect_unavailable:
            assert not result.ok, "runner isolation unexpectedly available"
            assert not (workspace / "probe-created.txt").exists(), "probe was executed"
            assert "bwrap:" in result.stderr and any(
                message in result.stderr.lower()
                for message in ("namespace", "operation not permitted", "permission denied")
            ), result.stderr
            print("Runner correctly refused execution: kernel denied sandbox creation")
            return
        assert result.ok, f"Linux isolation probe failed: {result.stderr}"
        assert (workspace / "probe-created.txt").read_text() == "workspace is writable"
        limits = json.loads(result.stdout)
        extracted = await runner.run("archive.unpack", inputs=["sample.tar"], outputs=["unpacked"])
        assert extracted.ok, extracted.stderr
        assert (workspace / "unpacked" / "sample.txt").read_text() == "real bsdtar extraction\n"
        rejected = await runner.run("archive.unpack", inputs=["escape.tar"], outputs=["rejected"])
        assert not rejected.ok, "bsdtar accepted a parent-path escape"
        assert not (root / "escape.txt").exists()
        bounded = await runner.run("probe.output", inputs=["output.py"])
        assert bounded.ok and bounded.truncated and len(bounded.stdout.encode()) <= 2048
        timeout = await runner.run("probe.timeout", inputs=["timeout.py"], timeout_seconds=0.75)
        assert timeout.timed_out and "child started" in timeout.stdout, timeout
        await asyncio.sleep(2.2)
        assert not (workspace / "orphan.txt").exists(), "timed-out descendant survived"
        assert private.read_text() == "synthetic private host data"
        print(
            "Linux runner passed: private files/environment, read-only system, network, "
            "memory/process/file/CPU limits, bounded output, descendant timeout, real bsdtar. "
            + json.dumps(limits, sort_keys=True)
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expect-unavailable", action="store_true")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="dra-runner-sandbox-") as directory:
        asyncio.run(verify(Path(directory), expect_unavailable=args.expect_unavailable))


if __name__ == "__main__":
    main()
