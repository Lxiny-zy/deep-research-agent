"""A request-scoped ceiling for model HTTP attempts, shared across QA roles."""

from dataclasses import dataclass

from .token_budget import TokenBudgetExceeded


class ModelCallLimitExceeded(TokenBudgetExceeded):
    """No more model requests may start in this QA turn."""


@dataclass
class ModelCallBudget:
    limit: int
    used: int = 0
    rejected: bool = False

    def __post_init__(self) -> None:
        if self.limit < 1:
            raise ValueError("Model call limit must be positive")

    def reserve(self) -> None:
        # No await between checking and reserving: parallel async roles share
        # one counter and failed HTTP attempts still consume a slot.
        if self.used >= self.limit:
            self.rejected = True
            raise ModelCallLimitExceeded(
                f"本轮已达到模型调用上限（{self.limit} 次），已停止新增模型请求。"
            )
        self.used += 1

