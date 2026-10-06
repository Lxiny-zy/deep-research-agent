"""Complete mindmap indexes and bounded, readable overview/branch PNG pages."""

from __future__ import annotations

import io
import json
from collections.abc import Iterator
from typing import Any

from .mindmap_contract import Mindmap, node_index, semantic_payload
from .mindmap_duplicates import duplicate_nodes
from .support import digest

MAX_PNG_PAGES = 80
PNG_DPI = 120


class MindmapImageLimit(ValueError):
    pass


def graph_version(raw: dict[str, Any]) -> str:
    return digest(
        {
            "model": semantic_payload(Mindmap.model_validate(raw)),
            "sources": raw.get("sources", []),
            "evidence": raw.get("evidence", []),
            "bibliography": raw.get("bibliography"),
            "review": raw.get("node_review"),
            "display_citations": _display_citations(raw),
            "link_display_citations": [
                link.get("display_citations", link.get("citations", []))
                for link in raw.get("links", [])
            ],
        }
    )


def _display_citations(raw: dict[str, Any]) -> dict[str, list[int]]:
    mapped: dict[str, list[int]] = {}

    def walk(nodes: list[dict], prefix: str = "") -> None:
        for i, node in enumerate(nodes):
            path = f"{prefix}.{i}" if prefix else str(i)
            mapped[path] = node.get("display_citations", node.get("citations", []))
            walk(node.get("children", []), path)

    walk(raw.get("branches", []))
    return mapped


def delivery_index(raw: dict[str, Any]) -> dict[str, Any]:
    model = Mindmap.model_validate(raw)
    nodes = node_index(model)
    displayed = _display_citations(raw)
    decisions = {d["unit_id"]: d for d in raw.get("node_review", {}).get("decisions", [])}
    return {
        "schema_version": 1,
        "mindmap_version": graph_version(raw),
        "root": model.root,
        "overview_scope": "top_level_branches_only",
        "complete_content": "完整节点说明保存在大纲与分支页；总览不代替完整内容。",
        "nodes": [
            {
                "path": key,
                "branch": key.split(".")[0],
                "label": node.label,
                "details": node.details,
                "kind": node.kind,
                "relation": node.relation,
                "citations": node.citations,
                "display_citations": displayed[key],
                "review_status": decisions.get(key, {}).get("verdict", "not_checked"),
                "evidence_ids": decisions.get(key, {}).get("evidence_ids", []),
            }
            for key, node in nodes.items()
        ],
        "branches": [
            {
                "path": str(i),
                "title": branch.label,
                "node_paths": [key for key in nodes if key == str(i) or key.startswith(f"{i}.")],
                "png_suffix_pattern": f"-mindmap-b{i + 1:03d}-p*.png",
                "diagram_groups": _diagram_groups(model, i),
            }
            for i, branch in enumerate(model.branches)
        ],
        "links": [
            {
                "id": f"L{i + 1}",
                **link.model_dump(mode="json"),
                "display_citations": raw.get("links", [])[i].get(
                    "display_citations", link.citations
                ),
                "review_status": decisions.get(f"link:{i}", {}).get("verdict", "not_checked"),
                "evidence_ids": decisions.get(f"link:{i}", {}).get("evidence_ids", []),
            }
            for i, link in enumerate(model.links)
        ],
        "duplicate_candidates": duplicate_nodes(model),
        "node_count": len(nodes),
        "review_status": raw.get("node_review", {}).get("status", "not_checked"),
        "review_input_hash": raw.get("node_review", {}).get("input_hash"),
        "render_limits": {"max_png_pages": MAX_PNG_PAGES, "dpi": PNG_DPI},
    }


def _diagram_groups(model: Mindmap, branch: int) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    prefix = str(branch)
    for path, node in node_index(model).items():
        if path != prefix and not path.startswith(prefix + "."):
            continue
        children = [f"{path}.{i}" for i in range(len(node.children))]
        if not children and path != prefix:
            continue
        batches = [children[i : i + 8] for i in range(0, len(children), 8)] or [[]]
        groups.extend({"parent": path, "children": batch} for batch in batches)
    return groups


