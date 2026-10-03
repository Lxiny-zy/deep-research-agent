"""概念图：内容是「画出来的」而不是「算出来的」图（框架、流程、分类法、机制示意）。

两条路径互斥，与数据图（``analysis``，matplotlib 按统计台账绘制）分开：

1. **图像模型**（可选）：部署配置了 ``DR_IMAGE_MODEL`` 与 OpenAI 兼容网关时，
   一图一提示词一次调用（``/images/generations``），生成后做尺寸与格式检查；
2. **确定性示意图**（默认，也是图像模型失败时的回退）：把结构化的「节点 + 关系」
   用固定版式画成 PNG。可见文字一律来自结构描述本身，跟随正文语言，不会出现
   模型乱写的伪文字；同样的描述永远得到同样的图。

结构描述由写作者从已核验素材整理（``ConceptFigure``），不引入素材之外的事实。
"""

from __future__ import annotations

import base64
import io
import os
import re
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from ..bibliography import Bibliography

Layout = Literal["flow", "taxonomy"]
FigureCitation = Annotated[int, Field(ge=1)]


class ConceptNode(BaseModel):
    id: str = Field(max_length=40)
    label: str = Field(max_length=40)
    group: str = Field("", max_length=30)
    kind: Literal["concept", "claim", "question"] = "concept"
    citations: list[FigureCitation] = Field(
        default_factory=list, description="支撑本节点及其分组的本次素材编号"
    )


class ConceptEdge(BaseModel):
    source: str = Field(max_length=40)
    target: str = Field(max_length=40)
    label: str = Field("", max_length=24)
    kind: Literal["concept", "claim", "question"] = "claim"
    citations: list[FigureCitation] = Field(
        default_factory=list,
        description="支撑源节点、方向关系、目标节点完整陈述（含分组、公式）的本次素材编号；关系须有直接依据",
    )


class ConceptFigure(BaseModel):
    title: str = Field(max_length=60)
    caption: str = Field("", max_length=200)
    layout: Layout = "flow"
    nodes: list[ConceptNode] = Field(default_factory=list, max_length=24)
    edges: list[ConceptEdge] = Field(default_factory=list, max_length=40)
    citations: list[FigureCitation] = Field(
        default_factory=list, description="支撑图题、图注中事实的本次素材编号；纯主题或图例可留空"
    )
    evidence_mode: Literal["legacy", "scoped"] = "legacy"


def present_figure(figure: ConceptFigure, catalog: Bibliography) -> ConceptFigure:
    """Project printed references only after verifying the unchanged source diagram."""
    from ..bibliography import project_citations

    def shown(text: str) -> str:
        return project_citations(text, catalog, links=False)

    return figure.model_copy(
        update={
            "title": shown(figure.title),
            "caption": shown(figure.caption),
            "nodes": [
                node.model_copy(update={"label": shown(node.label), "group": shown(node.group)})
                for node in figure.nodes
            ],
            "edges": [
                edge.model_copy(update={"label": shown(edge.label)}) for edge in figure.edges
            ],
        }
    )


def _levels(figure: ConceptFigure) -> list[list[ConceptNode]]:
    """Layer the condensation DAG; feedback cycles must not reset their descendants."""
    ids = [node.id for node in figure.nodes]
    children: dict[str, list[str]] = {node_id: [] for node_id in ids}
    for edge in figure.edges:
        if edge.source in children and edge.target in children:
            if edge.target not in children[edge.source]:
                children[edge.source].append(edge.target)
    indices: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    active: set[str] = set()
    components: list[list[str]] = []

    def visit(node: str) -> None:
        indices[node] = low[node] = len(indices)
        stack.append(node)
        active.add(node)
        for child in children[node]:
            if child not in indices:
                visit(child)
                low[node] = min(low[node], low[child])
            elif child in active:
                low[node] = min(low[node], indices[child])
        if low[node] == indices[node]:
            group = []
            while True:
                member = stack.pop()
                active.remove(member)
                group.append(member)
                if member == node:
                    break
            components.append(group)

    for node in ids:
        if node not in indices:
            visit(node)
    order = {node: i for i, node in enumerate(ids)}
    components.sort(key=lambda group: min(order[node] for node in group))
    owner = {node: i for i, group in enumerate(components) for node in group}
    successors: dict[int, list[int]] = {i: [] for i in range(len(components))}
    incoming = dict.fromkeys(successors, 0)
    for node, targets in children.items():
        for child in targets:
            a, b = owner[node], owner[child]
            if a != b and b not in successors[a]:
                successors[a].append(b)
                incoming[b] += 1
    depth = dict.fromkeys(successors, 0)
    frontier = [i for i in successors if incoming[i] == 0]
    while frontier:
        current = frontier.pop(0)
        for component_child in successors[current]:
            depth[component_child] = max(depth[component_child], depth[current] + 1)
            incoming[component_child] -= 1
            if incoming[component_child] == 0:
                frontier.append(component_child)
    by_id = {node.id: node for node in figure.nodes}
    levels: dict[int, list[ConceptNode]] = {}
    for node_id in ids:
        levels.setdefault(depth[owner[node_id]], []).append(by_id[node_id])
    return [levels[key] for key in sorted(levels)]


