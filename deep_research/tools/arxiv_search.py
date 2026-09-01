"""arXiv 检索实现：预印本一侧的一手来源。

存在的理由：本项目面向的高光谱计算成像方向，重建算法一侧的工作大量首发于 arXiv，
并且 **arXiv 提供 LaTeX e-print 源码**——论文里的 benchmark 数值表在 LaTeX 里是
``\\begin{tabular}`` 结构化文本，而 PDF 表格抽取是有损的。全文解析阶段会消费这一点，
本阶段先把元数据与 abstract 接进证据链。

**XML 安全**：arXiv 返回 Atom XML，用 stdlib ``xml.etree.ElementTree`` 解析（不引入
``defusedxml``，因为本仓库用带哈希的锁文件，加依赖要重生成两个 lock）。两道防护：

1. **拒绝带 DOCTYPE 的文档**。实体声明只能出现在 DTD 里，掐掉 DOCTYPE 就掐掉了
   实体展开类攻击（billion laughs / 二次爆破）的唯一入口——这比限制展开深度更彻底，
   而正常的 arXiv 响应从不带 DOCTYPE。
2. **响应体积上限**。防的是「超大但合法」的响应把内存吃掉，与 (1) 覆盖的攻击面不同。

expat 默认不解析外部实体，因此 XXE（读本地文件 / 打内网）不在此列。
"""

from __future__ import annotations

import logging
import re
from typing import Any
from xml.etree import ElementTree

import httpx

from ..models import ScholarlyMetadata, Source
from .arxiv_fulltext import ArxivEprintFetcher, ArxivFulltextError
from .base import SearchTool

logger = logging.getLogger(__name__)

_ENDPOINT = "https://export.arxiv.org/api/query"
_MAX_RESULTS = 50
# arXiv 单次响应正常在几十 KB 量级；1 MB 已经是极宽松的上限。
_MAX_BYTES = 1_048_576

_ATOM = "{http://www.w3.org/2005/Atom}"
_ARXIV = "{http://arxiv.org/schemas/atom}"

# 从 abs URL 尾部取版本号：.../abs/2205.10102v2 → v2
_VERSION_RE = re.compile(r"(v\d+)$")
_WHITESPACE_RE = re.compile(r"\s+")


class ArxivFeedError(RuntimeError):
    """响应不是一份可安全解析的 arXiv Atom feed。"""


class ArxivSearch(SearchTool):
    def __init__(
        self,
        *,
        timeout: float = 30.0,
        fulltext: bool = False,
        fulltext_max_chars: int = 12_000,
        eprint_fetcher: ArxivEprintFetcher | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={"Accept": "application/atom+xml"},
        )
        self._fulltext = fulltext
        self._fulltext_max_chars = fulltext_max_chars
        self._eprint_fetcher = eprint_fetcher or (
            ArxivEprintFetcher(timeout=timeout) if fulltext else None
        )

    @property
    def backend_name(self) -> str:
        return "ArxivSearch"

    async def search(self, query: str, *, max_results: int = 5) -> list[Source]:
        if max_results <= 0:
            return []
        requested = min(max_results, _MAX_RESULTS)
        response = await self._client.get(
            _ENDPOINT,
            params={
                # 每个库的检索式翻译（同义词扩展、字段限定）属于「检索策略生成」
                # 阶段的职责；本后端只负责如实转发，不在这里塞领域启发式规则。
                "search_query": f"all:{query}",
                "start": 0,
                "max_results": requested,
                "sortBy": "relevance",
                "sortOrder": "descending",
            },
        )
        response.raise_for_status()
        entries = _parse_feed(response.content)
        sources: list[Source] = []
        for entry in entries:
            source = _to_source(entry)
            if source is not None:
                if self._eprint_fetcher is not None:
                    try:
                        sources.extend(await self._fulltext_sources(source, query))
                    except (ArxivFulltextError, RuntimeError) as exc:
                        # Metadata/abstract remains a valid fallback. Full-text coverage is
                        # observable in the source section field and never claimed on failure.
                        logger.warning("arXiv full-text unavailable for %s: %s", source.url, exc)
                        sources.append(source)
                else:
                    sources.append(source)
            if len(sources) >= requested:
                break
        return sources[:requested]

    async def aclose(self) -> None:
        await self._client.aclose()
        if self._eprint_fetcher is not None:
            await self._eprint_fetcher.aclose()

    async def _fulltext_sources(self, source: Source, query: str) -> list[Source]:
        if source.scholarly is None or not source.scholarly.work_id:
            return [source]
        required: set[str] = set()
        query_lower = query.casefold()
        if any(
            token in query_lower
            for token in ("psnr", "ssim", "benchmark", "accuracy", "数值", "指标", "结果")
        ):
            required.add("results")
        elif any(
            token in query_lower for token in ("method", "approach", "protocol", "方法", "实验")
        ):
            required.update(("method", "experiment"))
        # The selector receives only canonical section names; it ignores a required name
        # that is absent. The fetcher also keeps an abstract section when one exists.
        fetcher = self._eprint_fetcher
        if fetcher is None:
            return [source]
        sections = await fetcher.sections(
            source,
            query,
            max_chars=self._fulltext_max_chars,
            required=required,
        )
        return sections


