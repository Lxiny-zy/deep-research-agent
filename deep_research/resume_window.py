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
    scratch["_deadline_at"] = time.time() + seconds
    return updated


def remaining_seconds(limit: int, elapsed: float, scratch: dict[str, Any]) -> float:
    origin = scratch.get("_attempt_elapsed_origin", 0)
    if not isinstance(origin, (int, float)) or not math.isfinite(origin) or origin < 0:
        origin = 0
    remaining = limit - max(0, elapsed - origin)
    deadline = scratch.get("_deadline_at")
    if isinstance(deadline, (float, int)) and math.isfinite(deadline):
        remaining = min(remaining, deadline - time.time())
    return max(0, remaining)
