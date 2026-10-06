from __future__ import annotations

import io
import re
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pymupdf
import pytest

from deep_research.workbench.delivery.docx import render_docx
from deep_research.workbench.delivery.html import render_html
from deep_research.workbench.delivery.markdown import parse_blocks
from deep_research.workbench.delivery.math import MathRenderError, checked_mathml, math_asset
from deep_research.workbench.delivery.pdf import render_pdf
from deep_research.workbench.prose_review import prose_units

FORMULAS = [
    r"\frac{a}{b}+x_i^2",
    r"\sqrt{x^2+y^2}",
    r"\begin{bmatrix}a&b\\c&d\end{bmatrix}",
    r"\begin{aligned}y&=Ax\\z&=By\end{aligned}",
    r"f(x)=\begin{cases}x&x>0\\0&x\le0\end{cases}",
    r"\text{误差}=\frac{1}{N}\sum_{i=1}^{N}x_i^2",
]


@pytest.mark.parametrize("tex", FORMULAS)
def test_math_is_converted_to_real_structures_without_losing_source(tex):
    asset = math_asset(tex, True)
    assert asset.width > 0 and asset.height > 0
    assert "<path" in asset.svg and "<symbol" not in asset.svg
    assert asset.tex == tex
    ET.fromstring(asset.mathml)
    pdf = render_pdf("$$\n" + tex + "\n$$", title="公式测试")
    with pymupdf.open(stream=pdf, filetype="pdf") as document:
        assert tex in document[0].get_text()
        assert not document[0].get_image_info()
        assert len(document[0].get_drawings()) >= 2


def test_parser_preserves_tex_before_markdown_escapes_and_leaves_code_literal():
    text = r"正文 $x_i+\frac{a}{b}$ 与 \(y_j^2\)；`$z_i$` 是代码。"
    items = parse_blocks(text)[0].inlines
    assert [item.text for item in items if item.math] == [r"x_i+\frac{a}{b}", r"y_j^2"]
    assert any(item.code and item.text == "$z_i$" for item in items)
    assert parse_blocks(r"\[\begin{matrix}a&b\\c&d\end{matrix}\]")[0].kind == "math"
    assert parse_blocks("```math\n\\frac{a}{b}\n```")[0].kind == "math"
    assert parse_blocks("Cost $5 and $10.")[0].plain() == "Cost $5 and $10."


def test_prices_adjacent_to_formula_code_do_not_consume_code_or_following_math():
    source = "Cost $5 and $10. 行内代码 `$x_i$`。公式 $y_i$。"
    items = parse_blocks(source)[0].inlines
    assert [item.text for item in items if item.math] == ["y_i"]
    assert any(item.code and item.text == "$x_i$" for item in items)
    assert "Cost $5 and $10." in "".join(item.text for item in items if not item.math)


def test_formula_fence_in_list_preserves_following_item():
    blocks = parse_blocks("- first\n\n  ```math\n  x_i\n  ```\n\n- second")
    assert len(blocks) == 1 and len(blocks[0].items) == 2
    assert blocks[0].items[0].inlines[-1].math
    assert blocks[0].items[1].inlines[0].text == "second"


def test_word_contains_native_fractions_scripts_and_matrices():
    body = "\n\n".join("$$\n" + tex + "\n$$" for tex in FORMULAS)
    data = render_docx(body, title="公式")
    root = ET.fromstring(ZipFile(io.BytesIO(data)).read("word/document.xml"))
    ns = {"m": "http://schemas.openxmlformats.org/officeDocument/2006/math"}
    assert len(root.findall(".//m:oMath", ns)) == len(FORMULAS)
    assert root.findall(".//m:f", ns) and root.findall(".//m:sSubSup", ns)
    assert root.findall(".//m:m", ns) and root.findall(".//m:rad", ns)
    assert "误差" in "".join(root.itertext())


@pytest.mark.parametrize(
    "tex",
    [
        r"\input{file}",
        r"\href{https://evil.example}{x}",
        r"\begin{document}x\end{document}",
        r"\madeup{x}",
        r"\frac{x}{y",
        r"\text{<script>bad</script>}",
    ],
)
def test_unsupported_or_unsafe_math_is_not_silently_rendered(tex):
    with pytest.raises(MathRenderError):
        checked_mathml(tex)


