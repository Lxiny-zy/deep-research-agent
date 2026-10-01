"""思维导图渲染：交互式 HTML（纯内联 SVG + 少量折叠脚本）与静态 PNG。

布局是按子树高度分配空间的横向树；所有层级和完整标签共用同一布局，
SVG 与 PNG 不再裁掉深层节点或长文字。

HTML 里唯一的脚本是「点击分支折叠/展开」，不访问网络、不读取外部资源；
节点文字全部经 HTML 转义，大纲里即使混入标签也只会显示为文本。
"""

from __future__ import annotations

import io
import unicodedata
from html import escape
from typing import Any
from urllib.parse import urlsplit

_PALETTE = ("#1f5f8b", "#2e8b57", "#b5651d", "#7b4fa0", "#b03a48", "#3a7d7c", "#8a6d1f", "#4a5d9e")


def _label(node: dict[str, Any]) -> str:
    kind = {"claim": "【结论】", "question": "【待研究】"}.get(node.get("kind", ""), "")
    citations = "".join(f"[{int(i)}]" for i in node.get("citations", []))
    relation = str(node.get("relation", "包含"))
    relation = f"（{relation}）" if relation != "包含" else ""
    return f"{kind}{relation}{node.get('label', '')}{citations}"


def _wrap(text: str, columns: int = 32) -> list[str]:
    lines, line, width = [], "", 0
    for char in text:
        size = 2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1
        if char == "\n" or (line and width + size > columns):
            lines.append(line)
            line, width = "", 0
        if char != "\n":
            line += char
            width += size
    if line or not lines:
        lines.append(line)
    return lines


def _layout(mindmap: dict[str, Any]) -> tuple[list[dict[str, Any]], list[tuple[int, int]]]:
    nodes: list[dict[str, Any]] = []
    edges: list[tuple[int, int]] = []
    columns: dict[int, float] = {}

    def measure(raw: dict, depth: int, branch: int) -> int:
        label = _label(raw)
        lines = _wrap(label)
        size = 18 if depth == 0 else 14
        width = max(
            130,
            max(
                sum(2 if unicodedata.east_asian_width(c) in {"F", "W"} else 1 for c in line)
                for line in lines
            )
            * size
            * 0.56
            + 28,
        )
        height = len(lines) * size * 1.5 + 24
        index = len(nodes)
        node = {
            "label": label,
            "lines": lines,
            "width": width,
            "height": height,
            "size": size,
            "depth": depth,
            "branch": branch,
            "children": [],
            "citations": raw.get("citations", []),
        }
        nodes.append(node)
        columns[depth] = max(columns.get(depth, 0), width)
        for i, child in enumerate(raw.get("children", [])):
            target = measure(child, depth + 1, i if depth == 0 else branch)
            node["children"].append(target)
            edges.append((index, target))
        child_height = sum(nodes[c]["span"] for c in node["children"])
        child_height += max(0, len(node["children"]) - 1) * 24
        node["span"] = max(height, child_height)
        return index

    measure({"label": mindmap.get("root", "主题"), "children": mindmap.get("branches", [])}, 0, -1)
    offsets, left = {}, 0.0
    for depth, width in sorted(columns.items()):
        offsets[depth] = left + width / 2
        left += width + 100

    def place(index: int, top: float) -> None:
        node = nodes[index]
        node["x"], node["y"] = offsets[node["depth"]], top + node["span"] / 2
        total = sum(nodes[c]["span"] for c in node["children"])
        total += max(0, len(node["children"]) - 1) * 24
        cursor = top + (node["span"] - total) / 2
        for child in node["children"]:
            place(child, cursor)
            cursor += nodes[child]["span"] + 24

    place(0, 0)
    return nodes, edges


def _bounds(nodes: list[dict[str, Any]]) -> tuple[float, float, float, float]:
    return (
        min(n["x"] - n["width"] / 2 for n in nodes) - 24,
        min(n["y"] - n["height"] / 2 for n in nodes) - 24,
        max(n["x"] + n["width"] / 2 for n in nodes) + 24,
        max(n["y"] + n["height"] / 2 for n in nodes) + 24,
    )


