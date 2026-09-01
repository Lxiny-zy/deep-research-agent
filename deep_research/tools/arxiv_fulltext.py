"""Bounded, stdlib-only parsing of arXiv LaTeX e-print archives.

The parser intentionally keeps LaTeX source (rather than attempting to render it),
which preserves tables and numeric values for downstream evidence extraction.
"""

from __future__ import annotations

import io
import re
import tarfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import PurePosixPath

import httpx

from ..models import Source


class ArxivFulltextError(ValueError):
    """Base error raised for malformed or unsafe e-print archives."""


class ArxivArchiveError(ArxivFulltextError):
    """The archive violates a safety or size limit."""


class ArxivLatexError(ArxivFulltextError):
    """The archive contains no usable LaTeX source."""


class ArxivFulltextFetchError(ArxivFulltextError):
    """An arXiv e-print could not be fetched safely."""


# Public compatibility aliases keep the parser usable by callers that name the
# errors after the resource being parsed rather than the backend.
LatexParseError = ArxivLatexError
LatexArchiveError = ArxivArchiveError


@dataclass(frozen=True)
class ParseLimits:
    """Resource limits applied while reading an e-print archive."""

    max_input_bytes: int = 32 * 1024 * 1024
    max_members: int = 512
    max_member_bytes: int = 8 * 1024 * 1024
    max_total_bytes: int = 32 * 1024 * 1024
    max_tex_bytes: int = 16 * 1024 * 1024
    max_include_depth: int = 16


ArxivParseLimits = ParseLimits
Limits = ParseLimits


@dataclass(frozen=True)
class LatexSection:
    title: str
    text: str
    level: int = 1
    starred: bool = False
    command: str = "section"
    index: int = 0

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
    def canonical(self) -> str:
        value = _canonical(self.title)
        return value if value in _CANONICAL_KINDS else "other"

    @property
    def ordinal(self) -> int:
        return self.index

    def render(self) -> str:
        return (self.title + "\n" + self.text).strip()


@dataclass(frozen=True)
class LatexDocument:
    main_file: str
    text: str
    sections: tuple[LatexSection, ...]
    files: Mapping[str, bytes] = field(default_factory=dict, repr=False)

    @property
    def content(self) -> str:
        return self.text

    @property
    def source_files(self) -> tuple[str, ...]:
        return tuple(sorted(self.files))


