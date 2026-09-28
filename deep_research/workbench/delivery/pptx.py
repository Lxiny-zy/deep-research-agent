"""SlideDeck → PPTX（python-pptx，16:9，统一版式，每页附演讲备注）。

版式是固定的设计系统，不是模型自由发挥：标题页、内容页、结束页三种布局，
同一套配色与字号。模型只决定每页写什么，不决定字号和位置——这样每份交付
都「演示就绪」，不会出现文字溢出或一页十几条要点的墙。

``fit_report`` 是版面自检：每条要点的估算行数、每页总行数是否超出文本框容量。
验收门据此报告溢出页，而不是等用户打开才发现。
"""

from __future__ import annotations

import io
from typing import Any

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Emu, Pt

_W, _H = Emu(12192000), Emu(6858000)  # 16:9
_INK = RGBColor(0x14, 0x22, 0x30)
_ACCENT = RGBColor(0x1F, 0x5F, 0x8B)
_SOFT = RGBColor(0xEE, 0xF3, 0xF7)
_MUTED = RGBColor(0x5B, 0x66, 0x75)
_FONT = "Microsoft YaHei"
_BULLET_PT = 20
_MAX_LINES = 11
_CHARS_PER_LINE = 34  # 20pt 中文在 11.2 英寸宽文本框中的近似容量


def _text(frame, text: str, *, size: int, bold: bool = False, color: RGBColor = _INK) -> None:  # type: ignore[no-untyped-def]
    frame.clear()
    frame.word_wrap = True
    paragraph = frame.paragraphs[0]
    run = paragraph.add_run()
    run.text = text
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.name = _FONT
    run.font.color.rgb = color


def _band(slide, top: int, height: int, color: RGBColor) -> None:  # type: ignore[no-untyped-def]
    shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, top, _W, height)
    shape.fill.solid()
    shape.fill.fore_color.rgb = color
    shape.line.fill.background()


def _estimated_lines(text: str) -> int:
    width = sum(2 if ord(ch) > 0x2E80 else 1 for ch in text)
    return max(1, -(-width // (_CHARS_PER_LINE * 2)))


def fit_report(deck: dict[str, Any]) -> list[dict[str, Any]]:
    """估算每页要点占用的行数，返回超出容量的页面。"""
    problems: list[dict[str, Any]] = []
    for number, slide in enumerate(deck.get("slides", []), 2):
        bullets = slide.get("bullets", [])
        lines = sum(_estimated_lines(bullet) for bullet in bullets)
        if lines > _MAX_LINES or len(bullets) > 5:
            problems.append({"slide": number, "lines": lines, "bullets": len(bullets)})
        if not bullets:
            problems.append({"slide": number, "lines": 0, "bullets": 0, "empty": True})
    return problems


def render_pptx(deck: dict[str, Any], *, citations: list[str] | None = None) -> bytes:
    presentation = Presentation()
    presentation.slide_width, presentation.slide_height = _W, _H
    blank = presentation.slide_layouts[6]

    cover = presentation.slides.add_slide(blank)
    _band(cover, 0, _H, _ACCENT)
    box = cover.shapes.add_textbox(Emu(800000), Emu(2200000), Emu(10600000), Emu(1400000))
    _text(
        box.text_frame,
        deck.get("title", "研究汇报"),
        size=40,
        bold=True,
        color=RGBColor(255, 255, 255),
    )
    if deck.get("subtitle"):
        sub = cover.shapes.add_textbox(Emu(800000), Emu(3700000), Emu(10600000), Emu(800000))
        _text(sub.text_frame, deck["subtitle"], size=20, color=RGBColor(0xDD, 0xE8, 0xF1))
    cover.notes_slide.notes_text_frame.text = "开场：介绍题目与汇报结构。"

    slides = deck.get("slides", [])
    for number, spec in enumerate(slides, 2):
        slide = presentation.slides.add_slide(blank)
        _band(slide, 0, Emu(1150000), _SOFT)
        _band(slide, Emu(1150000), Emu(40000), _ACCENT)
        title = slide.shapes.add_textbox(Emu(600000), Emu(250000), Emu(11000000), Emu(750000))
        _text(title.text_frame, spec.get("title", ""), size=28, bold=True)
        title.text_frame.vertical_anchor = MSO_ANCHOR.MIDDLE
        body = slide.shapes.add_textbox(Emu(700000), Emu(1450000), Emu(10800000), Emu(4600000))
        frame = body.text_frame
        frame.word_wrap = True
        frame.clear()
        for index, bullet in enumerate(spec.get("bullets", [])[:5]):
            paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
            run = paragraph.add_run()
            run.text = f"•  {bullet}"
            run.font.size = Pt(_BULLET_PT)
            run.font.name = _FONT
            run.font.color.rgb = _INK
            paragraph.space_after = Pt(10)
        cites = [int(i) for i in spec.get("citations", []) if isinstance(i, int)]
        if cites:
            foot = slide.shapes.add_textbox(Emu(700000), Emu(6250000), Emu(10800000), Emu(400000))
            _text(
                foot.text_frame, "来源：" + " ".join(f"[{i}]" for i in cites), size=11, color=_MUTED
            )
        page = slide.shapes.add_textbox(Emu(11000000), Emu(6300000), Emu(900000), Emu(350000))
        _text(page.text_frame, str(number), size=11, color=_MUTED)
        page.text_frame.paragraphs[0].alignment = PP_ALIGN.RIGHT
        slide.notes_slide.notes_text_frame.text = spec.get("notes", "") or "（无备注）"

    # 每页 14 条分页排完，不截断：幻灯片正文引用的 [15]+ 也必须能在这里查到
    per_slide = 14
    references = citations or []
    for start in range(0, len(references), per_slide):
        refs = presentation.slides.add_slide(blank)
        _band(refs, 0, Emu(1150000), _SOFT)
        title = refs.shapes.add_textbox(Emu(600000), Emu(250000), Emu(11000000), Emu(750000))
        heading = "参考来源" if start == 0 else "参考来源（续）"
        _text(title.text_frame, heading, size=28, bold=True)
        body = refs.shapes.add_textbox(Emu(700000), Emu(1450000), Emu(10800000), Emu(4800000))
        frame = body.text_frame
        frame.word_wrap = True
        frame.clear()
        chunk = references[start : start + per_slide]
        for offset, url in enumerate(chunk):
            paragraph = frame.paragraphs[0] if offset == 0 else frame.add_paragraph()
            run = paragraph.add_run()
            run.text = f"[{start + offset + 1}] {url}"
            run.font.size = Pt(12)
            run.font.name = _FONT
        refs.notes_slide.notes_text_frame.text = "参考来源列表。"

    buffer = io.BytesIO()
    presentation.save(buffer)
    return buffer.getvalue()


def pptx_stats(data: bytes) -> dict[str, int]:
    presentation = Presentation(io.BytesIO(data))
    notes = sum(
        1
        for slide in presentation.slides
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame.text.strip()
    )
    return {"slides": len(presentation.slides), "with_notes": notes}


__all__ = ["fit_report", "pptx_stats", "render_pptx"]
