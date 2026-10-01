"""Layout math placeholders with Story, then replace them with vector PDF forms."""

from __future__ import annotations

import base64
import hashlib
import io
import math
import re
from html import escape
from typing import Any

from .math import MathAsset, MathRenderError, math_asset


def math_html(
    tex: str,
    *,
    display: bool = False,
    pdf: bool = False,
    assets: dict[bytes, MathAsset] | None = None,
    size: float | None = None,
) -> str:
    asset = math_asset(tex, display, size if pdf and size else (12 if display else 10.5))
    if pdf:
        from PIL import Image

        if assets is None:
            raise MathRenderError("公式排版缺少资源登记")
        if asset.width > 475 or asset.height > 680:
            raise MathRenderError("公式超过报告版心，请拆分为多行表达式")
        marker = hashlib.sha256(asset.svg.encode()).digest()[:24]
        assets[marker] = asset
        image = Image.frombytes("RGB", (8, 1), marker)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        data = base64.b64encode(buffer.getvalue()).decode()
        img = (
            f'<img class="math-image" id="math-{marker.hex()}" src="data:image/png;base64,{data}" '
            f'width="{math.ceil(asset.width)}" height="{math.ceil(asset.height)}" '
            f'style="vertical-align:-{asset.descent:.4f}pt">'
        )
        return (
            f'<div class="equation"><p style="text-align:center">{img}</p></div>'
            if display
            else img
        )
    svg = re.sub(r'(fill|stroke)="(?:black|#000(?:000)?)"', r'\1="currentColor"', asset.svg)
    svg = svg.replace("<svg ", '<svg role="img" aria-hidden="true" fill="currentColor" ', 1)
    visual = (
        f'<span class="math-svg" style="width:{asset.width / 10.5:.4f}em;'
        f"height:{asset.height / 10.5:.4f}em;"
        f'vertical-align:-{asset.descent / 10.5:.4f}em">{svg}</span>'
    )
    return (
        f'<span class="rendered-math{" display-math" if display else ""}" '
        f'aria-label="{escape(tex, quote=True)}">'
        f'<span class="math-accessible">{asset.mathml}</span>{visual}</span>'
    )


def place_vector_math(document: Any, assets: dict[bytes, MathAsset]) -> int:
    import pymupdf
    from PIL import Image

    if not assets:
        return 0
    identified: dict[int, MathAsset] = {}
    placements = []
    for page in document:
        for info in page.get_image_info(xrefs=True):
            xref = info.get("xref", 0)
            if not xref or info["width"] != 8 or info["height"] != 1:
                continue
            if xref not in identified:
                data = document.extract_image(xref)
                with Image.open(io.BytesIO(data["image"])) as image:
                    marker = image.convert("RGB").tobytes()
                if marker not in assets:
                    continue
                identified[xref] = assets[marker]
            asset = identified[xref]
            box = pymupdf.Rect(info["bbox"])
            if (
                abs(box.width - math.ceil(asset.width)) > 0.1
                or abs(box.height - math.ceil(asset.height)) > 0.1
            ):
                raise MathRenderError("公式被排版器意外缩放，未交付模糊或截断的公式")
            exact = pymupdf.Rect(box.x0, box.y1 - asset.height, box.x0 + asset.width, box.y1)
            placements.append((page.number, exact, asset))
        names = [item[7] for item in page.get_images(full=True) if item[0] in identified]
        for content in page.get_contents():
            stream = document.xref_stream(content)
            for name in names:
                stream = re.sub(rb"/" + re.escape(name.encode()) + rb"\s+Do\b", b"", stream)
            document.update_stream(content, stream)
        if names:
            page.clean_contents()
    if {asset.svg for _, _, asset in placements} != {asset.svg for asset in assets.values()}:
        raise MathRenderError("PDF 未保留全部公式位置，未生成有效公式")
    pdfs: dict[str, bytes] = {}
    for page_number, box, asset in placements:
        page = document[page_number]
        if asset.svg not in pdfs:
            with pymupdf.open(stream=asset.svg.encode(), filetype="svg") as svg_document:
                pdfs[asset.svg] = svg_document.convert_to_pdf()
        with pymupdf.open(stream=pdfs[asset.svg], filetype="pdf") as math_document:
            page.show_pdf_page(box, math_document)
        # ActualText gives a selectable, source-preserving alternative to the
        # vector paths. A tiny invisible anchor keeps the text inside the box.
        old = set(page.get_contents())
        page.insert_text((box.x0, box.y1 - 0.2), "x", fontsize=0.1, render_mode=3)
        actual = ("\ufeff" + asset.tex).encode("utf-16-be").hex().encode()
        for content in set(page.get_contents()) - old:
            stream = document.xref_stream(content)
            document.update_stream(
                content, b"/Span << /ActualText <" + actual + b"> >> BDC\n" + stream + b"\nEMC\n"
            )
    return len(placements)