def test_html_embeds_mathml_and_paths_with_local_ids_and_theme_color():
    html = render_html("$x_i$ 与 $y_j$", title="数学")
    assert "<svg" in html and "<math" in html
    assert "currentColor" in html and "math-accessible" in html
    assert "<script" not in html


def test_formula_blocks_remain_in_final_prose_review_and_indices_are_not_citations():
    body = "由原文得到 [3]。\n\n$$x=[1,2]$$\n\n表达式 $y=[4,5]$ 见原文 [3]。"
    units, _ = prose_units(body, [3])
    formula = next(u for u in units if u.text.startswith("$$"))
    assert formula.citations == [3] and formula.kind == "summary"
    assert units[-1].citations == [3]


def test_legacy_pdf_and_tex_exports_preserve_math_and_do_not_execute_code():
    from deep_research.report.document import ProseBlock, ReportDocument
    from deep_research.report.latex import render_latex
    from deep_research.report.pdf import render_pdf_html

    source = r"公式 $x_i^2$。" + "\n\n" + r"\[\frac{a}{b}\]"
    document = ReportDocument(query="公式", blocks=[ProseBlock(markdown=source)])
    html = render_pdf_html(document)
    tex = render_latex(document)
    assert "<svg" in html and "<mfrac>" in html
    assert r"\(x_i^2\)" in tex and r"\[\frac{a}{b}\]" in tex
    assert r"\usepackage{amsmath,amssymb}" in tex
    document.blocks = [ProseBlock(markdown="```tex\n\\end{verbatim}\\input{secret}\n```")]
    with pytest.raises(MathRenderError):
        render_latex(document)
    document.blocks = [ProseBlock(markdown="```python\n\\end{verbatim}\\input{secret}\n```")]
    assert r"\input{secret}" not in render_latex(document)


def test_finalization_preserves_math_indices_and_checks_their_numbers():
    from deep_research.models import Report, ResearchResult
    from deep_research.report.validation import finalize_report, validate_body
    from tests.fakes import verified_finding

    finding = verified_finding(statement="向量取值 1、2。", evidence_quote="向量取值 1、2。")
    results = [ResearchResult(sub_question="向量", findings=[finding])]
    body = "向量 $x=[1,2]$ 来自原文 [2]。\n\n$$\nx=[1,2]\n\n$$"
    report, check = finalize_report(
        Report(
            query="向量", markdown=body, citations=["https://unused.example", finding.source_url]
        ),
        results,
    )
    assert not check.issues
    assert "$x=[1,2]$" in report.markdown and "原文 [1]" in report.markdown
    assert "\n\n$$" in report.markdown
    assert finalize_report(report, results)[0] == report
    bad = validate_body("$$x=[999]$$", results, {finding.source_url: 1}, fallback=False)
    assert "unsupported_number" in bad.issues and "invalid_citation" not in bad.issues


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_citation_masks_keep_offsets_and_protect_code_links_and_nested_math(newline):
    from deep_research.workbench.delivery.math_markdown import citation_text, replace_citations

    body = (
        "> 公式 $x=[1,2]$ 引用 [7]。\n\n"
        "- \\[y=[3]\\] 来源 [8]\n\n"
        "```python\na[4]\n```\n\n"
        "`x[5]` [链接](https://example.test/[6]) \\[9] [10]"
    )
    body = body.replace("\n", newline)
    masked = citation_text(body)
    assert len(masked) == len(body)
    assert re.findall(r"\[\d+\]", masked) == ["[7]", "[8]", "[10]"]
    rewritten = replace_citations(body, lambda _: "[1]")
    assert "x=[1,2]" in rewritten and "y=[3]" in rewritten and "a[4]" in rewritten
    assert "https://example.test/[6]" in rewritten and r"\[9]" in rewritten


def test_all_citation_quality_checks_ignore_equation_indices():
    from deep_research.workbench.gates import citation_gate
    from deep_research.workbench.revision import _used_indices
    from deep_research.workbench.scholarly import check_abstract
    from deep_research.workbench.templates import TaskTemplate

    body = "## 摘要\n\n向量 $x=[1,2]$。\n\n## 结论\n\n来自原文 [1]。"
    assert not check_abstract(body)
    assert _used_indices(body) == {1}
    from deep_research.workbench.templates import get_template

    template: TaskTemplate = get_template("paperReading")
    gate = citation_gate(body, ["https://example.test"], template, min_citations=1)
    assert gate.status == "pass"


