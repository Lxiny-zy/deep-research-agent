"""Bound source-page images from task-owned PDFs; no network or model crop guesses."""

from __future__ import annotations

import base64
import hashlib
import re
from typing import Any
from urllib.parse import parse_qs, urlparse

from ..guardrails import report_eligible
from .attachments import (
    ATTACHMENT_URL_PREFIX,
    ImageRegion,
    attachments_from_scratch,
    figure_label_key,
    load_original,
)
from .support import evidence_id

MAX_SOURCE_PDF_BYTES = 4 * 1024 * 1024
MAX_SOURCE_PDF_TOTAL = 4 * 1024 * 1024
MAX_SELECTED_IMAGES = 4
_FIGURE = re.compile(r"(?:\bFig(?:ure)?\.?\s*\d+[a-z]?\b|图\s*\d+[a-z]?)", re.I)


def source_image_catalog(results: list[Any], scratch: dict[str, Any]) -> list[dict[str, Any]]:
    attachments = {
        a.id: a for a in attachments_from_scratch(scratch) if a.kind == "pdf" and a.stored
    }
    catalog = []
    for result in results:
        for finding in result.findings:
            if not report_eligible(finding) or not finding.source_url.startswith(
                ATTACHMENT_URL_PREFIX
            ):
                continue
            parsed = urlparse(finding.source_url)
            attachment = attachments.get(parsed.path.rsplit("/", 1)[-1])
            try:
                ordinal = int(parse_qs(parsed.query)["chunk"][0]) - 1
            except (KeyError, ValueError, IndexError):
                continue
            if attachment is None:
                continue
            chunk = next((c for c in attachment.chunks if c.ordinal == ordinal), None)
            labels = list(dict.fromkeys(_FIGURE.findall(finding.evidence_quote)))
            if chunk is None or chunk.page is None or not labels:
                continue
            catalog.append(
                {
                    "finding_id": evidence_id(finding),
                    "attachment_id": attachment.id,
                    "source_url": finding.source_url,
                    "filename": attachment.filename,
                    "page": chunk.page,
                    "figure_labels": labels,
                    "quote": finding.evidence_quote,
                    "user_regions": [
                        region.model_dump(mode="json") for region in attachment.image_regions
                    ],
                }
            )
    return catalog[:40]


def freeze_selected_images(
    deck: Any, results: list[Any], scratch: dict[str, Any], settings: Any
) -> dict[str, Any]:
    """Read only task-owned originals and freeze bytes before delivery/retries.

    Page numbers come from parsed attachments, not model assertions. Region
    selection is accepted only from the user-submitted attachment contract.
    The model can choose a bound figure, but cannot invent crop coordinates.
    """
    catalog = {c["finding_id"]: c for c in source_image_catalog(results, scratch)}
    selected = [s.image for s in deck.slides if s.image is not None]
    if len(selected) > MAX_SELECTED_IMAGES:
        raise ValueError("一份幻灯片最多选择四张原文图页，请缩小范围")
    documents: dict[str, Any] = {}
    images: dict[str, Any] = {}
    total = 0
    for image in selected:
        source = catalog.get(image.finding_id)
        if source is None or image.figure_label not in source["figure_labels"]:
            raise ValueError("原文图片必须从当前任务已登记的图号和发现中选择")
        document_id = source["attachment_id"]
        if document_id not in documents:
            raw = load_original(settings, document_id)
            if raw is None:
                raise ValueError("所选图示的PDF原件不可用，请重新上传")
            total += len(raw)
            if len(raw) > MAX_SOURCE_PDF_BYTES or total > MAX_SOURCE_PDF_TOTAL:
                raise ValueError("原文图页的冻结材料超过容量，请上传包含所需页的较小PDF")
            documents[document_id] = {
                "sha256": hashlib.sha256(raw).hexdigest(),
                "bytes": base64.b64encode(raw).decode("ascii"),
                "size": len(raw),
            }
        image.page = source["page"]
        image.source_name = source["filename"]
        image.source_url = source["source_url"]
        regions = [
            ImageRegion.model_validate(region) for region in source["user_regions"]
            if figure_label_key(region["figure_label"]) == figure_label_key(image.figure_label)
        ]
        if len(regions) > 1:
            raise ValueError("同一原文图号有多个用户区域，请明确保留一个来源页")
        selected_region = regions[0] if regions else None
        image.snapshot = "user_region" if selected_region else "full_page"
        if selected_region:
            image.page = selected_region.page
        bounds = selected_region.bounds if selected_region else None
        page_identity = hashlib.sha256(
            f"{documents[document_id]['sha256']}:{image.page}".encode()
        ).hexdigest()
        identity = f"{page_identity}:{image.figure_label}:{bounds}".encode()
        image.asset_id = "source-" + hashlib.sha256(identity).hexdigest()[:20]
        images[image.asset_id] = {
            **source,
            "figure_label": image.figure_label,
            "document_id": document_id,
            "id": image.asset_id,
            "page": image.page,
            "snapshot": image.snapshot,
            "bounds": list(bounds) if bounds else None,
            "selection_origin": "user" if bounds else None,
            "page_identity_sha256": page_identity,
        }
    return {"version": 2, "documents": documents, "images": images}


