"""Bounded extraction of section-scoped text from open-access PDF files.

PyMuPDF is intentionally imported lazily.  Scholarly metadata and abstract
search must continue to work in installations that do not install the
optional PDF extra.  The parser is conservative: malformed, oversized, or
non-PDF responses raise a typed error so callers can keep the original
metadata source instead of presenting a partial document as full text.
"""

from __future__ import annotations

import importlib
import ipaddress
import re
from collections.abc import Iterable
from dataclasses import dataclass
from types import ModuleType
from urllib.parse import urlsplit

import httpx

from ..models import Source
from ..security import provider_http_client


class OaPdfFulltextError(ValueError):
    """Base error raised for unsafe or unusable OA PDF input."""


class OaPdfParseError(OaPdfFulltextError):
    """The PDF could not be parsed or PyMuPDF is unavailable."""


class OaPdfFetchError(OaPdfFulltextError):
    """An OA PDF could not be fetched safely."""


@dataclass(frozen=True, init=False)
class OaPdfLimits:
    """Resource limits applied before and during PDF text extraction."""

    max_input_bytes: int
    max_pages: int
    max_page_chars: int
    max_total_chars: int

    def __init__(
        self,
        max_input_bytes: int = 32 * 1024 * 1024,
        max_pages: int = 128,
        max_page_chars: int = 100_000,
        max_total_chars: int = 1_000_000,
        *,
        max_pdf_bytes: int | None = None,
        max_text_chars: int | None = None,
    ) -> None:
        # The aliases make the limit names readable to callers while keeping
        # ``max_input_bytes`` / ``max_total_chars`` parallel to the arXiv
        # parser's API.
        if max_pdf_bytes is not None:
            max_input_bytes = max_pdf_bytes
        if max_text_chars is not None:
            max_total_chars = max_text_chars
        values = (max_input_bytes, max_pages, max_page_chars, max_total_chars)
        if any(value < 1 for value in values):
            raise ValueError("OA PDF limits must be positive")
        object.__setattr__(self, "max_input_bytes", max_input_bytes)
        object.__setattr__(self, "max_pages", max_pages)
        object.__setattr__(self, "max_page_chars", max_page_chars)
        object.__setattr__(self, "max_total_chars", max_total_chars)

    @property
    def max_pdf_bytes(self) -> int:
        return self.max_input_bytes

    @property
    def max_text_chars(self) -> int:
        return self.max_total_chars


# Compatibility names mirror the arXiv full-text module and make the public
# limit type discoverable without coupling callers to one spelling.
PdfLimits = OaPdfLimits
OaPdfParseLimits = OaPdfLimits
Limits = OaPdfLimits


@dataclass(frozen=True, init=False)
class PdfSection:
    """A heading-delimited portion of a PDF text stream."""

    title: str
    text: str
    index: int = 0
    page_start: int = 0
    page_end: int = 0
    canonical: str = "other"

    def __init__(
        self,
        title: str,
        text: str,
        index: int = 0,
        page_start: int = 0,
        page_end: int = 0,
        canonical: str | None = None,
        *,
        _kind: str | None = None,
    ) -> None:
        if _kind is not None:
            canonical = _kind
        if not canonical:
            canonical = _heading_kind(title) or "other"
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "text", text)
        object.__setattr__(self, "index", index)
        object.__setattr__(self, "page_start", page_start)
        object.__setattr__(self, "page_end", page_end)
        object.__setattr__(self, "canonical", canonical)

    @property
    def name(self) -> str:
        return self.title

    @property
    def content(self) -> str:
        return self.text

    @property
    def heading(self) -> str:
        return self.title

    @property
    def ordinal(self) -> int:
        return self.index

    def render(self) -> str:
        return (self.title + "\n" + self.text).strip()


@dataclass(frozen=True)
class PdfDocument:
    """Text and deterministic sections extracted from an OA PDF."""

    text: str
    sections: tuple[PdfSection, ...]
    page_count: int = 0

    @property
    def content(self) -> str:
        return self.text


# A few callers use the resource-oriented spelling; keep it as a harmless
# alias while the canonical public name remains ``PdfDocument``.
OaPdfDocument = PdfDocument
OaPdfSection = PdfSection


