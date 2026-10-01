"""Generate review files, recover one failed PDF and prove other bytes are unchanged."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from deep_research.models import Report
from deep_research.persistence.repository import RunDetail
from deep_research.workbench.delivery import docx, html, pdf
from deep_research.workbench.delivery_store import build_or_load, load_version, retry_format
from deep_research.workbench.publish import build_bundle

BODY = r"""# 综述交付格式恢复验收

## 摘要

本文件是合成的格式验收材料，不代表真实研究结论。验收重点是恢复失败的 PDF，
保持已经生成的 Word、HTML 与 Markdown 字节不变。

## 分析

数学排版示例：行内表达式 $x_i^2$，以及多行公式：

$$
\begin{aligned}y&=Ax+b\\z&=Cy+d\end{aligned}
$$

| 检查项 | 要求 |
|---|---|
| 成功格式 | 原文件字节保持一致 |
| 失败格式 | 从同一份定稿重新生成 |
| 历史版本 | 原下载与读取结果保持不变 |
| 重复请求 | 复用同一次重试结果 |

## 结论

格式恢复不重新调用模型，也不将渲染成功解释为研究结论已通过事实核验。
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    root = parser.parse_args().output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    detail = RunDetail(
        id="retry-acceptance",
        query="综述交付格式恢复验收",
        status="done",
        created_at=datetime.now(UTC),
        report=Report(query="格式验收", markdown=BODY, citations=[]),
    )
    with patch.object(pdf, "render_pdf", side_effect=pdf.PdfRenderError("模拟首次 PDF 导出失败")):
        first = build_or_load(detail, str(root), None, build_bundle)
    assert any(f["format"] == "pdf" and f["retryable"] for f in first.failures)
    first_bytes = {file.name: file.data for file in first.files}
    with (
        patch.object(html, "render_html", side_effect=AssertionError("HTML must be reused")),
        patch.object(docx, "render_docx", side_effect=AssertionError("Word must be reused")),
    ):
        second = retry_format(
            detail, str(root), None, first.content_version, "pdf", "retry-acceptance-1"
        )
    assert not second.failures
    assert all(
        next(f.data for f in second.files if f.name == name) == data
        for name, data in first_bytes.items()
    )
    assert load_version(detail, str(root), first.content_version).registry() == first.registry()
    repeated = retry_format(
        detail, str(root), None, first.content_version, "pdf", "retry-acceptance-1"
    )
    assert repeated.registry() == second.registry()
    recovered = root / "recovered"
    recovered.mkdir()
    for file in second.files:
        target = recovered / file.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(file.data)
    (root / "before.json").write_text(
        json.dumps(first.registry(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (root / "after.json").write_text(
        json.dumps(second.registry(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    summary = {
        "reused_files": len(first_bytes),
        "recovered_files": len(second.files),
        "retry_format": "pdf",
        "old_version_preserved": True,
        "idempotent": True,
        "model_calls": 0,
        "quality_status": second.status,
    }
    (root / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
