"""Readable distribution panels covering every analysed measurement."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .analysis import Figure


def distribution_figures(frame: Any, numeric: list[str], categorical: list[str]) -> list[Figure]:
    """Paginate four panels per image; never discard the remaining variables."""
    import matplotlib.pyplot as plt

    from .analysis import Figure, _png, _safe

    figures: list[Figure] = []
    groups: list[str | None] = [*categorical] if categorical else [None]
    for group in groups:
        for start in range(0, len(numeric), 4):
            columns = numeric[start : start + 4]
            ncols = 2 if len(columns) > 1 else 1
            nrows = (len(columns) + ncols - 1) // ncols
            fig, axes = plt.subplots(
                nrows,
                ncols,
                figsize=(6.4 * ncols, 4.2 * nrows),
                squeeze=False,
                layout="constrained",
            )
            counts = []
            for ax, column in zip(axes.flat, columns, strict=False):
                if group is not None:
                    grouped = list(frame.groupby(group, dropna=True)[column])
                    samples = [values.dropna().to_numpy() for _, values in grouped]
                    labels = [
                        f"{label}\nn={len(values)}"
                        for (label, _), values in zip(grouped, samples, strict=True)
                    ]
                    ax.boxplot(samples, tick_labels=labels)
                    ax.set_xlabel(group, fontsize=18)
                    ax.set_ylabel(column, fontsize=16)
                    if any(len(str(label)) > 12 for label, _ in grouped) or len(grouped) > 4:
                        ax.tick_params(axis="x", labelrotation=25)
                    counts.append(
                        f"{column}："
                        + "、".join(
                            f"{label} n={len(values)}"
                            for (label, _), values in zip(grouped, samples, strict=True)
                        )
                    )
                else:
                    values = frame[column].dropna().to_numpy()
                    ax.hist(values, bins=min(20, max(5, len(values) // 5)))
                    ax.set_xlabel(column, fontsize=16)
                    ax.set_ylabel("频数", fontsize=18)
                    counts.append(f"{column} n={len(values)}")
                ax.set_title(column, fontsize=18)
                ax.tick_params(axis="both", labelsize=16)
                ax.grid(axis="y", alpha=0.3)
            for ax in list(axes.flat)[len(columns) :]:
                ax.set_visible(False)
            title = f"按 {group} 分组的测量分布" if group else "测量变量分布"
            if len(numeric) > 4:
                title += f"（{start // 4 + 1}）"
            figures.append(
                Figure(
                    name=f"fig_{len(figures) + 1:02d}_distributions_{_safe(group or 'all')}.png",
                    title=title,
                    caption=(
                        "各面板采用各变量的有效观测，未插补；坐标范围分别设置，测量单位同输入列。"
                        + (
                            "分组缺失的记录不进入分组图。箱体为四分位区间，中线为中位数，"
                            "须线延伸至四分位距 1.5 倍范围内的最远观测，散点为范围外观测。"
                            if group
                            else "纵轴表示各区间的观测频数。"
                        )
                        + "有效样本量："
                        + "；".join(counts)
                    ),
                    png=_png(fig),
                )
            )
    return figures
