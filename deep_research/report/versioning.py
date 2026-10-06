"""Content identity shared by previews, export preconditions and offline copies."""

from __future__ import annotations

import hashlib
import json

from .document import ReportDocument


def document_version(document: ReportDocument) -> str:
    payload = document.model_dump(mode="json", exclude={"content_version"})
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def stamp_document(document: ReportDocument) -> ReportDocument:
    document.content_version = document_version(document)
    return document


def export_provenance(document: ReportDocument) -> str:
    validation = document.final_validation
    status = validation.support_status if validation else None
    return (
        f"> 文档版本：`{document.content_version or document_version(document)}`\n"
        f"> 核验状态：{status or '未完成核验'}；"
        f"核验范围：{validation.scope if validation else '未提供'}。\n"
        "> 格式范围：Markdown 保留正文、数据源表和证据附录；"
        "不包含交互定位与二进制附件，图形以数据源表表示。"
    )
