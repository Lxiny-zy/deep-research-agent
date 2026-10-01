"""块树 → 自包含 HTML（CSS 全内联、图片 base64 内联、无脚本）。

同一个渲染函数有两种用途：

* ``standalone=True``：交付给用户的阅读版 HTML，带目录、打印样式、暗色适配；
* ``standalone=False``：交给 PyMuPDF 排版 PDF 的精简版（PyMuPDF 的 Story 只支持
  CSS 子集，不认识 flex / 变量 / 媒体查询），保证两份输出来自同一棵块树。
"""

from __future__ import annotations

import base64
from collections.abc import Mapping
from html import escape

from .markdown import Block, Inline, parse_blocks, plain

_READING_CSS = """
:root{--ink:#1c2430;--muted:#5b6675;--line:#dfe4ea;--accent:#1f5f8b;
  --bg:#ffffff;--soft:#f5f7fa}
@media (prefers-color-scheme:dark){:root{--ink:#e6e9ee;--muted:#9aa4b2;
  --line:#2c3440;--accent:#7cb7e3;--bg:#12161c;--soft:#1a2029}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:16px/1.75 "Noto Sans SC","Microsoft YaHei","PingFang SC",system-ui,sans-serif}
main{max-width:860px;margin:0 auto;padding:48px 24px 96px}
header.doc{border-bottom:1px solid var(--line);margin-bottom:32px;padding-bottom:20px}
header.doc .kicker{color:var(--accent);font-size:13px;letter-spacing:.08em;
  text-transform:uppercase}
header.doc .meta{color:var(--muted);font-size:13px;margin-top:8px}
nav.toc{background:var(--soft);border:1px solid var(--line);border-radius:10px;
  padding:14px 20px;margin:0 0 32px}
nav.toc strong{display:block;margin-bottom:6px}
nav.toc ol{margin:0;padding-left:20px}
h1{font-size:30px;line-height:1.3;margin:.2em 0}
h2{font-size:22px;margin:1.8em 0 .6em;padding-bottom:.3em;border-bottom:1px solid var(--line)}
h3{font-size:18px;margin:1.4em 0 .5em}
p,li{margin:.5em 0}
a{color:var(--accent)}
table{border-collapse:collapse;width:100%;margin:1em 0;font-size:14px;
  display:block;overflow-x:auto}
th,td{border:0;padding:6px 10px;text-align:left;vertical-align:top}
th{border-top:1.5px solid var(--ink);border-bottom:1px solid var(--ink)}
tbody tr:last-child td{border-bottom:1.5px solid var(--ink)}
code{font-family:"JetBrains Mono",Consolas,monospace;font-size:.9em;
  background:var(--soft);padding:.1em .35em;border-radius:4px}
pre{background:var(--soft);border:1px solid var(--line);border-radius:8px;
  padding:12px 16px;overflow-x:auto}
pre code{background:none;padding:0}
blockquote{margin:1em 0;padding:.4em 1em;border-left:3px solid var(--accent);
  color:var(--muted);background:var(--soft)}
figure{margin:1.4em 0;text-align:center}
figure img{max-width:100%;height:auto;border:1px solid var(--line);border-radius:6px}
figcaption{color:var(--muted);font-size:13px;margin-top:6px}
.math{font-family:"Latin Modern Math","Cambria Math",serif;font-style:italic}
.math-block{display:block;text-align:center;margin:1em 0}
sup.cite a{text-decoration:none}
@media print{nav.toc{display:none}main{max-width:none;padding:0}a{color:inherit}}
"""

# PyMuPDF Story 支持的 CSS 子集：不用变量、flex、媒体查询。
_PDF_CSS = """
body{margin:0;font-family:sans-serif;font-size:10.5pt;line-height:1.6;color:#1c2430}
h1{font-size:20pt;margin:0 0 8pt 0;color:#111}
h2{font-size:14pt;margin:16pt 0 6pt 0;color:#111;page-break-after:avoid}
h3{font-size:12pt;margin:12pt 0 4pt 0;page-break-after:avoid}
p{margin:6pt 0;orphans:2;widows:2}
li{margin:2pt 0}
table{border-collapse:collapse;width:100%;margin:8pt 0}
th,td{border:0;padding:4pt 5pt;font-size:9.5pt;vertical-align:top;text-align:left}
th{border-top:1.2pt solid #111;border-bottom:0.6pt solid #111}
tbody tr:last-child td{border-bottom:1.2pt solid #111}
thead{display:table-header-group}
tr{page-break-inside:avoid}
pre{font-family:monospace;font-size:9pt;background-color:#f5f7fa;padding:6pt}
code{font-family:monospace;font-size:9.5pt}
blockquote{margin:6pt 0 6pt 12pt;color:#5b6675}
.meta{color:#5b6675;font-size:9pt}
.caption{color:#5b6675;font-size:9pt;text-align:center}
.figure{page-break-inside:avoid;margin:10pt 0}
.math{font-style:italic}
"""


def _inline_html(inlines: list[Inline]) -> str:
    out: list[str] = []
    for item in inlines:
        text = escape(item.text)
        if item.math:
            out.append(f'<span class="math">{text}</span>')
            continue
        if item.code:
            text = f"<code>{text}</code>"
        if item.bold:
            text = f"<strong>{text}</strong>"
        if item.italic:
            text = f"<em>{text}</em>"
        if item.href and item.href.startswith(("http://", "https://")):
            text = f'<a href="{escape(item.href, quote=True)}">{text}</a>'
        out.append(text)
    return "".join(out).replace("\n", "<br>")