_ALIASES: dict[str, str] = {
    "abstract": "abstract",
    "summary": "abstract",
    "introduction": "introduction",
    "intro": "introduction",
    "background": "introduction",
    "related work": "introduction",
    "method": "method",
    "methods": "method",
    "methodology": "method",
    "approach": "method",
    "materials and methods": "method",
    "experiment": "experiment",
    "experiments": "experiment",
    "experimental setup": "experiment",
    "experimental results": "experiment",
    "result": "results",
    "results": "results",
    "evaluation": "results",
    "findings": "results",
    "analysis": "results",
    "conclusion": "conclusion",
    "conclusions": "conclusion",
    "discussion": "conclusion",
}
_CANONICAL_KINDS = frozenset(
    {"abstract", "introduction", "method", "experiment", "results", "conclusion"}
)
_NUMBERING_RE = re.compile(
    r"^(?:(?:\d+|[IVXLC]+)(?:\s*[.:-]\s*(?:\d+|[IVXLC]+))*[.)]?\s+)+",
    re.IGNORECASE,
)
_WHITESPACE_RE = re.compile(r"\s+")


def _fitz_module() -> ModuleType:
    try:
        return importlib.import_module("fitz")
    except (ImportError, ModuleNotFoundError) as exc:
        raise OaPdfParseError(
            "PyMuPDF is not installed; install the optional 'fulltext' extra to parse OA PDFs"
        ) from exc


def _normalise_heading(value: str) -> str:
    value = _NUMBERING_RE.sub("", value.strip())
    value = re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()
    return _WHITESPACE_RE.sub(" ", value)


def _heading_kind(value: str) -> str | None:
    normalized = _normalise_heading(value)
    if not normalized:
        return None
    direct = _ALIASES.get(normalized)
    if direct:
        return direct
    # Headings such as "3 Experiments and Results" are common in papers.  A
    # known leading phrase is enough to classify them without treating prose
    # containing the word "results" as a section boundary.
    for alias, kind in sorted(_ALIASES.items(), key=lambda item: -len(item[0])):
        if normalized.startswith(alias + " "):
            return kind
    return None


def _sections_from_pages(pages: list[str]) -> tuple[PdfSection, ...]:
    lines: list[str] = []
    line_pages: list[int] = []
    for page_index, page in enumerate(pages):
        page_lines = page.splitlines()
        if page_index and lines and lines[-1].strip():
            lines.append("")
            line_pages.append(page_index)
        lines.extend(page_lines)
        line_pages.extend([page_index] * len(page_lines))

    boundaries: list[tuple[int, str, str]] = []
    for index, line in enumerate(lines):
        title = _WHITESPACE_RE.sub(" ", line.strip())
        kind = _heading_kind(title)
        if kind is not None and len(title) <= 160:
            boundaries.append((index, title, kind))

    if not boundaries:
        text = "\n".join(lines).strip()
        if not text:
            return ()
        page_end = max(0, len(pages) - 1)
        return (PdfSection("Full text", text, 0, 0, page_end, "other"),)

    sections: list[PdfSection] = []
    # Text before the first recognized heading is useful (it often contains an
    # unlabelled abstract), so retain it as an ``other`` section.
    first_start = boundaries[0][0]
    if "\n".join(lines[:first_start]).strip():
        sections.append(
            PdfSection(
                "Preamble",
                "\n".join(lines[:first_start]).strip(),
                len(sections),
                line_pages[0] if line_pages else 0,
                line_pages[first_start - 1] if first_start else 0,
                "other",
            )
        )
    for boundary_index, (start, title, kind) in enumerate(boundaries):
        end = (
            boundaries[boundary_index + 1][0]
            if boundary_index + 1 < len(boundaries)
            else len(lines)
        )
        body = "\n".join(lines[start + 1 : end]).strip()
        page_start = line_pages[start] if start < len(line_pages) else 0
        page_end = line_pages[end - 1] if end and end - 1 < len(line_pages) else page_start
        sections.append(PdfSection(title, body, len(sections), page_start, page_end, kind))
    return tuple(sections)


