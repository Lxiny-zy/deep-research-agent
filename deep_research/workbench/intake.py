"""论文导入：把用户点名的论文变成可逐字核验的来源，并抽取带出处的发现。

同行评审与论文精读的证据对象是**一篇指定论文**，不是开放检索的结果。
开放检索按相关性排序，很可能把用户给的那篇换成相近的另一篇——评审错论文
比没有评审更糟。因此这个角色：

1. 从任务契约里取出 arXiv / DOI / URL 指针；
2. arXiv 走 ``id_list`` 精确取回元数据与 LaTeX 全文章节；PDF 链接走 OA PDF 解析；
   普通网页走资料库导入的同一条安全抓取路径；
3. 把来源交给 Researcher 的同一套「来源门禁 → 抽取 → 逐字核验 → 语义核验」链。

任何一步失败都不会伪造内容：拿不到正文就只用摘要，连摘要都没有就如实报告
「未能取得论文正文」，交给写作者以 partial 状态交付。
"""

from __future__ import annotations

import logging
from typing import Any

from ..agents.base import Blackboard, RunContext
from ..agents.researcher import Researcher
from ..guardrails import verify_claim_consistency
from ..models import ResearchResult, Source, SubQuestion
from ..registry import register
from ..tools.base import SearchTool
from .contract import PaperReference, contract_from_scratch
from .roles import PAPER_INTAKE_ROLE

logger = logging.getLogger(__name__)

INTAKE_SOURCES_KEY = "intake_sources"
_MAX_SOURCES = 12


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
    base = prepared.origin_url or url
    sources: list[Source] = []
    for chunk in prepared.chunks[:_MAX_SOURCES]:
        content = str(chunk.get("content", ""))
        if not content.strip():
            continue
        # 每个片段一个独立 URL：逐字核验按 URL 找来源，片段共用 URL 会让引文锚错位置。
        sources.append(
            Source(title=title, url=f"{base}#chunk-{chunk.get('ordinal', 0)}", content=content)
        )
    return sources


async def fetch_paper(paper: PaperReference, ctx: RunContext) -> list[Source]:
    if paper.kind == "arxiv":
        return await _fetch_arxiv(paper.value, ctx)
    return await _fetch_document(paper.url)


def _question_for(template_key: str, focus: str) -> str:
    base = {
        "peerReview": "这篇论文的研究问题、方法、实验设置、主要结果与作者声称的贡献分别是什么？",
        "paperRead": "这篇论文的核心贡献、方法细节、关键公式、实验结果与局限分别是什么？",
    }.get(template_key, "这篇论文的主要内容、方法与结论是什么？")
    return f"{base} 用户关注：{focus}" if focus else base


@register(PAPER_INTAKE_ROLE)
class PaperIntake:
    """取回用户指定的论文并抽取已核验发现，写入 ``bb.results``。"""

    name: str

    async def step(self, bb: Blackboard, ctx: RunContext) -> Blackboard:
        contract = contract_from_scratch(bb.scratch)
        papers = contract.papers if contract is not None else []
        if contract is None or not papers:
            ctx.tracer.emit(
                "RESEARCHER",
                "info",
                "输入中没有可解析的论文链接，改为按主题检索",
                data={"category": "paper_intake", "papers": 0},
            )
            bb.scratch["pending_sub_questions"] = [SubQuestion(question=bb.query)]
            return await Researcher().step(bb, ctx)

        collected: list[Source] = []
        failures: list[dict[str, Any]] = []
        for paper in papers:
            try:
                sources = await fetch_paper(paper, ctx)
            except Exception as exc:  # 单篇失败隔离：记录原因，继续下一篇
                logger.info("paper intake failed for %s: %s", paper.url, exc)
                failures.append({"url": paper.url, "error": f"{type(exc).__name__}: {exc}"[:300]})
                continue
            if not sources:
                failures.append({"url": paper.url, "error": "未取得任何可用正文"})
            collected.extend(sources)
        collected = collected[:_MAX_SOURCES]
        bb.scratch[INTAKE_SOURCES_KEY] = {
            "papers": [paper.model_dump() for paper in papers],
            "sections": [
                {
                    "url": source.url,
                    "title": source.title,
                    "section": source.scholarly.section if source.scholarly else "",
                    "chars": len(source.content),
                }
                for source in collected
            ],
            "failures": failures,
        }
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
        # 每个章节单独一次抽取：整篇塞进一次调用会超出上下文，也会让模型只盯住开头。
        question = _question_for(contract.template, contract.focus)
        for index in range(0, len(collected), 3):
            batch = collected[index : index + 3]
            researcher.search = _FixedSources(batch)
            result = await researcher.run(question)
            if result is not None:
                bb.results.append(
                    ResearchResult(
                        sub_question=f"{question}（第 {index // 3 + 1} 组章节）",
                        findings=result.findings,
                    )
                )
        await verify_claim_consistency(
            bb.results,
            researcher.consistency_verifier,
            researcher.verification_llm,
            ctx.tracer,
            stage="RESEARCHER",
        )
        return bb


__all__ = ["INTAKE_SOURCES_KEY", "PaperIntake", "fetch_paper"]
