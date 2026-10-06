"""思维导图渲染：交互式 HTML（纯内联 SVG + 少量折叠脚本）与静态 PNG。

布局是按子树高度分配空间的横向树；所有层级和完整标签共用同一布局，
SVG 与 PNG 不再裁掉深层节点或长文字。

HTML 里唯一的脚本是「点击分支折叠/展开」，不访问网络、不读取外部资源；
节点文字全部经 HTML 转义，大纲里即使混入标签也只会显示为文本。
"""

from __future__ import annotations

import io
import re
import unicodedata
from html import escape
from typing import Any
from urllib.parse import urlsplit

_PALETTE = ("#1f5f8b", "#2e8b57", "#b5651d", "#7b4fa0", "#b03a48", "#3a7d7c", "#8a6d1f", "#4a5d9e")


def _rich_text(text: str) -> str:
    from .html import _inline_html
    from .markdown import _inlines, _parser

    tokens = _parser().parseInline(text)
    return _inline_html(_inlines(tokens[0])) if tokens else escape(text)


def _label(node: dict[str, Any]) -> str:
    kind = {"claim": "【结论】", "question": "【待研究】"}.get(node.get("kind", ""), "")
    citations = "".join(
        f"[{int(i)}]" for i in node.get("display_citations", node.get("citations", []))
    )
    relation = str(node.get("relation", "包含"))
    relation = f"（{relation}）" if relation != "包含" else ""
    return f"{kind}{relation}{node.get('label', '')}{citations}"


def _wrap(text: str, columns: int = 32) -> list[str]:
    lines, line, width = [], "", 0
    # Keep method names, decimal/scientific values, ranges and citation marks
    # intact. An unusually long token widens its node instead of losing text.
    tokens = re.findall(r"\r?\n|(?:\[\d+\])+|[A-Za-z0-9][A-Za-z0-9_+./%−–-]*|.", text)
    for token in tokens:
        size = sum(2 if unicodedata.east_asian_width(c) in {"F", "W"} else 1 for c in token)
        if token in {"\n", "\r\n"} or (line and width + size > columns):
            lines.append(line)
            line, width = "", 0
        if token not in {"\n", "\r\n"}:
            line += token
            width += size
    if line or not lines:
        lines.append(line)
    return lines


def _layout(mindmap: dict[str, Any]) -> tuple[list[dict[str, Any]], list[tuple[int, int]]]:
    nodes: list[dict[str, Any]] = []
    edges: list[tuple[int, int]] = []
    columns: dict[int, float] = {}

    def measure(raw: dict, depth: int, branch: int, path: str = "root") -> int:
        label = _label(raw)
        lines = _wrap(label)
        size = 18 if depth == 0 or mindmap.get("view") == "overview" else 14
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
            "path": path,
            "raw_label": str(raw.get("label", "")),
            "details": str(raw.get("details", "")),
        }
        nodes.append(node)
        columns[depth] = max(columns.get(depth, 0), width)
        for i, child in enumerate(raw.get("children", [])):
            child_path = str(i) if depth == 0 else f"{path}.{i}"
            target = measure(child, depth + 1, i if depth == 0 else branch, child_path)
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


