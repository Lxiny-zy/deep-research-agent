"""Classify completion issues using their checker codes, never prose keywords."""

from __future__ import annotations

from .gates import GateResult

COMPLETION_POLICY_VERSION = 2
ADVISORY_LENGTH_RATIO = 0.8
STYLE_CODES = frozenset(
    {
        "paired-frame",
        "colloquial",
        "production-narration",
        "overlong-sentence",
        "cjk-ascii-punctuation",
        "citation-cluster",
        "abstract-citation",
    }
)


def _messages(value: object) -> list[str] | None:
    return (
        list(value) if isinstance(value, list) and all(isinstance(x, str) for x in value) else None
    )


def classify_gates(gates: list[GateResult], *, minimum_length: int = 0) -> None:
    """Recompute classifications even when a registry already contains labels."""
    known_advisories: set[str] = set()
    for gate in gates:
        if gate.name == "revision":
            continue
        blocked: list[str] = []
        advice: list[str] = []
        fallback = gate.issues or [f"交付检查 {gate.name} 尚未通过"]
        findings = gate.metrics.get("findings")
        if (
            gate.name == "scholarly"
            and "findings" in gate.metrics
            and not isinstance(findings, list)
        ):
            blocked.append("学术检查问题记录无法分类")
        elif gate.name == "scholarly" and isinstance(findings, list) and findings:
            covered = set()
            for item in findings:
                if (
                    not isinstance(item, dict)
                    or not isinstance(item.get("code"), str)
                    or not isinstance(item.get("message"), str)
                    or not item["message"]
                ):
                    blocked.append("学术检查问题记录无法分类")
                    continue
                message = item["message"]
                covered.update({message, "（建议）" + message})
                (advice if item["code"] in STYLE_CODES else blocked).append(message)
            # Unknown or omitted checker output cannot disappear behind known style codes.
            blocked.extend(message for message in gate.issues if message not in covered)
            if gate.status == "fail" and not blocked:
                blocked.extend(fallback)
        elif gate.name == "length" and gate.status == "warn":
            length = gate.metrics.get("length")
            if (
                isinstance(length, int)
                and not isinstance(length, bool)
                and minimum_length > 0
                and length >= minimum_length * ADVISORY_LENGTH_RATIO
            ):
                advice.extend(fallback)
            else:
                blocked.extend(fallback)
        elif gate.status == "pass":
            advice.extend(gate.issues)
        else:
            blocked.extend(fallback)
        gate.blocking_issues = list(dict.fromkeys(blocked))
        gate.advisories = list(dict.fromkeys(advice))
        known_advisories.update(advice)
    for gate in gates:
        if gate.name != "revision":
            continue
        remaining = _messages(gate.metrics.get("remaining_issues"))
        malformed = "remaining_issues" in gate.metrics and remaining is None
        if remaining is None:
            remaining = list(gate.issues) if gate.status != "pass" else []
        soft_record = _messages(gate.metrics.get("advisory_issues"))
        malformed = malformed or ("advisory_issues" in gate.metrics and soft_record is None)
        soft = soft_record or []
        gate.blocking_issues = [message for message in remaining if message not in known_advisories]
        gate.advisories = list(
            dict.fromkeys(
                [*soft, *(message for message in remaining if message in known_advisories)]
            )
        )
        if gate.status == "pass" and not remaining:
            gate.advisories = list(dict.fromkeys([*gate.advisories, *gate.issues]))
        if gate.status != "pass" and not remaining:
            gate.blocking_issues = ["修订检查未通过且缺少可分类的问题记录"]
        if gate.status == "fail" and not gate.blocking_issues:
            gate.blocking_issues = gate.issues or ["修订检查失败"]
        declared = gate.metrics.get("remaining")
        if malformed or (isinstance(declared, int) and declared > len(remaining)):
            gate.blocking_issues.append("修订问题记录缺失或无法完整分类")


def blocking_status(gates: list[GateResult]) -> str:
    blockers = [gate for gate in gates if gate.blocking_issues]
    return "fail" if any(g.status == "fail" for g in blockers) else "warn" if blockers else "pass"
