"""Frozen compatibility rules for template-expanded request hashes (v1).

Do not consult today's template registry when recognizing historical requests.
All fields except the two historically overwritten fields remain in the hash.
"""

from __future__ import annotations

from typing import Any

_V1_TEMPLATES: dict[str, tuple[str, dict[str, str]]] = {
    "autoResearch": ("deep", {"deep": "research", "quick": "research_quick"}),
    "litReview": ("deep", {
        "deep": "lit_review", "quick": "lit_review_quick", "none": "lit_review_provided",
    }),
    "peerReview": ("none", {"none": "peer_review"}),
    "paperRead": ("none", {"none": "paper_read"}),
    "dataAnalysis": ("none", {"none": "data_analysis"}),
    "slides": ("quick", {
        "quick": "slides", "deep": "slides_deep", "none": "slides_provided",
    }),
    "mindmap": ("quick", {
        "quick": "mindmap", "deep": "mindmap_deep", "none": "mindmap_provided",
    }),
}


def legacy_template_update(
    template: str | None, strategy: str | None, workflow: str | None,
) -> dict[str, Any] | None:
    entry = _V1_TEMPLATES.get(template or "")
    if entry is None:
        return None
    default, workflows = entry
    effective = workflows.get(strategy or default)
    if effective is None:
        return None
    if template == "autoResearch" and strategy is None and workflow:
        return None  # The old handler preserved this explicit workflow.
    # An arbitrary explicit workflow cannot safely be reconstructed from a
    # hash that discarded it. Only absent or matching workflow is compatible.
    if workflow not in (None, "", effective):
        return None
    result: dict[str, Any] = {"workflow": effective}
    if template != "autoResearch":
        result["clarified"] = True
    return result
