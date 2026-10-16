"""Persist model request reservations before HTTP, including across recovery."""

from __future__ import annotations

import asyncio
import hashlib
import json

from .artifacts import ArtifactStore
from .blocking import run_blocking
from .call_budget import ModelCallBudget, ModelCallLimitExceeded
from .persistence.repository import LeaseLostError
from .token_budget import TokenBudgetExceeded

TASK_CALL_SCOPE_KEY = "_task_model_call_scope"


class CallBudgetStateError(TokenBudgetExceeded):
    """Paid work must stop when its durable request allowance cannot be verified."""


class TaskCallBudget(ModelCallBudget):
    def __init__(self, limit: int, store: ArtifactStore, scope: str) -> None:
        super().__init__(limit)
        self.store = store
        self.key = hashlib.sha256(scope.encode()).hexdigest()
        self.path = f"model-calls/{self.key}.json"
        self.lock = asyncio.Lock()

    def reserve(self) -> None:
        raise RuntimeError("Task request reservations must be awaited")

    async def reserve_async(self) -> None:
        async with self.lock:
            try:
                try:
                    raw = await run_blocking(self.store.read_control_text, self.path)
                except FileNotFoundError:
                    record = {"version": 1, "key": self.key, "limit": self.limit, "used": 0}
                else:
                    record = json.loads(raw)
                used = record.get("used")
                if not isinstance(used, int) or isinstance(used, bool):
                    raise ValueError("Task model-call counter is invalid")
                if (
                    record.get("version") != 1 or record.get("key") != self.key
                    or record.get("limit") != self.limit
                    or not self.used <= used <= self.limit
                ):
                    raise ValueError("Task model-call budget binding mismatch")
                self.used = used
                if self.used >= self.limit:
                    self.rejected = True
                    raise ModelCallLimitExceeded(
                        f"本任务已达到模型请求上限（{self.limit} 次），已停止新增请求。"
                    )
                record["used"] = used + 1
                # Persist first. A crash may consume a reserved slot but cannot
                # grant a fresh allowance to the same run after restart.
                saving = asyncio.create_task(
                    run_blocking(self.store.write_control_json, self.path, record)
                )
                cancelled = False
                while not saving.done():
                    try:
                        await asyncio.shield(saving)
                    except asyncio.CancelledError:
                        if saving.cancelled():
                            raise
                        cancelled = True
                saving.result()
                self.used = used + 1
                if cancelled:
                    raise asyncio.CancelledError
            except (LeaseLostError, ModelCallLimitExceeded):
                raise
            except Exception as exc:
                self.rejected = True
                raise CallBudgetStateError("模型请求额度记录无法确认，已停止新增请求。") from exc
