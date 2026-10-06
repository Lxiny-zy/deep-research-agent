"""Shared conservative context estimates, not a provider's exact tokenizer.

ASCII word runs use three characters per estimated token, whitespace four;
punctuation costs one and non-ASCII text up to two UTF-8 bytes per token.
The estimate deliberately leaves room for mixed Chinese, URLs and structured
records. Provider usage remains the sole source of exact token accounting.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, replace
from typing import Any

PROMPT_OVERHEAD_TOKENS = 128
DEFAULT_OUTPUT_RESERVE = 8192


def _calibration_key(llm: Any) -> str:
    model = str(getattr(llm, "model", ""))
    endpoint = str(
        getattr(getattr(llm, "client", None), "base_url", None) or getattr(llm, "base_url", "")
    )
    return hashlib.sha256((model + "\n" + endpoint).encode()).hexdigest()[:24]


def observe_context_usage(llm: Any, system: str, user: str, actual_input_tokens: Any) -> None:
    """Consume an already-returned per-request input count; never make a call."""
    if (
        not isinstance(actual_input_tokens, int)
        or isinstance(actual_input_tokens, bool)
        or not 0 < actual_input_tokens <= 2**31
    ):
        return
    # LLM initializes this dictionary once; for_role's shallow copies share it.
    # Keep only aggregate numbers, not prompts, raw responses or credentials.
    calibration = getattr(llm, "_context_usage_calibration", None)
    if not isinstance(calibration, dict):
        calibration = {}
        llm._context_usage_calibration = calibration
    key = _calibration_key(llm)
    if key + ":factor" not in calibration and len(calibration) >= 128:
        return
    estimated = prompt_tokens(system, user)
    ratio = actual_input_tokens / estimated
    calibration[key + ":factor"] = max(1.0, calibration.get(key + ":factor", 1.0), ratio * 1.1)
    calibration[key + ":samples"] = calibration.get(key + ":samples", 0.0) + 1.0
    calibration[key + ":last_ratio"] = ratio
    calibration[key + ":last_reported"] = float(actual_input_tokens)


class TokenEstimator:
    """Incremental equivalent of estimate_tokens, independent of stream chunk size."""

    def __init__(self) -> None:
        self.total = 0
        self._kind = ""
        self._run = 0

    def feed(self, text: str) -> int:
        for char in text:
            kind = (
                "word"
                if char.isascii() and (char.isalnum() or char == "_")
                else ("space" if char in " \t\r\n" else "")
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
    calibration_factor: float = 1.0
    calibration_samples: int = 0

    @classmethod
    def for_limits(
        cls,
        context_window_tokens: int | None,
        max_output_tokens: int | None,
        fallback_chars: int = 200_000,
    ) -> ContextBudget:
        output = max_output_tokens or (
            min(DEFAULT_OUTPUT_RESERVE, max(1, context_window_tokens // 4))
            if context_window_tokens
            else DEFAULT_OUTPUT_RESERVE
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
        budget = cls.for_limits(
            window if isinstance(window, int) and not isinstance(window, bool) else None,
            output,
            chars,
        )
        calibration = getattr(llm, "_context_usage_calibration", None)
        key = _calibration_key(llm)
        if isinstance(calibration, dict) and key + ":factor" in calibration:
            budget = replace(
                budget,
                calibration_factor=calibration[key + ":factor"],
                calibration_samples=int(calibration[key + ":samples"]),
            )
        return budget

    @property
    def input_capacity_chars(self) -> int:
        """Safe legacy planning hint; modern callers can fit actual text more tightly."""
        if self.legacy_input_chars is not None:
            return self.legacy_input_chars
        return (
            max(0, int(self.input_tokens / self.calibration_factor) - PROMPT_OVERHEAD_TOKENS) // 2
        )

    def estimated_prompt(self, system: str, *parts: str) -> int:
        return math.ceil(prompt_tokens(system, *parts) * self.calibration_factor)

    def diagnostics(self) -> dict[str, Any]:
        return {
            "method": "usage_calibrated_estimate"
            if self.calibration_samples
            else "conservative_estimate",
            "calibration_samples": self.calibration_samples,
            "calibration_factor": round(self.calibration_factor, 4),
            "configured_input_tokens": self.input_tokens if self.enforced else None,
            "output_reserve_tokens": self.output_tokens,
            "provider_tokenizer_verified": False,
        }

    def remaining(self, system: str, *fixed_parts: str, reserve_tokens: int = 0) -> int:
        # Return planning-estimator units so callers dividing by two to reserve
        # Unicode characters also respect an observed provider overhead increase.
        available = int(max(0, self.input_tokens - reserve_tokens) / self.calibration_factor)
        return max(0, available - prompt_tokens(system, *fixed_parts))

    def fits(self, system: str, user: str, *, reserve_tokens: int = 0) -> bool:
        if (
            self.legacy_input_chars is not None
            and len(system) + len(user) > self.legacy_input_chars
        ):
            return False
        return self.estimated_prompt(system, user) + reserve_tokens <= self.input_tokens