def _cross_links(mindmap: dict[str, Any], nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_path = {n["path"]: n for n in nodes}
    right = max(n["x"] + n["width"] / 2 for n in nodes)
    result = []
    for index, link in enumerate(mindmap.get("links", [])):
        source, target = by_path.get(link["source"]), by_path.get(link["target"])
        if source is None or target is None or source is target:
            raise ValueError("跨分支关联的节点不存在或连接自身")
        cite = "".join(
            f"[{int(i)}]" for i in link.get("display_citations", link.get("citations", []))
        )
        result.append(
            {
                "source": source,
                "target": target,
                "number": index + 1,
                "lane": right + 40 + index * 28,
                "label": (
                    f"{source['raw_label']} —{link['relation']}→ {target['raw_label']} {cite}"
                ).rstrip(),
                "citations": link.get("citations", []),
            }
        )
    return result


def _cross_notes(
    nodes: list[dict[str, Any]],
    links: list[dict[str, Any]],
) -> tuple[tuple[float, float, float, float], list[dict[str, Any]]]:
    x0, y0, x1, y1 = _bounds(nodes)
    if not links:
        return (x0, y0, x1, y1), []
    x1 = max(x1, max(link["lane"] for link in links) + 35)
    notes = []
    cursor = y1 + 20
    for link in links:
        lines = _wrap(f"关联 {link['number']}：{link['label']}", max(32, int((x1 - x0 - 32) / 8)))
        notes.append({"x": x0 + 16, "y": cursor, "lines": lines})
        cursor += len(lines) * 20 + 10
    return (x0, y0, x1, cursor + 14), notes


def render_svg(mindmap: dict[str, Any]) -> str:
    nodes, edges = _layout(mindmap)
    links = _cross_links(mindmap, nodes)
    (x0, y0, x1, y1), notes = _cross_notes(nodes, links)
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{x1 - x0:.0f}" height="{y1 - y0:.0f}" '
        f'viewBox="{x0:.0f} {y0:.0f} {x1 - x0:.0f} {y1 - y0:.0f}" '
        'font-family="Microsoft YaHei, Noto Sans SC, sans-serif" role="img">'
    ]
    if links:
        parts.append(
            '<defs><marker id="cross-arrow" viewBox="0 0 10 10" refX="9" refY="5" '
            'markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
            '<path d="M 0 0 L 10 5 L 0 10 z" fill="#536579"/></marker></defs>'
        )
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
    for link in links:
        a, b, lane = link["source"], link["target"], link["lane"]
        path = (
            f"M{a['x'] + a['width'] / 2:.1f},{a['y']:.1f} L{lane:.1f},{a['y']:.1f} "
            f"L{lane:.1f},{b['y']:.1f} L{b['x'] + b['width'] / 2:.1f},{b['y']:.1f}"
        )
        binding = f'data-source="{a["path"]}" data-target="{b["path"]}"'
        parts.append(
            f'<path class="cross-edge" {binding} d="{path}" fill="none" stroke="#536579" '
            'stroke-width="1.6" stroke-dasharray="6 4" marker-end="url(#cross-arrow)">'
            f"<title>{escape(link['label'])}</title></path>"
        )
        parts.append(
            f'<text class="cross-edge-label" {binding} x="{lane + 4:.1f}" '
            f'y="{(a["y"] + b["y"]) / 2:.1f}" font-size="12" fill="#334155">'
            f"{link['number']}</text>"
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
        text_markup = (
            f'<foreignObject x="{x - width / 2 + 12:.1f}" y="{y - height / 2 + 10:.1f}" '
            f'width="{width - 24:.1f}" height="{height - 20:.1f}">'
            '<div xmlns="http://www.w3.org/1999/xhtml" '
            f'style="text-align:center;font-size:{size}px;'
            f'color:{text_color};line-height:1.5">{_rich_text(node["label"])}</div></foreignObject>'
            if "$" in node["label"] or r"\(" in node["label"]
            else f'<text x="{x:.1f}" y="{y + size * 0.36:.1f}" font-size="{size}" '
            f'text-anchor="middle" fill="{text_color}">{spans}</text>'
        )
        full_label = node["label"] + (" — " + node["details"] if node["details"] else "")
        parts.append(
            f'<g class="{cls}" data-branch="{node["branch"]}" data-depth="{depth}" '
            f'data-node-path="{node["path"]}">'
            f"<title>{escape(full_label)}</title>"
            f'<rect x="{x - width / 2:.1f}" y="{y - height / 2:.1f}" '
            f'width="{width:.1f}" height="{height:.1f}" '
            f'rx="{size:.0f}" fill="{fill}" stroke="{color}" stroke-width="1.2"/>'
            f"{text_markup}</g>"
        )
    for note in notes:
        for index, line in enumerate(note["lines"]):
            parts.append(
                f'<text x="{note["x"]:.1f}" y="{note["y"] + index * 20:.1f}" '
                f'font-size="14" fill="#334155">{escape(line)}</text>'
            )
    parts.append("</svg>")
    return "".join(parts)


_SCRIPT = """
var canvas=document.querySelector('.canvas');
var svg=canvas.querySelector('svg');
var box=svg.viewBox.baseVal;
var zoom=1;
function center(x,y){
  canvas.scrollLeft=(x-box.x)*zoom-canvas.clientWidth/2+16;
  canvas.scrollTop=(y-box.y)*zoom-canvas.clientHeight/2+16;
}
function scale(value,x,y){
  zoom=Math.max(0.01,Math.min(2,value));
  svg.style.width=(box.width*zoom)+'px';
  svg.style.height=(box.height*zoom)+'px';
  document.querySelector('[data-zoom-value]').textContent=Math.round(zoom*100)+'%';
  center(x,y);
}
function overview(){
  scale(Math.min((canvas.clientWidth-32)/box.width,(canvas.clientHeight-32)/box.height),
    box.x+box.width/2,box.y+box.height/2);
}
function currentCenter(){
  return [box.x+(canvas.scrollLeft+canvas.clientWidth/2-16)/zoom,
    box.y+(canvas.scrollTop+canvas.clientHeight/2-16)/zoom];
}
document.querySelectorAll('[data-zoom]').forEach(function(button){
  button.addEventListener('click',function(){
    var action=button.getAttribute('data-zoom');var c=currentCenter();
    if(action==='fit'){overview();return;}
    scale(action==='actual'?1:zoom*(action==='in'?1.25:0.8),c[0],c[1]);
  });
});
document.querySelectorAll('[data-focus-branch]').forEach(function(button){
  button.addEventListener('click',function(){
    var bounds=[];var b=button.getAttribute('data-focus-branch');
    svg.querySelectorAll('g.b'+b).forEach(function(g){
      if(g.style.display==='none')return;
      var r=g.getBBox();bounds.push(r);
    });
    if(!bounds.length)return;
    var x=Math.min.apply(null,bounds.map(function(r){return r.x;}));
    var y=Math.min.apply(null,bounds.map(function(r){return r.y;}));
    var right=Math.max.apply(null,bounds.map(function(r){return r.x+r.width;}));
    var bottom=Math.max.apply(null,bounds.map(function(r){return r.y+r.height;}));
    scale(Math.min(1,(canvas.clientWidth-48)/(right-x),(canvas.clientHeight-48)/(bottom-y)),
      (x+right)/2,(y+bottom)/2);
  });
});
document.querySelectorAll('g.node.d1').forEach(function(g){
  g.style.cursor='pointer';
  g.setAttribute('role','button');g.setAttribute('tabindex','0');
  g.setAttribute('aria-expanded','true');
  function toggle(){
    var b=g.getAttribute('data-branch');var hide=!g.classList.contains('collapsed');
    g.classList.toggle('collapsed');
    g.setAttribute('aria-expanded',hide?'false':'true');
    document.querySelectorAll('.b'+b).forEach(function(el){
      if(el===g)return; if(el.getAttribute('data-depth')==='1')return;
      el.style.display=hide?'none':'';});
    svg.querySelectorAll('.cross-edge,.cross-edge-label').forEach(function(el){
      var a=svg.querySelector('g[data-node-path="'+el.getAttribute('data-source')+'"]');
      var t=svg.querySelector('g[data-node-path="'+el.getAttribute('data-target')+'"]');
      el.style.display=(!a||!t||a.style.display==='none'||t.style.display==='none')?'none':'';
    });
  }
  g.addEventListener('click',toggle);
  g.addEventListener('keydown',function(event){
    if(event.key==='Enter'||event.key===' '){event.preventDefault();toggle();}
  });
});
function showNode(path){
  var item=document.getElementById('outline-'+path);
  if(!item)return;
  var content=item.querySelector('.node-content');
  if(content)document.getElementById('node-inspector').innerHTML=content.innerHTML;
}
svg.querySelectorAll('g.node').forEach(function(g){
  g.setAttribute('role','button');g.setAttribute('tabindex','0');
  g.setAttribute('aria-label','查看节点 '+g.getAttribute('data-node-path')+' 的完整说明');
  g.addEventListener('click',function(){showNode(g.getAttribute('data-node-path'));});
  g.addEventListener('keydown',function(event){
    if(event.key==='Enter'||event.key===' '){
      event.preventDefault();showNode(g.getAttribute('data-node-path'));
    }
  });
});
document.addEventListener('click',function(event){
  if(!event.target||!event.target.closest)return;
  var a=event.target.closest('a');var href=a&&a.getAttribute('href');
  if(href&&/^#(?:outline-|cite-|source-)/.test(href)){
    document.getElementById('complete-outline').open=true;
  }
});
overview();
"""


def render_mindmap_html(mindmap: dict[str, Any], *, title: str) -> str:
    import json

    from ..mindmap_delivery import delivery_index

    index = delivery_index(mindmap)
    version = index["mindmap_version"]
    candidates = "".join(
        f'<li><a href="#outline-{item["left"]}">节点 {item["left"]}</a> / '
        f'<a href="#outline-{item["right"]}">节点 {item["right"]}</a>：'
        f"{'相同文字' if item['exact'] else '疑似重复'}，相似度 {item['similarity']:.0%}；"
        "保留原节点，请核对条件与证据后决定。</li>"
        for item in index["duplicate_candidates"]
    )
    inspection = (
        '<details class="map-review"><summary>关系与重复检查</summary>'
        f"<p>记录状态：{escape(index['review_status'])}；{len(index['links'])} 条跨分支关联，"
        f"{len(index['duplicate_candidates'])} 组重复候选。</p>"
        + (
            f"<ul>{candidates}</ul>"
            if candidates
            else "<p>未发现现有规则覆盖的重复候选；这不代表人工验收通过。</p>"
        )
        + "<p>全部关系及其引用见完整大纲；相似度不用于自动合并或删除科学内容。</p></details>"
    )
    svg = render_svg(mindmap)
    outline = _outline_html(mindmap)
    branches = "".join(
        f'<button type="button" data-focus-branch="{i}">'
        f"{escape(str(branch.get('label', '')))}</button>"
        for i, branch in enumerate(mindmap.get("branches", []))
    )
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<meta name="deep-research-mindmap-version" content="{version}">'
        f"<title>{escape(title)}</title><style>{_CSS}</style></head><body>"
        f"<header><h1>{escape(title)}</h1>"
        '<div class="hint">选择分支定位，缩放查看完整标签；点击一级分支可折叠或展开。'
        "结论带引用，待研究问题不代表已证实。选择节点查看完整说明，"
        "PNG 总览只显示一级分支，完整内容见分支页。</div></header>"
        f'<p class="version">导图内容版本 {version}</p>'
        '<nav class="map-controls" aria-label="导图视图">'
        '<button type="button" data-zoom="fit">总览</button>'
        '<button type="button" data-zoom="actual">原始大小</button>'
        '<button type="button" data-zoom="out" aria-label="缩小导图">−</button>'
        '<output data-zoom-value aria-label="当前缩放">100%</output>'
        '<button type="button" data-zoom="in" aria-label="放大导图">+</button></nav>'
        f'<nav class="map-branches" aria-label="导图分支">{branches}</nav>'
        f'<div class="canvas">{svg}</div>'
        '<aside id="node-inspector" class="node-inspector" aria-live="polite">'
        '选择节点查看完整说明与引用。</aside>'
        f'{inspection}<details id="complete-outline"><summary>完整大纲与引用'
        f'（{index["node_count"]} 个节点）</summary>{outline}</details>'
        '<script type="application/json" id="mindmap-index">'
        + json.dumps(index, ensure_ascii=False).replace("<", "\\u003c")
        + "</script>"
        f"<script>{_SCRIPT}</script></body></html>\n"
    )


