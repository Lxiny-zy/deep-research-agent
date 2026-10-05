"""Visible TeX is a delivery failure; actual math and literal code are distinct."""

import pytest

from deep_research.workbench.delivery.docx import render_docx
from deep_research.workbench.delivery.html import render_html
from deep_research.workbench.delivery.pdf import render_pdf
from deep_research.workbench.gates import consistency_gate, markdown_gate

RAW = r"输入空间为 R^{H×W×C}，参数为 x_{i}，比例为 \frac{a}{b}。"


def artifact(fmt, markdown):
    if fmt == "html":
        return render_html(markdown, title="数学检查").encode()
    return {"docx": render_docx, "pdf": render_pdf}[fmt](markdown, title="数学检查")


def test_source_math_residue_is_blocked_before_it_can_enter_every_format():
    result = markdown_gate(RAW)
    assert result.status == "fail" and any("公式" in issue for issue in result.issues)


@pytest.mark.parametrize("expression", [r"\epsilon > 0", r"\hat{x}", "x^2"])
def test_clear_unmarked_math_commands_and_powers_are_rejected(expression):
    assert markdown_gate(f"模型使用 {expression}。").status == "fail"


@pytest.mark.parametrize("fmt", ["html", "pdf", "docx"])
def test_each_rendered_format_checks_visible_tex_independently(fmt):
    result = consistency_gate(RAW, {f"report.{fmt}": artifact(fmt, RAW)})
    assert result.status == "fail"
    assert result.metrics["failed_formats"] == [fmt]
    assert any("公式" in issue for issue in result.issues)


@pytest.mark.parametrize("fmt", ["html", "pdf", "docx"])
def test_real_math_and_its_accessibility_source_are_not_residue(fmt):
    body = r"输入空间为 $R^{H\times W\times C}$，比例为 $\frac{a}{b}$。"
    assert markdown_gate(body).status == "pass"
    result = consistency_gate(body, {f"report.{fmt}": artifact(fmt, body)})
    assert result.status == "pass", result.issues


@pytest.mark.parametrize("fmt", ["html", "pdf", "docx"])
def test_deliberate_literal_tex_code_can_still_be_documented(fmt):
    body = "语法示例：`R^{H×W×C}`。\n\n```tex\n\\frac{a}{b}\n```\n\n说明结束。"
    # A tex fence is rendered as math by this application; inline code stays literal.
    assert markdown_gate(body).status == "pass"
    result = consistency_gate(body, {f"report.{fmt}": artifact(fmt, body)})
    assert result.status == "pass", result.issues


def test_renderer_regression_cannot_hide_behind_clean_source_math():
    good = r"输入空间为 $R^{H\times W}$。"
    broken = render_html(good, title="数学检查").replace("</article>", "<p>R^{H×W}</p></article>")
    result = consistency_gate(good, {"report.html": broken.encode()})
    assert result.status == "fail" and result.metrics["failed_formats"] == ["html"]


async def test_writer_receives_raw_math_as_a_local_repair_and_rechecks_the_result():
    from dataclasses import replace

    from deep_research.workbench.quality import QualityPolicy
    from deep_research.workbench.revision import assess_draft, write_with_revisions
    from deep_research.workbench.templates import get_template

    template = replace(get_template("litReview"), sections=(), min_length=0, min_citations=0)
    policy = QualityPolicy(register_check=False, require_limitations=False)
    fixed = r"输入空间为 $R^{H\times W\times C}$，参数为 $x_{i}$，比例为 $\frac{a}{b}$。"

    def assess(body):
        return assess_draft(
            body, template=template, query="解释变量", results=[], url_to_idx={},
            policy=policy, min_citations=0, check_citations=False,
        )

    initial = assess(RAW)
    assert not initial.clean and initial.local_problems
    assert initial.local_problems[0][0] == RAW
    revisions = []

    async def write(revision):
        revisions.append(revision)
        return RAW if revision is None else fixed

    body, log = await write_with_revisions(write, assess, max_revisions=1)
    assert body == fixed and not log.remaining and log.attempts == 2
    assert "公式" in revisions[1] and RAW in revisions[1]


def test_published_bundle_and_frozen_format_retry_cannot_pass_raw_tex():
    from deep_research.workbench.delivery_render import render_bundle
    from deep_research.workbench.publish import build_bundle
    from tests.test_delivery_persistence import detail

    result = build_bundle(detail(RAW))
    assert result.status == "fail"
    assert any(g.name == "markdown" and g.blocking_issues for g in result.gates)
    for fmt in ["html", "docx", "pdf"]:
        files = [f for f in result.files if f.format == fmt]
        assert files and all(f.status == "fail" for f in files)
    retried = render_bundle(
        result.render_context,
        [f for f in result.files if f.format != "pdf"],
        retry_format="pdf", previous_failures=result.failures,
    )
    assert retried.status == "fail"
    assert any(f.format == "pdf" and f.status == "fail" for f in retried.files)
    assert any(g.name == "markdown" and g.blocking_issues for g in retried.gates)
