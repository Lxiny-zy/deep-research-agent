"""Renew an explicitly requested attempt without resetting cumulative usage."""

from __future__ import annotations

import math
import time
from copy import deepcopy
from typing import Any


def renewed_checkpoint(checkpoint: dict[str, Any], seconds: int) -> dict[str, Any]:
    if seconds <= 0:
        raise ValueError("resume execution window must be positive")
    updated = deepcopy(checkpoint)
    scratch = updated.setdefault("scratch", {})
    metrics = scratch.get("_runtime_metrics", {})
    elapsed = metrics.get("elapsed", 0) if isinstance(metrics, dict) else 0
    if not isinstance(elapsed, (int, float)) or not math.isfinite(elapsed) or elapsed < 0:
        elapsed = 0
    scratch["_attempt_elapsed_origin"] = elapsed
    # Explicit user recovery authorizes a fresh bounded window. Start clocks
    # after admission, so even a long worker queue cannot consume that window.
    scratch.pop("_deadline_at", None)
    scratch.pop("_task_deadline_at", None)
    recovery = scratch.get("_recovery")
    if isinstance(recovery, dict):
        recovery["not_before"] = 0
    return updated


def remaining_seconds(limit: int, elapsed: float, scratch: dict[str, Any]) -> float:
    origin = scratch.get("_attempt_elapsed_origin", 0)
    if not isinstance(origin, (int, float)) or not math.isfinite(origin) or origin < 0:
        origin = 0
    remaining = limit - max(0, elapsed - origin)
    for key in ("_deadline_at", "_task_deadline_at"):
        deadline = scratch.get(key)
        if isinstance(deadline, (float, int)) and math.isfinite(deadline):
            remaining = min(remaining, deadline - time.time())
    return max(0, remaining)
