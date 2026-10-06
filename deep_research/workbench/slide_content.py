"""Slide contracts and deterministic projections; reuse the evidence-table pipeline."""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..models import ResearchResult
from .tables import TableSpec, admitted_findings, render_specs, render_table

SLIDE_POLICY_VERSION = 2
_EMPTY_NOTES = {"", "无", "无备注", "暂无", "暂无备注", "none", "n/a", "notes"}


def meaningful_notes(text: str) -> bool:
    text = re.sub(r"\[\d+(?:[,，\s]+\d+)*\]", "", text)
    normalized = re.sub(r"[\s（）()【】\[\]。.!！?？:：,，]", "", text).casefold()
    return normalized not in _EMPTY_NOTES and bool(re.search(r"[\w\u3400-\u9fff]", normalized))


def spoken_units(text: str) -> int:
    """CJK characters plus non-CJK words; an estimate, not a speaking assessment."""
    text = re.sub(r"\[\d+(?:[,，\s]+\d+)*\]", "", text)
    return len(re.findall(r"[\u3400-\u9fff]|[A-Za-z0-9]+(?:['.-][A-Za-z0-9]+)*", text))


class SlideVisual(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["table", "bar", "line"] = "table"
    table: TableSpec
    value_columns: list[str] = Field(default_factory=list, max_length=3)


class SlideImage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    finding_id: str = Field(min_length=1, max_length=64)
    figure_label: str = Field(min_length=2, max_length=40)
    # These fields are populated from the task-owned source catalog, not trusted
    # from the model's assertions. No model crop coordinates are accepted.
    page: int | None = Field(None, ge=1)
    source_name: str = ""
    source_url: str = ""
    asset_id: str = ""
    snapshot: Literal["full_page", "user_region"] = "full_page"


class Slide(BaseModel):
    title: str = Field(max_length=120)
    bullets: list[str] = Field(default_factory=list, max_length=6)
    notes: str = Field("", max_length=6000)
    citations: list[int] = Field(default_factory=list)
    visual: SlideVisual | None = None
    image: SlideImage | None = None

    @model_validator(mode="after")
    def one_visual(self) -> Slide:
        if self.visual is not None and self.image is not None:
            raise ValueError("一页只能选择一种图表或原文图片")
        return self


class SlideDeck(BaseModel):
    title: str = Field(max_length=160)
    subtitle: str = Field("", max_length=200)
    slides: list[Slide] = Field(default_factory=list, max_length=30)
    audience: str = Field("", max_length=160)
    # Filled from the frozen user contract, never trusted from model output.
    target_minutes: float | None = Field(None, gt=0, le=180)
    target_pages: int | None = Field(None, ge=1, le=100)
    speaking_rate: int = Field(220, ge=100, le=350)


def apply_brief(deck: SlideDeck, contract: Any, query: str) -> SlideDeck:
    """Only unambiguous explicit targets become hard budgets; retain other prose."""
    text = "\n".join(
        [
            str(getattr(contract, "original_request", "") or query),
            *getattr(contract, "constraints", []),
            str(getattr(contract, "confirmed_choices", {})),
        ]
    )
    minutes = {
        float(x)
        for x in re.findall(r"(?<![\d.])(\d+(?:\.\d+)?)\s*(?:分钟|minutes?\b|min\b)", text, re.I)
    }
    pages = {
        int(x) for x in re.findall(r"(?<![\d.])(\d+)\s*(?:页(?:幻灯片|PPT)?|slides?\b)", text, re.I)
    }
    rates = {
        int(x) for x in re.findall(r"每分钟\s*(?:约\s*)?(\d+)\s*(?:字|词)", text)
    }
    choice_rate = getattr(contract, "confirmed_choices", {}).get("speaking_rate")
    if isinstance(choice_rate, int) and not isinstance(choice_rate, bool):
        rates.add(choice_rate)
    rate = next(iter(rates)) if len(rates) == 1 else 220
    rate = rate if 100 <= rate <= 350 else 220
    # Ranges, maxima and negations require interpretation, not guessed equalities.
    ambiguous = bool(
        re.search(
            r"至少|最多|不超过|不少于|不要|不需要|[-–—~～至]\s*\d|at (?:least|most)", text, re.I
        )
    )
    return deck.model_copy(
        update={
            "target_minutes": next(iter(minutes))
            if len(minutes) == 1 and not ambiguous and 0 < next(iter(minutes)) <= 180
            else None,
            "target_pages": next(iter(pages))
            if len(pages) == 1 and not ambiguous and 1 <= next(iter(pages)) <= 100
            else None,
            "speaking_rate": rate,
        }
    )


def deck_to_markdown(deck: SlideDeck) -> str:
    lines = [f"# {deck.title}", ""]
    if deck.subtitle:
        cite = "".join(f"[{i}]" for i in sorted({i for s in deck.slides for i in s.citations}))
        lines += [f"{deck.subtitle} {cite}".strip(), ""]
    for number, slide in enumerate(deck.slides, 1):
        cite = "".join(f"[{i}]" for i in slide.citations)
        lines.append(f"## {number}. {slide.title}")
        lines += [f"- {bullet} {cite}".rstrip() for bullet in slide.bullets]
        if slide.visual:
            lines += ["", "```evidence-table", slide.visual.table.model_dump_json(), "```", ""]
        if slide.image:
            pic = slide.image
            if not pic.asset_id or pic.page is None:
                raise ValueError("原文图页尚未登记，不能生成虚构图片链接")
            selection = (
                "用户选择区域；完整上下文请查看原文"
                if pic.snapshot == "user_region" else "整页图；包含周边上下文"
            )
            lines += [
                "",
                f"![{pic.figure_label}，原文第 {pic.page} 页{selection}，"
                f"图片不可编辑 {cite}]({pic.asset_id}.png)",
                "",
                f"图片来源：{pic.source_name}，{pic.figure_label}，第 {pic.page} 页 {cite}",
                "",
            ]
        if cite:
            lines.append(f"\n来源：{cite}")
        if slide.notes:
            lines += ["", f"> 演讲备注：{slide.notes} {cite}".rstrip()]
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def compile_deck(
    raw: dict[str, Any],
    results: list[ResearchResult],
    mapping: dict[str, int],
    *,
    corroboration: bool = False,
    image_sources: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str]:
    """Rebuild visible data from admitted findings; model-supplied data is ignored.

    This does not declare evidence verified. Existing table/prose gates must pass
    before publication. The same specification goes through review_tables.
    """
    deck = SlideDeck.model_validate(raw)
    compiled = deck.model_dump(mode="json")
    ids: set[str] = set()
    for source, target in zip(deck.slides, compiled["slides"], strict=True):
        if source.image:
            from .slide_images import validate_image_registry

            record = validate_image_registry(image_sources or {}, source.image)
            finding = admitted_findings(results, mapping, corroboration).get(
                source.image.finding_id
            )
            if finding is None or record["quote"] != finding.evidence_quote:
                raise ValueError("原文图片的发现已变化或不再通过当前核验")
            image_citation = mapping.get(record["source_url"])
            if image_citation is None or image_citation not in source.citations:
                raise ValueError("原文图页必须引用对应的当前材料编号")
            target["visual_data"] = {
                "kind": "source_image",
                "id": record["id"],
                "asset": record["id"] + ".png",
                "caption": f"{record['figure_label']} · {record['filename']} · "
                f"第 {record['page']} 页"
                + ("（用户选择区域；完整上下文请查看原文）"
                   if record.get("snapshot") == "user_region"
                   else "（整页图，保留上下文；细节请查看原文）"),
                "citations": [image_citation],
            }
            continue
        if source.visual is None:
            continue
        spec = source.visual.table
        if spec.id in ids:
            raise ValueError("同一表格不能在多页重复，请合并或使用独立规格")
        ids.add(spec.id)
        table = render_table(spec, results, mapping, corroboration=corroboration)
        block = table.block
        if len(block.columns) > 5:
            raise ValueError("幻灯片表格最多五个数据列，请按比较问题拆分规格")
        data: dict[str, Any] = {
            "kind": source.visual.kind,
            "id": block.id,
            "title": block.title,
            "headers": [
                "对象",
                *[c.label + (f"（{c.unit}）" if c.unit else "") for c in block.columns],
            ],
            "rows": [
                [
                    row.label,
                    *[
                        (row.cell(c.key).value or "未报告")
                        + "".join(f"[{i}]" for i in row.cell(c.key).citations)
                        for c in block.columns
                    ],
                ]
                for row in block.rows
            ],
            "caption": block.caption,
            "scope_notes": block.notes,
            "citations": sorted(
                {i for row in block.rows for cell in row.cells.values() for i in cell.citations}
            ),
            "markdown": table.markdown,
        }
        if source.visual.kind != "table":
            keys = source.visual.value_columns
            columns = [block.column(key) for key in keys]
            if not keys or len(set(keys)) != len(keys) or any(c is None for c in columns):
                raise ValueError("原生图表须明确选择表格中一至三个数值列")
            units = {c.unit for c in columns if c is not None}
            if len(units) != 1:
                raise ValueError("不同单位不能共用幻灯片图表数值轴，请拆分")
            series = []
            for col in columns:
                assert col is not None
                values = []
                for row in block.rows:
                    cell = row.cell(col.key)
                    if cell.disputed or cell.numeric is None or not math.isfinite(cell.numeric):
                        raise ValueError(
                            "原生图表含缺失、争议或非结构化数值；请保留原生表格，不补零或猜测"
                        )
                    if "±" in cell.value:
                        raise ValueError("含不确定度的数值暂保留原生表格，不能在图中遗漏误差")
                    if re.search(r"[<>≤≥]", cell.value):
                        raise ValueError("上下界不能作为精确点值作图，请保留原生表格")
                    values.append(cell.numeric)
                series.append({"name": col.label, "values": values})
            data.update(
                categories=[row.label for row in block.rows], series=series, unit=next(iter(units))
            )
        target["visual_data"] = data
    markdown, record = render_specs(
        deck_to_markdown(deck), results, mapping, corroboration=corroboration
    )
    if record.get("errors"):
        raise ValueError("；".join(record["errors"]))
    return compiled, markdown


def slide_quality(deck: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    """Run on original pages, before pagination can conceal density problems."""
    from .delivery.pptx import _has_title_slide, fit_report, paginate_deck

    slides = deck.get("slides", [])
    issues = []
    for number, spec in enumerate(slides, 1):
        if not meaningful_notes(str(spec.get("notes", ""))):
            issues.append(f"原始第 {number} 页缺少有效演讲备注")
    notes = [re.sub(r"\s+", "", str(s.get("notes", ""))) for s in slides]
    duplicated = [
        text for text, count in Counter(notes).items() if count > 1 and meaningful_notes(text)
    ]
    if duplicated:
        issues.append("不同页面重复使用整段演讲备注，需要分别编写")
    for problem in fit_report(deck):
        if problem.get("title"):
            issues.append(f"原始第 {problem['slide']} 页标题超过两行，请缩短标题并保留正文说明")
            continue
        issues.append(
            f"原始第 {problem['slide']} 页没有内容"
            if problem.get("empty")
            else (
                f"原始第 {problem['slide']} 页约 {problem['lines']} 行/"
                f"{problem['bullets']} 条要点，须降低密度或明确拆页"
            )
        )
    paginated = paginate_deck(deck)["slides"]
    if any(not meaningful_notes(str(s.get("notes", ""))) for s in paginated):
        issues.append("分页后仍有页面缺少独立讲稿，请按内容拆分原始页面与讲稿")
    pages = len(paginated) + (not _has_title_slide(deck))
    units = sum(spoken_units(str(s.get("notes", ""))) for s in slides)
    rate = deck.get("speaking_rate", 220)
    target = deck.get("target_minutes")
    if target and units < target * rate * 0.7:
        issues.append(
            f"讲稿约 {units} 个语音单位，按每分钟 {rate} 个估算不足目标 {target:g} 分钟；需试讲确认"
        )
    if target and units > target * rate * 1.3:
        issues.append(f"讲稿按每分钟 {rate} 个语音单位估算超过目标 {target:g} 分钟；需压缩并试讲")
    if deck.get("target_pages") and pages > deck["target_pages"]:
        issues.append(
            f"正文与封面预计 {pages} 页，超过目标 {deck['target_pages']} 页（参考文献另计）"
        )
    return issues, {
        "original_pages": len(slides),
        "content_pages": pages,
        "spoken_units": units,
        "estimated_minutes": round(units / rate, 2),
        "speaking_rate": rate,
        "target_minutes": target,
        "target_pages": deck.get("target_pages"),
        "timing_is_estimate": True,
    }
