"""科研工作台回归：模板契约、各类任务端到端运行、同源多格式交付与验收门、HTTP 接口。"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from httpx import ASGITransport

from deep_research.agents.base import Blackboard
from deep_research.models import Report, ResearchResult, Source
from deep_research.orchestrator import DeepResearchAgent, create_initial_execution
from deep_research.persistence.memory_repository import InMemoryRepository
from deep_research.workbench import gates
from deep_research.workbench.analysis import analyse, check_numbers
from deep_research.workbench.contract import CONTRACT_SCRATCH_KEY, build_contract, extract_papers
from deep_research.workbench.delivery.docx import docx_stats, render_docx
from deep_research.workbench.delivery.html import render_html
from deep_research.workbench.delivery.markdown import parse_blocks
from deep_research.workbench.delivery.mindmap import render_mindmap_html, render_mindmap_png
from deep_research.workbench.delivery.pdf import PdfRenderError, pdf_text, render_pdf, verify_pdf
from deep_research.workbench.delivery.pptx import fit_report, pptx_stats, render_pptx
from deep_research.workbench.intake import PaperIntake
from deep_research.workbench.publish import build_bundle
from deep_research.workbench.templates import TASK_TEMPLATES, get_template
from deep_research.workbench.writers import (
    Mindmap,
    SlideDeck,
    extract_review_score,
    mindmap_stats,
)
from deep_research.workflows import WORKFLOWS
from tests.fakes import FakeLLM, FakeSearch, verified_finding

# --------------------------------------------------------------------------- 模板与契约


def test_every_template_maps_to_a_registered_workflow_with_terminal_writer() -> None:
    from deep_research.registry import available

    roles = set(available())
    for template in TASK_TEMPLATES.values():
        assert template.workflow in WORKFLOWS, template.key
        agents = {step.agent for step in WORKFLOWS[template.workflow].steps if step.agent}
        assert agents <= roles, (template.key, agents - roles)
        assert template.deliverables, template.key


def test_extract_papers_normalises_and_dedupes() -> None:
    text = (
        "请评审 https://arxiv.org/abs/2205.10102v2 以及 arXiv:2501.12705，"
        "还有 https://doi.org/10.1109/CVPR.2022.01234 与 2205.10102v2。"
    )
    papers = extract_papers(text)
    assert [(p.kind, p.value) for p in papers] == [
        ("arxiv", "2205.10102v2"),
        ("doi", "10.1109/CVPR.2022.01234"),
        ("arxiv", "2501.12705"),
    ]


def test_dataset_contract_splits_question_from_csv() -> None:
    template = get_template("dataAnalysis")
    assert template is not None
    contract = build_contract(template, "三种方法差异显著吗？\nmethod,psnr\nA,30.1\nB,31.2\nC,32.3")
    assert contract.focus == "三种方法差异显著吗？"
    assert contract.dataset_csv.startswith("method,psnr")
    assert "## 必须包含的章节" in contract.render()


def test_contract_is_deterministic() -> None:
    template = get_template("peerReview")
    assert template is not None
    a = build_contract(template, "https://arxiv.org/abs/2205.10102 重点看实验")
    b = build_contract(template, "https://arxiv.org/abs/2205.10102 重点看实验")
    assert a == b
    assert a.focus == "重点看实验"


# --------------------------------------------------------------------------- 交付格式

_SAMPLE_MD = """# 高光谱重建综述

## 摘要
深度展开方法在 CASSI 上达到 38.36 dB [1]。公式 $y=\\Phi x$ 表示测量模型。

## 方法对比
| 方法 | PSNR |
|---|---|
| MST | 35.18 |

- 优点一 [1]
- 优点二 [2]

