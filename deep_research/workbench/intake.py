"""论文导入：把用户点名的论文变成可逐字核验的来源，并抽取带出处的发现。

同行评审与论文精读的证据对象是**一篇指定论文**，不是开放检索的结果。
开放检索按相关性排序，很可能把用户给的那篇换成相近的另一篇——评审错论文
比没有评审更糟。因此这个角色：

1. 从任务契约里取出 arXiv / DOI / URL 指针；没有指针时依次改用上传的论文文件、
   粘贴的论文文本，三者都没有就如实报告缺少论文——任何情况下都不退回开放检索；
2. arXiv 走 ``id_list`` 精确取回元数据与 LaTeX 全文章节；PDF 链接走 OA PDF 解析；
   普通网页走资料库导入的同一条安全抓取路径；
3. 把来源交给 Researcher 的同一套「来源门禁 → 抽取 → 逐字核验 → 语义核验」链。

任何一步失败都不会伪造内容：拿不到正文就只用摘要，连摘要都没有就如实报告
「未能取得论文正文」，交给写作者以 partial 状态交付。
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from ..agents.base import Blackboard, RunContext
from ..agents.researcher import Researcher
from ..guardrails import verify_claim_consistency
from ..models import Source, SubQuestion
from ..registry import register
from ..tools.base import SearchTool
from .attachments import attachments_from_scratch
from .contract import PaperReference, contract_from_scratch, pasted_paper_text, provided_material
from .roles import PAPER_INTAKE_ROLE

logger = logging.getLogger(__name__)

INTAKE_SOURCES_KEY = "intake_sources"
PAPER_SOURCES_KEY = "paper_sources"


class _FixedSources(SearchTool):
    """把已取回的论文章节伪装成一次检索结果，复用 Researcher 的核验链。"""

    def __init__(self, sources: list[Source]) -> None:
        self._sources = sources

    @property
    def backend_name(self) -> str:
        return "PaperIntake"

    async def search(self, query: str, *, max_results: int = 5) -> list[Source]:
        return list(self._sources[: max(max_results, len(self._sources))])


async def _fetch_arxiv(identifier: str, ctx: RunContext) -> list[Source]:
    from ..tools.arxiv_search import ArxivSearch

    tool = ArxivSearch(fulltext=bool(getattr(ctx.settings, "fulltext_enabled", True)))
    try:
        return await tool.lookup(identifier)
    finally:
        await tool.aclose()


async def _fetch_document(url: str) -> list[Source]:
    """PDF 或网页：复用资料库导入的安全抓取（SSRF 校验、体积上限、重定向校验）。"""
    from ..library.ingestion import prepare_source

    kind = "pdf" if url.casefold().split("?", 1)[0].endswith(".pdf") else "url"
    prepared = await prepare_source(kind=kind, title="", origin_url=url)
    title = prepared.title or url
    metadata = getattr(prepared, "metadata", {})
    authors = metadata.get("authors")
    base = (prepared.origin_url or url).split("#", 1)[0]
    sources: list[Source] = []
    for chunk in prepared.chunks:
        content = str(chunk.get("content", ""))
        if not content.strip():
            continue
        # 每个片段一个独立 URL：逐字核验按 URL 找来源，片段共用 URL 会让引文锚错位置。
        sources.append(
            Source(
                title=title,
                url=f"{base}#chunk-{chunk.get('ordinal', 0)}",
                content=content,
                locator=str(chunk.get("locator") or ""),
                document_authors=authors if isinstance(authors, list) else [],
            )
        )
    return sources


async def fetch_paper(paper: PaperReference, ctx: RunContext) -> list[Source]:
    if paper.kind == "arxiv":
        return await _fetch_arxiv(paper.value, ctx)
    return await _fetch_document(paper.url)


PASTED_URL_PREFIX = "https://workspace.invalid/pasted/"
_PASTED_CHUNK_CHARS = 2500
_MISSING_PAPER = "未提供论文：请粘贴 arXiv / DOI / 论文链接或论文文本，或上传论文文件"


def pasted_sources(text: str) -> list[Source]:
    """把用户粘贴的论文文本切成带定位的来源；每段一个独立 URL，逐字核验才能锚准位置。"""
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    chunks: list[str] = []
    current = ""
    for paragraph in (p.strip() for p in text.splitlines()):
        if not paragraph:
            continue
        if current and len(current) + len(paragraph) > _PASTED_CHUNK_CHARS:
            chunks.append(current)
            current = ""
        current = f"{current}\n{paragraph}" if current else paragraph
    if current:
        chunks.append(current)
    return [
        Source(
            title="粘贴的论文文本",
            url=f"{PASTED_URL_PREFIX}{digest}?chunk={index}",
            content=chunk,
            locator=f"第 {index} 段",
        )
        for index, chunk in enumerate(chunks, 1)
    ]


def _record(
    bb: Blackboard,
    mode: str,
    papers: list[PaperReference],
    sources: list[Source],
    failures: list[dict[str, Any]],
    documents: list[dict[str, Any]] | None = None,
) -> None:
    bb.scratch[INTAKE_SOURCES_KEY] = {
        "mode": mode,
        "papers": [paper.model_dump() for paper in papers],
        "sections": [
            {
                "url": source.url,
                "title": source.title,
                "section": (source.scholarly.section if source.scholarly else "") or source.locator,
                "chars": len(source.content),
            }
            for source in sources
        ],
        "failures": failures,
        "documents": [
            *(documents or []),
            *[
                {
                    "input_url": f"https://workspace.invalid/attachments/{attachment.id}",
                    "title": attachment.filename,
                    "source_urls": [s.url for s in attachment.sources()],
                    "truncated": attachment.truncated,
                }
                for attachment in attachments_from_scratch(bb.scratch)
            ],
        ],
    }


def _question_for(template_key: str, focus: str) -> str:
    base = {
        "peerReview": "这篇论文的研究问题、方法、实验设置、主要结果与作者声称的贡献分别是什么？",
        "paperRead": "这篇论文的核心贡献、方法细节、关键公式、实验结果与局限分别是什么？",
        "litReview": (
            "按研究主题提取指定文献各自的方法、输入与实验条件、主要结果和边界，"
            "保留论文归属，不能混写不同文献的数值或假设。"
        ),
        "slides": "按汇报主题提取问题、方法、关键结果与局限，保留讲解所需的条件和来源归属。",
        "mindmap": "按导图主题提取核心概念、概念间关系、支持事实与待研究问题，保留来源归属。",
    }.get(template_key, "这篇论文的主要内容、方法与结论是什么？")
    return f"{base} 用户关注：{focus}" if focus else base


@register(PAPER_INTAKE_ROLE)
class PaperIntake:
    """取回用户指定的论文并抽取已核验发现，写入 ``bb.results``。"""

    name: str

    async def step(self, bb: Blackboard, ctx: RunContext) -> Blackboard:
        contract = contract_from_scratch(bb.scratch)
        closed_workflows = {
            "lit_review_provided": "litReview",
            "slides_provided": "slides",
            "mindmap_provided": "mindmap",
        }
        template_key = closed_workflows.get(bb.scratch.get("requested_workflow", ""))
        if contract is None and template_key:
            from .contract import CONTRACT_SCRATCH_KEY, build_contract
            from .templates import get_template

            template = get_template(template_key)
            assert template is not None
            contract = build_contract(
                template, bb.query, strategy="none", quality=ctx.settings.quality
            )
            bb.scratch[CONTRACT_SCRATCH_KEY] = contract.model_dump(mode="json")
        if contract is None:
            # 没有任务契约的旧运行无从得知评审对象，沿用主题检索
            bb.scratch["pending_sub_questions"] = [SubQuestion(question=bb.query)]
            return await Researcher().step(bb, ctx)

        papers = contract.papers
        failures: list[dict[str, Any]] = []
        focus = contract.focus or (contract.original_request if provided_material(contract) else "")
        documents: list[dict[str, Any]] = []
        if papers:
            mode = "papers"
            collected: list[Source] = []
            for paper in papers:
                document: dict[str, Any] = {
                    "input_url": paper.url,
                    "title": paper.value,
                    "source_urls": [],
                }
                documents.append(document)
                try:
                    sources = await fetch_paper(paper, ctx)
                except Exception as exc:  # 单篇失败隔离：记录原因，继续下一篇
                    logger.info("paper intake failed for %s: %s", paper.url, exc)
                    failures.append(
                        {"url": paper.url, "error": f"{type(exc).__name__}: {exc}"[:300]}
                    )
                    continue
                if not sources:
                    failures.append({"url": paper.url, "error": "未取得任何可用正文"})
                collected.extend(sources)
                document["source_urls"] = [source.url for source in sources]
        elif attachments := attachments_from_scratch(bb.scratch):
            # 上传的论文已由 attachment_reader 逐片段读过并核验；这里只登记评审对象，
            # 绝不退回开放检索——那会把用户给的论文换成相关度排序里的另一篇。
            _record(bb, "attachments", [], [s for a in attachments for s in a.sources()], [])
            ctx.tracer.emit(
                "RESEARCHER",
                "info",
                f"未提供论文链接，以上传的 {len(attachments)} 个文件为研究对象，不做开放检索",
                data={"category": "paper_intake", "mode": "attachments"},
            )
            return bb
        elif not provided_material(contract) and (pasted := pasted_paper_text(contract)):
            mode = "pasted"
            collected = pasted_sources(pasted)
            focus = ""  # 粘贴的是论文本身，不是用户的关注点
        else:
            _record(bb, "missing", [], [], [{"url": "", "error": _MISSING_PAPER}])
            ctx.tracer.emit(
                "RESEARCHER",
                "error",
                _MISSING_PAPER,
                data={"category": "paper_intake", "mode": "missing"},
            )
            return bb

        _record(bb, mode, papers, collected, failures, documents)
        # 取回的正文随 checkpoint 冻结：精读工作区的后续对话直接复用，不必再次联网取回
        bb.scratch[PAPER_SOURCES_KEY] = [source.model_dump(mode="json") for source in collected]
        ctx.tracer.emit(
            "RESEARCHER",
            "info",
            f"论文导入：取得 {len(collected)} 个章节来源，失败 {len(failures)} 篇",
            data={"category": "paper_intake", "sources": len(collected), "failures": failures},
        )
        if not collected:
            return bb

        researcher = Researcher()
        researcher.llm = ctx.llm_for("researcher")
        researcher.verification_llm = ctx.llm_for("evidence_verifier")
        researcher.search = _FixedSources(collected)
        researcher.tracer = ctx.tracer
        researcher.settings = ctx.settings
        researcher.system = ctx.system_prompt(researcher.system)
        from .attachment_reader import source_batches

        question = _question_for(contract.template, focus)
        for index, batch in enumerate(source_batches(collected, researcher, question), 1):
            researcher.search = _FixedSources(batch)
            result = await researcher.run(question)
            if result is not None:
                bb.results.append(
                    result.model_copy(update={"sub_question": f"{question}（第 {index} 组章节）"})
                )
        await verify_claim_consistency(
            bb.results,
            researcher.consistency_verifier,
            researcher.verification_llm,
            ctx.tracer,
            stage="RESEARCHER",
        )
        return bb


__all__ = [
    "INTAKE_SOURCES_KEY",
    "PAPER_SOURCES_KEY",
    "PASTED_URL_PREFIX",
    "PaperIntake",
    "fetch_paper",
    "pasted_sources",
]
