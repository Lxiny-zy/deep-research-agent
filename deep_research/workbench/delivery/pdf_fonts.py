"""Prepare CJK fonts for faithful PDF text extraction as well as visible glyphs."""

from __future__ import annotations

import io
from pathlib import Path


def story_font(path: str, family: str) -> tuple[bytes, str]:
    """Extract the selected CFF face without unencoded typographic substitutions.

    MuPDF Story can assign private-use Unicode to Noto CFF substitute glyphs
    (including ordinary digits). Keep its original cmap and glyph outlines,
    but disable GSUB for these fonts so copied numbers retain their characters.
    TrueType fonts keep their existing bytes and shaping behavior.
    """
    from fontTools.ttLib import TTCollection, TTFont

    file = Path(path)
    collection = TTCollection(path, lazy=True) if file.suffix.lower() == ".ttc" else None
    font = (
        next(
            (
                f
                for f in collection.fonts
                if family in {f["name"].getDebugName(1), f["name"].getDebugName(16)}
            ),
            collection.fonts[0],
        )
        if collection is not None
        else TTFont(path, lazy=True)
    )
    try:
        if font.sfntVersion != "OTTO":
            return file.read_bytes(), file.suffix.lower()
        if "GSUB" in font:
            del font["GSUB"]
        stream = io.BytesIO()
        font.save(stream)
        return stream.getvalue(), ".otf"
    finally:
        if collection is not None:
            collection.close()
        else:
            font.close()