def _selection(registry: dict[str, Any], record: dict[str, Any]) -> ImageRegion | None:
    snapshot = record.get("snapshot", "full_page")
    if snapshot not in {"full_page", "user_region"}:
        raise ValueError("未知的原文图片范围")
    region = None
    if snapshot == "user_region":
        if record.get("selection_origin") != "user":
            raise ValueError("图片裁剪区域没有用户选择依据")
        region = ImageRegion.model_validate({
            "page": record["page"], "figure_label": record["figure_label"],
            "bounds": record.get("bounds"),
        })
    if registry.get("version") == 2 or region:
        document = registry["documents"][record["document_id"]]
        expected_page = hashlib.sha256(
            f"{document['sha256']}:{record['page']}".encode()
        ).hexdigest()
        bounds = region.bounds if region else None
        expected_id = "source-" + hashlib.sha256(
            f"{expected_page}:{record['figure_label']}:{bounds}".encode()
        ).hexdigest()[:20]
        if record.get("page_identity_sha256") != expected_page or record["id"] != expected_id:
            raise ValueError("原文图片的来源页或选择区域已变化")
    return region


def validate_image_registry(registry: dict[str, Any], image: Any) -> dict[str, Any]:
    if registry.get("version") not in {1, 2}:
        raise ValueError("原文图片缺少冻结素材登记")
    record = registry.get("images", {}).get(image.asset_id)
    if not isinstance(record, dict) or any(
        [
            record.get("finding_id") != image.finding_id,
            record.get("figure_label") != image.figure_label,
            record.get("page") != image.page,
            record.get("source_url") != image.source_url,
            record.get("snapshot", "full_page") != image.snapshot,
        ]
    ):
        raise ValueError("原文图片与冻结来源、图号或页码不一致")
    _selection(registry, record)
    return record


def render_registered_page(registry: dict[str, Any], asset_id: str) -> bytes:
    """Called inside the existing bounded rendering process, never in a model step."""
    record = registry["images"][asset_id]
    document = registry["documents"][record["document_id"]]
    if len(document["bytes"]) > (MAX_SOURCE_PDF_BYTES * 4 // 3 + 8):
        raise ValueError("冻结PDF超过容量")
    raw = base64.b64decode(document["bytes"], validate=True)
    if len(raw) != document["size"] or hashlib.sha256(raw).hexdigest() != document["sha256"]:
        raise ValueError("原文PDF与冻结哈希不一致")
    region = _selection(registry, record)
    import fitz

    with fitz.open(stream=raw, filetype="pdf") as pdf:
        page_number = record["page"]
        if not isinstance(page_number, int) or not 1 <= page_number <= len(pdf):
            raise ValueError("原文图页不存在")
        page = pdf[page_number - 1]
        def normalize(text: str) -> str:
            return re.sub(r"[\s.]", "", text).casefold()
        if normalize(record["figure_label"]) not in normalize(page.get_text("text")):
            raise ValueError("所选页未确认原文图号，不能猜测邻页或裁剪区域")
        width, height = page.rect.width, page.rect.height
        if width <= 0 or height <= 0 or width * height > 5_000_000:
            raise ValueError("原文页面尺寸超过处理范围")
        clip = page.rect
        if region is not None:
            x0, y0, x1, y1 = region.bounds
            clip = fitz.Rect(
                page.rect.x0 + x0 * width, page.rect.y0 + y0 * height,
                page.rect.x0 + x1 * width, page.rect.y0 + y1 * height,
            )
        # PyMuPDF's clip uses the visible page coordinates, including rotation.
        # Render the region at its own resolution; do not enlarge a low-res crop.
        scale = min(
            4.0 if region is not None else 2.0, 2200 / max(clip.width, clip.height),
        )
        pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip, alpha=False)
        return bytes(pixmap.tobytes("png"))