def _list_html(block: Block) -> str:
    html: list[str] = []
    stack: list[str] = []
    for item in block.items:
        tag = "ol" if item.ordered else "ul"
        while len(stack) > item.depth + 1:
            html.append(f"</{stack.pop()}>")
        while len(stack) < item.depth + 1:
            stack.append(tag)
            html.append(f"<{tag}>")
        html.append(f"<li>{_inline_html(item.inlines)}</li>")
    while stack:
        html.append(f"</{stack.pop()}>")
    return "".join(html)


def _image_src(src: str, images: Mapping[str, bytes]) -> str | None:
    data = images.get(src) or images.get(src.rsplit("/", 1)[-1])
    if data is None:
        return None
    mime = "image/png" if data[:4] == b"\x89PNG" else "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def blocks_html(
    blocks: list[Block],
    *,
    images: Mapping[str, bytes] | None = None,
    pdf: bool = False,
    anchors: bool = True,
) -> str:
    images = images or {}
    parts: list[str] = []
    heading_index = 0
    for block in blocks:
        if block.kind == "heading":
            level = min(max(block.level, 1), 4)
            heading_index += 1
            anchor = f' id="h-{heading_index}"' if anchors and not pdf else ""
            parts.append(f"<h{level}{anchor}>{_inline_html(block.inlines)}</h{level}>")
        elif block.kind == "paragraph":
            if len(block.inlines) == 1 and block.inlines[0].math:
                parts.append(f'<p class="math math-block">{escape(block.inlines[0].text)}</p>')
            else:
                parts.append(f"<p>{_inline_html(block.inlines)}</p>")
        elif block.kind == "quote":
            parts.append(f"<blockquote>{_inline_html(block.inlines)}</blockquote>")
        elif block.kind == "list":
            parts.append(_list_html(block))
        elif block.kind == "code":
            parts.append(f"<pre><code>{escape(block.text)}</code></pre>")
        elif block.kind == "rule":
            parts.append("<hr>")
        elif block.kind == "table" and block.rows:
            head, *body = block.rows
            rows = ["<tr>" + "".join(f"<th>{_inline_html(c)}</th>" for c in head) + "</tr>"]
            rows += [
                "<tr>" + "".join(f"<td>{_inline_html(c)}</td>" for c in row) + "</tr>"
                for row in body
            ]
            parts.append(
                "<table><thead>"
                + rows[0]
                + "</thead><tbody>"
                + "".join(rows[1:])
                + "</tbody></table>"
            )
        elif block.kind == "image":
            src = _image_src(block.src, images)
            caption = escape(block.text)
            if src is None:
                parts.append(f'<p class="caption">［图缺失：{caption}］</p>')
            elif pdf:
                parts.append(
                    f'<div class="figure"><p><img src="{src}" width="440"></p>'
                    f'<p class="caption">{caption}</p></div>'
                )
            else:
                parts.append(
                    f'<figure><img src="{src}" alt="{caption}">'
                    f"<figcaption>{caption}</figcaption></figure>"
                )
    return "\n".join(parts)


def render_html(
    markdown: str,
    *,
    title: str,
    kicker: str = "研究交付",
    meta: str = "",
    images: Mapping[str, bytes] | None = None,
) -> str:
    """交付用自包含 HTML：一份文件，离线可读，无任何外链资源与脚本。"""
    blocks = parse_blocks(markdown)
    # 正文若以一级标题开头，把它提升为文档标题，避免与页眉重复出现。
    # 必须先剥离再编号：目录锚点与 blocks_html 的标题序号出自同一份块列表。
    if blocks and blocks[0].kind == "heading" and blocks[0].level == 1:
        title = plain(blocks[0].inlines) or title
        blocks = blocks[1:]
    headings = [(i + 1, b) for i, b in enumerate(b for b in blocks if b.kind == "heading")]
    toc_items = [
        f'<li><a href="#h-{index}">{escape(plain(block.inlines))}</a></li>'
        for index, block in headings
        if block.level == 2
    ]
    toc = (
        '<nav class="toc" aria-label="目录"><strong>目录</strong>'
        f"<ol>{''.join(toc_items)}</ol></nav>"
        if len(toc_items) >= 3
        else ""
    )
    body = blocks_html(blocks, images=images)
    return (
        '<!doctype html>\n<html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{escape(title)}</title><style>{_READING_CSS}</style></head><body><main>"
        f'<header class="doc"><div class="kicker">{escape(kicker)}</div><h1>{escape(title)}</h1>'
        + (f'<div class="meta">{escape(meta)}</div>' if meta else "")
        + f"</header>{toc}<article>{body}</article></main></body></html>\n"
    )


def pdf_html(
    markdown: str, *, title: str, meta: str = "", images: Mapping[str, bytes] | None = None
) -> tuple[str, str]:
    blocks = parse_blocks(markdown)
    if blocks and blocks[0].kind == "heading" and blocks[0].level == 1:
        title = plain(blocks[0].inlines) or title
        blocks = blocks[1:]
    head = f"<h1>{escape(title)}</h1>" + (f'<p class="meta">{escape(meta)}</p>' if meta else "")
    return head + blocks_html(blocks, images=images, pdf=True), _PDF_CSS


__all__ = ["blocks_html", "pdf_html", "render_html"]
