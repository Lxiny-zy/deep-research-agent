"""Bibliographic documents and precise citation locations are separate identities.

The research report keeps its verified location numbers. Presentation may show a
single document number for several locations, but always retains their binding.
No model call, fuzzy title matching or guessed publication metadata is used here.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterator
from typing import Literal
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, Field

from .models import Finding, ScholarlyMetadata, Source, SourceIdentity


class ReferenceDocument(BaseModel):
    index: int
    identity: str
    title: str = ""
    reference: str = ""
    url: str = ""
    locations: list[int] = Field(default_factory=list)


class ReferenceLocation(BaseModel):
    index: int
    document: int
    url: str
    label: str = ""
    content_hashes: list[str] = Field(default_factory=list)


class CitationOccurrence(BaseModel):
    id: str
    run: int
    document: int
    locations: list[int]
    unit_id: str = ""
    scope: Literal["reviewed_unit", "source_location", "unused_location", "fulltext_review"] = (
        "source_location"
    )
    evidence_ids: list[str] = Field(default_factory=list)
    review_note: str = ""


class Bibliography(BaseModel):
    source_body: str = ""
    body: str = ""
    documents: list[ReferenceDocument] = Field(default_factory=list)
    locations: list[ReferenceLocation] = Field(default_factory=list)
    occurrences: list[CitationOccurrence] = Field(default_factory=list)
    binding_status: Literal["unavailable", "bound", "invalid"] = "unavailable"
    # Locations retain the full evidence inventory. Only these documents are
    # printed as references; None is the compatibility state for old catalogs.
    cited_documents: list[int] | None = None


_DOI = re.compile(r"10\.\d{4,9}/\S+", re.I)
_ARXIV = re.compile(
    r"/(?:abs|pdf|html)/((?:\d{4}\.\d{4,5}|[a-z-]+/\d{7})(?:v\d+)?)(?:\.pdf)?$", re.I
)
_LOCAL_PATH = re.compile(r"^/(?:attachments|pasted|sources)/[^/]+/?$")
_REFERENCE_SECTION = re.compile(
    r"(?:^|\n)#{1,3}[ \t]+(?:参考来源|参考文献|References)[ \t]*(?:\r?\n|$)", re.I
)


def _doi(value: str) -> str:
    value = unquote(value.strip())
    value = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value, flags=re.I)
    return value.casefold() if _DOI.fullmatch(value) else ""


def document_identity(url: str, doi: str = "") -> tuple[str, str]:
    """Only remove application-owned locations; preserve article IDs and versions."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url, ""
    host = (parts.hostname or "").casefold()
    local = host == "workspace.invalid" and bool(_LOCAL_PATH.fullmatch(parts.path))
    arxiv = _ARXIV.fullmatch(parts.path) if host in {"arxiv.org", "www.arxiv.org"} else None
    if arxiv:
        identifier = arxiv[1].casefold()
        return f"arxiv:{identifier}", f"https://arxiv.org/abs/{identifier}"
    parsed_doi = _doi(parts.path.lstrip("/")) if host in {"doi.org", "dx.doi.org"} else ""
    identifier = parsed_doi or (_doi(doi) if not local else "")
    if identifier:
        return f"doi:{identifier}", f"https://doi.org/{identifier}"
    query = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if local and key in {"chunk", "abstract-reparse"} and value.isdigit():
            continue
        if (
            local
            and re.fullmatch(r"/attachments/[0-9a-f]{24}/?", parts.path)
            and key == "text_revision"
            and re.fullmatch(r"[0-9a-f]{16}", value)
        ):
            # A parser snapshot does not change the immutable PDF bytes.
            # Its full URL/hash remains distinct in ReferenceLocation.
            continue
        if key == "dr_section" and re.fullmatch(r"pdf-\d+", value):
            continue
        if key.casefold().startswith("utm_"):
            continue
        query.append((key, value))
    fragment = parts.fragment
    chunk = re.fullmatch(r"(?:(.*)#)?chunk-\d+", fragment)
    if chunk:
        fragment = chunk[1] or ""
    if local:
        fragment = ""
    if len({key for key, _ in query}) == len(query):
        query.sort()
    canonical = urlunsplit(
        (
            parts.scheme.casefold(),
            parts.netloc.casefold(),
            parts.path,
            urlencode(query),
            fragment,
        )
    )
    clickable = canonical if parts.scheme in {"http", "https"} and host and not local else ""
    return canonical, clickable


