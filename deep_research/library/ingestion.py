"""Bounded document fetching, extraction and locator-aware chunking."""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from ..blocking import run_blocking
from ..security import ProviderURLPolicyError, provider_http_client, validate_provider_url
from ..tools.oa_pdf_fulltext import OaPdfLimits, OaPdfParseError, parse_oa_pdf
from .office import MIME_BY_KIND, OfficeParseError, parse_office, sniff_office

MAX_SOURCE_BYTES = 16 * 1024 * 1024
MAX_SOURCE_CHARS = 1_000_000
CHUNK_CHARS = 3_600
CHUNK_OVERLAP = 320
MAX_REDIRECTS = 4
DOCUMENT_ACCEPT = "text/html,text/plain,text/markdown,application/pdf;q=0.9,*/*;q=0.1"


class SourceImportError(ValueError):
    pass


@dataclass(frozen=True)
class PreparedSource:
    title: str
    kind: str
    origin_url: str
    mime_type: str
    content_hash: str
    char_count: int
    metadata: dict[str, object]
    chunks: list[dict[str, object]]


class _ReadableHtml(HTMLParser):
    _SKIP = {"script", "style", "noscript", "svg", "canvas", "template"}
    _BLOCK = {
        "article",
        "aside",
        "blockquote",
        "br",
        "dd",
        "div",
        "dl",
        "dt",
        "figcaption",
        "figure",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "td",
        "th",
        "tr",
        "ul",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized = tag.casefold()
        if normalized in self._SKIP:
            self._skip_depth += 1
        if normalized == "title":
            self._in_title = True
        if normalized in self._BLOCK and not self._skip_depth:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.casefold()
        if normalized in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
        if normalized == "title":
            self._in_title = False
        if normalized in self._BLOCK and not self._skip_depth:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self.title_parts.append(data)
        self.parts.append(data)

    def text(self) -> str:
        lines = [re.sub(r"\s+", " ", line).strip() for line in "".join(self.parts).splitlines()]
        return "\n\n".join(line for line in lines if line)

    def title(self) -> str:
        return re.sub(r"\s+", " ", " ".join(self.title_parts)).strip()


def _validate_document_url(url: str) -> str:
    value = url.strip()
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise SourceImportError("来源 URL 格式无效") from exc
    if parsed.scheme != "https" or not parsed.hostname:
        raise SourceImportError("来源 URL 必须使用 HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise SourceImportError("来源 URL 不允许包含账号信息")
    if parsed.fragment:
        parsed = parsed._replace(fragment="")
        value = urlunsplit(parsed)
    # Provider validation plus the pinned network backend reject loopback,
    # private and rebinding targets. Queries remain allowed for document URLs.
    base = urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", "", ""))
    try:
        validate_provider_url(base)
    except ProviderURLPolicyError as exc:
        raise SourceImportError(str(exc).replace("provider endpoint", "来源 URL")) from exc
    return value


def normalize_doi(value: str) -> str:
    doi = value.strip()
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi, flags=re.I)
    doi = re.sub(r"^doi:\s*", "", doi, flags=re.I)
    if not re.fullmatch(r"10\.\d{4,9}/\S+", doi, flags=re.I):
        raise SourceImportError("DOI 格式无效")
    return doi.rstrip(".,; ")


async def _fetch(url: str) -> tuple[bytes, str, str]:
    current = _validate_document_url(url)
    async with provider_http_client(timeout=30.0) as client:
        for _ in range(MAX_REDIRECTS + 1):
            try:
                async with client.stream(
                    "GET",
                    current,
                    headers={"Accept": DOCUMENT_ACCEPT},
                ) as response:
                    if response.is_redirect:
                        location = response.headers.get("location", "")
                        if not location:
                            raise SourceImportError("来源重定向缺少目标地址")
                        current = _validate_document_url(urljoin(current, location))
                        continue
                    response.raise_for_status()
                    declared = response.headers.get("content-length", "")
                    if declared.isdigit() and int(declared) > MAX_SOURCE_BYTES:
                        raise SourceImportError("来源文件超过 16 MB 限制")
                    pieces: list[bytes] = []
                    total = 0
                    async for piece in response.aiter_bytes():
                        total += len(piece)
                        if total > MAX_SOURCE_BYTES:
                            raise SourceImportError("来源文件超过 16 MB 限制")
                        pieces.append(piece)
                    mime = (
                        response.headers.get("content-type", "application/octet-stream")
                        .split(";", 1)[0]
                        .strip()
                        .lower()
                    )
                    return b"".join(pieces), mime, current
            except SourceImportError:
                raise
            except (httpx.HTTPError, OSError) as exc:
                raise SourceImportError(f"无法读取来源：{type(exc).__name__}") from exc
    raise SourceImportError("来源重定向次数过多")


def _decode_text(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise SourceImportError("文档不是可识别的 UTF-8 或 GB18030 文本")


def _chunks_for_text(
    text: str,
    *,
    section: str = "",
    page_start: int | None = None,
    page_end: int | None = None,
    ordinal_start: int = 0,
) -> list[dict[str, object]]:
    cleaned = text.replace("\x00", "").strip()
    if not cleaned:
        return []
    chunks: list[dict[str, object]] = []
    start = 0
    ordinal = ordinal_start
    while start < len(cleaned):
        hard_end = min(len(cleaned), start + CHUNK_CHARS)
        end = hard_end
        if hard_end < len(cleaned):
            candidates = [
                cleaned.rfind("\n\n", start + CHUNK_CHARS // 2, hard_end),
                cleaned.rfind("。", start + CHUNK_CHARS // 2, hard_end),
                cleaned.rfind(". ", start + CHUNK_CHARS // 2, hard_end),
            ]
            boundary = max(candidates)
            if boundary > start:
                end = boundary + (1 if cleaned[boundary] != "." else 2)
        content = cleaned[start:end].strip()
        if content:
            locator_parts = []
            if section:
                locator_parts.append(section)
            if page_start is not None:
                page_label = f"第 {page_start + 1} 页"
                if page_end is not None and page_end != page_start:
                    page_label = f"第 {page_start + 1}-{page_end + 1} 页"
                locator_parts.append(page_label)
            locator_parts.append(f"片段 {ordinal + 1}")
            chunks.append(
                {
                    "ordinal": ordinal,
                    "content": content,
                    "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    "locator": " / ".join(locator_parts),
                    "start_char": start,
                    "end_char": end,
                    "page_start": page_start,
                    "page_end": page_end,
                    "section": section,
                }
            )
            ordinal += 1
        if end >= len(cleaned):
            break
        start = max(start + 1, end - CHUNK_OVERLAP)
    return chunks


def _decode_base64(value: str) -> bytes:
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SourceImportError("文件内容不是有效的 Base64") from exc
    if len(raw) > MAX_SOURCE_BYTES:
        raise SourceImportError("来源文件超过 16 MB 限制")
    return raw


async def prepare_source(
    *,
    kind: str,
    title: str,
    text: str = "",
    data_base64: str = "",
    origin_url: str = "",
    mime_type: str = "",
    filename: str = "",
) -> PreparedSource:
    resolved_url = origin_url.strip()
    resolved_mime = mime_type.strip().lower()
    raw: bytes
    if kind == "doi":
        doi = normalize_doi(origin_url)
        resolved_url = f"https://doi.org/{doi}"
    if text.strip():
        raw = text.encode("utf-8")
        resolved_mime = resolved_mime or ("text/markdown" if kind == "markdown" else "text/plain")
    elif data_base64:
        raw = _decode_base64(data_base64)
    elif kind in {"url", "doi", "pdf"} and resolved_url:
        raw, fetched_mime, resolved_url = await _fetch(resolved_url)
        resolved_mime = resolved_mime or fetched_mime
    else:
        raise SourceImportError("请提供文档内容或可访问的来源地址")

    is_pdf = kind == "pdf" or resolved_mime == "application/pdf" or raw.startswith(b"%PDF")
    office_kind = None if is_pdf else sniff_office(raw, resolved_mime, filename)
    metadata: dict[str, object] = {}
    resolved_title = title.strip()
    chunks: list[dict[str, object]]
    if office_kind is not None:
        try:
            office_sections = await run_blocking(parse_office, raw, office_kind)
        except OfficeParseError as exc:
            raise SourceImportError(str(exc)) from exc
        full_text = "\n\n".join(part.text for part in office_sections)
        if len(full_text) > MAX_SOURCE_CHARS:
            raise SourceImportError("提取后的文档超过 100 万字符限制")
        chunks = []
        for part in office_sections:
            chunks.extend(
                _chunks_for_text(part.text, section=part.title, ordinal_start=len(chunks))
            )
        metadata = {"format": office_kind, "sections": len(office_sections)}
        resolved_mime = MIME_BY_KIND[office_kind]
    elif is_pdf:
        try:
            document = await run_blocking(
                parse_oa_pdf,
                raw,
                OaPdfLimits(max_input_bytes=MAX_SOURCE_BYTES, max_total_chars=MAX_SOURCE_CHARS),
            )
        except OaPdfParseError as exc:
            raise SourceImportError(str(exc)) from exc
        full_text = document.text
        chunks = []
        for section in document.sections:
            chunks.extend(
                _chunks_for_text(
                    section.render(),
                    section=section.title,
                    page_start=section.page_start,
                    page_end=section.page_end,
                    ordinal_start=len(chunks),
                )
            )
        metadata = {"page_count": document.page_count}
        resolved_mime = "application/pdf"
    else:
        decoded = _decode_text(raw)
        if (
            resolved_mime in {"text/html", "application/xhtml+xml"}
            or "<html" in decoded[:1000].casefold()
        ):
            parser = _ReadableHtml()
            parser.feed(decoded)
            full_text = parser.text()
            resolved_title = resolved_title or parser.title()
            resolved_mime = "text/html"
        else:
            full_text = decoded
            resolved_mime = resolved_mime or "text/plain"
        if len(full_text) > MAX_SOURCE_CHARS:
            raise SourceImportError("提取后的文档超过 100 万字符限制")
        chunks = _chunks_for_text(full_text)

    if not full_text.strip() or not chunks:
        raise SourceImportError("来源中没有可提取的正文")
    resolved_title = (
        resolved_title
        or (urlsplit(resolved_url).hostname if resolved_url else "未命名来源")
        or "未命名来源"
    )
    return PreparedSource(
        title=resolved_title[:300],
        kind=kind,
        origin_url=resolved_url,
        mime_type=resolved_mime,
        content_hash=hashlib.sha256(full_text.encode("utf-8")).hexdigest(),
        char_count=len(full_text),
        metadata=metadata,
        chunks=chunks,
    )