def _short_label(label: str) -> str:
    if "$" in label or r"\(" in label or r"\[" in label:
        return "公式节点（完整公式见说明）"
    return label if len(label) <= 32 else label[:30] + "…"


def branch_markdown(
    raw: dict[str, Any],
    branch: int,
    *,
    diagram_names: list[str] | None = None,
) -> str:
    model = Mindmap.model_validate(raw)
    index = delivery_index(raw)
    selected = index["branches"][branch]
    nodes = node_index(model)
    displayed = _display_citations(raw)
    lines = [
        f"# {model.root} · 分支 {branch + 1}",
        "",
        "完整说明与必要条件保留如下；节点路径与导图索引一致。",
        f"内容版本：`{index['mindmap_version']}`",
        "",
    ]
    if diagram_names:
        lines.extend(
            [
                "## 分支图",
                "",
                "图块以父节点和直接子节点保持可读字号；相同路径指向同一节点。"
                "标签仅作索引，完整说明、公式、条件与引用见后文。",
                "",
            ]
        )
        for number, name in enumerate(diagram_names, 1):
            lines.extend([f"![分支 {branch + 1} 图块 {number}]({name})", ""])
    for path in selected["node_paths"]:
        node = nodes[path]
        parent = path.rsplit(".", 1)[0] if "." in path else "root"
        kind = {"claim": "结论", "question": "待研究", "concept": "概念"}[node.kind]
        cite = "".join(f"[{value}]" for value in displayed[path])
        lines += [
            f"## 节点 {path} · {node.label}",
            "",
            f"类型：{kind}；父节点：{parent}；关系：{node.relation}。 "
            + (node.details or node.label)
            + " "
            + cite,
            "",
        ]
    links = [
        link
        for link in index["links"]
        if branch
        in {
            int(link["source"].split(".")[0]),
            int(link["target"].split(".")[0]),
        }
    ]
    if links:
        lines += ["## 跨分支关联索引", ""]
        for link in links:
            citations = "".join(f"[{i}]" for i in link["display_citations"])
            lines.append(
                f"- {link['id']}：节点 {link['source']} —{link['relation']}→ "
                f"节点 {link['target']}；"
                f"对应分支 {int(link['source'].split('.')[0]) + 1} / "
                f"{int(link['target'].split('.')[0]) + 1}。{citations}"
            )
    printed = {citation for path in selected["node_paths"] for citation in displayed[path]}
    printed.update(citation for link in links for citation in link["display_citations"])
    if raw.get("bibliography"):
        from ..bibliography import Bibliography, cited_references

        references = [
            f"[{entry.index}] {entry.reference}"
            for entry in cited_references(Bibliography.model_validate(raw["bibliography"]))
            if entry.index in printed
        ]
    else:
        references = [
            f"[{source['index']}] {source.get('title') or source.get('url', '')}"
            for source in raw.get("sources", [])
            if source["index"] in printed
        ]
    if references:
        lines.extend(["", "## 参考来源", "", *references])
    return "\n".join(lines).strip() + "\n"


def _metadata(data: bytes, values: dict[str, Any], *, dpi: int = PNG_DPI) -> bytes:
    from PIL import Image, PngImagePlugin

    info = PngImagePlugin.PngInfo()
    info.add_itxt("deep-research-mindmap", json.dumps(values, ensure_ascii=False, sort_keys=True))
    output = io.BytesIO()
    with Image.open(io.BytesIO(data)) as image:
        image.save(output, format="PNG", pnginfo=info, dpi=(dpi, dpi))
    return output.getvalue()


