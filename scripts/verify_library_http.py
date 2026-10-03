"""Independently check saved library HTTP acceptance without calling a model."""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path
from urllib.parse import urlsplit


def verify(directory: Path) -> dict:
    def read(name: str):
        return json.loads((directory / name).read_text(encoding="utf-8"))

    inputs, run, registry, qa = (
        read("inputs.json"),
        read("run-detail.json"),
        read("deliverables.json"),
        read("qa.json"),
    )
    identity = read("run.json")
    source_ids = {item["source_id"] for item in inputs["sources"]}
    issues = []

    def citations_are_local(citations):
        if not citations:
            return False
        for citation in citations:
            url = urlsplit(citation)
            if url.hostname != "workspace.invalid" or url.path not in {
                f"/sources/{source_id}" for source_id in source_ids
            }:
                return False
        return True

    if run["status"] != "done" or run["id"] != identity["run_id"]:
        issues.append("研究任务未正常完成或标识不一致")
    if run.get("project_id") != identity["project_id"]:
        issues.append("研究任务未绑定本次资料库项目")
    report = run.get("report") or {}
    if not report.get("markdown", "").strip() or not citations_are_local(report.get("citations")):
        issues.append("研究报告为空或引用不属于本次导入资料")
    if registry["status"] == "fail":
        issues.append("交付登记未通过")
    required = {"md", "html", "docx", "pdf"}
    if not required <= {item["format"] for item in registry["items"]}:
        issues.append("缺少承诺的报告格式")
    downloads = (directory / "downloads").resolve()
    for item in registry["items"]:
        path = (downloads / item["name"]).resolve()
        if not path.is_relative_to(downloads) or not path.is_file():
            issues.append(f"下载文件缺失或路径无效：{item['name']}")
            continue
        raw = path.read_bytes()
        if len(raw) != item["size"] or hashlib.sha256(raw).hexdigest() != item["sha256"]:
            issues.append(f"下载文件与登记哈希不一致：{item['name']}")
        if item["format"] in {"docx", "xlsx", "pptx"}:
            with zipfile.ZipFile(path) as archive:
                if archive.testzip() is not None:
                    issues.append(f"Office 文件结构损坏：{item['name']}")
        elif item["format"] == "pdf":
            import pymupdf

            with pymupdf.open(path) as pdf:
                if not len(pdf) or not any(page.get_text().strip() for page in pdf):
                    issues.append("PDF 无可读取正文")
    messages = qa.get("messages", [])
    if not messages:
        issues.append("问答没有持久化消息")
    for message in messages:
        if message.get("status") != "done" or not message.get("answer", "").strip():
            issues.append("问答未正常完成")
        if message.get("request_payload", {}).get("project_id") != identity["project_id"]:
            issues.append("问答未使用同一个资料库项目")
        if not citations_are_local(message.get("citations")):
            issues.append("问答引用为空或不属于本次导入资料")
    return {
        "status": "pass" if not issues else "fail",
        "issues": issues,
        "run_id": run["id"],
        "source_count": len(source_ids),
        "downloaded_files": len(registry["items"]),
        "qa_messages": len(messages),
        "scope": "Saved HTTP state, source ownership, file hashes and formats; no model call",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    result = verify(args.directory)
    (args.directory / "independent-check.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result["status"] == "pass" else 1)
