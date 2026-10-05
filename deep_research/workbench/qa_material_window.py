"""Fit complete verified findings into one answer request without weakening evidence.

Selection changes only the generation window. Callers retain the original
findings/sources for verification and must not infer absence from omitted items.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from ..context_budget import ContextBudget
from ..models import Finding
from ..prompting import PrefixPrompt
from .support import evidence_id

_ORIGIN_TAG = {
    "paper": "【本论文】",
    "research": "【本次任务】",
    "library": "【其他文献·资料库】",
    "web": "【其他文献】",
}
CapacityStatus = Literal["complete", "partial", "no_capacity", "bound_material_exceeds_capacity"]


def render_evidence_lines(
    findings: list[Finding], origins: dict[str, str] | None = None,
    citations: list[str] | None = None,
) -> tuple[list[str], dict[str, int]]:
    """Use the same full quote, reference and citation numbering as QA composition."""
    mapping = {url: index for index, url in enumerate(citations or [], 1)}
    lines = []
    for finding in findings:
        index = mapping.setdefault(finding.source_url, len(mapping) + 1)
        tag = _ORIGIN_TAG.get((origins or {}).get(finding.source_url, "web"), "")
        reference = finding.verification.source_reference or finding.verification.source_title
        lines.append(
            f"- [{index}]{tag} {finding.statement}\n  原文：{finding.evidence_quote}"
            + (f"\n  出处：{reference}" if reference else "")
        )
    return lines, mapping


def _materials(lines: list[str], omitted: int) -> str:
    note = (
        f"本轮上下文仅包含以下 {len(lines)} 条完整核验发现；另有 {omitted} 条已保存发现"
        "未纳入本次生成，不能据此推断原文缺失或没有其他证据。\n"
        if omitted else ""
    )
    return "【已核验素材】\n" + note + "\n".join(lines)


def _prompt(materials: str, question: str, context: str, indices: list[int]) -> PrefixPrompt:
    return PrefixPrompt(
        materials,
        f"\n\n{context}\n\n【用户问题】\n{question}\n\n本次可用引用编号："
        + " ".join(f"[{index}]" for index in indices)
        + "。引用只选这些编号，不复制引句中原论文的文献编号。",
    )


def _remaining_chars(budget: ContextBudget, system: str, prompt: str) -> int:
    # Inserting dialogue can split an existing whitespace/word run at either
    # boundary, so leave a few estimated tokens beyond the literal text length.
    chars = max(0, budget.remaining(system, prompt) - 4) // 2
    if budget.legacy_input_chars is not None:
        chars = min(chars, budget.legacy_input_chars - len(system) - len(prompt))
    return max(0, chars)


@dataclass(frozen=True)
class AnswerMaterialSelection:
    findings: list[Finding]
    omitted_ids: list[str]
    omitted_count: int
    materials_text: str
    url_to_idx: dict[str, int]
    available_citations: list[int]
    dialogue_capacity_chars: int
    dialogue_capacity_tokens: int
    dialogue_reserved_chars: int
    status: CapacityStatus
    question: str
    fixed_context: str

    @property
    def can_generate(self) -> bool:
        return bool(self.findings) and self.status in {"complete", "partial"}

    def prompt(self, dialogue: str = "") -> PrefixPrompt:
        """Render exactly the framing budgeted by selection; dialogue is the dynamic suffix."""
        return _prompt(
            self.materials_text, self.question, self.fixed_context + dialogue,
            self.available_citations,
        )


def select_answer_findings(
    findings: list[Finding], *, model: Any, system: str, question: str,
    fixed_context: str = "", origins: dict[str, str] | None = None,
    citations: list[str] | None = None, reserve_dialogue_chars: int = 1024,
    preserve_all: bool = False, fallback_chars: int = 200_000,
) -> AnswerMaterialSelection:
    """Stable greedy selection of whole findings, skipping individually oversized items.

    Dialogue reservation is capped at a quarter of otherwise available space.
    A bound revision uses preserve_all=True: oversized material is returned
    intact with an explicit failure status, never silently renumbered or cut.
    system must be the final composed system prompt, including applicable rules.
    """
    budget = ContextBudget.from_model(model, fallback_chars)
    empty = _prompt(_materials([], len(findings)), question, fixed_context, [])
    reserve = min(max(0, reserve_dialogue_chars), _remaining_chars(budget, system, empty) // 4)

    def render(selected: list[Finding]) -> tuple[str, dict[str, int], list[int], PrefixPrompt]:
        lines, mapping = render_evidence_lines(selected, origins, citations)
        materials = _materials(lines, len(findings) - len(selected))
        used = {finding.source_url for finding in selected}
        indices = [index for url, index in mapping.items() if url in used]
        return materials, mapping, indices, _prompt(materials, question, fixed_context, indices)

    def fits(prompt: str) -> bool:
        return budget.fits(system, prompt, reserve_tokens=reserve * 2) and (
            _remaining_chars(budget, system, prompt) >= reserve
        )

    selected: list[Finding] = []
    omitted: list[Finding] = []
    full = render(findings)
    if preserve_all or fits(full[3]):
        selected = list(findings)
    else:
        for finding in findings:
            trial = [*selected, finding]
            if fits(render(trial)[3]):
                selected = trial
            else:
                omitted.append(finding)
    materials, mapping, indices, prompt = render(selected)
    fits_final = fits(prompt)
    status: CapacityStatus
    if preserve_all and not fits_final:
        status = "bound_material_exceeds_capacity"
    elif not fits_final or (findings and not selected):
        status = "no_capacity"
    else:
        status = "partial" if omitted else "complete"
    return AnswerMaterialSelection(
        findings=selected,
        omitted_ids=list(dict.fromkeys(evidence_id(finding) for finding in omitted)),
        omitted_count=len(omitted), materials_text=materials, url_to_idx=mapping,
        available_citations=indices,
        dialogue_capacity_chars=_remaining_chars(budget, system, prompt) if fits_final else 0,
        dialogue_capacity_tokens=max(0, budget.remaining(system, prompt) - 4) if fits_final else 0,
        dialogue_reserved_chars=reserve, status=status, question=question,
        fixed_context=fixed_context,
    )