_CSS = """
body{margin:0;background:#f6f8fb;color:#14222f;
  font:15px/1.6 'Microsoft YaHei','Noto Sans SC',sans-serif}
header{padding:20px 28px;border-bottom:1px solid #dfe4ea;background:#fff}
h1{margin:0;font-size:22px;overflow-wrap:anywhere}
.hint{color:#5b6675;font-size:13px}
.map-controls,.map-branches{display:flex;flex-wrap:wrap;gap:8px;padding:10px 28px}
.map-controls{align-items:center}
.map-branches button{max-width:100%;box-sizing:border-box;overflow-wrap:anywhere}
button{border:1px solid #b9c9d6;border-radius:6px;padding:6px 10px;
  background:#fff;color:#18354a;font:inherit;cursor:pointer}
button:focus-visible,g[role=button]:focus-visible{outline:3px solid #3478a5;outline-offset:2px}
.node-inspector{margin:12px 28px;padding:14px 18px;border:1px solid #c6d5e3;
  background:#fff;border-radius:8px;overflow-x:auto}
.node-path,.version{color:#52616d;font-size:12px;overflow-wrap:anywhere}
.version{padding:0 28px}.node-details{white-space:normal;margin:5px 0}
.math-svg{display:inline-block}.math-svg svg{max-width:100%;border:0;background:transparent}
.math-accessible{display:none}
.canvas{padding:16px;overflow:auto;height:72vh;min-height:360px;box-sizing:border-box}
svg{max-width:none;background:#fff;border:1px solid #dfe4ea;
  border-radius:12px}
g.collapsed rect{stroke-dasharray:4 3}
details{margin:16px 28px 40px;background:#fff;border:1px solid #dfe4ea;border-radius:10px;
  padding:12px 18px;overflow-x:auto}
@media (prefers-color-scheme:dark){body{background:#12161c;color:#e6e9ee}
  header,details,.node-inspector{background:#1a2029;border-color:#2c3440}
  .hint,.version,.node-path{color:#a7b8ca}a{color:#92bde8}}
"""