def render_concept_png(figure: ConceptFigure) -> bytes:
    """Draw at a fixed report width so embedding never shrinks labels to tiny text."""
    if figure.evidence_mode == "scoped" and len(figure.nodes) >= 8:
        from .graphviz_figure import render_grouped_png

        if png := render_grouped_png(figure):
            return png
    import math

    import matplotlib

    matplotlib.use("Agg")
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from matplotlib.font_manager import FontProperties
    from matplotlib.patches import FancyArrowPatch, FancyBboxPatch
    from matplotlib.path import Path as PlotPath

    from .analysis import _chart_font

    _chart_font()
    levels = _levels(figure) or [[]]
    horizontal = figure.layout == "flow" and len(levels) <= 3 and max(map(len, levels)) <= 3
    if not horizontal:
        # Four columns keep long, branched flows within report height while
        # preserving the same readable font size and measured text wrapping.
        max_columns = 4 if len(levels) > 8 else 3
        levels = [
            level[i : i + max_columns]
            for level in levels
            for i in range(0, len(level), max_columns)
        ] or [[]]
    span = max(map(len, levels)) or 1
    columns = len(levels) if horizontal else span
    width = 6.8
    plot_width = width * 72 * 0.9
    x_range = columns * 3.2 + (0 if horizontal else 0.6)
    scale = plot_width / x_range
    fig = Figure(figsize=(width, 3.2), dpi=200)
    renderer = FigureCanvasAgg(fig).get_renderer()

    def wrap(text: str, size: float, points: float) -> str:
        output = []
        font = FontProperties(size=size)
        if text.count("$") and text.count("$") % 2 == 0:
            measured = renderer.get_text_width_height_descent(text, font, True)[0]
            if measured > points * fig.dpi / 72:
                raise ValueError("图示数学标签过宽，请使用简短标签并将公式置于正文")
            return text
        for source_line in text.splitlines() or [""]:
            line = ""
            # Keep short English terms and acronyms (e.g. SAD) together.
            tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9_./-]*|\s+|.", source_line)
            for token in tokens:
                token_width = renderer.get_text_width_height_descent(token, font, False)[0]
                if token_width > points * fig.dpi / 72:
                    for char in token:
                        measured = renderer.get_text_width_height_descent(line + char, font, False)[
                            0
                        ]
                        if line and measured > points * fig.dpi / 72:
                            output.append(line.rstrip())
                            line = ""
                        line += char
                    continue
                measured = renderer.get_text_width_height_descent(line + token, font, False)[0]
                if line and measured > points * fig.dpi / 72:
                    output.append(line.rstrip())
                    line = token.lstrip()
                else:
                    line += token
            output.append(line)
        return "\n".join(output)

    labels = {node.id: wrap(node.label, 10.5, 2.4 * scale - 12) for node in figure.nodes}
    edge_labels = [wrap(edge.label, 8.5, min(120, plot_width / 3)) for edge in figure.edges]
    node_points = max([28.0] + [len(label.splitlines()) * 13 + 12 for label in labels.values()])
    gap_points = max([24.0] + [len(label.splitlines()) * 11 + 12 for label in edge_labels if label])
    node_height, step = node_points / scale, (node_points + gap_points) / scale
    positions = {}
    for level_index, level in enumerate(levels):
        for slot, node in enumerate(level):
            offset = (span - len(level)) / 2 + slot
            positions[node.id] = (
                (level_index * 3.2, -offset * step)
                if horizontal
                else (offset * 3.2, -level_index * step)
            )
    xs = [p[0] for p in positions.values()] or [0]
    ys = [p[1] for p in positions.values()] or [0]
    groups = sorted({node.group for node in figure.nodes if node.group})
    group_columns = min(3, len(groups)) or 1
    group_labels = [wrap(group, 8.5, plot_width / group_columns - 12) for group in groups]
    group_line_height = max([0] + [len(label.splitlines()) * 11 + 4 for label in group_labels])
    legend_height = math.ceil(len(groups) / group_columns) * group_line_height
    title = wrap(figure.title, 13, plot_width)
    top = 20 + len(title.splitlines()) * 16 + legend_height
    bottom = 12
    padding = gap_points / scale
    y_min, y_max = min(ys) - node_height / 2 - padding, max(ys) + node_height / 2 + padding
    plot_height = max(100, (y_max - y_min) * scale)
    height_points = bottom + plot_height + top
    fig.set_size_inches(width, height_points / 72)
    ax = fig.add_axes((0.05, bottom / height_points, 0.9, plot_height / height_points))
    ax.axis("off")
    ax.set_xlim(min(xs) - 1.6, min(xs) - 1.6 + x_range)
    ax.set_ylim(y_min, y_max)
    palette = ["#1f5f8b", "#2e8b57", "#b5651d", "#7b4fa0", "#b03a48", "#3a7d7c"]
    for node in figure.nodes:
        x, y = positions[node.id]
        color = palette[groups.index(node.group) % len(palette)] if node.group else "#14222f"
        ax.add_patch(
            FancyBboxPatch(
                (x - 1.2, y - node_height / 2),
                2.4,
                node_height,
                boxstyle="round,pad=0.025,rounding_size=0.09",
                facecolor="white",
                edgecolor=color,
                linewidth=1.2,
                zorder=3,
            )
        )
        ax.text(
            x,
            y,
            labels[node.id],
            ha="center",
            va="center",
            fontsize=10.5,
            color="#14222f",
            zorder=4,
        )
    label_positions = []
    for index, edge in enumerate(figure.edges):
        if edge.source not in positions or edge.target not in positions:
            continue
        (x0, y0), (x1, y1) = positions[edge.source], positions[edge.target]
        if abs(y1 - y0) > step * 1.5 and not horizontal:
            direction = 1 if y1 > y0 else -1
            lane = max(xs) + 1.45 + 0.1 * (index % 3)
            start = (x0, y0 + direction * node_height / 2)
            end = (x1, y1 - direction * node_height / 2)
            near_source, near_target = y0 + direction * step / 2, y1 - direction * step / 2
            vertices = [
                start,
                (x0, near_source),
                (lane, near_source),
                (lane, near_target),
                (x1, near_target),
                end,
            ]
            arrow = FancyArrowPatch(
                path=PlotPath(vertices),
                arrowstyle="-|>",
                mutation_scale=10,
                color="#5b6675",
                lw=1,
                zorder=1,
            )
            label_x, label_y = (lane + x1) / 2, near_target
        elif abs(y1 - y0) < 0.01:
            if x1 < x0 or abs(x1 - x0) > 3.3:
                # Feedback/long lateral arrows run below the row, never through
                # intermediate boxes or over the labels of forward arrows.
                lane_y = y0 - node_height / 2 - padding / 2
                vertices = [
                    (x0, y0 - node_height / 2),
                    (x0, lane_y),
                    (x1, lane_y),
                    (x1, y1 - node_height / 2),
                ]
                arrow = FancyArrowPatch(
                    path=PlotPath(vertices),
                    arrowstyle="-|>",
                    mutation_scale=10,
                    color="#5b6675",
                    lw=1,
                    zorder=1,
                )
                label_x, label_y = (x0 + x1) / 2, lane_y
            else:
                start, end = (x0 + 1.2, y0), (x1 - 1.2, y1)
                arrow = FancyArrowPatch(
                    start, end, arrowstyle="-|>", mutation_scale=10, color="#5b6675", lw=1, zorder=1
                )
                label_x, label_y = (x0 + x1) / 2, y0 + node_height / 2 + padding / 2
        else:
            direction = 1 if y1 > y0 else -1
            start, end = (
                (x0, y0 + direction * node_height / 2),
                (x1, y1 - direction * node_height / 2),
            )
            arrow = FancyArrowPatch(
                start, end, arrowstyle="-|>", mutation_scale=10, color="#5b6675", lw=1, zorder=1
            )
            label_x, label_y = (x0 + x1) / 2, (start[1] + end[1]) / 2
        ax.add_patch(arrow)
        if edge.label:
            anchor = (label_x, label_y)
            if abs(y1 - y0) < 0.01 and 0 < x1 - x0 <= 3.3:
                anchor = (label_x, y0)
            label_positions.append((label_x, label_y, edge_labels[index], anchor))
    # Long labels from a fan-out, feedback loop and side links can share the
    # same inter-row gap. Place them against measured bounds rather than
    # allowing text to overlap; displaced labels retain a leader to their edge.
    renderer = FigureCanvasAgg(fig).get_renderer()
    occupied = [
        patch.get_window_extent(renderer).expanded(1.04, 1.08)
        for patch in ax.patches
        if isinstance(patch, FancyBboxPatch)
    ]
    bounds = ax.get_window_extent(renderer)
    offsets = sorted(
        [
            (x, y)
            for y in (0, -12, 12, -24, 24, -36, 36)
            for x in range(-int(plot_width / 2), int(plot_width / 2) + 1, 10)
        ],
        key=lambda pair: pair[0] ** 2 + (pair[1] * 2) ** 2,
    )
    offsets.insert(0, (0, 0))
    for label_x, label_y, label, anchor in label_positions:
        artist = ax.text(
            label_x,
            label_y,
            label,
            ha="center",
            va="center",
            fontsize=8.5,
            color="#5b6675",
            zorder=2,
            bbox={"facecolor": "white", "edgecolor": "none", "pad": 1},
        )
        for dx, dy in offsets:
            point = (label_x + dx / scale, label_y + dy / scale)
            artist.set_position(point)
            box = artist.get_window_extent(renderer).expanded(1.08, 1.2)
            if (
                bounds.contains(*box.p0)
                and bounds.contains(*box.p1)
                and not any(box.overlaps(other) for other in occupied)
            ):
                occupied.append(box)
                if dx or dy:
                    ax.plot(
                        [anchor[0], point[0]],
                        [anchor[1], point[1]],
                        color="#8a949d",
                        linewidth=0.5,
                        linestyle=":",
                        zorder=1,
                    )
                break
        else:
            raise ValueError("图示关系标签无法无重叠排布，请拆分复杂图示")
    fig.text(0.5, 1 - 8 / height_points, title, ha="center", va="top", fontsize=13, color="#10283d")
    legend_top = height_points - 14 - len(title.splitlines()) * 16
    for index, _group in enumerate(groups):
        row, column = divmod(index, group_columns)
        fig.text(
            0.05 + (column + 0.5) * 0.9 / group_columns,
            (legend_top - row * group_line_height) / height_points,
            "■ " + group_labels[index],
            color=palette[index % len(palette)],
            fontsize=8.5,
            ha="center",
            va="top",
        )
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=200, bbox_inches="tight", facecolor="white")
    return buffer.getvalue()