def parse_oa_pdf(raw: bytes, limits: OaPdfLimits | None = None) -> PdfDocument:
    """Extract bounded text from a PDF byte string."""
    lim = limits or OaPdfLimits()
    if not isinstance(raw, (bytes, bytearray, memoryview)):
        raise TypeError("raw must be bytes-like")
    data = bytes(raw)
    if len(data) > lim.max_input_bytes:
        raise OaPdfParseError("OA PDF exceeds input size limit")
    if not data.startswith(b"%PDF"):
        raise OaPdfParseError("response is not a PDF")

    fitz = _fitz_module()
    document = None
    try:
        document = fitz.open(stream=data, filetype="pdf")
        page_count = len(document)
        if page_count < 1:
            raise OaPdfParseError("PDF contains no pages")
        if page_count > lim.max_pages:
            raise OaPdfParseError("PDF exceeds page count limit")
        pages: list[str] = []
        total = 0
        for page in document:
            try:
                text = str(page.get_text("text"))
            except Exception as exc:  # fitz raises backend-specific exceptions
                raise OaPdfParseError(f"PDF text extraction failed: {exc}") from exc
            if len(text) > lim.max_page_chars:
                raise OaPdfParseError("PDF page exceeds text limit")
            total += len(text)
            if total > lim.max_total_chars:
                raise OaPdfParseError("PDF text exceeds total text limit")
            pages.append(text)
    except OaPdfParseError:
        raise
    except Exception as exc:
        raise OaPdfParseError(f"invalid PDF: {exc}") from exc
    finally:
        if document is not None:
            document.close()

    text = "\n\n".join(page.strip() for page in pages if page.strip()).strip()
    sections = _sections_from_pages(pages)
    if not text or not sections:
        raise OaPdfParseError("PDF contains no extractable text")
    return PdfDocument(text=text, sections=sections, page_count=len(pages))


def _canonical_required(value: str) -> str:
    return _heading_kind(value) or _normalise_heading(value)


def select_pdf_sections(
    document: PdfDocument,
    query: str,
    max_chars: int = 12_000,
    required: Iterable[str] | bool = (),
) -> list[PdfSection]:
    """Select required and query-relevant PDF sections deterministically."""
    if max_chars <= 0 or not document.sections:
        return []
    if required is True:
        required_names = set(_CANONICAL_KINDS)
    elif required is False:
        required_names = set()
    elif isinstance(required, str):
        required_names = {_canonical_required(required)}
    else:
        required_names = {_canonical_required(str(item)) for item in required}
    query_terms = [term for term in re.findall(r"[a-z0-9]+", query.casefold()) if len(term) > 1]
    scored: list[tuple[float, int, PdfSection]] = []
    for section in document.sections:
        title_terms = set(re.findall(r"[a-z0-9]+", section.title.casefold()))
        body_terms = re.findall(r"[a-z0-9]+", section.text.casefold())
        score = (
            (1000.0 if section.canonical in required_names else 0.0)
            + sum(4 for term in query_terms if term in title_terms)
            + sum(1 for term in query_terms if term in body_terms)
        )
        if score:
            scored.append((score, section.index, section))
    if not scored:
        scored = [(0.0, section.index, section) for section in document.sections]
    scored.sort(key=lambda item: (-item[0], item[1]))
    chosen: list[PdfSection] = []
    used = 0
    for _, _, section in scored:
        rendered = section.render()
        if not rendered:
            continue
        remaining = max_chars - used
        if remaining <= 0:
            break
        if len(rendered) > remaining:
            if chosen:
                continue
            if remaining <= len(section.title):
                section = PdfSection(
                    section.title[:remaining],
                    "",
                    section.index,
                    section.page_start,
                    section.page_end,
                    section.canonical,
                )
            else:
                section = PdfSection(
                    section.title,
                    section.text[: remaining - len(section.title) - 1],
                    section.index,
                    section.page_start,
                    section.page_end,
                    section.canonical,
                )
            rendered = section.render()
        chosen.append(section)
        used += len(rendered)
    chosen.sort(key=lambda section: section.index)
    return chosen