def _parse_feed(raw: bytes) -> list[ElementTree.Element]:
    if len(raw) > _MAX_BYTES:
        raise ArxivFeedError(f"arXiv 响应超过 {_MAX_BYTES} 字节上限，已拒绝解析")
    if b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
        raise ArxivFeedError("arXiv 响应包含 DOCTYPE/ENTITY 声明，已拒绝解析")
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError as exc:
        raise ArxivFeedError(f"arXiv 响应不是合法 XML：{exc}") from exc
    return list(root.iter(f"{_ATOM}entry"))


def _to_source(entry: ElementTree.Element) -> Source | None:
    raw_id = _text(entry.find(f"{_ATOM}id"))
    if not raw_id:
        return None
    # arXiv 的 id 用 http；统一成 https 后再进来源策略与去重，避免同一条目
    # 因协议不同被当成两个来源。
    url = _https(raw_id)
    journal_ref = _text(entry.find(f"{_ARXIV}journal_ref"))
    meta = ScholarlyMetadata(
        doi=_text(entry.find(f"{_ARXIV}doi")),
        work_id=_arxiv_work_id(raw_id),
        authors=_authors(entry),
        # arXiv 不提供结构化机构信息；留空而不是从作者串里猜。
        venue=journal_ref[:200] or "arXiv",
        year=_year(entry),
        version=_version(raw_id),
        # 取回的这份文档本身就是预印本，与「是否另有已发表版本」是两件事：
        # journal_ref 存在只说明存在期刊版，不代表这份 arXiv 记录经过评审。
        peer_reviewed=False,
        oa_pdf_url=_pdf_url(entry),
    )
    return Source(
        title=_collapse(_text(entry.find(f"{_ATOM}title")))[:200],
        url=url,
        content=_collapse(_text(entry.find(f"{_ATOM}summary"))),
        scholarly=meta,
    )


def _authors(entry: ElementTree.Element) -> list[str]:
    names: list[str] = []
    for author in entry.findall(f"{_ATOM}author"):
        name = _collapse(_text(author.find(f"{_ATOM}name")))
        if name:
            names.append(name)
    return names[:32]


def _pdf_url(entry: ElementTree.Element) -> str:
    for link in entry.findall(f"{_ATOM}link"):
        if link.get("title") == "pdf" or link.get("type") == "application/pdf":
            href = (link.get("href") or "").strip()
            if href:
                return _https(href)
    return ""


def _year(entry: ElementTree.Element) -> int | None:
    """取首次投稿年份（published），而不是 updated——版本更新不该改变发表年份。"""
    published = _text(entry.find(f"{_ATOM}published"))
    if len(published) >= 4 and published[:4].isdigit():
        return int(published[:4])
    return None


def _version(raw_id: str) -> str:
    match = _VERSION_RE.search(raw_id.rstrip("/"))
    return match.group(1) if match else ""


def _arxiv_work_id(raw_id: str) -> str:
    """去掉版本号的稳定标识：``arxiv:2205.10102``。

    刻意剥掉 ``v2``，因为同一篇论文的 v1 与 v2 是同一份工作。跨来源判独立性时
    必须把它们算成一个，否则「同一篇的两个版本」会被当成两个独立发布方。
    """
    tail = raw_id.rstrip("/").rsplit("/abs/", 1)[-1]
    return f"arxiv:{_VERSION_RE.sub('', tail)}" if tail else ""


def _https(url: str) -> str:
    return f"https://{url[len('http://') :]}" if url.casefold().startswith("http://") else url


def _collapse(value: str) -> str:
    """折叠 arXiv 为排版插入的换行与多余空格。

    这一步必须在进 ``Source.content`` 之前完成，因为逐字证据校验匹配的对象就是
    ``Source.content``：模型看到的、被哈希留证的、被校验的必须是同一份文本。
    """
    return _WHITESPACE_RE.sub(" ", value).strip()


def _text(node: Any) -> str:
    if node is None:
        return ""
    return (node.text or "").strip()
