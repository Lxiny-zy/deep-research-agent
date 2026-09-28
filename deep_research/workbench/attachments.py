"""任务附件：用户随任务上传的文件（PDF / Word / PPT / Excel / Markdown / 文本 / CSV）。

流程分两步，都是确定性的：

1. **上传即解析**（``POST /api/attachments``）：复用资料库的同一条解析链（PDF 按章节、
   Office 按标题 / 幻灯片 / 工作表、文本按段落），返回带定位的片段与摘要信息；
   前端展示解析结果，用户确认后随创建任务一起提交。原始文件不落盘。
2. **任务内阅读**：片段冻结进任务契约所在的 checkpoint（``scratch.attachments``），
   ``AttachmentReader`` 角色把它们交给 Researcher 的「抽取 → 逐字核验 → 语义核验」链，
   和论文、检索来源走同一套证据纪律——模型读到的每句话都能指回文件里的具体位置。

每个附件片段一个独立 URL（``https://workspace.invalid/attachments/<id>?chunk=<n>``，与资料库
非网页来源同一约定，保留域名 ``.invalid`` 永不可解析），逐字核验按 URL 定位来源，
片段共用 URL 会让引文锚错位置。
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from pydantic import BaseModel, Field

from ..models import Source

ATTACHMENTS_SCRATCH_KEY = "attachments"
MAX_ATTACHMENTS = 8
MAX_CHUNKS_PER_FILE = 40
MAX_TOTAL_CHUNKS = 120
MAX_FILE_BYTES = 16 * 1024 * 1024

SUPPORTED_EXTENSIONS = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".pptx": "pptx",
    ".xlsx": "xlsx",
    ".md": "markdown",
    ".markdown": "markdown",
    ".txt": "text",
    ".csv": "text",
    ".tsv": "text",
    ".json": "text",
    ".tex": "text",
    ".html": "text",
    ".htm": "text",
}


class AttachmentChunk(BaseModel):
    ordinal: int
    locator: str = Field(default="", max_length=300)
    content: str = Field(max_length=8000)


class Attachment(BaseModel):
    """一个已解析的上传文件。``id`` 是内容摘要，同一文件重复上传得到同一 id。"""

    id: str = Field(min_length=8, max_length=64)
    filename: str = Field(max_length=300)
    kind: str = Field(max_length=20)
    mime_type: str = Field(default="", max_length=120)
    size: int = Field(ge=0)
    char_count: int = Field(ge=0)
    truncated: bool = False
    chunks: list[AttachmentChunk] = Field(default_factory=list, max_length=MAX_CHUNKS_PER_FILE)

    def preview(self, limit: int = 240) -> str:
        text = self.chunks[0].content if self.chunks else ""
        flat = re.sub(r"\s+", " ", text).strip()
        return flat if len(flat) <= limit else flat[:limit] + "…"

    def summary(self) -> dict[str, Any]:
        """前端展示用：不含全部正文。"""
        return {
            "id": self.id,
            "filename": self.filename,
            "kind": self.kind,
            "mime_type": self.mime_type,
            "size": self.size,
            "char_count": self.char_count,
            "chunk_count": len(self.chunks),
            "truncated": self.truncated,
            "preview": self.preview(),
            "sections": list(dict.fromkeys(c.locator.split(" / ")[0] for c in self.chunks))[:12],
        }

    def sources(self) -> list[Source]:
        return [
            Source(
                title=self.filename,
                url=attachment_url(self.id, chunk.ordinal),
                content=chunk.content,
                locator=chunk.locator,
            )
            for chunk in self.chunks
        ]


ATTACHMENT_URL_PREFIX = "https://workspace.invalid/attachments/"


def attachment_url(attachment_id: str, ordinal: int) -> str:
    return f"{ATTACHMENT_URL_PREFIX}{attachment_id}?chunk={ordinal + 1}"


def kind_for(filename: str, mime_type: str = "") -> str | None:
    lowered = filename.casefold()
    for extension, kind in SUPPORTED_EXTENSIONS.items():
        if lowered.endswith(extension):
            return kind
    if mime_type == "application/pdf":
        return "pdf"
    if mime_type.startswith("text/"):
        return "text"
    return None


class AttachmentError(ValueError):
    pass


async def parse_attachment(raw: bytes, filename: str, mime_type: str = "") -> Attachment:
    """解析一个上传文件为带定位的片段；不支持或无法解析时抛 ``AttachmentError``。"""
    from ..library.ingestion import SourceImportError, prepare_source

    if len(raw) > MAX_FILE_BYTES:
        raise AttachmentError("文件超过 16 MB 限制")
    if not raw:
        raise AttachmentError("文件为空")
    kind = kind_for(filename, mime_type)
    if kind is None:
        raise AttachmentError(
            "不支持的文件类型；支持 PDF、Word、PowerPoint、Excel、Markdown、TXT、CSV 等"
        )
    import base64

    try:
        prepared = await prepare_source(
            kind="pdf" if kind == "pdf" else ("markdown" if kind == "markdown" else "text"),
            title=filename,
            data_base64=base64.b64encode(raw).decode("ascii"),
            mime_type=mime_type,
            filename=filename,
        )
    except SourceImportError as exc:
        raise AttachmentError(str(exc)) from exc
    chunks = [
        AttachmentChunk(
            ordinal=int(str(chunk.get("ordinal", index))),
            locator=str(chunk.get("locator", ""))[:300],
            content=str(chunk.get("content", ""))[:8000],
        )
        for index, chunk in enumerate(prepared.chunks)
        if str(chunk.get("content", "")).strip()
    ]
    return Attachment(
        id=hashlib.sha256(raw).hexdigest()[:24],
        filename=filename[:300] or "未命名文件",
        kind=str(prepared.metadata.get("format") or kind),
        mime_type=prepared.mime_type,
        size=len(raw),
        char_count=prepared.char_count,
        truncated=len(chunks) > MAX_CHUNKS_PER_FILE,
        chunks=chunks[:MAX_CHUNKS_PER_FILE],
    )


def limit_attachments(items: list[Attachment]) -> list[Attachment]:
    """按总量上限截断（去重后保序），保证 checkpoint 与模型上下文都有界。"""
    seen: set[str] = set()
    kept: list[Attachment] = []
    budget = MAX_TOTAL_CHUNKS
    for item in items:
        if item.id in seen or budget <= 0 or len(kept) >= MAX_ATTACHMENTS:
            continue
        seen.add(item.id)
        chunks = item.chunks[:budget]
        budget -= len(chunks)
        kept.append(
            item.model_copy(
                update={
                    "chunks": chunks,
                    "truncated": item.truncated or len(chunks) < len(item.chunks),
                }
            )
        )
    return kept


def attachments_from_scratch(scratch: dict[str, Any]) -> list[Attachment]:
    raw = scratch.get(ATTACHMENTS_SCRATCH_KEY)
    if not isinstance(raw, list):
        return []
    result: list[Attachment] = []
    for item in raw:
        try:
            result.append(Attachment.model_validate(item))
        except ValueError:
            continue
    return result


__all__ = [
    "ATTACHMENTS_SCRATCH_KEY",
    "ATTACHMENT_URL_PREFIX",
    "attachment_url",
    "Attachment",
    "AttachmentChunk",
    "AttachmentError",
    "MAX_ATTACHMENTS",
    "attachments_from_scratch",
    "kind_for",
    "limit_attachments",
    "parse_attachment",
]
