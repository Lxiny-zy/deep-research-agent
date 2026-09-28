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
from typing import Literal

from pydantic import BaseModel, Field

Layout = Literal["flow", "taxonomy"]


class ConceptNode(BaseModel):
    id: str = Field(max_length=40)
    label: str = Field(max_length=40)
    group: str = Field("", max_length=30)


class ConceptEdge(BaseModel):
    source: str = Field(max_length=40)
    target: str = Field(max_length=40)
    label: str = Field("", max_length=24)


class ConceptFigure(BaseModel):
    title: str = Field(max_length=60)
    caption: str = Field("", max_length=200)
    layout: Layout = "flow"
    nodes: list[ConceptNode] = Field(default_factory=list, max_length=24)
    edges: list[ConceptEdge] = Field(default_factory=list, max_length=40)


def _levels(figure: ConceptFigure) -> list[list[ConceptNode]]:
    """按关系做拓扑分层（有环时退化为声明顺序），每层从左到右排布。"""
    ids = [node.id for node in figure.nodes]
    incoming = {node_id: 0 for node_id in ids}
    children: dict[str, list[str]] = {node_id: [] for node_id in ids}
    for edge in figure.edges:
        if edge.source in incoming and edge.target in incoming:
            incoming[edge.target] += 1
            children[edge.source].append(edge.target)
    depth = {node_id: 0 for node_id in ids}
    frontier = [node_id for node_id in ids if incoming[node_id] == 0]
    seen: set[str] = set()
    while frontier:
        current = frontier.pop(0)
        if current in seen:
            continue
        seen.add(current)
        for child in children[current]:
            depth[child] = max(depth[child], depth[current] + 1)
            incoming[child] -= 1
            if incoming[child] <= 0:
                frontier.append(child)
    by_id = {node.id: node for node in figure.nodes}
    levels: dict[int, list[ConceptNode]] = {}
    for node_id in ids:
        levels.setdefault(depth[node_id] if node_id in seen else 0, []).append(by_id[node_id])
    return [levels[key] for key in sorted(levels)]


def render_concept_png(figure: ConceptFigure) -> bytes:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

    from .analysis import _chart_font

    _chart_font()
    levels = _levels(figure) or [[]]
    horizontal = figure.layout == "flow"
    span = max(len(level) for level in levels) or 1
    width = 3.2 * (len(levels) if horizontal else span) + 1
    height = 1.6 * (span if horizontal else len(levels)) + 1.4
    fig, ax = plt.subplots(figsize=(max(6.0, width), max(3.2, height)))
    ax.axis("off")
    palette = ["#1f5f8b", "#2e8b57", "#b5651d", "#7b4fa0", "#b03a48", "#3a7d7c"]
    groups = sorted({node.group for node in figure.nodes if node.group})
    positions: dict[str, tuple[float, float]] = {}
    for level_index, level in enumerate(levels):
        for slot, node in enumerate(level):
            offset = (span - len(level)) / 2 + slot
            x, y = (
                (level_index * 3.2, -offset * 1.6)
                if horizontal
                else (offset * 3.2, -level_index * 1.8)
            )
            positions[node.id] = (x, y)
            color = palette[groups.index(node.group) % len(palette)] if node.group else "#14222f"
            ax.add_patch(
                FancyBboxPatch(
                    (x - 1.2, y - 0.42),
                    2.4,
                    0.84,
                    boxstyle="round,pad=0.04,rounding_size=0.18",
                    facecolor="white",
                    edgecolor=color,
                    linewidth=1.8,
                )
            )
            ax.text(x, y, node.label, ha="center", va="center", fontsize=10.5, color="#14222f")
    for edge in figure.edges:
        if edge.source not in positions or edge.target not in positions:
            continue
        (x0, y0), (x1, y1) = positions[edge.source], positions[edge.target]
        if horizontal:
            start, end = (x0 + 1.2, y0), (x1 - 1.2, y1)
        else:
            start, end = (x0, y0 - 0.42), (x1, y1 + 0.42)
        ax.add_patch(
            FancyArrowPatch(
                start, end, arrowstyle="-|>", mutation_scale=12, color="#5b6675", lw=1.2
            )
        )
        if edge.label:
            ax.text(
                (start[0] + end[0]) / 2,
                (start[1] + end[1]) / 2 + 0.18,
                edge.label,
                ha="center",
                fontsize=8.5,
                color="#5b6675",
            )
    for index, group in enumerate(groups):
        ax.text(
            0,
            0.9 + 0.35 * index,
            f"■ {group}",
            color=palette[index % len(palette)],
            fontsize=9,
            ha="left",
            transform=ax.transData,
        )
    xs = [p[0] for p in positions.values()] or [0]
    ys = [p[1] for p in positions.values()] or [0]
    ax.set_xlim(min(xs) - 1.6, max(xs) + 1.6)
    ax.set_ylim(min(ys) - 1.0, max(ys) + 1.2 + 0.35 * len(groups))
    ax.set_title(figure.title, fontsize=13, color="#10283d", pad=10)
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
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