def _section_source(source: Source, section: PdfSection) -> Source:
    scholarly = source.scholarly
    if scholarly is None:
        return source
    base_url = source.url.split("#", 1)[0]
    separator = "&" if "?" in base_url else "?"
    return Source(
        title=source.title,
        url=f"{base_url}{separator}dr_section=pdf-{section.index}",
        content=section.render(),
        scholarly=scholarly.model_copy(update={"section": section.canonical}),
    )


class OaPdfFetcher:
    """Fetch OA PDF bytes and turn them into section-scoped ``Source`` values."""

    def __init__(
        self,
        *,
        timeout: float = 30.0,
        limits: OaPdfLimits | None = None,
        max_bytes: int | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if max_bytes is not None:
            limits = OaPdfLimits(max_input_bytes=max_bytes)
        self._limits = limits or OaPdfLimits()
        self._client = client or provider_http_client(timeout=timeout)
        self._owns_client = client is None

    async def fetch(self, url: str) -> bytes:
        value = (url or "").strip()
        try:
            parts = urlsplit(value)
            _ = parts.port
            host = (parts.hostname or "").rstrip(".").casefold()
        except ValueError as exc:
            raise OaPdfFetchError("OA PDF URL is invalid") from exc
        if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
            raise OaPdfFetchError("OA PDF URL must be an absolute HTTP(S) URL")
        if parts.username is not None or parts.password is not None:
            raise OaPdfFetchError("OA PDF URL must not contain credentials")
        if not host or host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
            raise OaPdfFetchError("OA PDF URL host is not public")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise OaPdfFetchError("OA PDF URL host is not public")
        try:
            async with self._client.stream("GET", value) as response:
                if not 200 <= response.status_code < 300:
                    raise OaPdfFetchError(f"OA PDF request returned HTTP {response.status_code}")
                content_length = response.headers.get("content-length", "").strip()
                if content_length.isdigit() and int(content_length) > self._limits.max_input_bytes:
                    raise OaPdfFetchError("OA PDF exceeds input size limit")
                media_type = (
                    response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                )
                if media_type not in {
                    "application/pdf",
                    "application/octet-stream",
                    "binary/octet-stream",
                }:
                    raise OaPdfFetchError(f"unexpected OA PDF content type: {media_type}")
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > self._limits.max_input_bytes:
                        raise OaPdfFetchError("OA PDF exceeds input size limit")
                    chunks.append(chunk)
                content = b"".join(chunks)
        except (httpx.HTTPError, OSError) as exc:
            raise OaPdfFetchError(f"OA PDF request failed: {exc}") from exc
        if not content.startswith(b"%PDF"):
            raise OaPdfFetchError("OA PDF response does not contain a PDF")
        return content

    async def sections(
        self,
        source: Source,
        query: str,
        *,
        max_chars: int = 12_000,
        required: Iterable[str] | bool = (),
    ) -> list[Source]:
        scholarly = source.scholarly
        if scholarly is None or not scholarly.oa_pdf_url:
            return [source]
        raw = await self.fetch(scholarly.oa_pdf_url)
        document = parse_oa_pdf(raw, self._limits)
        required_names: set[str]
        if required is True:
            required_names = set(_CANONICAL_KINDS)
        elif required is False:
            required_names = set()
        elif isinstance(required, str):
            required_names = {_canonical_required(required)}
        else:
            required_names = {_canonical_required(str(item)) for item in required}
        if any(section.canonical == "abstract" for section in document.sections):
            required_names.add("abstract")
        selected = select_pdf_sections(
            document, query, max_chars=max_chars, required=required_names
        )
        if not selected:
            return [source]
        return [_section_source(source, section) for section in selected]

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


__all__ = [
    "OaPdfFetcher",
    "OaPdfFetchError",
    "OaPdfFulltextError",
    "OaPdfLimits",
    "OaPdfParseLimits",
    "OaPdfParseError",
    "PdfLimits",
    "PdfDocument",
    "PdfSection",
    "OaPdfDocument",
    "OaPdfSection",
    "Limits",
    "parse_oa_pdf",
    "select_pdf_sections",
]