def render_svg(mindmap: dict[str, Any]) -> str:
    nodes, edges = _layout(mindmap)
    x0, y0, x1, y1 = _bounds(nodes)
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{x1 - x0:.0f}" height="{y1 - y0:.0f}" '
        f'viewBox="{x0:.0f} {y0:.0f} {x1 - x0:.0f} {y1 - y0:.0f}" '
        'font-family="Microsoft YaHei, Noto Sans SC, sans-serif" role="img">'
    ]
    for parent, child in edges:
        a, b = nodes[parent], nodes[child]
        color = _PALETTE[b["branch"] % len(_PALETTE)]
        mx = (a["x"] + b["x"]) / 2
        path = f"M{a['x']:.1f},{a['y']:.1f} Q{mx:.1f},{a['y']:.1f} {b['x']:.1f},{b['y']:.1f}"
        # data-depth 取目标节点深度：折叠一级分支时，只隐藏通往更深节点的连线
        parts.append(
            f'<path class="edge b{b["branch"]}" data-depth="{b["depth"]}" d="{path}" '
            f'fill="none" stroke="{color}" stroke-opacity="0.55" '
            f'stroke-width="{3 - min(b["depth"], 2)}"/>'
        )
    for node in nodes:
        depth = node["depth"]
        size, width, height = node["size"], node["width"], node["height"]
        color = "#14222f" if depth == 0 else _PALETTE[node["branch"] % len(_PALETTE)]
        fill = color if depth <= 1 else "#ffffff"
        text_color = "#ffffff" if depth <= 1 else "#14222f"
        cls = "root" if depth == 0 else f"node d{depth} b{node['branch']}"
        x, y = node["x"], node["y"]
        first_y = y - (len(node["lines"]) - 1) * size * 0.75 + size * 0.35
        spans = "".join(
            f'<tspan x="{x:.1f}" y="{first_y + i * size * 1.5:.1f}">{escape(line)}</tspan>'
            for i, line in enumerate(node["lines"])
        )
        parts.append(
            f'<g class="{cls}" data-branch="{node["branch"]}" data-depth="{depth}">'
            f"<title>{escape(node['label'])}</title>"
            f'<rect x="{x - width / 2:.1f}" y="{y - height / 2:.1f}" '
            f'width="{width:.1f}" height="{height:.1f}" '
            f'rx="{size:.0f}" fill="{fill}" stroke="{color}" stroke-width="1.2"/>'
            f'<text x="{x:.1f}" y="{y + size * 0.36:.1f}" font-size="{size}" '
            f'text-anchor="middle" fill="{text_color}">{spans}</text></g>'
        )
    parts.append("</svg>")
    return "".join(parts)


_SCRIPT = """
document.querySelectorAll('g.node.d1').forEach(function(g){
  g.style.cursor='pointer';
  g.addEventListener('click',function(){
    var b=g.getAttribute('data-branch');var hide=!g.classList.contains('collapsed');
    g.classList.toggle('collapsed');
    document.querySelectorAll('.b'+b).forEach(function(el){
      if(el===g)return; if(el.getAttribute('data-depth')==='1')return;
      el.style.display=hide?'none':'';});
  });
});
"""


def render_mindmap_html(mindmap: dict[str, Any], *, title: str) -> str:
    svg = render_svg(mindmap)
    outline = _outline_html(mindmap)
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{escape(title)}</title><style>{_CSS}</style></head><body>"
        f"<header><h1>{escape(title)}</h1>"
        '<div class="hint">点击一级分支可折叠或展开；图中可滚动查看。'
        "结论带引用，待研究问题不代表已证实。</div></header>"
        f'<div class="canvas">{svg}</div>'
        f"<details><summary>完整大纲与引用</summary>{outline}</details>"
        f"<script>{_SCRIPT}</script></body></html>\n"
    )