def _outline_html(mindmap: dict[str, Any]) -> str:
    registered = {int(item["index"]) for item in mindmap.get("sources", [])}
    from ...bibliography import Bibliography, cited_references
    from .html import _citation_evidence_html

    catalog = (
        Bibliography.model_validate(mindmap["bibliography"])
        if mindmap.get("bibliography")
        else None
    )
    documents = {item.index: item.document for item in catalog.locations} if catalog else {}

    def label(node: dict) -> str:
        text = _rich_text(_label({**node, "citations": [], "display_citations": []}))
        if node.get("details"):
            text += '<div class="node-details">' + _rich_text(str(node["details"])) + "</div>"
        if catalog:
            grouped: dict[int, list[int]] = {}
            for index in node.get("citations", []):
                grouped.setdefault(documents.get(index, index), []).append(index)
            for document, locations in grouped.items():
                target = "-".join(map(str, locations))
                text += (
                    f'<a href="#cite-{target}">[{document}]</a>'
                    if all(index in registered for index in locations)
                    else f"[{document}]"
                )
            return text
        for index in node.get("citations", []):
            text += (
                f'<a href="#source-{int(index)}">[{int(index)}]</a>'
                if int(index) in registered
                else f"[{int(index)}]"
            )
        return text

    def walk(nodes: list[dict[str, Any]], prefix: str = "") -> str:
        if not nodes:
            return ""
        items = ""
        for i, node in enumerate(nodes):
            path = f"{prefix}.{i}" if prefix else str(i)
            items += (
                f'<li id="outline-{path}"><div class="node-content">'
                f'<span class="node-path">节点 {path} · </span>'
                f"{label(node)}</div>{walk(node.get('children', []), path)}</li>"
            )
        return f"<ul>{items}</ul>"

    links = _cross_links(mindmap, _layout(mindmap)[0])
    cross_outline = (
        "<h2>跨分支关联</h2><ol>"
        + "".join(
            "<li>"
            + label(
                {
                    "label": (
                        f"{item['source']['raw_label']} —{raw['relation']}→ "
                        f"{item['target']['raw_label']}"
                    ),
                    "citations": item["citations"],
                }
            )
            + "</li>"
            for item, raw in zip(links, mindmap.get("links", []), strict=True)
        )
        + "</ol>"
        if links
        else ""
    )

    if catalog:
        outline = walk(mindmap.get("branches", [])) + cross_outline
        bibliography = (
            "<h2>参考文献</h2><ol>"
            + "".join(
                f'<li value="{entry.index}">{escape(entry.reference)}</li>'
                for entry in cited_references(catalog)
            )
            + "</ol>"
        )
        return (
            outline
            + bibliography
            + _citation_evidence_html(outline, catalog, mindmap.get("evidence", []))
        )
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
    return (
        walk(mindmap.get("branches", []))
        + cross_outline
        + ("<h2>参考来源</h2><ul>" + "".join(references) + "</ul>" if references else "")
    )


