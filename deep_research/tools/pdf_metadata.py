"""Conservative first-page metadata extraction; no network or guessed venue."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from ..models import ScholarlyMetadata


def first_page_metadata(
    page: Any, text: str, title: str, authors: tuple[str, ...]
) -> tuple[str, tuple[str, ...], ScholarlyMetadata | None]:
    rows = []
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            spans = [span for span in line.get("spans", []) if span.get("text", "").strip()]
            if not spans or abs(line.get("dir", (1, 0))[1]) > 0.2:
                continue
            rows.append(
                {
                    "text": " ".join(span["text"].strip() for span in spans),
                    "base_text": " ".join(
                        span["text"].strip()
                        for span in spans
                        if span["size"] >= max(s["size"] for s in spans) * 0.85
                    ),
                    "size": max(span["size"] for span in spans),
                    "y": line["bbox"][1],
                    "end": line["bbox"][3],
                    "x": line["bbox"][0],
                }
            )
    rows.sort(key=lambda row: (row["y"], row["x"]))
    abstract_y = min(
        (row["y"] for row in rows if re.match(r"^(?:Abstract\b|摘要)", row["text"], re.I)),
        default=page.rect.height * 0.55,
    )
    header = [row for row in rows if 25 <= row["y"] < abstract_y]
    title_rows = []
    if not title:
        candidates = [
            row
            for row in header
            if row["size"] >= 14
            and len(row["text"]) >= 8
            and not re.search(
                r"arxiv|proceedings|copyright|©|https?://|^IEEE|^ACM|^Journal\b", row["text"], re.I
            )
        ]
        if candidates:
            largest = max(row["size"] for row in candidates)
            first = next(row for row in candidates if row["size"] >= largest - 0.5)
            title_rows = [first]
            for row in candidates[candidates.index(first) + 1 :]:
                if (
                    abs(row["size"] - largest) <= 0.5
                    and 0 <= row["y"] - title_rows[-1]["end"] <= largest
                ):
                    title_rows.append(row)
                else:
                    break
            candidate = re.sub(r"\s+", " ", " ".join(row["text"] for row in title_rows)).strip()
            if 8 <= len(candidate) <= 300:
                title = unicodedata.normalize("NFKC", candidate)
    else:
        normalized_title = re.sub(r"\W", "", title).casefold()
        title_rows = [
            row for row in header if re.sub(r"\W", "", row["text"]).casefold() in normalized_title
        ]
    if title and not authors and title_rows:
        after = max(row["end"] for row in title_rows)
        author_names: list[str] = []
        for row in header:
            if not after <= row["y"] <= min(after + 140, abstract_y):
                continue
            value = row["base_text"]
            if re.search(
                r"university|institute|department|laboratory|school|academy|college|research|@|https?://|arxiv|abstract|copyright|proceedings|conference|\d{4}",
                value,
                re.I,
            ):
                if author_names:
                    break
                continue
            names = re.split(r"\s*[,;、]\s*|\s+(?:and|&)\s+", value)
            clean = [re.sub(r"^(?:and\s+)|[\d*∗†‡]+$", "", name).strip() for name in names]
            clean = [name for name in clean if name]
            if clean and all(
                re.fullmatch(
                    r"[A-ZÀ-Þ][\wÀ-ÿ.'’-]*(?:[ -]+(?:[A-ZÀ-Þ][\wÀ-ÿ.'’-]*|de|van|von|la|da)){1,5}"
                    r"|[\u4e00-\u9fff]{2,5}",
                    name,
                )
                for name in clean
            ):
                author_names.extend(clean)
        authors = tuple(dict.fromkeys(author_names))[:32]

    front = re.split(r"(?im)^\s*(?:Abstract\b|摘要)", text, maxsplit=1)[0]
    metadata_text = re.split(
        r"(?im)^\s*(?:References|Bibliography|参考文献)\s*$", text, maxsplit=1
    )[0]
    arxiv_matches = list(
        re.finditer(
            r"(?im)^[ \t]*arXiv\s*:\s*((?:\d{4}\.\d{4,5}|[a-z-]+/\d{7}))(v\d+)?", metadata_text
        )
    )
    arxiv = arxiv_matches[0] if len(arxiv_matches) == 1 else None
    doi_matches = list(
        re.finditer(
            r"(?im)^[ \t]*(?:doi\s*:\s*|https?://(?:dx\.)?doi\.org/)(10\.\d{4,9}/\S+)",
            metadata_text,
        )
    )
    doi_match = doi_matches[0] if len(doi_matches) == 1 else None
    doi = doi_match[1].rstrip(".,;") if doi_match else ""
    venue_match = re.search(r"(?im)^(?:Published in|Journal|Conference)\s*:\s*(.+)$", front)
    venue = venue_match[1].strip() if venue_match else ""
    if not venue:
        footer_venues = [
            row["text"]
            for row in rows
            if row["y"] > page.rect.height * 0.8
            and re.fullmatch(
                r"(?:\d+(?:st|nd|rd|th)\s+)?(?:Conference on|Proceedings of|IEEE.*Conference).*"
                r"\b(?:19|20)\d{2}.*",
                row["text"],
                re.I,
            )
        ]
        if len(footer_venues) == 1:
            venue = footer_venues[0].rstrip(".")
    year_match = re.search(r"\b((?:19|20)\d{2})\b", venue) if venue else None
    year = int(year_match[1]) if year_match else None
    if arxiv:
        if not venue:
            venue = "arXiv"
        stamp = metadata_text[arxiv.end() :].split("\n", 1)[0]
        stamp_year = re.search(r"\b((?:19|20)\d{2})\b", stamp)
        if year is None and stamp_year:
            year = int(stamp_year[1])
    scholarly = (
        ScholarlyMetadata(
            doi=doi,
            work_id="arxiv:" + arxiv[1].casefold() if arxiv else "",
            version=arxiv[2] or "" if arxiv else "",
            authors=list(authors),
            year=year,
            venue=venue,
        )
        if (doi or arxiv or venue)
        else None
    )
    return title, authors, scholarly
