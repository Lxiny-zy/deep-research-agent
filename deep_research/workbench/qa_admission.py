"""Shared QA capacity policy. Counts are checked inside the admission lock."""

from dataclasses import dataclass
from typing import Any


class QaQueueFull(ValueError):
    pass


@dataclass(frozen=True)
class QaLimits:
    active: int = 4
    active_per_owner: int = 2
    pending: int = 64
    pending_per_owner: int = 16

    @classmethod
    def from_settings(cls, settings: Any) -> "QaLimits":
        defaults = cls()
        return cls(
            **{
                name: getattr(settings, f"qa_max_{name}", getattr(defaults, name))
                for name in cls.__dataclass_fields__
            }
        )

    def check_pending(self, total: int, owned: int) -> None:
        if total >= self.pending or owned >= self.pending_per_owner:
            raise QaQueueFull("问答队列已满，请等待已有问题完成后再提交")

    def can_claim(self, total: int, owned: int) -> bool:
        return total < self.active and owned < self.active_per_owner
