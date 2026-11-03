"""Offline entry point for the executable task-chain regression scenarios."""

import subprocess
import sys
from pathlib import Path

if __name__ == "__main__":
    root = Path(__file__).resolve().parents[2]
    raise SystemExit(subprocess.call(
        [sys.executable, "-m", "pytest", "-q", "tests/test_task_chain_repairs.py", "--tb=short"],
        cwd=root,
    ))