_SECTION_RE = re.compile(
    r"\\(part|chapter|section|subsection|subsubsection|paragraph|subparagraph)(\*)?"
)
_INPUT_RE = re.compile(r"\\(input|include)\s*(?:\{([^{}]+)\}|([^\s%]+))")
_ABSTRACT_RE = re.compile(r"\\begin\s*\{abstract\}(.*?)\\end\s*\{abstract\}", re.I | re.S)
_LEVELS = {
    "part": 0,
    "chapter": 0,
    "section": 1,
    "subsection": 2,
    "subsubsection": 3,
    "paragraph": 4,
    "subparagraph": 5,
}
_ALIASES = {
    "abstract": "abstract",
    "summary": "abstract",
    "introduction": "introduction",
    "intro": "introduction",
    "related work": "introduction",
    "background": "introduction",
    "method": "method",
    "methods": "method",
    "methodology": "method",
    "approach": "method",
    "experiment": "experiment",
    "experiments": "experiment",
    "experimental": "experiment",
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


def _safe_name(name: str) -> str:
    name = name.replace("\\", "/")
    path = PurePosixPath(name)
    parts = path.parts
    if not name or path.is_absolute() or ".." in parts or (parts and ":" in parts[0]):
        raise ArxivArchiveError(f"unsafe archive path: {name!r}")
    normalized = tuple(part for part in parts if part != ".")
    if not normalized:
        raise ArxivArchiveError(f"unsafe archive path: {name!r}")
    return "/".join(normalized)


def _strip_comments(source: str) -> str:
    lines: list[str] = []
    for line in source.splitlines(keepends=True):
        cut = None
        escaped = False
        for i, char in enumerate(line):
            if char == "%" and not escaped:
                cut = i
                break
            if char == "\\":
                escaped = not escaped
            else:
                escaped = False
        lines.append(line if cut is None else line[:cut] + ("\n" if line.endswith("\n") else ""))
    return "".join(lines)


def _brace_value(source: str, start: int) -> tuple[str, int] | None:
    while start < len(source) and source[start].isspace():
        start += 1
    if start >= len(source) or source[start] != "{":
        return None
    depth = 1
    escaped = False
    i = start + 1
    while i < len(source):
        char = source[i]
        if char == "{" and not escaped:
            depth += 1
        elif char == "}" and not escaped:
            depth -= 1
            if depth == 0:
                return source[start + 1 : i], i + 1
        if char == "\\":
            escaped = not escaped
        else:
            escaped = False
        i += 1
    return None


def _section_value(source: str, start: int) -> tuple[str, int] | None:
    """Read a section's optional short title and required long title."""
    cursor = start
    while cursor < len(source) and source[cursor].isspace():
        cursor += 1
    if cursor < len(source) and source[cursor] == "[":
        depth = 1
        escaped = False
        cursor += 1
        while cursor < len(source):
            char = source[cursor]
            if char == "[" and not escaped:
                depth += 1
            elif char == "]" and not escaped:
                depth -= 1
                if depth == 0:
                    cursor += 1
                    break
            if char == "\\":
                escaped = not escaped
            else:
                escaped = False
            cursor += 1
        else:
            return None
    return _brace_value(source, cursor)


def _section_title(value: str) -> str:
    # Keep numeric text and ordinary punctuation; only collapse source whitespace.
    return re.sub(r"\s+", " ", value).strip()


def _sections(source: str) -> list[LatexSection]:
    clean = _strip_comments(source)
    matches: list[tuple[int, str, bool, str, int]] = []
    for match in _SECTION_RE.finditer(clean):
        value = _section_value(clean, match.end())
        if value is None:
            continue
        title, after = value
        matches.append(
            (match.start(), match.group(1), bool(match.group(2)), _section_title(title), after)
        )
    sections: list[LatexSection] = []
    for index, (_start, command, starred, title, after_title) in enumerate(matches):
        end = matches[index + 1][0] if index + 1 < len(matches) else len(clean)
        body = clean[after_title:end].strip()
        sections.append(
            LatexSection(
                title=title,
                text=body,
                level=_LEVELS[command],
                starred=starred,
                command=command,
                index=index,
            )
        )
    abstract = _ABSTRACT_RE.search(clean)
    if abstract:
        sections.insert(
            0,
            LatexSection(
                title="Abstract",
                text=abstract.group(1).strip(),
                level=0,
                command="abstract",
                index=-1,
            ),
        )
        sections = [
            s.__class__(s.title, s.text, s.level, s.starred, s.command, i)
            for i, s in enumerate(sections)
        ]
    if not sections and clean.strip():
        sections.append(
            LatexSection(title="Full text", text=clean.strip(), level=0, command="document")
        )
    return sections


def parse_arxiv_eprint(raw: bytes, limits: ParseLimits | None = None) -> LatexDocument:
    """Parse a bounded arXiv source tarball into its main LaTeX document."""
    lim = limits or ParseLimits()
    if not isinstance(raw, (bytes, bytearray, memoryview)):
        raise TypeError("raw must be bytes-like")
    raw_bytes = bytes(raw)
    if len(raw_bytes) > lim.max_input_bytes:
        raise ArxivArchiveError("archive exceeds input size limit")
    files: dict[str, bytes] = {}
    total = 0
    try:
        archive = tarfile.open(fileobj=io.BytesIO(raw_bytes), mode="r:*")
    except (tarfile.TarError, OSError) as exc:
        raise ArxivArchiveError(f"invalid tar archive: {exc}") from exc
    with archive:
        members = archive.getmembers()
        if len(members) > lim.max_members:
            raise ArxivArchiveError("archive exceeds member count limit")
        for member in members:
            name = _safe_name(member.name)
            if name in files:
                raise ArxivArchiveError(f"duplicate archive path: {name}")
            if member.isdir():
                continue
            if member.issym() or member.islnk() or member.isdev() or not member.isfile():
                raise ArxivArchiveError(f"unsupported archive member: {name}")
            if member.size < 0 or member.size > lim.max_member_bytes:
                raise ArxivArchiveError(f"member exceeds size limit: {name}")
            if member.size > lim.max_total_bytes - total:
                raise ArxivArchiveError("archive exceeds total extracted size limit")
            extracted = archive.extractfile(member)
            if extracted is None:
                raise ArxivArchiveError(f"cannot read archive member: {name}")
            data = extracted.read(member.size + 1)
            if len(data) > member.size:
                raise ArxivArchiveError(f"member read exceeded declared size: {name}")
            total += len(data)
            if name.lower().endswith((".tex", ".ltx")):
                files[name] = data
    if not files:
        raise ArxivLatexError("archive contains no .tex source")
    root_tex = [
        name for name in files if "/" not in name and name.lower().endswith((".tex", ".ltx"))
    ]
    candidates = root_tex or list(files)
    preferred = next(
        (n for n in candidates if PurePosixPath(n).stem.lower() in {"main", "paper", "article"}),
        None,
    )
    documentclass = next(
        (
            n
            for n in sorted(candidates, key=lambda item: (item.count("/"), item.lower()))
            if re.search(rb"\\documentclass\s*\{|\\begin\s*\{\s*document\s*\}", files[n])
        ),
        None,
    )
    main = (
        preferred or documentclass or sorted(candidates, key=lambda n: (n.count("/"), n.lower()))[0]
    )
    assembled = _assemble(main, files, lim.max_include_depth)
    if len(assembled.encode("utf-8")) > lim.max_tex_bytes:
        raise ArxivArchiveError("expanded LaTeX exceeds size limit")
    return LatexDocument(
        main_file=main, text=assembled, sections=tuple(_sections(assembled)), files=files
    )


def _assemble(main: str, files: Mapping[str, bytes], max_depth: int) -> str:
    def visit(path: str, depth: int, stack: tuple[str, ...]) -> str:
        if depth > max_depth or path in stack:
            return ""
        source = _strip_comments(files[path].decode("utf-8", errors="replace"))
        out: list[str] = []
        cursor = 0
        for match in _INPUT_RE.finditer(source):
            out.append(source[cursor : match.start()])
            target = (match.group(2) or match.group(3) or "").strip()
            target = target.replace("\\", "/")
            base = str(PurePosixPath(path).parent)
            joined = str(PurePosixPath(base, target))
            try:
                normalized = _safe_name(joined)
            except ArxivArchiveError:
                normalized = ""
            if normalized and not normalized.lower().endswith((".tex", ".ltx")):
                normalized += ".tex"
            if normalized in files:
                out.append(visit(normalized, depth + 1, stack + (path,)))
            cursor = match.end()
        out.append(source[cursor:])
        return "".join(out)

    return visit(main, 0, ())


def _canonical(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()
    if value in _ALIASES:
        return _ALIASES[value]
    tokens = value.split()
    for token in tokens:
        if token in _ALIASES:
            return _ALIASES[token]
    return value


def select_sections(
    document: LatexDocument,
    query: str,
    max_chars: int = 12_000,
    required: Iterable[str] | bool = (),
) -> list[LatexSection]:
    """Select required and query-relevant sections in stable document order."""
    if max_chars <= 0 or not document.sections:
        return []
    if required is True:
        required_names = {
            "abstract",
            "introduction",
            "method",
            "experiment",
            "results",
            "conclusion",
        }
    elif required is False:
        required_names = set()
    elif isinstance(required, str):
        required_names = {_canonical(required)}
    else:
        required_names = {_canonical(str(x)) for x in required}
    query_terms = [t for t in re.findall(r"[a-z0-9]+", query.lower()) if len(t) > 1]
    scored: list[tuple[float, int, LatexSection]] = []
    for sec in document.sections:
        canon = _canonical(sec.title)
        title_terms = set(re.findall(r"[a-z0-9]+", sec.title.lower()))
        body_terms = re.findall(r"[a-z0-9]+", sec.text.lower())
        score = (
            (1000.0 if canon in required_names else 0.0)
            + sum(4 for t in query_terms if t in title_terms)
            + sum(1 for t in query_terms if t in body_terms)
        )
        if score:
            scored.append((score, sec.index, sec))
    if not scored:
        scored = [(0.0, sec.index, sec) for sec in document.sections]
    scored.sort(key=lambda item: (-item[0], item[1]))
    chosen: list[LatexSection] = []
    used = 0
    for _, _, sec in scored:
        rendered = sec.render()
        if not rendered:
            continue
        remaining = max_chars - used
        if remaining <= 0:
            break
        if len(rendered) > remaining:
            if chosen:
                continue
            if remaining <= len(sec.title):
                sec = LatexSection(
                    sec.title[:remaining], "", sec.level, sec.starred, sec.command, sec.index
                )
            else:
                sec = LatexSection(
                    sec.title,
                    sec.text[: remaining - len(sec.title) - 1],
                    sec.level,
                    sec.starred,
                    sec.command,
                    sec.index,
                )
            rendered = sec.render()
        chosen.append(sec)
        used += len(rendered)
    chosen.sort(key=lambda s: s.index)
    return chosen


def arxiv_eprint_url(work_id: str, version: str = "") -> str:
    """Build the stable arXiv e-print endpoint from stored metadata."""
    value = (work_id or "").strip()
    if value.lower().startswith("arxiv:"):
        value = value.split(":", 1)[1]
    value = value.strip().rstrip("/")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)?", value):
        raise ArxivFulltextFetchError("invalid arXiv work_id")
    requested = (version or "").strip().lower()
    if requested and not re.fullmatch(r"v[1-9][0-9]*", requested):
        raise ArxivFulltextFetchError("invalid arXiv version")
    if requested:
        value = re.sub(r"v[1-9][0-9]*$", "", value, flags=re.I)
        value += requested
    return f"https://export.arxiv.org/e-print/{value}"


class ArxivEprintFetcher:
    """Download and turn an arXiv e-print into bounded section-scoped sources."""

    def __init__(
        self,
        *,
        timeout: float = 30.0,
        max_bytes: int = 32 * 1024 * 1024,
        limits: ParseLimits | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        self._limits = limits or ParseLimits(max_input_bytes=max_bytes)
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None

    async def fetch(self, work_id: str, version: str = "") -> bytes:
        url = arxiv_eprint_url(work_id, version)
        try:
            async with self._client.stream("GET", url) as response:
                response.raise_for_status()
                content_length = response.headers.get("content-length", "").strip()
                if content_length.isdigit() and int(content_length) > self._limits.max_input_bytes:
                    raise ArxivFulltextFetchError("arXiv e-print exceeds input size limit")
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > self._limits.max_input_bytes:
                        raise ArxivFulltextFetchError("arXiv e-print exceeds input size limit")
                    chunks.append(chunk)
        except (httpx.HTTPError, OSError) as exc:
            raise ArxivFulltextFetchError(f"arXiv e-print request failed: {exc}") from exc
        return b"".join(chunks)

    async def sections(
        self,
        source: Source,
        query: str,
        *,
        max_chars: int = 12_000,
        required: Iterable[str] | bool = (),
    ) -> list[Source]:
        scholarly = source.scholarly
        if scholarly is None or not scholarly.work_id.lower().startswith("arxiv:"):
            return [source]
        raw = await self.fetch(scholarly.work_id, scholarly.version)
        document = parse_arxiv_eprint(raw, self._limits)
        required_names: set[str]
        if required is True:
            required_names = {
                "abstract",
                "introduction",
                "method",
                "experiment",
                "results",
                "conclusion",
            }
        elif required is False:
            required_names = set()
        elif isinstance(required, str):
            required_names = {_canonical(required)}
        else:
            required_names = {_canonical(str(item)) for item in required}
        if any(_canonical(section.title) == "abstract" for section in document.sections):
            required_names.add("abstract")
        selected = select_sections(
            document,
            query,
            max_chars=max_chars,
            required=required_names,
        )
        if not selected:
            return [source]
        return [_section_source(source, section) for section in selected]

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _section_source(source: Source, section: LatexSection) -> Source:
    """Give every section a distinct URL for unambiguous quote verification."""
    scholarly = source.scholarly
    if scholarly is None:
        return source
    label = section.canonical
    base_url = source.url.split("#", 1)[0]
    separator = "&" if "?" in base_url else "?"
    return Source(
        title=source.title,
        url=f"{base_url}{separator}dr_section={section.index}",
        content=section.render(),
        scholarly=scholarly.model_copy(update={"section": label}),
    )


__all__ = [
    "ArxivArchiveError",
    "ArxivEprintFetcher",
    "ArxivFulltextError",
    "ArxivFulltextFetchError",
    "ArxivLatexError",
    "ArxivParseLimits",
    "LatexArchiveError",
    "LatexDocument",
    "LatexParseError",
    "LatexSection",
    "Limits",
    "ParseLimits",
    "arxiv_eprint_url",
    "parse_arxiv_eprint",
    "parse_eprint",
    "select_sections",
]

parse_eprint = parse_arxiv_eprint
