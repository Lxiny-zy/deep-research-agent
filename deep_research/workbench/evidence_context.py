"""Lossless measured-value context shared by evidence review and research views."""

from __future__ import annotations

from typing import Any

from ..models import Finding

MEASUREMENT_CONTEXT_VERSION = 1


def measurement_context(finding: Finding) -> dict[str, Any] | None:
    quantity = finding.quantity
    conditions = finding.conditions
    has_quantity = quantity is not None and any(
        value not in (None, "") for value in quantity.model_dump().values()
    )
    if not has_quantity and (conditions is None or conditions.is_empty()):
        return None
    return {
        "version": MEASUREMENT_CONTEXT_VERSION,
        "quantity": quantity.model_dump(mode="json") if quantity else None,
        "conditions": conditions.model_dump(mode="json") if conditions else None,
        "quantity_status": finding.verification.quantity_status,
        "quantity_reason": finding.verification.quantity_reason,
        "condition_verification": "must_be_checked_against_quote",
    }