def source_body(markdown: str) -> str:
    from .workbench.delivery.math_markdown import citation_text

    masked = citation_text(markdown)
    headings = list(_REFERENCE_SECTION.finditer(masked))
    body = markdown
    for heading in reversed(headings):
        level = len(heading[0].split()[0])
        following = re.search(rf"(?m)^#{{1,{level}}}[ \t]+\S", masked[heading.end() :])
        end = heading.end() + following.start() if following else len(markdown)
        start = heading.start() + (1 if heading[0].startswith("\n") else 0)
        body = body[:start] + body[end:]
    return body.strip()


def _primary_reference(source: Source | None) -> str:
    """An explicitly labelled publisher citation is metadata, not a guessed title."""
    if source is None:
        return ""
    # Later pages may quote another paper's publisher citation as an example.
    # Only a known first-page / first-section snapshot can provide this metadata.
    page = re.search(r"第\s*(\d+)(?:\s*[-–]\s*\d+)?\s*页", source.locator)
    try:
        parts = urlsplit(source.url)
    except ValueError:
        return ""
    query = dict(parse_qsl(parts.query))
    if page:
        if page[1] != "1":
            return ""
    elif not (
        query.get("chunk") == "1"
        or query.get("dr_section") == "pdf-0"
        or parts.fragment == "chunk-1"
    ):
        return ""
    match = re.search(
        r"(?ims)^Citation:[ \t]*(.+?)"
        r"(?=^Academic Editor:|^Received:|^Accepted:|^Published:|^Copyright:)",
        source.content,
    )
    if match is None:
        return ""
    text = re.sub(r"\s+", " ", match[1]).strip()
    text = re.sub(r"(https?://(?:dx\.)?doi\.org/)\s+", r"\1", text, flags=re.I)
    # Do not turn a DOI suffix broken across PDF lines into a plausible but
    # incomplete identifier. This publisher format puts one DOI at the end.
    identifiers = list(re.finditer(r"https?://(?:dx\.)?doi\.org/10\.\d{4,9}/\S+", text, re.I))
    return text if len(identifiers) == 1 and identifiers[0].end() == len(text) else ""


def _location_label(url: str, source: Source | None, finding: Finding | None) -> str:
    locator = source.locator if source else ""
    if not locator and finding:
        reference = finding.verification.source_reference
        if "（" in reference and reference.endswith("）"):
            locator = reference.rsplit("（", 1)[1][:-1]
    # Page/chunk positions remain useful even when an old PDF parser mistook
    # a long prose line for a section heading.
    positions = re.findall(r"第\s*\d+(?:\s*[-–]\s*\d+)?\s*页|片段\s*\d+", locator)
    if positions:
        return " · ".join(positions)
    if locator:
        return locator
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    for key, value in parse_qsl(parts.query):
        if key == "chunk" and value.isdigit():
            return f"片段 {value}"
        if key == "dr_section" and re.fullmatch(r"(?:pdf-)?\d+", value):
            return f"章节 {value.removeprefix('pdf-')}"
    return parts.fragment if re.fullmatch(r"chunk-\d+", parts.fragment) else ""


def _title_key(title: str) -> str:
    if re.search(r"\.(?:pdf|docx?|txt|md)$", title, re.I):
        return ""
    value = "".join(c for c in unicodedata.normalize("NFKC", title).casefold() if c.isalnum())
    return value if len(value) >= 16 else ""