def test_title_and_section_math_survive_all_document_formats():
    body = "# 关于 $x_i$ 的结论\n\n## $y_j$ 的解释\n\n正文结论。"
    html = render_html(body, title="标题")
    assert html.count('<span class="rendered-math') == 2
    word = ET.fromstring(
        ZipFile(io.BytesIO(render_docx(body, title="标题"))).read("word/document.xml")
    )
    assert (
        len(word.findall(".//{http://schemas.openxmlformats.org/officeDocument/2006/math}oMath"))
        == 2
    )
    with pymupdf.open(stream=render_pdf(body, title="标题"), filetype="pdf") as pdf:
        text = "".join(page.get_text() for page in pdf)
        assert "x_i" in text and "y_j" in text
        assert not pdf[0].get_image_info()


def test_tail_check_does_not_require_vector_actualtext_in_visual_reading_order():
    with pymupdf.open(stream=render_pdf("结论 $x_i$ 结束。", title="结论"), filetype="pdf") as pdf:
        text = pdf[0].get_text()
        assert "x_i" in text and "结束" in text


def test_paginated_table_math_coexists_with_real_figure_without_loss():
    from PIL import Image

    image = io.BytesIO()
    Image.new("RGB", (80, 40), "blue").save(image, format="PNG")
    rows = [rf"| Row {i} | $x_{{{i}}}=\frac{{a+b+c+d}}{{e+f}}$ |" for i in range(45)]
    body = "| 样本 | 公式 |\n|---|---|\n" + "\n".join(rows)
    body += "\n\n![Figure](plot.png)\n\n完整的结束段落。"
    data = render_pdf(body, title="数学表格", images={"plot.png": image.getvalue()})
    with pymupdf.open(stream=data, filetype="pdf") as pdf:
        assert len(pdf) > 1
        text = "".join(page.get_text() for page in pdf)
        for i in range(45):
            assert text.count(rf"x_{{{i}}}=\frac{{a+b+c+d}}{{e+f}}") == 1
        assert sum(len(page.get_image_info()) for page in pdf) == 1


def test_long_numbered_math_footnotes_keep_equations_and_numbering_across_pages():
    rows = [
        rf"{i}. 注释编号 {i:03}：训练与测试条件分别保留；"
        rf"使用完整误差表达式 $e_{{{i}}}=\frac{{a+b}}{{N}}$，不缩小页底公式。"
        for i in range(1, 65)
    ]
    body = "## 条件脚注\n\n" + "\n".join(rows) + "\n\n脚注全部保留。"
    with pymupdf.open(stream=render_pdf(body, title="公式脚注"), filetype="pdf") as pdf:
        assert len(pdf) >= 3
        text = "".join(page.get_text() for page in pdf)
        for i in range(1, 65):
            assert text.count(rf"e_{{{i}}}=\frac{{a+b}}{{N}}") == 1
            assert re.search(rf"\b{i}\.\s*注释编号\s*{i:03}", text)
        assert not any(page.get_image_info() for page in pdf)


def test_nested_math_list_preserves_parent_child_text_and_parent_numbering():
    body = (
        "1. Parent A\n   - Child formula $x_i=\\frac{a}{b}$\n"
        "2. Parent B\n   - Child result $y_i^2$\n\nComplete ending."
    )
    with pymupdf.open(stream=render_pdf(body, title="Nested"), filetype="pdf") as pdf:
        text = "".join(page.get_text() for page in pdf)
        assert re.search(r"1\.\s*Parent A", text)
        assert re.search(r"2\.\s*Parent B", text)
        assert "Child formula" in text and "Child result" in text
        assert text.count(r"x_i=\frac{a}{b}") == 1 and text.count("y_i^2") == 1


def test_word_long_math_table_repeats_headers_and_keeps_rows_intact():
    rows = [rf"| Item {i} | $e_{{{i}}}=\frac{{a+b}}{{N}}$ |" for i in range(40)]
    body = "| Item | Expression |\n|---|---|\n" + "\n".join(rows)
    root = ET.fromstring(ZipFile(io.BytesIO(render_docx(body, title="Long table"))).read(
        "word/document.xml"
    ))
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
          "m": "http://schemas.openxmlformats.org/officeDocument/2006/math"}
    assert len(root.findall(".//w:tblHeader", ns)) == 1
    assert len(root.findall(".//w:cantSplit", ns)) == 41
    assert len(root.findall(".//m:oMath", ns)) == 40
