"""Shared conservative context estimates, not a provider's exact tokenizer.

ASCII word runs use three characters per estimated token, whitespace four;
punctuation costs one and non-ASCII text up to two UTF-8 bytes per token.
The estimate deliberately leaves room for mixed Chinese, URLs and structured
records. Provider usage remains the sole source of exact token accounting.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

PROMPT_OVERHEAD_TOKENS = 128
DEFAULT_OUTPUT_RESERVE = 8192


class TokenEstimator:
    """Incremental equivalent of estimate_tokens, independent of stream chunk size."""

    def __init__(self) -> None:
        self.total = 0
        self._kind = ""
        self._run = 0

    def feed(self, text: str) -> int:
        for char in text:
            kind = "word" if char.isascii() and (char.isalnum() or char == "_") else (
                "space" if char in " \t\r\n" else ""
            )
            if kind:
                if kind != self._kind:
                    self._run = 0
                divisor = 3 if kind == "word" else 4
                self.total += int(self._run % divisor == 0)
                self._run += 1
            else:
                self.total += (len(char.encode("utf-8")) + 1) // 2
                self._run = 0
            self._kind = kind
        return self.total


def estimate_tokens(*texts: str) -> int:
    """Estimate separate text fields without letting word runs cross their boundary."""
    return sum(TokenEstimator().feed(text) for text in texts)


def prompt_tokens(system: str, *parts: str) -> int:
    return PROMPT_OVERHEAD_TOKENS + estimate_tokens(system, *parts)


@dataclass(frozen=True)
class ContextBudget:
    input_tokens: int
    output_tokens: int
    enforced: bool
    legacy_input_chars: int | None = None

    @classmethod
    def for_limits(
        cls, context_window_tokens: int | None, max_output_tokens: int | None,
        fallback_chars: int = 200_000,
    ) -> ContextBudget:
        output = max_output_tokens or (
            min(DEFAULT_OUTPUT_RESERVE, max(1, context_window_tokens // 4))
            if context_window_tokens else DEFAULT_OUTPUT_RESERVE
        )
        if context_window_tokens is not None:
            return cls(max(0, context_window_tokens - output), output, True)
        # A legacy character target is planning guidance, not a known provider
        # window. Preserve its meaning without inventing a provider token limit.
        chars = max(0, fallback_chars)
        return cls(chars * 2 + PROMPT_OVERHEAD_TOKENS, output, False, chars)

    @classmethod
    def from_model(cls, llm: Any, fallback_chars: int = 200_000) -> ContextBudget:
        settings = getattr(llm, "settings", None)
        window = getattr(llm, "context_window_tokens", None)
        output = getattr(settings, "llm_max_output_tokens", None)
        if output is None:
            output = getattr(llm, "max_output_tokens", None)
        chars = getattr(llm, "input_capacity_chars", fallback_chars)
        if not isinstance(chars, int):
            chars = fallback_chars
        return cls.for_limits(window if isinstance(window, int) else None, output, chars)

    @property
    def input_capacity_chars(self) -> int:
        """Safe legacy planning hint; modern callers can fit actual text more tightly."""
        if self.legacy_input_chars is not None:
            return self.legacy_input_chars
        return max(0, self.input_tokens - PROMPT_OVERHEAD_TOKENS) // 2

    def remaining(self, system: str, *fixed_parts: str, reserve_tokens: int = 0) -> int:
        return max(0, self.input_tokens - prompt_tokens(system, *fixed_parts) - reserve_tokens)

    def fits(self, system: str, user: str, *, reserve_tokens: int = 0) -> bool:
        if (
            self.legacy_input_chars is not None
            and len(system) + len(user) > self.legacy_input_chars
        ):
            return False
        return prompt_tokens(system, user) + reserve_tokens <= self.input_tokens