def render_mindmap_png(mindmap: dict[str, Any]) -> bytes:
    """用 matplotlib 画同一套布局的静态 PNG（与 SVG 共用 ``_layout``）。"""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from ..analysis import _chart_font

    _chart_font()
    nodes, edges = _layout(mindmap)
    links = _cross_links(mindmap, nodes)
    (x0, y0, x1, y1), notes = _cross_notes(nodes, links)
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
    for link in links:
        a, b, lane = link["source"], link["target"], link["lane"]
        ax.plot(
            [a["x"] + a["width"] / 2, lane, lane],
            [a["y"], a["y"], b["y"]],
            color="#536579",
            linestyle="--",
            linewidth=1.2,
        )
        ax.annotate(
            "",
            xy=(b["x"] + b["width"] / 2, b["y"]),
            xytext=(lane, b["y"]),
            arrowprops={"arrowstyle": "->", "color": "#536579", "linestyle": "--"},
        )
        ax.text(lane + 4, (a["y"] + b["y"]) / 2, str(link["number"]), fontsize=9, color="#334155")
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
    for note in notes:
        for index, line in enumerate(note["lines"]):
            ax.text(
                note["x"],
                note["y"] + index * 20,
                line,
                fontsize=10,
                ha="left",
                va="baseline",
                color="#334155",
            )
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=100, facecolor="white")
    plt.close(fig)
    return buffer.getvalue()


__all__ = ["render_mindmap_html", "render_mindmap_png", "render_svg"]
