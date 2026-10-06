"""Render failure locations without exception text, source lines or local values."""

from __future__ import annotations

import os
from typing import Any

MAX_EXCEPTIONS = 4
MAX_FRAMES = 24


def exception_diagnostic(exc: BaseException) -> dict[str, Any]:
    chain: list[dict[str, Any]] = []
    seen = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen and len(chain) < MAX_EXCEPTIONS:
        seen.add(id(current))
        frames = []
        tb = current.__traceback__
        while tb is not None:
            code = tb.tb_frame.f_code
            frames.append({
                "file": os.path.basename(code.co_filename),
                "line": tb.tb_lineno,
                "function": code.co_name,
            })
            tb = tb.tb_next
        chain.append({"type": type(current).__name__, "frames": frames[-MAX_FRAMES:]})
        current = current.__cause__ or (
            None if current.__suppress_context__ else current.__context__
        )
    return {"exceptions": chain}


def validated_diagnostic(value: Any) -> dict[str, Any] | None:
    """Keep the log schema bounded even if a child sends malformed protocol data."""
    if not isinstance(value, dict) or not isinstance(value.get("exceptions"), list):
        return None
    chain: list[dict[str, Any]] = []
    for entry in value["exceptions"][:MAX_EXCEPTIONS]:
        if not isinstance(entry, dict) or not isinstance(entry.get("type"), str):
            return None
        if not isinstance(entry.get("frames"), list):
            return None
        frames = []
        for frame in entry["frames"][-MAX_FRAMES:]:
            if not isinstance(frame, dict) or not all(
                isinstance(frame.get(key), str) for key in ("file", "function")
            ) or not isinstance(frame.get("line"), int):
                return None
            frames.append({
                "file": os.path.basename(frame["file"].replace("\\", "/"))[:128],
                "line": max(0, frame["line"]),
                "function": frame["function"][:128],
            })
        chain.append({"type": entry["type"][:128], "frames": frames})
    return {"exceptions": chain}