def _work_alias(value: str) -> str:
    value = value.casefold().strip()
    match = re.fullmatch(r"(?:arxiv:|doi:10\.48550/arxiv\.)(.+?)(?:v\d+)?", value)
    return "arxiv:" + match[1] if match else value


def _bibliographic_identities(
    citations: list[str], by_url: dict[str, list[Finding]], snapshots: dict[tuple[str, str], Source]
) -> dict[int, str]:
    """Group exact scholarly identities without changing full-text snapshot IDs."""
    parents: dict[str, str] = {}

    def root(key: str) -> str:
        parents.setdefault(key, key)
        if parents[key] != key:
            parents[key] = root(parents[key])
        return parents[key]

    def join(a: str, b: str) -> None:
        a, b = root(a), root(b)
        if a != b:
            parents[max(a, b)] = min(a, b)

    slots: dict[int, str] = {}
    titles: dict[str, list[tuple[str, tuple[str, ...]]]] = defaultdict(list)
    for index, url in enumerate(citations, 1):
        records = by_url.get(url, [])
        source = next(
            (
                snapshots[(url, f.verification.source_content_hash)]
                for f in records
                if (url, f.verification.source_content_hash) in snapshots
            ),
            None,
        )
        base = _work_alias(document_identity(url)[0])
        aliases = {base}
        identities = [
            f.verification.source_identity for f in records if f.verification.source_identity
        ]
        meta = source.scholarly if source else None
        metadata_records: list[SourceIdentity | ScholarlyMetadata] = list(identities)
        if meta:
            metadata_records.append(meta)
        dois = {_doi(identity.doi) for identity in metadata_records if _doi(identity.doi)}
        if len(dois) > 1:
            slots[index] = base + f"|conflicting-metadata:{index}"
            continue
        for identity in metadata_records:
            if doi := _doi(identity.doi):
                aliases.add(_work_alias("doi:" + doi))
            work = identity.work_id
            if work.lower().startswith("arxiv:"):
                aliases.add(_work_alias(work))
            elif "arxiv.org/" in work:
                aliases.add(_work_alias(document_identity(work)[0]))
        for alias in aliases:
            join(base, alias)
        slots[index] = base
        title = (source.title if source else "") or next(
            (i.title for i in identities if i.title), ""
        )
        authors = (
            (meta.authors if meta else [])
            or (source.document_authors if source else [])
            or next((i.authors for i in identities if i.authors), [])
        )
        if (meta or identities or authors) and (key := _title_key(title)):
            titles[key].append(
                (
                    base,
                    tuple(sorted(_title_key(name) or name.casefold().strip() for name in authors)),
                )
            )
    for entries in titles.values():
        author_sets = {authors for _, authors in entries if authors}
        if len(author_sets) <= 1:
            for key, _ in entries[1:]:
                join(entries[0][0], key)
    return {index: root(key) for index, key in slots.items()}


def cited_references(catalog: Bibliography) -> list[ReferenceDocument]:
    if catalog.cited_documents is None:
        return catalog.documents
    used = set(catalog.cited_documents)
    return [document for document in catalog.documents if document.index in used]


