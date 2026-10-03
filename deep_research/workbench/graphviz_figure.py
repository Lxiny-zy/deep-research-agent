"""Grouped concept diagrams with routed, individually labelled relationships."""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
from collections import Counter
from html import escape
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .figures import ConceptFigure


def dot_source(figure: ConceptFigure) -> str:
    from matplotlib import rcParams
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from matplotlib.font_manager import FontProperties

    from .analysis import _chart_font

    _chart_font()
    family = rcParams["font.sans-serif"][0] if os.name == "nt" else "Noto Sans CJK SC"
    canvas = Figure(dpi=144)
    renderer = FigureCanvasAgg(canvas).get_renderer()

    def quoted(text: str) -> str:
        return json.dumps(text, ensure_ascii=False)

    def wrap(text: str, size: float, width: float) -> str:
        font = FontProperties(family=family, size=size)
        output = []
        for paragraph in text.splitlines() or [""]:
            line = ""
            tokens = re.findall(r"\S*[=→]\S*|[A-Za-z0-9][A-Za-z0-9_./–−-]*|\s+|.", paragraph)
            for token in tokens:
                length = renderer.get_text_width_height_descent(line + token, font, False)[0] / 2
                if line and length > width:
                    output.append(line.strip())
                    line = ""
                line += token
            output.append(line.strip())
        return "\n".join(output)

    font = quoted(family)
    groups = list(dict.fromkeys(node.group for node in figure.nodes))
    colors = ["#7b4fa0", "#b5651d", "#2e8b57", "#1f5f8b", "#b03a48", "#3a7d7c"]
    legend = [
        f'<TD><FONT COLOR="{colors[i % len(colors)]}" POINT-SIZE="11">'
        f"■ {escape(wrap(group, 11, 125)).replace(chr(10), '<BR/>')}</FONT></TD>"
        for i, group in enumerate(groups)
        if group
    ]
    title = escape(wrap(figure.title, 14, 410)).replace("\n", "<BR/>")
    label = (
        '<TABLE BORDER="0" CELLBORDER="0" CELLSPACING="5">'
        f'<TR><TD COLSPAN="3">{title}</TD></TR>'
        + "".join("<TR>" + "".join(legend[i : i + 3]) + "</TR>" for i in range(0, len(legend), 3))
        + "</TABLE>"
    )
    lines = [
        "digraph diagram {",
        "graph [rankdir=TB,splines=spline,nodesep=0.2,ranksep=0.45,pad=0.1,bgcolor=white,"
        f"fontname={font},fontsize=14,labelloc=t,label=<{label}>];",
        f"node [shape=box,style=rounded,fontname={font},fontsize=12,"
        'margin="0.08,0.08",penwidth=1];',
        f'edge [fontname={font},fontsize=11,color="#64748b",arrowsize=0.65,penwidth=0.9];',
    ]
    identifiers = {node.id: f"n{i}" for i, node in enumerate(figure.nodes)}
    for index, group in enumerate(groups):
        color = colors[index % len(colors)]
        if group:
            lines.extend(
                [
                    f"subgraph cluster_{index} {{",
                    f'label="";color="{color}";fontcolor="{color}";'
                    "style=rounded;margin=12;fontsize=12;",
                ]
            )
        for node in figure.nodes:
            if node.group == group:
                lines.append(
                    f"{identifiers[node.id]} [label={quoted(wrap(node.label, 12, 105))},"
                    f'color="{color}"];'
                )
        if group:
            lines.append("}")
    from .figures import _levels

    ranks = {node.id: i for i, row in enumerate(_levels(figure)) for node in row}
    for edge in figure.edges:
        # Shortcut links should not force unrelated branches into later ranks.
        constraint = "constraint=false," if abs(ranks[edge.target] - ranks[edge.source]) > 1 else ""
        lines.append(
            f"{identifiers[edge.source]} -> {identifiers[edge.target]} "
            f"[{constraint}label={quoted(wrap(edge.label, 11, 85))}];"
        )
    return "\n".join([*lines, "}"])


def render_grouped_png(figure: ConceptFigure) -> bytes | None:
    """Use Graphviz when available; retain the built-in renderer for math labels."""
    binary = shutil.which("dot")
    if binary is None or any(
        "$" in text
        for text in [
            figure.title,
            *[n.label for n in figure.nodes],
            *[e.label for e in figure.edges],
        ]
    ):
        return None
    source = dot_source(figure).encode("utf-8")

    def run(fmt: str) -> bytes:
        result = subprocess.run(
            [binary, f"-T{fmt}", "-Gdpi=200"],
            input=source,
            capture_output=True,
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode:
            raise ValueError("图示布局引擎未完成，未交付不完整图像")
        return result.stdout

    layout = json.loads(run("json"))
    nodes = {item["_gvid"]: item["name"] for item in layout["objects"] if "pos" in item}
    expected = {node.id: f"n{i}" for i, node in enumerate(figure.nodes)}
    edges = Counter((nodes[e["tail"]], nodes[e["head"]]) for e in layout.get("edges", []))
    if set(nodes.values()) != set(expected.values()) or edges != Counter(
        (expected[e.source], expected[e.target]) for e in figure.edges
    ):
        raise ValueError("布局结果未保留完整节点与关系")
    x0, y0, x1, y1 = map(float, layout["bb"].split(","))
    if min(440 / (x1 - x0 + 14.4), 590 / (y1 - y0 + 14.4)) * 11 < 7.5:
        raise ValueError("图示过密，需拆分后才能在报告中清晰展示")
    png = run("png")
    from PIL import Image

    with Image.open(io.BytesIO(png)) as image:
        image.verify()
    return png