def overview_pages(raw: dict[str, Any]) -> Iterator[tuple[str, bytes]]:
    from .delivery.mindmap import render_mindmap_png

    model = Mindmap.model_validate(raw)
    version = graph_version(raw)

    groups = [model.branches[i : i + 10] for i in range(0, len(model.branches), 10)] or [[]]
    if len(groups) > MAX_PNG_PAGES:
        raise MindmapImageLimit("总览页超过图片数量上限，请查看完整 HTML 与大纲")
    for page, group in enumerate(groups, 1):
        diagram = {
            "view": "overview",
            "root": _short_label(model.root) + f"\n总览 {page}/{len(groups)} · 仅一级分支",
            "branches": [
                {
                    "label": f"B{(page - 1) * 10 + i + 1} · {_short_label(node.label)}",
                    "children": [],
                }
                for i, node in enumerate(group)
            ],
        }
        data = render_mindmap_png(diagram)
        suffix = "-mindmap.png" if page == 1 else f"-mindmap-overview-{page:03d}.png"
        yield (
            suffix,
            _metadata(
                data,
                {
                    "mindmap_version": version,
                    "view": "overview",
                    "page": page,
                    "pages": len(groups),
                    "scope": "top_level_branches_only",
                    "branch_paths": [str((page - 1) * 10 + i) for i in range(len(group))],
                },
                dpi=100,
            ),
        )


def branch_png_pages(
    raw: dict[str, Any],
    branch: int,
    *,
    max_pages: int = MAX_PNG_PAGES,
) -> Iterator[tuple[str, bytes]]:
    import pymupdf

    from .delivery.mindmap import render_mindmap_png
    from .delivery.pdf import render_pdf

    if max_pages < 1:
        raise MindmapImageLimit("已达到图片页数上限，请查看完整 HTML 与大纲")
    index = delivery_index(raw)
    selected = index["branches"][branch]
    groups = selected["diagram_groups"]
    if len(groups) > MAX_PNG_PAGES:
        raise MindmapImageLimit("分支图块数量超过上限，请查看完整 HTML 与大纲")
    model_nodes = node_index(Mindmap.model_validate(raw))
    displayed = _display_citations(raw)
    images = {}
    for number, group in enumerate(groups, 1):
        parent = model_nodes[group["parent"]]
        diagram = {
            "view": "overview",
            "root": f"节点 {group['parent']} · {_short_label(parent.label)}",
            "branches": [
                {
                    "label": f"节点 {path} · {_short_label(model_nodes[path].label)}",
                    "kind": model_nodes[path].kind,
                    "relation": model_nodes[path].relation,
                    "display_citations": displayed[path],
                }
                for path in group["children"]
            ],
        }
        images[f"branch-diagram-{number}.png"] = render_mindmap_png(diagram)
    markdown = branch_markdown(raw, branch, diagram_names=list(images))
    pdf = render_pdf(markdown, title=f"分支 {branch + 1} · {selected['title']}", images=images)
    with pymupdf.open(stream=pdf, filetype="pdf") as document:
        if document.page_count > max_pages:
            raise MindmapImageLimit("分支超过剩余图片页数上限，请查看完整 HTML 与大纲")
        # Validate that every node heading was actually laid out before publishing any page.
        text = "\n".join(page.get_text() for page in document)
        if any(f"节点 {path} " not in text.replace("\n", " ") for path in selected["node_paths"]):
            raise ValueError("分支图片缺少节点定位标识，未交付不完整图片")
        for page_index, page in enumerate(document):
            pix = page.get_pixmap(dpi=PNG_DPI, alpha=False)
            if pix.width * pix.height > 2_500_000:
                raise ValueError("分支页超过受控像素上限")
            yield (
                f"-mindmap-b{branch + 1:03d}-p{page_index + 1:03d}.png",
                _metadata(
                    pix.tobytes("png"),
                    {
                        "mindmap_version": index["mindmap_version"],
                        "view": "branch",
                        "branch": str(branch),
                        "page": page_index + 1,
                        "pages": document.page_count,
                        "branch_node_paths": selected["node_paths"],
                        "page_node_starts": [
                            path
                            for path in selected["node_paths"]
                            if f"节点 {path} " in page.get_text().replace("\n", " ")
                        ],
                        "cross_link_ids": [
                            link["id"]
                            for link in index["links"]
                            if str(branch)
                            in {
                                link["source"].split(".")[0],
                                link["target"].split(".")[0],
                            }
                        ],
                    },
                ),
            )
