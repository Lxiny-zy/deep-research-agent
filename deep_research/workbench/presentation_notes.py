"""One deterministic appendix dispatch for pages, exports and acceptance locations."""

from __future__ import annotations

from typing import Any

PRESENTATION_POLICY_VERSION = 1


def record_references(markdown: str) -> str:
    """Record-source labels must not create new scientific citation occurrences."""
    import re

    from .delivery.math_markdown import citation_text

    visible = citation_text(markdown)
    for match in reversed(list(re.finditer(r"\[(\d+(?:\s*[,，]\s*\d+)*)\]", visible))):
        markdown = (
            markdown[: match.start()] + f"（原记录来源位置 {match[1]}）" + markdown[match.end() :]
        )
    return markdown


def presentation_sections(detail: Any) -> list[dict[str, str]]:
    from .contract import contract_from_scratch

    scratch = detail.orchestration.checkpoint.get("scratch", {}) if detail.orchestration else {}
    contract = contract_from_scratch(scratch)
    template = contract.template if contract else scratch.get("workbench", {}).get("template")
    if detail.report is None:
        return []
    if template in {"autoResearch", "litReview"}:
        from .research_presentation import research_notes

        body, key, title = research_notes(detail), "research-evidence", "研究证据与比较口径记录"
    elif template == "peerReview":
        from .reading_presentation import peer_notes

        body, key, title = peer_notes(detail), "peer-review-records", "评审覆盖与意见记录"
    elif template == "paperRead":
        from .reading_presentation import paper_notes

        body, key, title = paper_notes(detail), "paper-reading-records", "精读关注点与证据范围"
    else:
        return []
    return (
        [
            {
                "id": key,
                "title": title,
                "markdown": record_references(body),
                "origin": "record_presentation",
                "verification_status": "source_record",
            }
        ]
        if body
        else []
    )


def presentation_markdown(detail: Any) -> str:
    sections = presentation_sections(detail)
    if not sections:
        return ""
    heading = (
        "## 阅读与核验记录\n\n> 以下是已有记录的只读展示；"
        "未绑定项按未确认处理，不是新增论文结论。\n\n"
    )
    return heading + "\n\n".join(section["markdown"] for section in sections)


def append_presentation(markdown: str, detail: Any) -> str:
    appendix = presentation_markdown(detail)
    if not appendix:
        return markdown
    return markdown.rstrip() + "\n\n" + appendix + "\n"