## 结论
最后一句话用于检测截断的结尾哨兵片段。
"""


def test_markdown_parser_never_passes_raw_html_through() -> None:
    html = render_html("正文\n\n<script>alert(1)</script>\n", title="t")
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html


def test_same_source_formats_agree_on_structure() -> None:
    png = render_mindmap_png({"root": "r", "branches": [{"label": "a", "children": []}]})
    markdown = _SAMPLE_MD + "\n![示例图](fig.png)\n"
    images = {"fig.png": png}
    docx = render_docx(markdown, title="t", images=images)
    html = render_html(markdown, title="t", images=images).encode()
    pdf = render_pdf(markdown, title="t", images=images)
    stats = docx_stats(docx)
    assert stats["images"] == 1 and stats["tables"] == 1
    assert html.count(b'src="data:image') == 1
    pages, text = pdf_text(pdf)
    assert pages >= 1 and "38.36" in text
    result = gates.consistency_gate(markdown, {"r.docx": docx, "r.html": html, "r.pdf": pdf})
    assert result.status == "pass", result.issues


def test_pdf_is_small_after_font_subsetting() -> None:
    pdf = render_pdf(_SAMPLE_MD, title="t")
    assert len(pdf) < 400_000


def test_pdf_self_check_detects_truncation() -> None:
    pdf = render_pdf("## 甲\n前半部分正文内容。\n", title="t")
    with pytest.raises(PdfRenderError, match="截断"):
        verify_pdf(pdf, "## 甲\n前半部分正文内容。\n\n完全不同的结尾段落内容。\n")


def test_pptx_has_notes_on_every_slide_and_fit_check_flags_walls() -> None:
    deck = {
        "title": "组会汇报",
        "slides": [
            {"title": "背景", "bullets": ["短要点"], "notes": "备注", "citations": [1]},
            {"title": "墙", "bullets": ["很长的要点" * 30] * 5, "notes": "", "citations": []},
        ],
    }
    data = render_pptx(deck, citations=["https://a.com"])
    stats = pptx_stats(data)
    assert stats["slides"] == 6  # 内容溢出自动续页，保留全部要点
    assert stats["with_notes"] == stats["slides"]
    assert [p["slide"] for p in fit_report(deck)] == [3]


def test_mindmap_html_escapes_labels_and_png_renders() -> None:
    mindmap = {
        "root": "<b>根</b>",
        "branches": [{"label": "<img src=x>", "children": [{"label": "子"}]}],
    }
    html = render_mindmap_html(mindmap, title="导图")
    assert "<img src=x>" not in html and "&lt;img src=x&gt;" in html
    assert render_mindmap_png(mindmap)[:4] == b"\x89PNG"


# --------------------------------------------------------------------------- 验收门


def test_citation_gate_fails_on_out_of_range_reference() -> None:
    template = get_template("autoResearch")
    assert template is not None
    result = gates.citation_gate("结论 [1][4]。", ["https://a.com", "https://b.com"], template)
    assert result.status == "fail"
    assert "越界" in result.issues[0]


def test_structure_gate_accepts_aliases_and_reports_missing() -> None:
    template = get_template("peerReview")
    assert template is not None
    md = "## Summary\n...\n## 优点\n...\n## 不足\n...\n## 详细意见\n..."
    result = gates.structure_gate(md, template)
    assert result.status == "warn"
    assert "总体推荐" in result.issues[0]


def test_markdown_gate_blocks_raw_html() -> None:
    assert gates.markdown_gate("正文 <div>x</div>").status == "fail"
    assert gates.markdown_gate("正文 [跳转](#sec)").status == "warn"
    assert gates.markdown_gate("正文").status == "pass"


def test_review_score_extraction() -> None:
    assert extract_review_score("## 总体推荐\n评分：7/10\n") == 7
    assert extract_review_score("**Score: 11/10**") is None
    assert extract_review_score("没有分数") is None


# --------------------------------------------------------------------------- 数据分析


def test_analysis_runs_tests_and_ignores_id_columns() -> None:
    csv = "method,scene,psnr\n" + "\n".join(
        f"{m},{s},{base + s * 0.01:.2f}"
        for m, base in (("A", 30), ("B", 32), ("C", 34))
        for s in range(1, 8)
    )
    result = analyse(csv, "差异显著吗")
    assert "scene" not in result.numeric  # 编号列不当作测量值
    assert result.tests and result.tests[0]["method"] == "单因素方差分析"
    assert result.tests[0]["significant"] is True
    assert result.figures and all(fig.png[:4] == b"\x89PNG" for fig in result.figures)


def test_analysis_without_data_uses_labelled_synthetic_example() -> None:
    result = analyse("", "", allow_synthetic=True)
    assert result.synthetic and "合成数据" in result.facts()


def test_number_check_catches_invented_values() -> None:
    facts = "- psnr: 均值=32.1234, p=7.06e-06"
    assert check_numbers("均值为 32.1234，p=7.06e-06。", facts) == []
    assert check_numbers("均值为 99.9。", facts) == ["99.9"]


# --------------------------------------------------------------------------- 端到端运行


class WorkbenchLLM(FakeLLM):
    """在 FakeLLM 基础上补上幻灯片/导图结构化输出与各写作者的流式正文。"""

    def __init__(self, body: str) -> None:
        super().__init__()
        self.body = body

    async def parse(self, system, user, schema, *, temperature=0.2, retries=2):  # type: ignore[no-untyped-def]
        if schema is SlideDeck:
            return SlideDeck(
                title="组会汇报",
                slides=[
                    {"title": name, "bullets": ["发现X [1]"], "notes": "讲解", "citations": [1]}
                    for name in (
                        "背景与动机",
                        "问题与目标",
                        "方法",
                        "关键结果",
                        "讨论与局限",
                        "结论",
                        "Q&A",
                    )
                ],
            )
        if schema is Mindmap:
            return Mindmap(
                root="主题",
                branches=[
                    {"label": f"分支{i}", "children": [{"label": f"节点{i}-{j}"} for j in range(5)]}
                    for i in range(6)
                ],
            )
        return await super().parse(system, user, schema, temperature=temperature, retries=retries)

    async def stream(self, system, user, *, temperature=0.4):  # type: ignore[no-untyped-def]
        self.stream_calls += 1
        yield self.body


def _execution(query: str, template_key: str, settings):  # type: ignore[no-untyped-def]
    template = get_template(template_key)
    assert template is not None
    execution = create_initial_execution(query, template.workflow, settings)
    execution.checkpoint.setdefault("scratch", {})[CONTRACT_SCRATCH_KEY] = build_contract(
        template, query
    ).model_dump(mode="json")
    return template, execution


async def _run(template_key: str, query: str, body: str, settings, **kwargs):  # type: ignore[no-untyped-def]
    template, execution = _execution(query, template_key, settings)
    repo = InMemoryRepository()
    run_id = await repo.create_run(query, execution=execution)
    agent = DeepResearchAgent(
        settings,
        llm=WorkbenchLLM(body),
        search_tool=FakeSearch(),
        workflow=template.workflow,
        repo=repo,
        run_id=run_id,
        initial_execution=execution,
        **kwargs,
    )
    report = await agent.run(query)
    detail = await repo.get_run(run_id)
    assert detail is not None
    return report, detail, agent


@pytest.mark.asyncio
async def test_lit_review_run_produces_checked_deliverables(settings) -> None:
    body = "\n\n".join(
        f"## {title}\n发现X [1]"
        for title in ("摘要", "引言", "主题综述", "方法对比", "开放问题", "结论")
    )
    report, detail, _ = await _run("litReview", "高光谱重建综述", body, settings)
    assert "## 参考来源" in report.markdown
    bundle = build_bundle(detail)
    formats = {file.format for file in bundle.files}
    assert {"md", "docx", "pdf", "html"} <= formats
    by_name = {gate.name: gate for gate in bundle.gates}
    assert by_name["structure"].status == "pass"
    assert by_name["consistency"].status == "pass", by_name["consistency"].issues
    assert by_name["citation"].status in {"pass", "warn"}  # 假数据只有 1 个来源，低于 6 的下限


async def test_closed_review_cannot_export_when_a_supplied_document_is_missing(settings):
    body = "\n\n".join(
        f"## {title}\n发现X [1]"
        for title in ("摘要", "引言", "主题综述", "方法对比", "开放问题", "结论")
    )
    _, detail, _ = await _run("litReview", "比较指定文献", body, settings)
    scratch = detail.orchestration.checkpoint["scratch"]
    scratch[CONTRACT_SCRATCH_KEY] = build_contract(
        get_template("litReview"),
        "Compare https://a.com and https://missing.test/paper.pdf",
        strategy="none",
    ).model_dump(mode="json")
    bundle = build_bundle(detail)
    corpus = next(gate for gate in bundle.gates if gate.name == "provided_corpus")
    assert corpus.status == "fail" and any("missing.test" in issue for issue in corpus.issues)
    assert not {"pdf", "docx", "html"}.intersection(file.format for file in bundle.files)
    assert all(file.status == "fail" for file in bundle.files if file.format == "md")


async def test_missing_corpus_does_not_trigger_rewrites_or_optional_figures(settings, monkeypatch):
    from deep_research.agents.base import Blackboard, RunContext
    from deep_research.models import ResearchResult
    from deep_research.observability import Tracer
    from deep_research.workbench.writers import SurveyWriter
    from tests.fakes import verified_finding

    query = "Compare https://a.com and https://missing.test/paper.pdf"
    contract = build_contract(get_template("litReview"), query, strategy="none")
    bb = Blackboard(
        query=query,
        scratch={CONTRACT_SCRATCH_KEY: contract.model_dump()},
        results=[
            ResearchResult(sub_question="q", findings=[verified_finding("发现X", "https://a.com")])
        ],
    )
    body = "\n\n".join(
        f"## {title}\n发现X [1]" for title in ("引言", "主题综述", "方法对比", "开放问题", "结论")
    )
    body = "## 摘要\n本文比较所给文献的方法与局限。\n\n" + body
    llm = WorkbenchLLM(body)

    async def unexpected(*args, **kwargs):
        raise AssertionError("Incomplete corpus must not trigger optional figure generation")

    monkeypatch.setattr(SurveyWriter, "concept_figure", unexpected)
    await SurveyWriter().step(
        bb, RunContext(llm=llm, search_tool=FakeSearch(), tracer=Tracer(), settings=settings)
    )
    extras = bb.scratch["workbench"]["extras"]
    assert extras["revision"]["attempts"] == 1
    assert "concept_figure_skipped" in extras
    assert any("missing.test" in item for item in extras["revision"]["advisories"])


class RevisingLLM(WorkbenchLLM):
    """首稿缺章节并带口语化措辞，收到返工要求后给出合格正文。"""

    def __init__(self, first: str, fixed: str) -> None:
        super().__init__(first)
        self.fixed = fixed
        self.users: list[str] = []

    async def stream(self, system, user, *, temperature=0.4):  # type: ignore[no-untyped-def]
        self.stream_calls += 1
        self.users.append(user)
        yield self.fixed if "## 返工要求" in user else self.body


@pytest.mark.asyncio
async def test_writer_revises_draft_that_fails_quality_checks(settings) -> None:
    good = "\n\n".join(
        f"## {title}\n发现X [1]"
        for title in ("摘要", "引言", "主题综述", "方法对比", "开放问题", "结论")
    ).replace("## 摘要\n发现X [1]", "## 摘要\n本文梳理快照光谱重建方法的主要进展与局限。")
    bad = "## 引言\n说白了，发现X [1]"
    template, execution = _execution("高光谱重建综述", "litReview", settings)
    repo = InMemoryRepository()
    run_id = await repo.create_run("高光谱重建综述", execution=execution)
    llm = RevisingLLM(bad, good)
    agent = DeepResearchAgent(
        settings,
        llm=llm,
        search_tool=FakeSearch(),
        workflow=template.workflow,
        repo=repo,
        run_id=run_id,
        initial_execution=execution,
    )
    report = await agent.run("高光谱重建综述")
    assert "说白了" not in report.markdown and "## 方法对比" in report.markdown
    revision_prompts = [user for user in llm.users if "## 返工要求" in user]
    assert revision_prompts and "缺少章节" in revision_prompts[0]
    assert "说白了" in revision_prompts[0]  # 上一版全文随返工要求一起交回
    detail = await repo.get_run(run_id)
    assert detail is not None
    bundle = build_bundle(detail)
    by_name = {gate.name: gate for gate in bundle.gates}
    assert by_name["revision"].metrics["revisions"] >= 1
    # 假数据只有 1 个来源，远低于综述下限 20：如实标为需关注，而不是悄悄通过
    assert by_name["citation"].status == "warn"
    assert any("20" in issue for issue in by_name["citation"].issues)


async def test_rejected_writer_draft_is_retained_for_repair_without_repeating_research(settings):
    settings.quality = {"max_revisions": 0}
    body = "## 结论\n\n准确率达到 99.99% [1]。"
    report, detail, _ = await _run("autoResearch", "研究结论", body, settings)
    assert "99.99" not in report.markdown
    extras = detail.orchestration.checkpoint["scratch"]["workbench"]["extras"]
    assert "99.99" in extras["unapproved_draft"]
    bundle = build_bundle(detail)
    assert next(g for g in bundle.gates if g.name == "task_content").status == "fail"
    assert bundle.status == "fail" and {file.format for file in bundle.files} == {"md"}
    assert bundle.files[0].status == "fail"


@pytest.mark.asyncio
async def test_peer_review_uses_named_paper_not_open_search(settings, monkeypatch) -> None:
    from deep_research.workbench import intake

    fetched: list[str] = []

    async def fake_fetch(paper, ctx):  # type: ignore[no-untyped-def]
        fetched.append(paper.url)
        return [Source(title="论文", url="https://a.com", content="内容A提供了可核验的原文证据")]

    monkeypatch.setattr(intake, "fetch_paper", fake_fetch)

    class NoSearch(FakeSearch):
        async def search(self, query, *, max_results=5):  # type: ignore[no-untyped-def]
            raise AssertionError("peer review must not fall back to open search")

    body = (
        "## 论文摘要\n发现X [1]\n\n## 优点\n- 发现X [1]\n\n## 不足\n- 发现X [1]\n\n"
        "## 详细意见\n发现X [1]\n\n## 总体推荐\n发现X [1]\n\n评分：7/10 [1]"
    )
    template, execution = _execution("https://arxiv.org/abs/2205.10102", "peerReview", settings)
    repo = InMemoryRepository()
    run_id = await repo.create_run("q", execution=execution)
    agent = DeepResearchAgent(
        settings,
        llm=WorkbenchLLM(body),
        search_tool=NoSearch(),
        workflow=template.workflow,
        repo=repo,
        run_id=run_id,
        initial_execution=execution,
    )
    await agent.run("https://arxiv.org/abs/2205.10102")
    detail = await repo.get_run(run_id)
    assert detail is not None
    assert fetched == ["https://arxiv.org/abs/2205.10102"]
    bundle = build_bundle(detail)
    review = next(g for g in bundle.gates if g.name == "review")
    assert review.status == "pass" and review.metrics["score"] == 7


@pytest.mark.asyncio
async def test_slides_and_mindmap_runs_deliver_binary_formats(settings) -> None:
    _, detail, _ = await _run("slides", "组会汇报：CASSI", "unused", settings)
    bundle = build_bundle(detail)
    pptx = next(f for f in bundle.files if f.format == "pptx")
    assert pptx_stats(pptx.data)["slides"] >= 8
    assert next(g for g in bundle.gates if g.name == "slides").status == "pass"

    _, detail, _ = await _run("mindmap", "Transformer 知识图谱", "unused", settings)
    bundle = build_bundle(detail)
    names = {f.name for f in bundle.files}
    assert any(n.endswith("-mindmap.html") for n in names)
    assert any(n.endswith("-mindmap.png") for n in names)
    assert next(g for g in bundle.gates if g.name == "structure").status == "pass"


@pytest.mark.asyncio
async def test_data_analysis_run_falls_back_when_writer_invents_numbers(settings) -> None:
    query = "三种方法差异显著吗？\nmethod,psnr\n" + "\n".join(
        f"{m},{v}" for m, vs in (("A", (30, 30.5, 31)), ("B", (33, 33.2, 34))) for v in vs
    )
    report, detail, _ = await _run("dataAnalysis", query, "## 结论\n均值高达 99.99。", settings)
    assert "99.99" not in report.markdown
    assert "## 统计结果" in report.markdown
    bundle = build_bundle(detail)
    formats = [f.format for f in bundle.files]
    assert "png" in formats and "xlsx" in formats
    assert next(g for g in bundle.gates if g.name == "consistency").status == "pass"


def test_mindmap_stats_counts_nodes() -> None:
    mindmap = Mindmap(
        root="r", branches=[{"label": "a", "children": [{"label": "b"}, {"label": "c"}]}]
    )
    assert mindmap_stats(mindmap) == {"branches": 1, "nodes": 3, "min_branch_nodes": 2}


@pytest.mark.asyncio
async def test_paper_intake_isolates_failures(settings, monkeypatch) -> None:
    from deep_research.agents.base import RunContext
    from deep_research.observability import Tracer
    from deep_research.workbench import intake

    async def failing(paper, ctx):  # type: ignore[no-untyped-def]
        raise RuntimeError("network down")

    monkeypatch.setattr(intake, "fetch_paper", failing)
    template = get_template("paperRead")
    assert template is not None
    bb = Blackboard(query="q")
    bb.scratch[CONTRACT_SCRATCH_KEY] = build_contract(
        template, "https://arxiv.org/abs/2205.10102"
    ).model_dump(mode="json")
    ctx = RunContext(llm=FakeLLM(), search_tool=FakeSearch(), tracer=Tracer(), settings=settings)
    bb = await PaperIntake().step(bb, ctx)
    assert bb.results == []
    assert bb.scratch["intake_sources"]["failures"][0]["error"].startswith("RuntimeError")


class _NoSearch(FakeSearch):
    async def search(self, query, *, max_results=5):  # type: ignore[no-untyped-def]
        raise AssertionError("paper tasks must never fall back to open search")


PASTED_ABSTRACT = (
    "本文提出一种面向编码孔径快照光谱成像的深度展开重建网络。"
    "我们将物理前向模型嵌入每一级迭代，并以可学习的先验替代手工正则项。"
    "在 CAVE 与 KAIST 仿真数据上，所提方法的平均 PSNR 达到 38.4 dB，"
    "相比同等参数量的端到端网络提升 1.2 dB，同时推理耗时降低约三成。"
    "我们还在实拍数据上验证了方法对掩膜标定误差的稳健性，并讨论了噪声模型失配带来的局限。"
    "代码与训练配置将随论文一同公开，便于复现与后续比较。"
)


class _PastedLLM(FakeLLM):
    async def parse(self, system, user, schema, *, temperature=0.2, retries=2):  # type: ignore[no-untyped-def]
        from deep_research.models import ExtractedFindingList

        if schema is ExtractedFindingList and "https://workspace.invalid/pasted/" in user:
            start = user.index("https://workspace.invalid/pasted/")
            url = user[start:].split()[0].rstrip("）)]，,")
            return ExtractedFindingList(
                findings=[
                    {
                        "statement": "方法在仿真数据上平均 PSNR 为 38.4 dB",
                        "source_url": url,
                        "evidence_quote": "平均 PSNR 达到 38.4 dB",
                        "confidence": 0.9,
                    }
                ]
            )
        return await super().parse(system, user, schema, temperature=temperature, retries=retries)


@pytest.mark.asyncio
async def test_paper_intake_reads_pasted_paper_text_instead_of_searching(settings) -> None:
    from deep_research.agents.base import RunContext
    from deep_research.observability import Tracer

    template = get_template("peerReview")
    assert template is not None
    bb = Blackboard(query=PASTED_ABSTRACT)
    bb.scratch[CONTRACT_SCRATCH_KEY] = build_contract(template, PASTED_ABSTRACT).model_dump(
        mode="json"
    )
    ctx = RunContext(llm=_PastedLLM(), search_tool=_NoSearch(), tracer=Tracer(), settings=settings)
    bb = await PaperIntake().step(bb, ctx)
    intake = bb.scratch["intake_sources"]
    assert intake["mode"] == "pasted" and intake["sections"]
    findings = [f for r in bb.results for f in r.findings]
    assert findings and findings[0].verification.status == "verified"


@pytest.mark.asyncio
async def test_paper_intake_without_any_paper_reports_instead_of_searching(settings) -> None:
    from deep_research.agents.base import RunContext
    from deep_research.observability import Tracer

    template = get_template("paperRead")
    assert template is not None
    bb = Blackboard(query="帮我精读一下")
    bb.scratch[CONTRACT_SCRATCH_KEY] = build_contract(template, "帮我精读一下").model_dump(
        mode="json"
    )
    ctx = RunContext(llm=FakeLLM(), search_tool=_NoSearch(), tracer=Tracer(), settings=settings)
    bb = await PaperIntake().step(bb, ctx)
    assert bb.results == []
    intake = bb.scratch["intake_sources"]
    assert intake["mode"] == "missing" and "未提供论文" in intake["failures"][0]["error"]


# --------------------------------------------------------------------------- HTTP


def _client(app) -> httpx.AsyncClient:  # type: ignore[no-untyped-def]
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
def api_repo(monkeypatch):  # type: ignore[no-untyped-def]
    from deep_research import api
    from deep_research.config import Settings

    repo = InMemoryRepository()
    monkeypatch.setattr(api.app.state, "catalog", None, raising=False)
    api.app.state.settings = Settings()
    api.app.state.repo = repo
    api.app.state.live = {}
    api.app.state.tasks = set()
    api.app.state.run_tasks = {}
    api.app.state.cancellation_requested = set()
    api.app.state.config_lock = asyncio.Lock()
    api.app.state.delivery_cache = {}
    api.app.state.run_admission = api.RunAdmission(
        api.app.state.settings.max_active_runs, api.app.state.settings.max_queued_runs
    )

    async def _noop(*args, **kwargs):  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr(api, "_execute", _noop)
    # 限流器是进程级单例：本文件的请求不得耗掉后续测试文件的配额
    monkeypatch.setattr(api, "_run_limiter", api._RateLimiter(max_calls=1000, window_seconds=60.0))
    return api, repo


@pytest.mark.asyncio
async def test_templates_endpoint_and_contract_preview(api_repo) -> None:
    api, _ = api_repo
    async with _client(api.app) as client:
        templates = (await client.get("/api/templates")).json()
        preview = await client.post(
            "/api/templates/contract",
            json={"template": "peerReview", "query": "https://arxiv.org/abs/2205.10102"},
        )
        unknown = await client.post("/api/templates/contract", json={"template": "x", "query": "q"})
    assert {t["key"] for t in templates} == set(TASK_TEMPLATES)
    assert preview.status_code == 200
    assert preview.json()["papers"][0]["value"] == "2205.10102"
    assert unknown.status_code == 404


@pytest.mark.asyncio
async def test_create_run_with_template_freezes_contract_and_workflow(api_repo) -> None:
    api, repo = api_repo
    async with _client(api.app) as client:
        response = await client.post(
            "/api/runs",
            json={
                "query": "https://arxiv.org/abs/2205.10102",
                "template": "paperRead",
                "workflow": "deep",
            },
        )
        bad = await client.post("/api/runs", json={"query": "q", "template": "nope"})
    assert response.status_code == 202
    assert bad.status_code == 422
    detail = await repo.get_run(response.json()["run_id"])
    assert detail is not None and detail.orchestration is not None
    assert detail.orchestration.workflow_name == "paper_read"
    contract = detail.orchestration.checkpoint["scratch"][CONTRACT_SCRATCH_KEY]
    assert contract["template"] == "paperRead"
    assert contract["strategy"] == "none"


async def test_provided_review_requires_real_inputs_and_freezes_closed_scope(api_repo):
    api, repo = api_repo
    async with _client(api.app) as client:
        missing = await client.post(
            "/api/runs",
            json={
                "template": "litReview",
                "strategy": "none",
                "query": "Only supplied documents. " * 12,
            },
        )
        created = await client.post(
            "/api/runs",
            json={
                "template": "litReview",
                "strategy": "none",
                "query": "Compare https://arxiv.org/abs/2401.00001 and https://arxiv.org/abs/2401.00002",
            },
        )
    assert missing.status_code == 422
    assert created.status_code == 202, created.text
    detail = await repo.get_run(created.json()["run_id"])
    assert detail.orchestration.workflow_name == "lit_review_provided"
    contract = detail.orchestration.checkpoint["scratch"][CONTRACT_SCRATCH_KEY]
    assert contract["min_citations"] == 0 and len(contract["papers"]) == 2


async def test_task_creation_retains_all_attachment_chunks_and_rejects_partial_uploads(api_repo):
    from deep_research.workbench.attachments import parse_attachment

    api, repo = api_repo
    files = [
        await parse_attachment(
            (f"Paper {i} " + "Evidence text. " * 14000 + f"END-{i}").encode(), f"{i}.txt"
        )
        for i in range(3)
    ]
    payload = {
        "template": "litReview",
        "strategy": "none",
        "query": "综述这些文献",
        "attachments": [item.model_dump() for item in files],
    }
    async with _client(api.app) as client:
        created = await client.post("/api/runs", json=payload)
        payload["attachments"][2]["truncated"] = True
        rejected = await client.post("/api/runs", json=payload)
    assert created.status_code == 202, created.text
    assert rejected.status_code == 422 and "未完整" in rejected.json()["detail"]["message"]
    detail = await repo.get_run(created.json()["run_id"])
    frozen = detail.orchestration.checkpoint["scratch"]["attachments"]
    assert len(frozen) == 3 and sum(len(a["chunks"]) for a in frozen) > 120
    assert all(f"END-{i}" in a["chunks"][-1]["content"] for i, a in enumerate(frozen))
    assert len(await repo.list_runs()) == 1


@pytest.mark.asyncio
async def test_paper_task_without_paper_is_rejected_before_creating_a_run(api_repo) -> None:
    """论文类任务没有链接、正文或附件：直接提示补充，不建一条注定空转的 run。"""
    api, repo = api_repo
    async with _client(api.app) as client:
        missing = await client.post(
            "/api/runs", json={"query": "帮我评审", "template": "peerReview"}
        )
        pasted = await client.post(
            "/api/runs", json={"query": PASTED_ABSTRACT, "template": "peerReview"}
        )
        preview = await client.post(
            "/api/templates/contract", json={"template": "peerReview", "query": PASTED_ABSTRACT}
        )
    assert missing.status_code == 422
    assert missing.json()["detail"]["code"] == "paper_required"
    assert pasted.status_code == 202, pasted.text
    assert preview.json()["pasted_paper_chars"] == len(PASTED_ABSTRACT)
    assert len(await repo.list_runs(limit=10)) == 1


@pytest.mark.asyncio
async def test_template_contract_carries_the_user_quality_settings(api_repo) -> None:
    """契约里的质量策略优先于 settings.quality；不带上用户设置，模板任务就永远用默认值。"""
    from dataclasses import replace

    api, repo = api_repo
    api.app.state.settings = replace(api.app.state.settings, quality={"survey_min_citations": 5})
    async with _client(api.app) as client:
        created = await client.post(
            "/api/runs",
            json={"query": "快照光谱成像重建方法", "template": "litReview", "clarified": True},
        )
        preview = await client.post(
            "/api/templates/contract", json={"template": "litReview", "query": "快照光谱成像"}
        )
    assert created.status_code == 202, created.text
    detail = await repo.get_run(created.json()["run_id"])
    assert detail is not None and detail.orchestration is not None
    contract = detail.orchestration.checkpoint["scratch"][CONTRACT_SCRATCH_KEY]
    assert contract["quality"]["survey_min_citations"] == 5
    assert preview.json()["quality"]["survey_min_citations"] == 5


def test_failed_attempt_keeps_the_replan_ledger() -> None:
    """失败尝试的候选黑板被丢弃时，补救额度账本必须带回已提交状态。"""
    from deep_research.agents.base import Blackboard
    from deep_research.workbench import replan
    from deep_research.workflow import _carry_replan_ledger

    committed = Blackboard(query="q")
    failed = committed.model_copy(deep=True)
    replan.replan_state(failed.scratch)["replans"] = 1

    _carry_replan_ledger(committed, failed)

    assert replan.replan_state(committed.scratch)["replans"] == 1


@pytest.mark.asyncio
async def test_strategy_selects_the_evidence_chain_for_a_task(api_repo) -> None:
    """深度检索是任务的一种策略：同一任务换策略只换检索链，交付角色不变。"""
    api, repo = api_repo
    cases = [
        ({"template": "litReview"}, "lit_review"),
        ({"template": "litReview", "strategy": "quick"}, "lit_review_quick"),
        ({"template": "slides", "strategy": "deep"}, "slides_deep"),
        ({"template": "mindmap"}, "mindmap"),
        ({"template": "autoResearch", "strategy": "quick"}, "research_quick"),
        ({"template": "autoResearch"}, "research"),
    ]
    async with _client(api.app) as client:
        run_ids = []
        for extra, _ in cases:
            response = await client.post(
                "/api/runs", json={"query": "快照光谱成像重建方法", "clarified": True, **extra}
            )
            assert response.status_code == 202, (extra, response.text)
            run_ids.append(response.json()["run_id"])
        unsupported = await client.post(
            "/api/runs",
            json={
                "query": "https://arxiv.org/abs/2205.10102",
                "template": "paperRead",
                "strategy": "deep",
            },
        )
    for run_id, (_, expected) in zip(run_ids, cases, strict=True):
        detail = await repo.get_run(run_id)
        assert detail is not None and detail.orchestration is not None
        assert detail.orchestration.workflow_name == expected
    assert unsupported.status_code == 422
    assert unsupported.json()["detail"]["code"] == "unsupported_strategy"


def test_every_template_strategy_resolves_to_a_registered_workflow() -> None:
    from deep_research.workbench.templates import STRATEGY_LABELS, TASK_TEMPLATES
    from deep_research.workflows import WORKFLOWS

    for template in TASK_TEMPLATES.values():
        assert template.default_strategy in template.strategies
        for strategy, workflow in template.strategies.items():
            assert strategy in STRATEGY_LABELS and workflow in WORKFLOWS
        public = template.to_public()
        assert public["default_strategy"] == template.default_strategy


@pytest.mark.asyncio
async def test_deliverables_endpoints(api_repo) -> None:
    api, repo = api_repo
    template, execution = _execution("研究问题", "autoResearch", api.app.state.settings)
    run_id = await repo.create_run("研究问题", execution=execution)
    async with _client(api.app) as client:
        pending = await client.get(f"/api/runs/{run_id}/deliverables")
        await repo.save_result(
            run_id, ResearchResult(sub_question="s", findings=[verified_finding()])
        )
        await repo.save_report(
            run_id,
            Report(
                query="q",
                markdown="## 摘要\n发现X [1]\n\n## 分析\n发现X [1]\n\n## 结论\n发现X [1]",
                citations=["https://a.com"],
            ),
        )
        await repo.set_status(run_id, "done")
        registry = (await client.get(f"/api/runs/{run_id}/deliverables")).json()
        pdf_name = next(i["name"] for i in registry["items"] if i["format"] == "pdf")
        pdf = await client.get(f"/api/runs/{run_id}/deliverables/{pdf_name}")
        html_name = next(i["name"] for i in registry["items"] if i["format"] == "html")
        html = await client.get(f"/api/runs/{run_id}/deliverables/{html_name}?download=0")
        traversal = await client.get(f"/api/runs/{run_id}/deliverables/..%2F..%2Fetc")
        meta = (await client.get(f"/api/runs/{run_id}/template")).json()
    assert pending.status_code == 409
    assert registry["template"] == "autoResearch" and registry["primary"].endswith(".pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert pdf.headers["x-content-sha256"]
    assert "default-src 'none'" in html.headers["content-security-policy"]
    assert html.headers["content-disposition"].startswith("inline")
    assert traversal.status_code in {400, 404}
    assert meta["template"]["key"] == "autoResearch"
    assert "filename*=UTF-8''" in pdf.headers["content-disposition"]
    assert pdf.headers["cache-control"] == "private, no-store"


@pytest.mark.asyncio
async def test_concurrent_cold_deliverable_requests_build_once(api_repo, monkeypatch) -> None:
    """登记表与预览同时到达冷缓存时，只生成一次交付包。"""
    api, repo = api_repo
    template, execution = _execution("研究问题", "autoResearch", api.app.state.settings)
    run_id = await repo.create_run("研究问题", execution=execution)
    await repo.save_report(run_id, Report(query="q", markdown="## 摘要\n正文", citations=[]))
    await repo.set_status(run_id, "done")
    from deep_research.workbench import api as workbench_api

    calls: list[str] = []
    real_build = workbench_api.build_bundle

    def counting_build(detail):  # type: ignore[no-untyped-def]
        calls.append(detail.id)
        return real_build(detail)

    monkeypatch.setattr(workbench_api, "build_bundle", counting_build)
    async with _client(api.app) as client:
        responses = await asyncio.gather(
            *(client.get(f"/api/runs/{run_id}/deliverables") for _ in range(3))
        )
        warm = await client.get(f"/api/runs/{run_id}/deliverables")
        assert warm.status_code == 200
    assert all(response.status_code == 200 for response in responses)
    assert len(calls) == 1


def test_parse_blocks_handles_nested_lists_and_tables() -> None:
    blocks = parse_blocks("- a\n  - b\n\n| x | y |\n|---|---|\n| 1 | 2 |\n")
    assert [b.kind for b in blocks] == ["list", "table"]
    assert [(i.depth, i.ordered) for i in blocks[0].items] == [(0, False), (1, False)]
    assert blocks[1].rows[1][1][0].text == "2"


# --------------------------------------------------------------------------- 学术问答


class QaLLM(FakeLLM):
    async def stream(self, system, user, *, temperature=0.4):  # type: ignore[no-untyped-def]
        self.stream_calls += 1
        yield "根据检索，发现X [1]。"


@pytest.mark.asyncio
async def test_answer_question_verifies_and_cites(settings) -> None:
    from deep_research.agents.base import RunContext
    from deep_research.observability import Tracer
    from deep_research.workbench.qa import answer_question

    ctx = RunContext(llm=QaLLM(), search_tool=FakeSearch(), tracer=Tracer(), settings=settings)
    result = await answer_question("CASSI 是什么？", history=[], ctx=ctx, include_web=True)
    assert result.citations == ["https://a.com"]
    assert "[1]" in result.answer and not result.fallback
    assert [t["tool"] for t in result.thoughts] == [
        "rewrite",
        "extraction_audit",
        "search_and_verify",
        "claim_check",
        "citation_check",
        "citation_binding",
    ]


@pytest.mark.asyncio
async def test_answer_question_skips_search_for_greetings(settings) -> None:
    from deep_research.agents.base import RunContext
    from deep_research.observability import Tracer
    from deep_research.workbench.qa import answer_question

    class NoSearch:
        async def search(self, query, *, max_results=5):  # type: ignore[no-untyped-def]
            raise AssertionError("greetings must not trigger web search")

    ctx = RunContext(llm=QaLLM(), search_tool=NoSearch(), tracer=Tracer(), settings=settings)
    result = await answer_question("你好！", history=[], ctx=ctx)
    assert result.citations == [] and not result.fallback
    assert result.thoughts[0]["tool"] == "skip_search"


@pytest.mark.asyncio
async def test_answer_question_admits_when_no_evidence(settings) -> None:
    from deep_research.agents.base import RunContext
    from deep_research.observability import Tracer
    from deep_research.workbench.qa import answer_question

    class Empty(FakeSearch):
        async def search(self, query, *, max_results=5):  # type: ignore[no-untyped-def]
            return []

    ctx = RunContext(llm=QaLLM(), search_tool=Empty(), tracer=Tracer(), settings=settings)
    result = await answer_question("不存在的方向", history=[], ctx=ctx, include_web=True)
    assert result.fallback and result.citations == []
    assert "不足以回答" in result.answer


@pytest.mark.asyncio
async def test_answer_question_uses_model_knowledge_without_sources(settings) -> None:
    from deep_research.agents.base import RunContext
    from deep_research.observability import Tracer
    from deep_research.workbench.qa import answer_question

    class NoSearch:
        async def search(self, query, *, max_results=5):  # type: ignore[no-untyped-def]
            raise AssertionError("source-free answers must not trigger web search")

    ctx = RunContext(llm=QaLLM(), search_tool=NoSearch(), tracer=Tracer(), settings=settings)
    result = await answer_question("什么是 CASSI？", history=[], ctx=ctx)
    assert result.citations == [] and not result.fallback
    assert result.thoughts[-1]["tool"] == "model_knowledge"


def test_followup_query_uses_previous_turn() -> None:
    from deep_research.workbench.qa import _contextual_query

    history = [{"query": "DOE 光谱成像误差补偿", "answer": "..."}]
    assert _contextual_query("第二篇呢", history).startswith("DOE 光谱成像误差补偿")
    assert _contextual_query("深度展开网络在压缩感知中的作用是什么", history).startswith("深度")


@pytest.mark.asyncio
async def test_qa_endpoints_round_trip_and_isolate_owners(api_repo, monkeypatch) -> None:
    api, _ = api_repo
    from deep_research.workbench.qa_store import InMemoryQaStore

    api.app.state.qa_store = InMemoryQaStore()

    async def fake_build_agent(app, settings, **kwargs):  # type: ignore[no-untyped-def]
        agent = DeepResearchAgent(settings, llm=QaLLM(), search_tool=FakeSearch())
        return agent, None

    monkeypatch.setattr(api, "_build_agent", fake_build_agent)
    async with _client(api.app) as client:
        created = (await client.post("/api/qa/conversations", json={"title": "误差文献"})).json()
        cid = created["id"]
        answer = await client.post(
            f"/api/qa/conversations/{cid}/messages",
            json={"query": "CASSI 是什么？", "sources": ["web"]},
        )
        follow = await client.post(
            f"/api/qa/conversations/{cid}/messages",
            json={"query": "第二篇呢", "sources": ["web"]},
        )
        detail = (await client.get(f"/api/qa/conversations/{cid}")).json()
        listing = (await client.get("/api/qa/conversations")).json()
        missing = await client.get("/api/qa/conversations/nope")
        deleted = await client.delete(f"/api/qa/conversations/{cid}")
        gone = await client.get(f"/api/qa/conversations/{cid}")
    assert answer.status_code == 201 and answer.json()["citations"] == ["https://a.com"]
    assert follow.json()["position"] == 1
    assert follow.json()["thoughts"][0]["observation"].startswith("CASSI")
    assert [m["query"] for m in detail["messages"]] == ["CASSI 是什么？", "第二篇呢"]
    assert listing[0]["message_count"] == 2 and "owner_id" not in listing[0]
    assert missing.status_code == 404
    assert deleted.status_code == 204 and gone.status_code == 404


async def test_qa_preserves_evidence_beyond_thirtieth_finding(api_repo, monkeypatch):
    from deep_research.workbench import qa_api
    from deep_research.workbench.qa import QaAnswer
    from deep_research.workbench.qa_store import InMemoryQaStore
    from tests.fakes import verified_finding

    api, _ = api_repo
    api.app.state.qa_store = InMemoryQaStore()
    findings = [verified_finding(source_url=f"https://paper.example/{i}") for i in range(44)]

    async def fake_build_agent(app, settings, **kwargs):
        return DeepResearchAgent(settings, llm=QaLLM(), search_tool=FakeSearch()), None

    async def answer(*args, **kwargs):
        return QaAnswer(
            answer="The final source [44].",
            citations=[f.source_url for f in findings],
            findings=findings,
        )

    monkeypatch.setattr(api, "_build_agent", fake_build_agent)
    monkeypatch.setattr(qa_api, "answer_question", answer)
    async with _client(api.app) as client:
        cid = (await client.post("/api/qa/conversations", json={})).json()["id"]
        response = await client.post(f"/api/qa/conversations/{cid}/messages", json={"query": "q"})
    assert response.status_code == 201
    data = response.json()
    assert len(data["evidence"]) == 44
    assert data["evidence"][-1]["source_url"] == data["citations"][-1]
    assert all(item["support_id"] for item in data["evidence"])


@pytest.mark.asyncio
async def test_qa_stream_keeps_source_free_turn_on_model_knowledge_path(
    api_repo, monkeypatch
) -> None:
    api, _ = api_repo
    from deep_research.workbench.qa_store import InMemoryQaStore

    api.app.state.qa_store = InMemoryQaStore()

    async def fake_build_agent(app, settings, **kwargs):  # type: ignore[no-untyped-def]
        agent = DeepResearchAgent(settings, llm=QaLLM(), search_tool=FakeSearch())
        return agent, None

    monkeypatch.setattr(api, "_build_agent", fake_build_agent)
    async with _client(api.app) as client:
        created = await client.post("/api/qa/conversations", json={"title": "无检索问答"})
        cid = created.json()["id"]
        response = await client.post(
            f"/api/qa/conversations/{cid}/messages/stream",
            json={"query": "什么是 CASSI？", "sources": []},
        )
        detail = (await client.get(f"/api/qa/conversations/{cid}")).json()
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    assert "event: complete" in response.text
    assert detail["messages"][0]["citations"] == []
    assert detail["messages"][0]["thoughts"][-1]["tool"] == "model_knowledge"


@pytest.mark.asyncio
async def test_qa_reasoning_is_streamed_and_saved_with_the_message(api_repo, monkeypatch) -> None:
    api, _ = api_repo
    from deep_research.workbench.qa_store import InMemoryQaStore

    api.app.state.qa_store = InMemoryQaStore()

    async def fake_build_agent(app, settings, **kwargs):  # type: ignore[no-untyped-def]
        agent = DeepResearchAgent(settings, llm=QaLLM(), search_tool=FakeSearch())

        async def stream(*args, **kwargs):  # type: ignore[no-untyped-def]
            agent.tracer.emit(
                "LLM",
                "info",
                data={
                    "call_id": "test-call",
                    "model": "test",
                    "reasoning_delta": "供应商返回的思考内容",
                },
            )
            yield "本轮答案。"

        monkeypatch.setattr(agent.llm, "stream", stream)
        return agent, None

    monkeypatch.setattr(api, "_build_agent", fake_build_agent)
    async with _client(api.app) as client:
        created = await client.post("/api/qa/conversations", json={"title": "流式活动"})
        cid = created.json()["id"]
        response = await client.post(
            f"/api/qa/conversations/{cid}/messages/stream", json={"query": "解释这个概念"}
        )
        detail = (await client.get(f"/api/qa/conversations/{cid}")).json()
    assert "event: reasoning" in response.text
    assert "event: delta" in response.text and "event: complete" in response.text
    thought = next(
        item for item in detail["messages"][0]["thoughts"] if item["tool"] == "model_reasoning"
    )
    assert thought["observation"] == "供应商返回的思考内容"


@pytest.mark.asyncio
async def test_sql_qa_store_orders_messages(tmp_path) -> None:
    from deep_research.persistence.db import create_all, make_engine, make_sessionmaker
    from deep_research.workbench.qa_store import QaMessage, SqlQaStore

    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'qa.db'}")
    try:
        await create_all(engine)
        store = SqlQaStore(make_sessionmaker(engine))
        conversation = await store.create("u1", "t")
        for text in ("一", "二"):
            await store.append(
                conversation.id, QaMessage(id="", position=0, query=text, answer="a")
            )
        loaded = await store.get(conversation.id)
        assert loaded is not None and [m.query for m in loaded.messages] == ["一", "二"]
        assert [m.position for m in loaded.messages] == [0, 1]
        assert (await store.list("u1"))[0].message_count == 2
        assert await store.list("u2") == []
        assert await store.delete(conversation.id) is True
    finally:
        await engine.dispose()


# --------------------------------------------------------------------------- 叙述


def test_narrative_summarises_phases_without_tokens() -> None:
    from deep_research.observability import Event
    from deep_research.workbench.narrative import build_narrative

    events = [
        Event(seq=0, stage="PLANNER", type="info", message="p", data={"sub_questions": ["a", "b"]}),
        Event(
            seq=1,
            stage="RESEARCHER",
            type="info",
            data={"category": "source_policy", "allowed": 3, "blocked": 1},
        ),
        Event(
            seq=2,
            stage="RESEARCHER",
            type="finding",
            data={"candidate_count": 4, "verified_count": 3},
        ),
        Event(seq=3, stage="SYNTHESIZER", type="token", data={"delta": "x"}),
        Event(seq=4, stage="ORCHESTRATOR", type="round", message="第 1 轮补洞"),
        Event(seq=5, stage="SYNTHESIZER", type="start"),
    ]
    running = build_narrative(events, run_status="running")
    keys = [s["key"] for s in running["sections"]]
    assert keys == ["understand", "research", "reflect", "write", "finish"]
    research = next(s for s in running["sections"] if s["key"] == "research")
    assert research["status"] == "done"
    assert any("放行 3 个、拦截 1 个" in line for line in research["lines"])
    assert any("4 条候选发现，其中 3 条通过" in line for line in research["lines"])
    assert next(s for s in running["sections"] if s["key"] == "write")["status"] == "active"
    done = build_narrative(events, run_status="done")
    assert all(s["status"] == "done" for s in done["sections"])
    assert done["headline"].startswith("已完成：3 条")


# --------------------------------------------------------------------------- 重规划


class ReplanLLM(FakeLLM):
    """第一次执行步骤时报错，重规划器给出补救，第二次执行成功。"""

    def __init__(self, decision: dict) -> None:
        super().__init__()
        self.decision = decision
        self.prompts: list[str] = []

    async def complete(self, system, user, *, temperature=0.3):  # type: ignore[no-untyped-def]
        self.complete_calls += 1
        self.prompts.append(user)
        if self.complete_calls == 1:
            raise RuntimeError("model produced unusable output")
        return "# 补救后的结果\n"

    async def parse(self, system, user, schema, *, temperature=0.2, retries=2):  # type: ignore[no-untyped-def]
        from deep_research.workbench.replan import ReplanDecision

        if schema is ReplanDecision:
            return ReplanDecision(**self.decision)
        return await super().parse(system, user, schema, temperature=temperature, retries=retries)


@pytest.mark.asyncio
async def test_failed_plan_step_is_rescued_once_and_logged(tmp_path) -> None:
    from deep_research.config import Settings

    plan = {
        "slug": "replan-run",
        "title": "Replan run",
        "steps": [{"id": "only", "name": "Only", "prompt": "Write the report"}],
    }
    llm = ReplanLLM(
        {"action": "rescue", "name": "缩小范围", "prompt": "只写摘要", "reason": "降级"}
    )
    agent = DeepResearchAgent(
        Settings(artifact_root=str(tmp_path), runner_enabled=False),
        llm=llm,
        search_tool=FakeSearch(),
        execution_plan=plan,
    )
    try:
        report = await agent.run("q")
    finally:
        await agent.aclose()
    assert "补救后的结果" in report.markdown
    assert llm.complete_calls == 2
    assert "只写摘要" in llm.prompts[1]
    assert any(
        isinstance(e.data, dict) and e.data.get("event_name") == "plan.replan"
        for e in agent.tracer.events
    )


def test_replan_limits_are_enforced() -> None:
    from deep_research.workbench import replan

    scratch: dict = {}
    decision = replan.ReplanDecision(action="rescue", prompt="p")
    assert replan.can_replan(scratch, "a") == (True, "")
    replan.record(scratch, step_id="a", outcome="failed", decision=decision, result="done")
    assert replan.can_replan(scratch, "a")[0] is False  # 同一步只补救一次
    replan.record(scratch, step_id="b", outcome="partial", decision=decision, result="done")
    replan.record(scratch, step_id="c", outcome="partial", decision=decision, result="done")
    allowed, why = replan.can_replan(scratch, "d")
    assert not allowed and "3 次" in why
    assert replan.is_infrastructure_error("LeaseLostError: lease gone")
    assert not replan.is_infrastructure_error("ValueError: bad json")
    # 鉴权失败按状态码识别；路径或计数里恰好出现 401/403 不算
    assert replan.is_infrastructure_error("HTTPStatusError: status 401 for url")
    assert replan.is_infrastructure_error("Error code: 403 - forbidden")
    assert not replan.is_infrastructure_error("FileNotFoundError: data/run-4013/table.csv")
    assert not replan.is_infrastructure_error("ValueError: expected 403 rows, got 12")


# --------------------------------------------------------------------------- 工作区


@pytest.mark.asyncio
async def test_workspace_lists_steps_and_manifest_files(api_repo, tmp_path) -> None:
    api, repo = api_repo
    from dataclasses import replace

    settings = replace(api.app.state.settings, artifact_root=str(tmp_path))
    api.app.state.settings = settings
    plan = {
        "slug": "ws-run",
        "title": "Workspace",
        "steps": [
            {
                "id": "collect",
                "name": "Collect",
                "prompt": "Collect",
                "artifacts": ["work/ws-run/explore/notes.md"],
            },
            {
                "id": "deliver",
                "name": "Deliver",
                "prompt": "Deliver",
                "artifacts": ["output/ws-run/final/report.md"],
            },
        ],
    }
    execution = create_initial_execution("q", "deep", settings, execution_plan=plan)
    run_id = await repo.create_run("q", execution=execution)
    agent = DeepResearchAgent(
        settings,
        llm=FakeLLM(),
        search_tool=FakeSearch(),
        repo=repo,
        run_id=run_id,
        initial_execution=execution,
        execution_plan=plan,
    )
    try:
        await agent.run("q")
    finally:
        await agent.aclose()
    async with _client(api.app) as client:
        ws = (await client.get(f"/api/runs/{run_id}/workspace")).json()
        paths = {f["path"] for f in ws["files"]}
        report = await client.get(
            f"/api/runs/{run_id}/workspace/file",
            params={"path": "output/ws-run/final/report.md"},
        )
        forged = await client.get(
            f"/api/runs/{run_id}/workspace/file", params={"path": "output/ws-run/../../x"}
        )
        unlisted = await client.get(
            f"/api/runs/{run_id}/workspace/file", params={"path": "output/ws-run/final/nope.md"}
        )
    assert [s["status"] for s in ws["steps"]] == ["succeeded", "succeeded"]
    assert {"work/ws-run/explore/notes.md", "output/ws-run/final/report.md"} <= paths
    assert ws["files"][0]["area"] == "output"  # 成品排在前面
    assert report.status_code == 200 and report.text.strip()
    assert forged.status_code == 400 and unlisted.status_code == 404


# --------------------------------------------------------------------------- 档位 / 额度 / GPU 确认


def test_tier_overrides_respect_explicit_params_and_ceilings() -> None:
    from deep_research.workbench.tiers import tier_overrides

    deep = tier_overrides("deep", explicit={}, ceilings={"max_tokens": 100_000})
    assert deep["max_rounds"] == 2 and "max_tokens" not in deep
    light = tier_overrides("light", explicit={"max_rounds": 3}, ceilings={})
    assert "max_rounds" not in light and light["max_sub_questions"] == 3
    assert tier_overrides(None, explicit={}, ceilings={}) == {}


@pytest.mark.asyncio
async def test_tier_is_frozen_into_run_settings(api_repo) -> None:
    api, repo = api_repo
    from deep_research.checkpoints import RUN_SETTINGS_KEY

    async with _client(api.app) as client:
        created = await client.post("/api/runs", json={"query": "研究问题", "tier": "light"})
        tiers = (await client.get("/api/tiers")).json()
    assert [t["key"] for t in tiers] == ["light", "standard", "deep"]
    detail = await repo.get_run(created.json()["run_id"])
    assert detail is not None and detail.orchestration is not None
    frozen = detail.orchestration.checkpoint["scratch"][RUN_SETTINGS_KEY]
    assert frozen["max_rounds"] == 0 and frozen["max_sub_questions"] == 3
    assert "max_tokens" not in frozen
    assert all("max_tokens" not in tier for tier in tiers)


def test_legacy_daily_token_quota_does_not_block_usage() -> None:
    from types import SimpleNamespace

    from deep_research.workbench.usage import quota_view

    view = quota_view(
        {"runs": 2, "tokens": 5_000_000},
        SimpleNamespace(daily_run_quota=None, daily_token_quota=1),
    )
    assert view["tokens"] == {"used": 5_000_000, "limit": None}
    assert view["exhausted"] is False


@pytest.mark.asyncio
async def test_daily_quota_blocks_new_runs_and_reports_usage(api_repo) -> None:
    api, repo = api_repo
    from dataclasses import replace

    api.app.state.settings = replace(api.app.state.settings, daily_run_quota=1)
    async with _client(api.app) as client:
        first = await client.post("/api/runs", json={"query": "第一个"})
        usage = (await client.get("/api/usage")).json()
        second = await client.post("/api/runs", json={"query": "第二个"})
    assert first.status_code == 202
    assert usage["runs"] == {"used": 1, "limit": 1} and usage["exhausted"] is True
    assert second.status_code == 429
    assert second.json()["detail"]["code"] == "quota_exhausted"


@pytest.mark.asyncio
async def test_gpu_plans_are_rejected_because_deployment_only_calls_cloud_llms(api_repo) -> None:
    api, _ = api_repo
    plan = {
        "slug": "gpu-run",
        "title": "GPU",
        "steps": [
            {
                "id": "train",
                "name": "Train",
                "prompt": "run train.py",
                "resource": {"gpu": "t4", "gpu_count": 1},
            }
        ],
    }
    async with _client(api.app) as client:
        response = await client.post("/api/runs", json={"query": "q", "plan": plan})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "gpu_unsupported"
    assert response.json()["detail"]["steps"] == ["train"]


# --------------------------------------------------------------------------- 地名规范门


def test_territory_findings_and_normalization() -> None:
    from deep_research.workbench.territory import findings, normalize

    assert findings("该团队来自中国台湾新竹。") == []
    assert findings("Data from Taiwan, China and Japan.") == []
    assert {f.code for f in findings("该数据集采集于台湾。")} == {"unqualified-name"}
    assert "country-relation" in {f.code for f in findings("mainland China and Taiwan")}
    fixed = normalize(
        "数据来自台湾与大陆；大陆与台湾合作。Samples from Taiwan and mainland China and Taiwan."
    )
    assert findings(fixed) == [], fixed
    assert "大陆与中国台湾" in fixed and "Taiwan, China" in fixed
    assert normalize("中国台湾") == "中国台湾"


def test_publish_normalizes_every_format_and_gates_them(tmp_path) -> None:
    from deep_research.persistence.repository import RunDetail
    from deep_research.workbench.territory import check_files

    template, execution = _execution(
        "研究问题",
        "autoResearch",
        __import__("deep_research.config", fromlist=["Settings"]).Settings(),
    )
    detail = RunDetail(
        id="r",
        query="q",
        status="done",
        orchestration=execution,
        results=[ResearchResult(sub_question="s", findings=[verified_finding()])],
        report=Report(
            query="q",
            markdown=(
                "## 摘要\n样本采集于台湾的实验室 [1]\n\n## 分析\n发现X [1]\n\n## 结论\n发现X [1]"
            ),
            citations=["https://a.com"],
        ),
    )
    bundle = build_bundle(detail)
    gate = next(g for g in bundle.gates if g.name == "territory")
    assert gate.status == "pass", gate.issues
    assert gate.metrics["files_checked"] >= 4  # md / html / docx / pdf 都查了
    files = {f.name: f.data for f in bundle.files}
    assert check_files(files) == {}
    md = next(f.data.decode() for f in bundle.files if f.format == "md")
    assert "中国台湾" in md


def test_territory_gate_flags_unnormalized_binary_formats() -> None:
    from deep_research.workbench.delivery.docx import render_docx

    docx = render_docx("## 摘要\n样本采集于台湾。", title="t")
    result = gates.territory_gate({"r.docx": docx})
    assert result.status == "fail" and "unqualified-name" in result.issues[0]


# --------------------------------------------------------------------------- 步骤级质量检查


class CheckLLM(FakeLLM):
    """第一版产物含未规范的地名，第二版修正。"""

    def __init__(self) -> None:
        super().__init__()
        self.prompts: list[str] = []

    async def complete(self, system, user, *, temperature=0.3):  # type: ignore[no-untyped-def]
        self.complete_calls += 1
        self.prompts.append(user)
        return (
            "# 结果\n样本来自台湾的实验室。\n"
            if self.complete_calls == 1
            else "# 结果\n样本来自中国台湾的实验室。\n"
        )


@pytest.mark.asyncio
async def test_enable_check_reruns_step_until_artifacts_pass(tmp_path) -> None:
    from deep_research.config import Settings

    plan = {
        "slug": "check-run",
        "title": "Check",
        "steps": [
            {
                "id": "write",
                "name": "Write",
                "prompt": "Write the result",
                "enable_check": True,
                "max_check_attempts": 2,
                "artifacts": ["output/check-run/final/result.md"],
            }
        ],
    }
    llm = CheckLLM()
    agent = DeepResearchAgent(
        Settings(artifact_root=str(tmp_path), runner_enabled=False),
        llm=llm,
        search_tool=FakeSearch(),
        execution_plan=plan,
    )
    try:
        await agent.run("q")
    finally:
        await agent.aclose()
    assert llm.complete_calls == 2
    assert "未通过质量检查" in llm.prompts[1] and "unqualified-name" in llm.prompts[1]
    checks = [
        e.data
        for e in agent.tracer.events
        if isinstance(e.data, dict) and e.data.get("event_name") == "plan.check"
    ]
    assert [bool(c["problems"]) for c in checks] == [True, False]


# --------------------------------------------------------------------------- 概念图


def test_concept_figure_renders_deterministically() -> None:
    from deep_research.workbench.figures import ConceptFigure, render_concept_png

    figure = ConceptFigure(
        title="CASSI 重建框架",
        layout="flow",
        nodes=[
            {"id": "y", "label": "测量 y"},
            {"id": "net", "label": "深度展开网络", "group": "算法"},
            {"id": "x", "label": "光谱立方 x"},
        ],
        edges=[{"source": "y", "target": "net"}, {"source": "net", "target": "x", "label": "重建"}],
    )
    first = render_concept_png(figure)
    assert first[:4] == b"\x89PNG" and first == render_concept_png(figure)


@pytest.mark.asyncio
async def test_image_model_path_falls_back_without_configuration(monkeypatch) -> None:
    from deep_research.workbench.figures import ConceptFigure, concept_figure

    monkeypatch.delenv("DR_IMAGE_MODEL", raising=False)
    data, route = await concept_figure(
        ConceptFigure(title="t", nodes=[{"id": "a", "label": "A"}, {"id": "b", "label": "B"}])
    )
    assert route == "diagram" and data[:4] == b"\x89PNG"


class FigureLLM(WorkbenchLLM):
    async def parse(self, system, user, schema, *, temperature=0.2, retries=2):  # type: ignore[no-untyped-def]
        from deep_research.workbench.figures import ConceptFigure

        if schema is ConceptFigure:
            return ConceptFigure(
                title="方法分类",
                layout="taxonomy",
                nodes=[{"id": i, "label": f"方法{i}"} for i in "abcd"],
                edges=[{"source": "a", "target": t} for t in "bcd"],
            )
        return await super().parse(system, user, schema, temperature=temperature, retries=retries)


@pytest.mark.asyncio
async def test_survey_delivers_concept_figure_in_every_format(settings) -> None:
    # This export fixture needs accepted prose before paying for an optional figure.
    body = "## 摘要\n\n发现X。\n\n" + "\n\n".join(
        f"## {t}\n发现X [1]" for t in ("引言", "主题综述", "方法对比", "开放问题", "局限", "结论")
    )
    template, execution = _execution("综述", "litReview", settings)
    repo = InMemoryRepository()
    run_id = await repo.create_run("综述", execution=execution)
    agent = DeepResearchAgent(
        settings,
        llm=FigureLLM(body),
        search_tool=FakeSearch(),
        workflow=template.workflow,
        repo=repo,
        run_id=run_id,
        initial_execution=execution,
    )
    await agent.run("综述")
    detail = await repo.get_run(run_id)
    assert detail is not None
    extras = detail.orchestration.checkpoint["scratch"]["workbench"]["extras"]
    assert extras["prose_review"]["status"] == "pass" and not extras["revision"]["remaining"]
    bundle = build_bundle(detail)
    assert any(f.name == "figures/fig_concept.png" for f in bundle.files)
    consistency = next(g for g in bundle.gates if g.name == "consistency")
    assert consistency.status == "pass" and consistency.metrics["docx_images"] == 1