def build_bibliography(
    markdown: str,
    citations: list[str],
    findings: list[Finding],
    sources: list[Source] | None = None,
    *,
    extra_citations: list[int] | None = None,
) -> Bibliography:
    by_url: dict[str, list[Finding]] = defaultdict(list)
    for finding in findings:
        by_url[finding.source_url].append(finding)
    snapshots = {(s.url, hashlib.sha256(s.content.encode()).hexdigest()): s for s in sources or []}
    catalog = Bibliography(source_body=source_body(markdown))
    from .workbench.delivery.math_markdown import citation_text

    order = list(
        dict.fromkeys(
            int(value)
            for match in re.finditer(
                r"\[(\d+(?:\s*[,，]\s*\d+)*)\]", citation_text(catalog.source_body)
            )
            for value in re.split(r"\s*[,，]\s*", match[1])
            if 0 < int(value) <= len(citations)
        )
    )
    order.extend(
        index
        for index in dict.fromkeys(extra_citations or [])
        if 0 < index <= len(citations) and index not in order
    )
    used = set(order)
    order.extend(index for index in range(1, len(citations) + 1) if index not in order)
    bibliography_ids = _bibliographic_identities(citations, by_url, snapshots)
    documents: dict[str, ReferenceDocument] = {}
    reference_quality: dict[str, int] = {}
    for index in order:
        url = citations[index - 1]
        records = by_url[url]
        first = records[0] if records else None
        hashes = list(
            dict.fromkeys(
                f.verification.source_content_hash
                for f in records
                if f.verification.source_content_hash
            )
        )
        source = next((snapshots[(url, h)] for h in hashes if (url, h) in snapshots), None)
        dois = {
            _doi(f.verification.source_identity.doi)
            for f in records
            if f.verification.source_identity and _doi(f.verification.source_identity.doi)
        }
        _, link = document_identity(url, next(iter(dois)) if len(dois) == 1 else "")
        identity = bibliography_ids[index]
        if not link and identity.startswith("arxiv:"):
            link = "https://arxiv.org/abs/" + identity.removeprefix("arxiv:")
        elif not link and identity.startswith("doi:"):
            link = "https://doi.org/" + identity.removeprefix("doi:")
        if len(dois) > 1:
            identity += f"|conflicting-metadata:{index}"
        title = first.verification.source_title if first else ""
        reference = next(
            (f.verification.source_reference for f in records if f.verification.source_reference),
            "",
        )
        primary = _primary_reference(source)
        if primary:
            reference = primary
            doi_link = re.search(r"https?://(?:dx\.)?doi\.org/10\.\d{4,9}/\S+", primary, re.I)
            if doi_link:
                link = doi_link[0].rstrip(".,;")
        elif source and source.document_authors:
            reference = ", ".join(source.document_authors) + ". " + (title or source.title)
        elif identity.startswith("https://workspace.invalid/"):
            reference = title or "本地文档"
            if source and source.scholarly and source.scholarly.authors:
                reference = ", ".join(source.scholarly.authors) + ". " + reference
        elif reference.endswith(url):
            reference = reference[: -len(url)] + link
        if not primary and (
            (source and (source.scholarly or source.document_authors))
            or (
                "workspace.invalid/" in url
                and (source or not title or re.search(r"\.(?:pdf|docx?|txt|md)$", title, re.I))
            )
        ):
            meta = source.scholarly if source else None
            known_title = (
                title
                if title.strip() and not re.search(r"\.(?:pdf|docx?|txt|md)$", title, re.I)
                else "标题未识别"
            )
            authors = (source.document_authors if source else []) or (meta.authors if meta else [])
            reference = ", ".join(authors) if authors else "作者未识别"
            reference += ". " + known_title
            reference += ". " + (str(meta.year) if meta and meta.year else "年份未识别")
            reference += ". " + (meta.venue if meta and meta.venue else "出处未识别")
            if meta and meta.doi:
                reference += ". doi:" + _doi(meta.doi)
            if meta:
                from .citation import _status_flags

                for flag in _status_flags(meta):
                    reference += ". " + flag
            if link:
                reference += ". " + link
        if identity not in documents:
            document = ReferenceDocument(
                index=len(documents) + 1,
                identity=identity,
                title=title,
                reference=reference or title or link or url,
                url=link,
            )
            documents[identity] = document
            catalog.documents.append(document)
        document = documents[identity]
        meta = source.scholarly if source else None
        score = (
            20
            if primary
            else 2 * bool(source and (source.document_authors or (meta and meta.authors)))
            + 2 * bool(meta and meta.year)
            + 2 * bool(meta and meta.venue)
            + 2 * bool(_title_key(title))
            + bool(reference and reference not in {title, url})
        )
        if score > reference_quality.get(identity, -1):
            document.reference = reference or title or link or url
            document.title = title or document.title
            document.url = link or document.url
            reference_quality[identity] = score
        # A later matching location can contain richer explicitly supplied metadata.
        if primary and not re.search(r"https?://(?:dx\.)?doi\.org/", document.reference):
            document.reference = primary
            document.url = link
        document.locations.append(index)
        catalog.locations.append(
            ReferenceLocation(
                index=index,
                document=document.index,
                url=url,
                label=_location_label(url, source, first),
                content_hashes=hashes,
            )
        )
    catalog.locations.sort(key=lambda location: location.index)
    for document in catalog.documents:
        document.locations.sort()
    catalog.cited_documents = [
        document.index for document in catalog.documents if used.intersection(document.locations)
    ]
    catalog.body = project_citations(catalog.source_body, catalog)
    return catalog


