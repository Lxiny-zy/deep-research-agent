"""思维导图渲染：交互式 HTML（纯内联 SVG + 少量折叠脚本）与静态 PNG。

布局是确定性的径向树：根居中，一级分支按角度均分，子节点沿分支方向外扩。
同样的大纲永远得到同样的图，便于回放和比对。

HTML 里唯一的脚本是「点击分支折叠/展开」，不访问网络、不读取外部资源；
节点文字全部经 HTML 转义，大纲里即使混入标签也只会显示为文本。
"""

from __future__ import annotations

import io
import math
from html import escape
from typing import Any

_PALETTE = ("#1f5f8b", "#2e8b57", "#b5651d", "#7b4fa0", "#b03a48", "#3a7d7c", "#8a6d1f", "#4a5d9e")


def _layout(mindmap: dict[str, Any]) -> tuple[list[dict[str, Any]], list[tuple[int, int]]]:
    nodes: list[dict[str, Any]] = [
        {"label": mindmap.get("root", "主题"), "x": 0.0, "y": 0.0, "depth": 0, "branch": -1}
    ]
    edges: list[tuple[int, int]] = []
    branches = mindmap.get("branches", [])
    count = max(1, len(branches))
    for b_index, branch in enumerate(branches):
        angle = 2 * math.pi * b_index / count - math.pi / 2
        bx, by = math.cos(angle) * 260, math.sin(angle) * 200
        nodes.append(
            {"label": branch.get("label", ""), "x": bx, "y": by, "depth": 1, "branch": b_index}
        )
        branch_id = len(nodes) - 1
        edges.append((0, branch_id))
        children = branch.get("children", [])
        spread = min(math.pi / count * 0.9, 0.9)
        for c_index, child in enumerate(children):
            offset = (c_index - (len(children) - 1) / 2) * (
                spread / max(1, len(children) - 1) * 2 if len(children) > 1 else 0
            )
            radius = 470 + (c_index % 2) * 40
            cx, cy = math.cos(angle + offset) * radius, math.sin(angle + offset) * radius * 0.78
            nodes.append(
                {"label": child.get("label", ""), "x": cx, "y": cy, "depth": 2, "branch": b_index}
            )
            child_id = len(nodes) - 1
            edges.append((branch_id, child_id))
            for g_index, grand in enumerate(child.get("children", [])[:4]):
                gx = cx + math.cos(angle + offset) * (110 + 22 * g_index)
                gy = cy + math.sin(angle + offset) * (80 + 18 * g_index) + (g_index - 1.5) * 18
                nodes.append(
                    {
                        "label": grand.get("label", ""),
                        "x": gx,
                        "y": gy,
                        "depth": 3,
                        "branch": b_index,
                    }
                )
                edges.append((child_id, len(nodes) - 1))
    return nodes, edges


def _bounds(nodes: list[dict[str, Any]]) -> tuple[float, float, float, float]:
    xs = [n["x"] for n in nodes]
    ys = [n["y"] for n in nodes]
    return min(xs) - 140, min(ys) - 60, max(xs) + 140, max(ys) + 60


def render_svg(mindmap: dict[str, Any]) -> str:
    nodes, edges = _layout(mindmap)
    x0, y0, x1, y1 = _bounds(nodes)
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" '
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
        label = escape(str(node["label"])[:40])
        size = (22, 16, 13, 11)[min(depth, 3)]
        width = max(40, len(str(node["label"])[:40]) * size * 0.95 + 18)
        color = "#14222f" if depth == 0 else _PALETTE[node["branch"] % len(_PALETTE)]
        fill = color if depth <= 1 else "#ffffff"
        text_color = "#ffffff" if depth <= 1 else "#14222f"
        cls = "root" if depth == 0 else f"node d{depth} b{node['branch']}"
        x, y = node["x"], node["y"]
        parts.append(
            f'<g class="{cls}" data-branch="{node["branch"]}" data-depth="{depth}">'
            f'<rect x="{x - width / 2:.1f}" y="{y - size:.1f}" '
            f'width="{width:.1f}" height="{size * 2:.1f}" '
            f'rx="{size:.0f}" fill="{fill}" stroke="{color}" stroke-width="1.2"/>'
            f'<text x="{x:.1f}" y="{y + size * 0.36:.1f}" font-size="{size}" '
            f'text-anchor="middle" fill="{text_color}">{label}</text></g>'
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
        '<div class="hint">点击一级分支可折叠 / 展开其子节点</div></header>'
        f'<div class="canvas">{svg}</div>'
        f"<details><summary>文本大纲</summary>{outline}</details>"
        f"<script>{_SCRIPT}</script></body></html>\n"
    )


_CSS = """
body{margin:0;background:#f6f8fb;color:#14222f;
  font:15px/1.6 'Microsoft YaHei','Noto Sans SC',sans-serif}
header{padding:20px 28px;border-bottom:1px solid #dfe4ea;background:#fff}
h1{margin:0;font-size:22px}
.hint{color:#5b6675;font-size:13px}
.canvas{padding:16px;overflow:auto}
svg{width:100%;height:auto;min-width:760px;background:#fff;border:1px solid #dfe4ea;
  border-radius:12px}
g.collapsed rect{stroke-dasharray:4 3}
details{margin:16px 28px 40px;background:#fff;border:1px solid #dfe4ea;border-radius:10px;
  padding:12px 18px}
@media (prefers-color-scheme:dark){body{background:#12161c;color:#e6e9ee}
  header,details{background:#1a2029;border-color:#2c3440}}
"""


def _outline_html(mindmap: dict[str, Any]) -> str:
    def walk(nodes: list[dict[str, Any]]) -> str:
        if not nodes:
            return ""
        items = "".join(
            f"<li>{escape(str(node.get('label', '')))}{walk(node.get('children', []))}</li>"
            for node in nodes
        )
        return f"<ul>{items}</ul>"

    return walk(mindmap.get("branches", []))


def render_mindmap_png(mindmap: dict[str, Any]) -> bytes:
    """用 matplotlib 画同一套布局的静态 PNG（与 SVG 共用 ``_layout``）。"""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from ..analysis import _chart_font

    _chart_font()
    nodes, edges = _layout(mindmap)
    x0, y0, x1, y1 = _bounds(nodes)
    fig, ax = plt.subplots(figsize=(16, 16 * (y1 - y0) / max(1.0, x1 - x0)))
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
            str(node["label"])[:40],
            ha="center",
            va="center",
            fontsize=(18, 12, 9.5, 8)[min(depth, 3)],
            color="white" if depth <= 1 else "#14222f",
            bbox={
                "boxstyle": "round,pad=0.45",
                "facecolor": color if depth <= 1 else "white",
                "edgecolor": color,
            },
        )
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=110, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return buffer.getvalue()


__all__ = ["render_mindmap_html", "render_mindmap_png", "render_svg"]
