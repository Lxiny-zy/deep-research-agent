"""论文精读工作区：列出一次任务的论文文档、提供原版 PDF，并为对话提供论文片段。

精读页右侧显示原版 PDF，左侧对话只基于这篇论文（可选叠加资料库与联网检索）作答。
这里只做三件确定性的事：

1. ``reader_documents``：从任务 checkpoint 里列出研究对象——上传的文件、论文链接、
   粘贴的文本——以及每份能否显示原版 PDF；
2. ``load_pdf``：读取原版 PDF。上传的 PDF 按内容哈希读取已保存的原文件；arXiv 与直链
   PDF 经资料库同一条 SSRF 校验路径代取，缓存到该任务的产物目录，下次直接读缓存；
3. ``paper_sources``：把任务里已冻结的论文片段还原为来源，交给对话的逐字核验链。

所有入口只接受 checkpoint 里登记过的文档 id，不接受任意路径或 URL。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..artifacts import ArtifactError
from ..models import Source
from ..persistence.repository import RunDetail
from .attachments import attachments_from_scratch, load_original
from .contract import PaperReference, contract_from_scratch, pasted_paper_text
from .intake import PAPER_SOURCES_KEY, pasted_sources

READER_CACHE_DIR = "reader"
_DOC_ID_RE = re.compile(r"^(?:att-[0-9a-f]{24}|paper-\d{1,2}|pasted)$")
_PDF_MAGIC = b"%PDF-"


class ReaderError(ValueError):
    """文档不存在、不属于该任务，或原文件无法取得。"""


def _scratch(detail: RunDetail) -> dict[str, Any]:
    execution = detail.orchestration
    raw = execution.checkpoint.get("scratch", {}) if execution else {}
    return raw if isinstance(raw, dict) else {}


def paper_pdf_url(paper: PaperReference) -> str | None:
    """论文链接对应的原版 PDF 地址；无法确定时返回 None（例如 DOI 落地页）。"""
    if paper.kind == "arxiv":
        return f"https://arxiv.org/pdf/{paper.value}"
    if paper.kind == "url" and paper.url.casefold().split("?", 1)[0].endswith(".pdf"):
        return paper.url
    return None


def reader_documents(detail: RunDetail) -> list[dict[str, Any]]:
    scratch = _scratch(detail)
    documents: list[dict[str, Any]] = []
    for attachment in attachments_from_scratch(scratch):
        documents.append(
            {
                "id": f"att-{attachment.id}",
                "kind": "attachment",
                "title": attachment.filename,
                "pdf": attachment.kind == "pdf" and attachment.stored,
                "note": ""
                if attachment.stored or attachment.kind != "pdf"
                else "原文件未保存，可重新上传后查看原版",
            }
        )
    contract = contract_from_scratch(scratch)
    if contract is not None:
        for index, paper in enumerate(contract.papers):
            pdf_url = paper_pdf_url(paper)
            documents.append(
                {
                    "id": f"paper-{index}",
                    "kind": "paper",
                    "title": paper.value,
                    "url": paper.url,
                    "pdf": pdf_url is not None,
                    "note": "" if pdf_url else "该链接没有可直接显示的 PDF，可在原网站查看",
                }
            )
        if not contract.papers and not documents and pasted_paper_text(contract):
            documents.append(
                {
                    "id": "pasted",
                    "kind": "pasted",
                    "title": "粘贴的论文文本",
                    "pdf": False,
                    "note": "粘贴的文本没有原版版面",
                }
            )
    return documents


def paper_sources(detail: RunDetail) -> list[Source]:
    """任务里已冻结的全部论文片段：上传文件 + 论文链接取回的正文 + 粘贴文本。"""
    scratch = _scratch(detail)
    sources = [source for item in attachments_from_scratch(scratch) for source in item.sources()]
    raw = scratch.get(PAPER_SOURCES_KEY)
    if isinstance(raw, list):
        for item in raw:
            try:
                sources.append(Source.model_validate(item))
            except ValueError:
                continue
    if not sources:
        contract = contract_from_scratch(scratch)
        pasted = pasted_paper_text(contract) if contract is not None else ""
        if pasted:
            sources = pasted_sources(pasted)
    return sources


def rank_paper_sources(query: str, sources: list[Source], limit: int = 6) -> list[Source]:
    """挑出与问题最相关的论文片段（按原文顺序返回），控制每轮抽取的调用成本。

    词项重合度排序，与资料库检索同一套中英文切词；泛问题（「总结一下」）没有
    命中词时取论文开头，那里通常是摘要与引言。
    """
    from collections import Counter

    from ..library.search import _lexemes, _terms

    terms = set(_terms(query))
    scored: list[tuple[int, int, Source]] = []
    for index, source in enumerate(sources):
        counts = Counter(_lexemes(f"{source.locator} {source.content}"))
        score = sum(min(counts[term], 3) for term in terms)
        if score > 0:
            scored.append((score, index, source))
    if not scored:
        return sources[:limit]
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [source for _, _, source in sorted(scored[:limit], key=lambda item: item[1])]


def _cache_store(settings: Any) -> Any:
    from ..artifacts import ArtifactStore

    return ArtifactStore(
        Path(settings.artifact_root) / READER_CACHE_DIR,
        max_bytes=None,
        max_total_bytes=settings.artifact_total_bytes,
        quota_root=settings.artifact_root,
    )


def _cached(settings: Any, slug: str) -> bytes | None:
    store = _cache_store(settings)
    try:
        manifest = store.load_manifest(slug)
        record = next((item for item in manifest.files if item.name == "paper.pdf"), None)
        if record is None:
            return None
        store.verify(
            record.path,
            expected_sha256=record.sha256,
            expected_size=record.size_bytes,
            raise_on_error=True,
        )
        data: bytes = store.read_bytes(record.path)
        return data
    except (ArtifactError, OSError, ValueError):
        return None


async def load_pdf(detail: RunDetail, document_id: str, settings: Any) -> bytes:
    """读取任务里某份文档的原版 PDF；不属于该任务或取不到时抛 ``ReaderError``。"""
    from ..blocking import run_blocking

    if not _DOC_ID_RE.fullmatch(document_id):
        raise ReaderError("文档标识无效")
    documents = {item["id"]: item for item in reader_documents(detail)}
    document = documents.get(document_id)
    if document is None:
        raise ReaderError("该任务没有这份文档")
    if not document["pdf"]:
        raise ReaderError(document.get("note") or "这份文档没有原版 PDF")

    if document["kind"] == "attachment":
        data = await run_blocking(load_original, settings, document_id.removeprefix("att-"))
        if data is None:
            raise ReaderError("原文件未保存，可重新上传后查看原版")
        return data

    contract = contract_from_scratch(_scratch(detail))
    assert contract is not None  # reader_documents 只在有契约时列出论文链接
    paper = contract.papers[int(document_id.removeprefix("paper-"))]
    url = paper_pdf_url(paper)
    assert url is not None
    slug = f"run-{detail.id}-{document_id}"
    cached = await run_blocking(_cached, settings, slug)
    if cached is not None:
        return cached
    from ..library.ingestion import SourceImportError
    from ..library.ingestion import _fetch as fetch_document

    try:
        data, _mime, _final = await fetch_document(url)
    except SourceImportError as exc:
        raise ReaderError(f"无法取得论文 PDF：{exc}") from exc
    if not data.startswith(_PDF_MAGIC):
        raise ReaderError("论文链接返回的不是 PDF 文件")

    def _save() -> None:
        _cache_store(settings).write(slug, "source", "paper.pdf", data, mime_type="application/pdf")

    try:
        await run_blocking(_save)
    except (ArtifactError, OSError, ValueError):
        pass  # 缓存失败只影响下次加载速度，这次仍然返回文件
    return data


__all__ = [
    "ReaderError",
    "load_pdf",
    "paper_pdf_url",
    "paper_sources",
    "rank_paper_sources",
    "reader_documents",
]