def project_citations(markdown: str, catalog: Bibliography, *, links: bool = True) -> str:
    by_index = {location.index: location.document for location in catalog.locations}
    # Occurrences belong to the complete checked body, not arbitrary captions
    # or snippets rendered with the same bibliography.
    scoped = (
        {(item.run, item.document): item for item in catalog.occurrences}
        if (
            markdown.replace("\r\n", "\n").strip()
            == catalog.source_body.replace("\r\n", "\n").strip()
        )
        else {}
    )

    def replace(match: re.Match[str], run: int) -> str:
        indices = [int(n) for n in re.findall(r"\d+", match[0])]
        if any(index not in by_index for index in indices):
            return match[0]  # Unknown citations remain visible and fail the original gate.
        groups: dict[int, list[int]] = {}
        for index in indices:
            group = groups.setdefault(by_index[index], [])
            if index not in group:
                group.append(index)
        rendered = []
        for document, locations in groups.items():
            occurrence = scoped.get((run, document))
            target = (
                f"o-{occurrence.id}"
                if occurrence and occurrence.locations == locations
                else "-".join(map(str, locations))
            )
            rendered.append(f"[[{document}]](#cite-{target})" if links else f"[{document}]")
        return ", ".join(rendered)

    parts: list[str] = []
    start = 0
    for run, match in enumerate(citation_runs(markdown)):
        parts.extend((markdown[start : match.start()], replace(match, run)))
        start = match.end()
    return "".join(parts) + markdown[start:]


def citation_runs(markdown: str) -> Iterator[re.Match[str]]:
    from .workbench.delivery.math_markdown import citation_text

    marker = r"\[\d+(?:\s*[,，]\s*\d+)*\]"
    pattern = rf"{marker}(?:[ \t]*(?:[,，][ \t]*)?{marker})*"
    return re.finditer(pattern, citation_text(markdown))


def bibliography_markdown(catalog: Bibliography) -> str:
    references = cited_references(catalog)
    if not references:
        return ""

    def escape(text: str) -> str:
        return re.sub(r"([\\`*_{}\[\]<>])", r"\\\1", text.replace("\n", " "))

    return "## 参考文献\n\n" + "\n\n".join(
        f"[{doc.index}] {escape(doc.reference)}" for doc in references
    )


def present_markdown(markdown: str, catalog: Bibliography, *, links: bool = True) -> str:
    body = project_citations(source_body(markdown), catalog, links=links)
    references = bibliography_markdown(catalog)
    return body + ("\n\n" + references if references else "") + "\n"


def work_keys(catalog: Bibliography) -> dict[str, str]:
    """Every precise location maps to the shared bibliographic work."""
    identities = {entry.index: entry.identity for entry in catalog.documents}
    return {
        item.url: re.sub(r"^(arxiv:.+?)v\d+$", r"\1", identities[item.document])
        for item in catalog.locations
    }
