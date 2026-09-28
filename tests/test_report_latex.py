from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime

import pytest

from deep_research.models import RunManifest, ScholarlyMetadata, Source
from deep_research.report import (
    EvidenceRecord,
    LatexExportUnavailable,
    ProseBlock,
    ReferenceEntry,
    ReportDocument,
    TableBlock,
    TableCell,
    TableColumn,
    TableRow,
    render_bibtex,
    render_latex,
    render_latex_pdf,
    render_reproducibility_bundle,
)


def _document() -> ReportDocument:
    return ReportDocument(
        query="中文论文题目 & 稳健性",
        abstract="这是一段摘要。",
        keywords=["证据链", "可复现"],
        authors=["研究团队"],
        institution="实验室",
        blocks=[
            ProseBlock(markdown="# 结论\n\n**关键** 结果为 38.36 dB，见 [1]。\n\n- 第一项"),
            TableBlock(
                id="results",
                title="实验结果",
                columns=[TableColumn(key="score", label="分数", numeric=True)],
                rows=[
                    TableRow(
                        label="方法 A",
                        cells={"score": TableCell(value="38.36", citations=[1])},
                    )
                ],
            ),
        ],
        references=[
            ReferenceEntry(
                index=1,
                url="https://example.test/a?q=1&x=2",
                reference="作者. 标题",
            )
        ],
        evidence=[
            EvidenceRecord(
                citation=1,
                statement="方法 A 达到 38.36 dB",
                quote="38.36 dB",
                source_url="https://example.test/a?q=1&x=2",
                verbatim_verified=True,
            )
        ],
    )


def test_latex_source_is_utf8_and_contains_fixed_academic_structure() -> None:
    source = render_latex(_document())

    assert r"\documentclass[UTF8,a4paper,11pt]{ctexart}" in source
    assert r"\usepackage{booktabs,longtable,array,enumitem,hyperref,xurl,fancyhdr}" in source
    assert r"\begin{abstract}" in source
    assert r"\begin{longtable}" in source
    assert r"\begin{thebibliography}{99}" in source
    assert r"\appendix" in source
    assert "中文论文题目 \\& 稳健性" in source
    assert r"\url{https://example.test/a?q=1&x=2}" in source


def test_latex_never_interprets_raw_user_commands() -> None:
    document = ReportDocument(query=r"标题 \\input{secrets}")

    source = render_latex(document)

    assert r"\textbackslash{}" in source
    assert r"\input{secrets}" not in source
    assert "shell-escape" not in source.lower()


def test_bibtex_is_deterministic_and_preserves_doi_when_present() -> None:
    bib = render_bibtex(_document())

    assert bib.startswith("@misc{ref1,")
    assert "title = {作者. 标题}" in bib
    assert "url = {https://example.test/a?q=1&x=2}" in bib
    assert bib.endswith("}\n")


def test_bibtex_uses_persisted_scholarly_metadata_when_available() -> None:
    reference_url = "https://example.test/a?q=1&x=2"
    bib = render_bibtex(
        _document(),
        sources=[
            Source(
                url=reference_url,
                title="真实论文标题",
                scholarly=ScholarlyMetadata(
                    authors=["Alice", "Bob"],
                    venue="Journal of Research",
                    year=2026,
                    doi="10.1234/example",
                ),
            )
        ],
    )

    assert "title = {真实论文标题}" in bib
    assert "author = {Alice and Bob}" in bib
    assert "journal = {Journal of Research}" in bib
    assert "year = {2026}" in bib
    assert "doi = {10.1234/example}" in bib


def test_reproducibility_bundle_contains_sorted_stable_non_secret_files() -> None:
    manifest = RunManifest(
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        workflow_name="deep",
        workflow_hash="workflow",
        query_hash="query",
    )
    bundle = render_reproducibility_bundle(
        _document(),
        run_id="run/1",
        manifest=manifest,
        sources=[Source(url="https://example.test/a", content="snapshot")],
    )
    assert bundle == render_reproducibility_bundle(
        _document(),
        run_id="run/1",
        manifest=manifest,
        sources=[Source(url="https://example.test/a", content="snapshot")],
    )
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        names = archive.namelist()
        assert names == sorted(names)
        assert {
            "README.txt",
            "checksums.sha256",
            "document.json",
            "manifest.json",
            "references.bib",
            "report.md",
            "report.tex",
            "sources.json",
        } == set(names)
        assert "snapshot" in archive.read("sources.json").decode("utf-8")
        assert "report.tex" in archive.read("checksums.sha256").decode("utf-8")


def test_latex_pdf_reports_missing_compiler(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("deep_research.report.latex.shutil.which", lambda _: None)

    with pytest.raises(LatexExportUnavailable, match="latexmk"):
        render_latex_pdf(_document())


def test_table_row_labels_are_escaped_like_other_cells() -> None:
    """行标签来自检索到的实体名；不转义时 & 多出一列、反斜杠命令直接进入源文件。"""
    document = _document()
    table = document.blocks[1]
    assert isinstance(table, TableBlock)
    table.rows[0].label = r"A&B_v2 \input{x}"

    source = render_latex(document)

    assert r"A\&B\_v2 \textbackslash{}input\{x\}" in source
    assert r"\input{x}" not in source


def test_inline_text_never_carries_a_paragraph_break() -> None:
    """\\title 等命令参数里出现空行会让 LaTeX 报 Paragraph ended before。"""
    document = _document()
    document.query = "第一行\n\n第二行"

    assert r"\title{第一行 第二行}" in render_latex(document)


def test_bibtex_fields_contain_no_bare_braces() -> None:
    """BibTeX 不认反斜杠转义，\\} 仍会提前闭合字段。"""
    document = _document()
    document.references = [
        ReferenceEntry(index=1, url="https://a.test/x}y{z", reference="标题 {含括号}")
    ]

    bib = render_bibtex(document)

    assert "url = {https://a.test/x%7Dy%7Bz}," in bib
    assert "title = {标题 (含括号)}," in bib
