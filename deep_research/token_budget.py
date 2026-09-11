"""Token 预算保护：单次研究的累计 token 消耗上限，防止反思循环无限烧 token。

与 Tracer 解耦——Tracer 只统计，TokenBudget 负责判断是否该停。
"""

from __future__ import annotations

from dataclasses import dataclass


class TokenBudgetExceeded(RuntimeError):
    """No unreserved allowance remains for another provider call."""


@dataclass(frozen=True)
class TokenReservation:
    input_tokens: int
    output_tokens: int

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


class TokenBudget:
    """轻量预算检查器：无锁、非异步，适合在循环判断点同步调用。"""

    def __init__(self, *, max_tokens: int | None = None) -> None:
        self.max_tokens = max_tokens  # None = 不限
        self._consumed = 0
        self._reserved = 0

    @property
    def remaining(self) -> int | None:
        if self.max_tokens is None:
            return None
        return max(0, self.max_tokens - self._consumed - self._reserved)

    def reserve(self, input_tokens: int, output_limit: int) -> TokenReservation:
        remaining = self.remaining
        output = output_limit if remaining is None else min(output_limit, remaining - input_tokens)
        if output < 1:
            raise TokenBudgetExceeded("Token 预算不足，已停止新增模型请求")
        reservation = TokenReservation(input_tokens, output)
        self._reserved += reservation.total
        return reservation

    def release(self, reservation: TokenReservation) -> None:
        self._reserved = max(0, self._reserved - reservation.total)

    @property
    def exhausted(self) -> bool:
        if self.max_tokens is None:
            return False
        return self._consumed >= self.max_tokens

    def charge(self, n: int) -> None:
        self._consumed += max(0, n)

    def update(self, total_consumed: int) -> None:
        """从外部累计值（如 Tracer.total_tokens）同步消耗量。

        生产路径用它把「唯一真相源」Tracer 的累计 token 喂进来，避免与 charge() 双重计数；
        charge() 仍保留给单测 / 独立计数场景。取 max 保证单调不回退。
        """
        self._consumed = max(self._consumed, max(0, total_consumed))

    @property
    def consumed(self) -> int:
        return self._consumed
