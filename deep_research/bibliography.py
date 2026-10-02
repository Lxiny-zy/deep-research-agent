"""Bibliographic documents and precise citation locations are separate identities.

The research report keeps its verified location numbers. Presentation may show a
single document number for several locations, but always retains their binding.
No model call, title similarity or guessed publication metadata is used here.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from collections.abc import Iterator
from typing import Literal
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, Field

from .models import Finding, Source


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
    scope: Literal["reviewed_unit", "source_location", "unused_location"] = "source_location"
    evidence_ids: list[str] = Field(default_factory=list)


class Bibliography(BaseModel):
    source_body: str = ""
    body: str = ""
    documents: list[ReferenceDocument] = Field(default_factory=list)
    locations: list[ReferenceLocation] = Field(default_factory=list)
    occurrences: list[CitationOccurrence] = Field(default_factory=list)
    binding_status: Literal["unavailable", "bound", "invalid"] = "unavailable"


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


def build_bibliography(
    markdown: str,
    citations: list[str],
    findings: list[Finding],
    sources: list[Source] | None = None,
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
    order.extend(index for index in range(1, len(citations) + 1) if index not in order)
    documents: dict[str, ReferenceDocument] = {}
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
        identity, link = document_identity(url, next(iter(dois)) if len(dois) == 1 else "")
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
        elif identity.startswith("https://workspace.invalid/"):
            reference = title or "本地文档"
        elif reference.endswith(url):
            reference = reference[: -len(url)] + link
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
    if not catalog.documents:
        return ""

    def escape(text: str) -> str:
        return re.sub(r"([\\`*_{}\[\]<>])", r"\\\1", text.replace("\n", " "))

    return "## 参考文献\n\n" + "\n\n".join(
        f"[{doc.index}] {escape(doc.reference)}" for doc in catalog.documents
    )


def present_markdown(markdown: str, catalog: Bibliography, *, links: bool = True) -> str:
    body = project_citations(source_body(markdown), catalog, links=links)
    references = bibliography_markdown(catalog)
    return body + ("\n\n" + references if references else "") + "\n"


def work_keys(catalog: Bibliography) -> dict[str, str]:
    """Versions remain separate bibliography entries but are one research work."""
    identities = {entry.index: entry.identity for entry in catalog.documents}
    return {
        item.url: re.sub(r"^(arxiv:.+?)v\d+$", r"\1", identities[item.document])
        for item in catalog.locations
    }