_CSS = """
body{margin:0;background:#f6f8fb;color:#14222f;
  font:15px/1.6 'Microsoft YaHei','Noto Sans SC',sans-serif}
header{padding:20px 28px;border-bottom:1px solid #dfe4ea;background:#fff}
h1{margin:0;font-size:22px}
.hint{color:#5b6675;font-size:13px}
.canvas{padding:16px;overflow:auto}
svg{max-width:none;background:#fff;border:1px solid #dfe4ea;
  border-radius:12px}
g.collapsed rect{stroke-dasharray:4 3}
details{margin:16px 28px 40px;background:#fff;border:1px solid #dfe4ea;border-radius:10px;
  padding:12px 18px}
@media (prefers-color-scheme:dark){body{background:#12161c;color:#e6e9ee}
  header,details{background:#1a2029;border-color:#2c3440}}
"""


def _outline_html(mindmap: dict[str, Any]) -> str:
    registered = {int(item["index"]) for item in mindmap.get("sources", [])}

    def label(node: dict) -> str:
        text = escape(_label({**node, "citations": []}))
        for index in node.get("citations", []):
            text += (
                f'<a href="#source-{int(index)}">[{int(index)}]</a>'
                if int(index) in registered
                else f"[{int(index)}]"
            )
        return text

    def walk(nodes: list[dict[str, Any]]) -> str:
        if not nodes:
            return ""
        items = "".join(f"<li>{label(node)}{walk(node.get('children', []))}</li>" for node in nodes)
        return f"<ul>{items}</ul>"

    references = []
    for item in mindmap.get("sources", []):
        url = str(item.get("url", ""))
        title = escape(str(item.get("title", url)))
        parsed = urlsplit(url)
        if (
            parsed.scheme in {"http", "https"}
            and parsed.netloc
            and parsed.hostname != "workspace.invalid"
        ):
            title = (
                f'<a href="{escape(url, quote=True)}" rel="noreferrer" target="_blank">{title}</a>'
            )
        references.append(
            f'<li id="source-{int(item["index"])}">[{int(item["index"])}] {title}'
            + "".join(
                f"<blockquote>{escape(str(quote))}</blockquote>" for quote in item.get("quotes", [])
            )
            + "</li>"
        )
    return walk(mindmap.get("branches", [])) + (
        "<h2>参考来源</h2><ul>" + "".join(references) + "</ul>" if references else ""
    )


def render_mindmap_png(mindmap: dict[str, Any]) -> bytes:
    """用 matplotlib 画同一套布局的静态 PNG（与 SVG 共用 ``_layout``）。"""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from ..analysis import _chart_font

    _chart_font()
    nodes, edges = _layout(mindmap)
    x0, y0, x1, y1 = _bounds(nodes)
    width, height = x1 - x0, y1 - y0
    if max(width, height) > 30000 or width * height > 60_000_000:
        raise ValueError("导图超出单张图片可读尺寸，请使用完整交互 HTML 或按主题拆分")
    fig, ax = plt.subplots(figsize=(width / 100, height / 100), dpi=100)
    fig.subplots_adjust(0, 0, 1, 1)
    ax.set_xlim(x0, x1)
    ax.set_ylim(y1, y0)
    ax.axis("off")
    for parent, child in edges:
        a, b = nodes[parent], nodes[child]
        color = _PALETTE[b["branch"] % len(_PALETTE)]
        ax.plot(
            [a["x"], b["x"]],
            [a["y"], b["y"]],
            color=color,
            alpha=0.5,
            linewidth=2.2 - 0.6 * min(b["depth"], 2),
        )
    for node in nodes:
        depth = node["depth"]
        color = "#14222f" if depth == 0 else _PALETTE[node["branch"] % len(_PALETTE)]
        ax.text(
            node["x"],
            node["y"],
            "\n".join(node["lines"]),
            ha="center",
            va="center",
            fontsize=node["size"] * 0.72,
            linespacing=1.5,
            color="white" if depth <= 1 else "#14222f",
            bbox={
                "boxstyle": "round,pad=0.45",
                "facecolor": color if depth <= 1 else "white",
                "edgecolor": color,
            },
        )
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=100, facecolor="white")
    plt.close(fig)
    return buffer.getvalue()


__all__ = ["render_mindmap_html", "render_mindmap_png", "render_svg"]
