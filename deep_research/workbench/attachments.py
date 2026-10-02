"""任务附件：用户随任务上传的文件（PDF / Word / PPT / Excel / Markdown / 文本 / CSV）。

流程分两步，都是确定性的：

1. **上传即解析**（``POST /api/attachments``）：复用资料库的同一条解析链（PDF 按章节、
   Office 按标题 / 幻灯片 / 工作表、文本按段落），返回带定位的片段与摘要信息；
   前端展示解析结果，用户确认后随创建任务一起提交。PDF 原文件按内容哈希保存，
   供精读工作区读取原版；其他格式只保留解析结果。
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
from ..upload_limits import DOCUMENT_LIMIT_LABEL, DOCUMENT_MAX_BYTES

ATTACHMENTS_SCRATCH_KEY = "attachments"
MAX_ATTACHMENTS = 8
# Resource bounds apply to complete inputs, never to a silently shortened copy.
MAX_CHUNKS_PER_FILE = 2048
MAX_PARSED_CHARS_PER_FILE = (
    2_000_000  # Includes chunk overlap; import text itself is bounded to 1M.
)
MAX_TOTAL_PARSED_CHARS = 8_000_000
MAX_FILE_BYTES = DOCUMENT_MAX_BYTES

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
    # PDF 片段的起始页（从 1 开始）；精读工作区据此把引用跳到原文对应页
    page: int | None = Field(default=None, ge=1)


class Attachment(BaseModel):
    """一个已解析的上传文件。``id`` 是内容摘要，同一文件重复上传得到同一 id。"""

    id: str = Field(min_length=8, max_length=64)
    filename: str = Field(max_length=300)
    kind: str = Field(max_length=20)
    mime_type: str = Field(default="", max_length=120)
    size: int = Field(ge=0)
    char_count: int = Field(ge=0)
    truncated: bool = False
    # 原文件是否已保存（目前仅 PDF），精读工作区据此决定能否显示原版版面
    stored: bool = False
    title: str = Field(default="", max_length=300)
    authors: list[str] = Field(default_factory=list, max_length=32)
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
            "stored": self.stored,
            "preview": self.preview(),
            "sections": list(dict.fromkeys(c.locator.split(" / ")[0] for c in self.chunks))[:12],
        }

    def sources(self) -> list[Source]:
        return [
            Source(
                title=self.title or self.filename,
                url=attachment_url(self.id, chunk.ordinal),
                content=chunk.content,
                locator=chunk.locator,
                document_authors=self.authors,
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
        raise AttachmentError(f"文件超过 {DOCUMENT_LIMIT_LABEL} 限制")
    if not raw:
        raise AttachmentError("文件为空")
    kind = kind_for(filename, mime_type)
    if kind is None:
        raise AttachmentError(
            "不支持的文件类型；支持 PDF、Word、PowerPoint、Excel、Markdown、TXT、CSV 等"
        )
    try:
        prepared = await prepare_source(
            kind="pdf" if kind == "pdf" else ("markdown" if kind == "markdown" else "text"),
            title=filename,
            raw_bytes=raw,
            mime_type=mime_type,
            filename=filename,
        )
    except SourceImportError as exc:
        raise AttachmentError(str(exc)) from exc
    chunks = [
        AttachmentChunk(
            ordinal=int(str(chunk.get("ordinal", index))),
            locator=str(chunk.get("locator", ""))[:300],
            content=str(chunk.get("content", "")),
            page=_page_of(chunk.get("page_start")),
        )
        for index, chunk in enumerate(prepared.chunks)
        if str(chunk.get("content", "")).strip()
    ]
    if (
        len(chunks) > MAX_CHUNKS_PER_FILE
        or sum(len(c.content) for c in chunks) > MAX_PARSED_CHARS_PER_FILE
    ):
        raise AttachmentError("完整解析结果超过文件处理容量，未截断文件；请拆分材料后重试")
    authors = prepared.metadata.get("authors")
    return Attachment(
        id=hashlib.sha256(raw).hexdigest()[:24],
        filename=filename[:300] or "未命名文件",
        kind=str(prepared.metadata.get("format") or kind),
        mime_type=prepared.mime_type,
        size=len(raw),
        char_count=prepared.char_count,
        title=str(prepared.metadata.get("document_title") or ""),
        authors=authors if isinstance(authors, list) else [],
        chunks=chunks,
    )


def _page_of(page_start: object) -> int | None:
    return page_start + 1 if isinstance(page_start, int) and page_start >= 0 else None


# ---- 原文件保存：精读工作区显示原版 PDF --------------------------------------
# 按内容哈希一文件一个产物目录（``<artifact_root>/attachments/att-<id>/``），
# 复用 ArtifactStore 的原子写入、清单哈希与全局配额；同一文件重复上传只保存一次。

ATTACHMENT_STORE_DIR = "attachments"
_ATTACHMENT_ID_RE = re.compile(r"^[0-9a-f]{24}$")
_ORIGINAL_STAGE = "source"
_ORIGINAL_NAME = "original.pdf"


def _original_store(settings: Any) -> Any:
    from pathlib import Path

    from ..artifacts import ArtifactStore

    return ArtifactStore(
        Path(settings.artifact_root) / ATTACHMENT_STORE_DIR,
        max_bytes=MAX_FILE_BYTES,
        max_total_bytes=settings.artifact_total_bytes,
        quota_root=settings.artifact_root,
    )


def _slug(attachment_id: str) -> str:
    # id 随创建任务的请求体回传，属于客户端可控数据：只接受内容哈希格式，杜绝路径注入
    if not _ATTACHMENT_ID_RE.fullmatch(attachment_id):
        raise AttachmentError("附件标识无效")
    return f"att-{attachment_id}"


def save_original(settings: Any, attachment_id: str, raw: bytes) -> None:
    """保存上传的 PDF 原文件；已保存过同一内容时不重复写入。"""
    if load_original(settings, attachment_id) is not None:
        return
    _original_store(settings).write(
        _slug(attachment_id),
        _ORIGINAL_STAGE,
        _ORIGINAL_NAME,
        raw,
        mime_type="application/pdf",
        max_size=MAX_FILE_BYTES,
    )


def load_original(settings: Any, attachment_id: str) -> bytes | None:
    """读取已保存的 PDF 原文件，并按清单哈希复核；不存在或校验失败时返回 None。"""
    from ..artifacts import ArtifactError

    store = _original_store(settings)
    slug = _slug(attachment_id)
    try:
        manifest = store.load_manifest(slug)
    except (ArtifactError, OSError, ValueError):
        return None
    record = next((item for item in manifest.files if item.name == _ORIGINAL_NAME), None)
    if record is None:
        return None
    try:
        store.verify(
            record.path,
            expected_sha256=record.sha256,
            expected_size=record.size_bytes,
            raise_on_error=True,
        )
        data: bytes = store.read_bytes(record.path)
    except (ArtifactError, OSError, ValueError):
        return None
    # 内容寻址：原文件的摘要必须与 id 对上，防止被替换成别的文件
    if hashlib.sha256(data).hexdigest()[:24] != attachment_id:
        return None
    return data


def limit_attachments(items: list[Attachment]) -> list[Attachment]:
    """Deduplicate complete files; reject excess inputs before creating a task."""
    seen: set[str] = set()
    kept: list[Attachment] = []
    total_chars = 0
    for item in items:
        if item.id in seen:
            continue
        if len(kept) >= MAX_ATTACHMENTS:
            raise AttachmentError(f"最多提交 {MAX_ATTACHMENTS} 个文件，请减少文件数量后重试")
        if item.truncated or not item.chunks:
            raise AttachmentError(f"「{item.filename}」未完整解析，请重新上传；未创建任务")
        chars = sum(len(chunk.content) for chunk in item.chunks)
        if chars > MAX_PARSED_CHARS_PER_FILE:
            raise AttachmentError(f"「{item.filename}」完整文本超过文件处理容量，未创建任务")
        total_chars += chars
        if total_chars > MAX_TOTAL_PARSED_CHARS:
            raise AttachmentError("附件合计文本超过 800 万字符，未创建任务；请减少文件或分批处理")
        seen.add(item.id)
        kept.append(item)
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
    "load_original",
    "save_original",
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