def image_prompt(figure: ConceptFigure) -> str:
    nodes = "；".join(node.label for node in figure.nodes)
    relations = "；".join(
        f"{edge.source}→{edge.target}{'（' + edge.label + '）' if edge.label else ''}"
        for edge in figure.edges
    )
    return (
        f"Clean academic {figure.layout} diagram titled '{figure.title}'. "
        f"Boxes (keep these exact labels, in the original language): {nodes}. "
        f"Arrows: {relations}. White background, flat vector style, no decorative text."
    )


async def generate_image(figure: ConceptFigure) -> bytes | None:
    """图像模型路径：未配置或失败时返回 None（调用方回退到确定性示意图）。"""
    model = os.environ.get("DR_IMAGE_MODEL", "").strip()
    base_url = os.environ.get("DR_IMAGE_BASE_URL", "").strip() or os.environ.get("LLM_BASE_URL", "")
    api_key = os.environ.get("DR_IMAGE_API_KEY", "").strip() or os.environ.get("LLM_API_KEY", "")
    if not model or not api_key:
        return None
    from ..security import provider_http_client, validate_provider_url

    endpoint = (base_url or "https://api.openai.com/v1").rstrip("/") + "/images/generations"
    try:
        validate_provider_url(endpoint)
        async with provider_http_client(timeout=120.0) as client:
            response = await client.post(
                endpoint,
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "prompt": image_prompt(figure),
                    "n": 1,
                    "size": "1536x1024",
                    "response_format": "b64_json",
                },
            )
        response.raise_for_status()
        data = base64.b64decode(response.json()["data"][0]["b64_json"])
    except Exception:
        return None
    # 生成后检查：必须是可解码的 PNG/JPEG，且尺寸合理——坏图不进交付物
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as image:
            if min(image.size) < 256:
                return None
    except Exception:
        return None
    return data


async def concept_figure(figure: ConceptFigure) -> tuple[bytes, str]:
    """一图一调用：图像模型优先，失败回退确定性示意图。返回 (字节, 路径名)。"""
    generated = await generate_image(figure)
    if generated is not None:
        return generated, "image_model"
    return render_concept_png(figure), "diagram"


__all__ = [
    "ConceptEdge",
    "ConceptFigure",
    "ConceptNode",
    "concept_figure",
    "generate_image",
    "image_prompt",
    "render_concept_png",
]
