"""Actual model transport attempts, without prompts, secrets or inferred billing."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from .metrics import metrics

if TYPE_CHECKING:
    from .observability import Tracer


@dataclass
class ModelOperation:
    kind: str
    role: str | None = None
    schema: str | None = None
    id: str = field(default_factory=lambda: uuid4().hex)
    attempts: int = 0
    retry_reason: str | None = None


_operation: ContextVar[ModelOperation | None] = ContextVar("model_operation", default=None)


@contextmanager
def model_operation(
    kind: str, *, role: str | None = None, schema: str | None = None,
) -> Iterator[ModelOperation]:
    operation = ModelOperation(kind=kind, role=role, schema=schema)
    token = _operation.set(operation)
    try:
        yield operation
    finally:
        _operation.reset(token)


def current_operation() -> ModelOperation:
    return _operation.get() or ModelOperation(kind="generation")


class ModelCall:
    """One actual SDK/HTTP invocation. Terminal recording is idempotent."""

    def __init__(
        self, tracer: Tracer | None, model: str, *, operation: ModelOperation | None = None,
        retry_reason: str | None = None,
    ) -> None:
        self.tracer = tracer
        self.operation = operation or current_operation()
        self.operation.attempts += 1
        self.id = uuid4().hex
        self.started = time.monotonic()
        self.finished = False
        self.data: dict[str, Any] = {
            "call_id": self.id,
            "operation_id": self.operation.id,
            "attempt": self.operation.attempts,
            "operation": self.operation.kind,
            "role": self.operation.role,
            "schema": self.operation.schema,
            "model": model,
            "retry_reason": retry_reason or self.operation.retry_reason,
            "trace_id": getattr(tracer, "trace_id", None),
            "run_id": getattr(tracer, "run_id", None),
            "request_id": getattr(tracer, "request_id", None),
            "conversation_id": getattr(tracer, "conversation_id", None),
        }
        self._emit("started", "模型请求开始", usage_state="pending")
        metrics.inc("dr_model_http_attempts_total", {"operation": self.operation.kind})

    def _emit(self, status: str, message: str, **details: Any) -> None:
        if self.tracer is not None:
            self.tracer.emit(
                "LLM", "info", message,
                data={"model_call": {**self.data, "status": status, **details}},
            )

    def finish(
        self, status: str = "succeeded", *, error: BaseException | None = None,
        usage: dict[str, int | None] | None = None, finish_reason: str | None = None,
    ) -> None:
        if self.finished:
            return
        self.finished = True
        if error is not None:
            status = (
                "cancelled" if isinstance(error, (asyncio.CancelledError, GeneratorExit))
                else "failed"
            )
        duration_ms = max(0, round((time.monotonic() - self.started) * 1000))
        safe_usage = {
            key: value if type(value) is int and value >= 0 else None
            for key, value in (usage or {}).items()
            if key in {
                "input_tokens", "output_tokens", "reasoning_tokens", "total_tokens",
                "cached_input_tokens", "cache_write_input_tokens",
            }
        }
        usage_state = "reported" if safe_usage.get("total_tokens") is not None else (
            "partial" if any(value is not None for value in safe_usage.values()) else "unavailable"
        )
        # Exception text and provider payloads can contain prompts or credentials.
        self._emit(
            status, "模型请求已结束" if status == "succeeded" else "模型请求未完成",
            duration_ms=duration_ms, finish_reason=finish_reason, usage_state=usage_state,
            usage=safe_usage or None,
            error_type=type(error).__name__ if error is not None else None,
        )
        labels = {"operation": self.operation.kind, "status": status}
        metrics.inc("dr_model_http_results_total", labels)
        metrics.observe("dr_model_http_duration_seconds", duration_ms / 1000, labels)
