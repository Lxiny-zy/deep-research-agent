"""Persist complete reflection rounds and pending research before paid work."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .artifacts import ArtifactStore
from .research_progress import ResearchProgressError


def digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


class ReflectionProgress:
    def __init__(self, store: ArtifactStore, scope: dict[str, Any]) -> None:
        self.store = store
        self.key = digest(scope)
        self.path = f"reflection/{self.key}.json"

    def load(self) -> dict[str, Any] | None:
        try:
            raw = self.store.read_control_text(self.path)
        except FileNotFoundError:
            return None
        except Exception as exc:
            raise ResearchProgressError("反思进度读取失败") from exc
        try:
            record = json.loads(raw)
            state = record["state"]
            if record["key"] != self.key or record["digest"] != digest(state):
                raise ValueError("reflection progress mismatch")
            if not isinstance(state, dict) or type(state["round"]) is not int:
                raise ValueError("invalid reflection round")
            return state
        except Exception as exc:
            raise ResearchProgressError("反思进度校验失败，未重新执行研究") from exc

    def save(self, state: dict[str, Any]) -> None:
        try:
            self.store.write_control_json(self.path, {
                "key": self.key, "state": state, "digest": digest(state),
            })
        except Exception as exc:
            raise ResearchProgressError("反思进度保存失败，已停止后续研究") from exc
