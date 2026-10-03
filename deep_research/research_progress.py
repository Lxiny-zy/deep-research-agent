"""Durable, run-scoped subquestion results independent of the enclosing step."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .artifacts import ArtifactStore
from .models import Finding, ResearchResult


class ResearchProgressError(RuntimeError):
    """A required progress record cannot be saved or safely restored."""


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class ResearchProgress:
    def __init__(self, store: ArtifactStore, scope: dict[str, Any]) -> None:
        self.store = store
        self.scope = _digest({"version": 1, **scope})

    def key(self, question: str, context: list[Finding] | None) -> str:
        return _digest(
            {
                "scope": self.scope,
                "question": question,
                "context": [finding.model_dump(mode="json") for finding in context or []],
            }
        )

    def load(self, key: str, question: str) -> ResearchResult | None:
        try:
            text = self.store.read_control_text(f"research/{key}.json")
        except FileNotFoundError:
            return None
        except Exception as exc:
            raise ResearchProgressError("子问题进度读取失败，未重新执行付费检索") from exc
        try:
            saved = json.loads(text)
            if saved["key"] != key or saved["digest"] != _digest(saved["result"]):
                raise ValueError("progress binding mismatch")
            result = ResearchResult.model_validate(saved["result"])
            if result.sub_question != question or result.extraction_audit is None:
                raise ValueError("progress question or source audit mismatch")
            return result
        except Exception as exc:
            raise ResearchProgressError("子问题进度校验失败，不能复用或静默覆盖") from exc

    def save(self, key: str, result: ResearchResult) -> None:
        # Legacy/direct callers may provide findings without raw source audits.
        # They cannot be restored as a complete, reproducible research result.
        if result.extraction_audit is None:
            return
        data = result.model_dump(mode="json")
        try:
            self.store.write_control_json(
                f"research/{key}.json",
                {
                    "key": key,
                    "digest": _digest(data),
                    "result": data,
                },
            )
        except Exception as exc:
            raise ResearchProgressError("子问题进度保存失败，已停止继续消耗模型调用") from exc
