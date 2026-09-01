"""内联 SVG 图表渲染：一份图，三种格式通用。

## 为什么是"Python 生成内联 SVG"而不是前端图表库

图表要同时出现在:交互视图、自包含 ``.html`` 导出、浏览器打印出的 PDF，将来可能还有
服务端生成的 PDF 与邮件推送。JS 图表库只在浏览器 DOM 里活着——它撑不住自包含导出
（除非把整个库打进去），更撑不住不执行 JS 的服务端渲染器。

内联 SVG 全部满足，而且是**矢量**：打印在任何 DPI 下都清晰，位图会糊。代价为零——
SVG 就是字符串拼接，和这个项目本来就在做的 Markdown 生成同一性质，**不引入任何依赖**。
刻意不用 matplotlib：它会带来 numpy + freetype，而且默认不含中文字体，中文标签会渲染
成空白方块——和服务端 PDF 那个字体坑是同一个坑。

## 交互也不靠 JS

每个数据标记内嵌 ``<title>``，浏览器原生显示 tooltip。零 JS，因此自包含导出、
打印预览、iframe 内嵌全都保留这一层。而"每个值都必须能在图外读到"由**源表**保证——
图不是读数的唯一途径，这同时满足了低对比度色（浅色系列色在浅背景上不足 3:1）
所要求的补偿手段。

## 颜色与形式的判断依据

* 单指标跨方法比大小时，方法名是**无序名义类别**，所以所有柱同一个颜色。按数值
  深浅上色会把柱长重复编码成色相，白占掉唯一的自由通道。
* 分类色最多 3 个系列（``MAX_CHART_SERIES``），这是调色板校验器在 all-pairs 口径下
  的实测上限，不是审美偏好。
* 文字一律用文本色，绝不用系列色——浅色系列色作为文字在浅背景上不可读；身份由
  文字**旁边**的色块承载。
* 永远不做双 Y 轴。两个量纲不同的指标就是两张图。

## 一条领域相关的取舍：柱状图的零基线

柱状图的长度就是数值，所以基线**必须**为零，截断 Y 轴是撒谎。但 CASSI 这类
benchmark 的 SOTA 差异常常是 35 dB 基座上的 0.5–2 dB——零基线会让所有方法看起来一样高。

这种情况正确的做法不是截断柱状图，而是**换形式**：``dot`` 用点的位置编码数值，
位置本来就没有"从零开始"的语义，因此非零基线是诚实的。所以本模块同时提供
``bar``（强制零基线）与 ``dot``（允许非零基线），由调用方按"要看绝对量还是看差异"选择。
"""

from __future__ import annotations

import math
from html import escape

from .document import MAX_CHART_SERIES, ChartBlock, TableBlock, TableCell, TableRow

# --- 版面常量（viewBox 单位；SVG 按 CSS width:100% 自适应） ---
_WIDTH = 720
_PAD_LEFT = 150  # 留给行标签：方法名可以很长（RDLUF-MixS2、DAUHST-9stg）
_PAD_RIGHT = 56  # 留给行末的数值直标
_PAD_TOP = 10
_ROW_HEIGHT = 30
_AXIS_BAND = 34  # 轴标签带；不计进来会让容器裁掉刻度（经典缺陷）
_BAR_MAX_THICKNESS = 24  # 柱最多 24px，band 余量留作空气
_BAR_RADIUS = 4  # 数据端圆角，基线端保持方角
_MARK_GAP = 2  # 相邻填充之间的表面色间隙，用留白分隔而不是描边
_DOT_RADIUS = 5  # ≥8px 直径；表面色描边环由 CSS 的 .dr-chart-dot 提供
# 行标签可用显示宽度：标签区 (_PAD_LEFT - 10)px ÷ 12px 字号下每单位约 6.2px。
_LABEL_MAX_UNITS = 21

_CARTESIAN_HEIGHT = 300
_CARTESIAN_PAD_LEFT = 64

CHART_CSS = """\
.dr-chart { margin: 20px 0; }
.dr-chart svg { display: block; width: 100%; height: auto; }
.dr-chart figcaption { margin-top: 8px; color: var(--dr-ink-2); font-size: 0.82rem;
  line-height: 1.55; }
.dr-chart-title { color: var(--dr-ink-1); font-size: 13px; font-weight: 600; }
.dr-chart-grid line { stroke: var(--dr-grid); stroke-width: 1; }
.dr-chart-axis line { stroke: var(--dr-axis); stroke-width: 1; }
.dr-chart-tick { fill: var(--dr-ink-3); font-size: 11px; font-variant-numeric: tabular-nums; }
.dr-chart-cat { fill: var(--dr-ink-2); font-size: 12px; }
.dr-chart-value { fill: var(--dr-ink-1); font-size: 11px; font-weight: 600;
  font-variant-numeric: tabular-nums; }
.dr-chart-unreported { fill: var(--dr-ink-3); font-size: 11px; font-style: italic; }
.dr-chart-mark { stroke: none; }
.dr-chart-dot { stroke: var(--dr-surface); stroke-width: 2; }
.dr-chart-line { fill: none; stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; }
.dr-chart-muted { fill: var(--dr-muted-mark); }
.dr-chart-legend { display: flex; flex-wrap: wrap; gap: 14px; margin: 2px 0 10px; padding: 0;
  list-style: none; }
.dr-chart-legend li { display: flex; align-items: center; gap: 6px; color: var(--dr-ink-2);
  font-size: 0.8rem; }
.dr-chart-swatch { width: 10px; height: 10px; border-radius: 2px; flex: none; }

/* 系列色取自已通过校验的参考调色板前三槽：明/暗两种表面、相邻与全两两组合下，
   CVD ΔE 与常视分辨 ΔE 全部达标。改色请重跑校验器，不要凭眼睛判断。 */
.dr-chart { --dr-surface: #fcfcfb; --dr-ink-1: #0b0b0b; --dr-ink-2: #52514e; --dr-ink-3: #898781;
  --dr-grid: #e1e0d9; --dr-axis: #c3c2b7; --dr-muted-mark: #c3c2b7;
  --dr-s1: #2a78d6; --dr-s2: #eb6834; --dr-s3: #1baf7a; }
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) .dr-chart {
    --dr-surface: #1a1a19; --dr-ink-1: #ffffff; --dr-ink-2: #c3c2b7; --dr-ink-3: #898781;
    --dr-grid: #2c2c2a; --dr-axis: #383835; --dr-muted-mark: #383835;
    --dr-s1: #3987e5; --dr-s2: #d95926; --dr-s3: #199e70; }
}
:root[data-theme="dark"] .dr-chart {
  --dr-surface: #1a1a19; --dr-ink-1: #ffffff; --dr-ink-2: #c3c2b7; --dr-ink-3: #898781;
  --dr-grid: #2c2c2a; --dr-axis: #383835; --dr-muted-mark: #383835;
  --dr-s1: #3987e5; --dr-s2: #d95926; --dr-s3: #199e70; }
/* 纸永远是白的：打印时强制回明色，不跟随屏幕主题。 */
@media print {
  .dr-chart, :root[data-theme="dark"] .dr-chart {
    --dr-surface: #ffffff; --dr-ink-1: #000000; --dr-ink-2: #3a3a38; --dr-ink-3: #6b6a66;
    --dr-grid: #dddcd6; --dr-axis: #b4b3ad; --dr-muted-mark: #c3c2b7;
    --dr-s1: #2a78d6; --dr-s2: #eb6834; --dr-s3: #1baf7a; }
}
"""


class ChartDataError(ValueError):
    """图表引用的源表/列不存在，或系列数超限。

    单独立类型而不是笼统 ValueError：这类失败的处置是"修占位符或表定义"，
    调用方据此决定降级成源表而不是让整份报告渲染失败。
    """


def render_chart(chart: ChartBlock, table: TableBlock) -> str:
    """把 ChartBlock 渲染成内联 SVG ``<figure>``。

    数据全部来自 ``table``——本函数不接受任何独立的数据入参，图表凭空造数在这里
    是不可表达的。
    """
    if chart.source_table != table.id:
        raise ChartDataError(f"图 {chart.id} 的源表是 {chart.source_table}，收到的是 {table.id}")
    columns = _resolve_columns(chart, table)
    if len(columns) > MAX_CHART_SERIES:
        raise ChartDataError(
            f"图 {chart.id} 有 {len(columns)} 个系列，超过分类色上限 {MAX_CHART_SERIES}；"
            "请拆成小倍数图或折叠尾部"
        )
    _assert_commensurable(chart, table, columns)

    if chart.form in {"bar", "dot", "grouped_bar"}:
        body, height = _render_categorical(chart, table, columns)
    else:
        body, height = _render_cartesian(chart, table, columns)

    legend = _legend(chart, table, columns)
    label = chart.title or f"{table.title} 图表"
    svg = (
        f'<svg viewBox="0 0 {_WIDTH} {_num(height)}" role="img" '
        f'aria-label="{escape(label)}" preserveAspectRatio="xMinYMin meet">'
        f"<title>{escape(label)}</title>"
        f"<desc>{escape(_describe(chart, table, columns))}</desc>"
        f"{body}</svg>"
    )
    caption = _caption(chart, table)
    return (
        f'<figure class="dr-chart" data-chart-id="{escape(chart.id)}">'
        f"{legend}{svg}{caption}</figure>"
    )


# --- 横向类别图：bar / dot / grouped_bar ---------------------------------------


def _render_categorical(
    chart: ChartBlock, table: TableBlock, columns: list[str]
) -> tuple[str, float]:
    """横向布局。

    横向而非纵向，是因为类别名是方法名——纵向柱状图要么旋转标签（难读）要么
    互相挤掉。横向后每行一个标签，天然不冲突，且行数增加时高度自然增长，
    不会出现"容器固定高度把轴标签裁掉"。
    """
    rows = table.rows
    # 柱状图必须零基线（长度即数值）；点图用位置编码，非零基线是诚实的。
    zero_based = chart.form != "dot"
    values = _all_numbers(rows, columns)
    lo, hi = _value_range(values, zero_based=zero_based)
    ticks = _ticks(lo, hi)
    lo, hi = min(lo, ticks[0]), max(hi, ticks[-1])

    plot_left, plot_right = _PAD_LEFT, _WIDTH - _PAD_RIGHT
    per_row = _ROW_HEIGHT * (len(columns) if chart.form == "grouped_bar" else 1)
    height = _PAD_TOP + max(per_row * len(rows), per_row) + _AXIS_BAND

    def x_of(value: float) -> float:
        if hi <= lo:
            return plot_left
        return plot_left + (value - lo) / (hi - lo) * (plot_right - plot_left)

    parts: list[str] = []

    grid = "".join(
        f'<line x1="{_num(x_of(t))}" y1="{_PAD_TOP}" '
        f'x2="{_num(x_of(t))}" y2="{_num(height - _AXIS_BAND)}"/>'
        for t in ticks
    )
    parts.append(f'<g class="dr-chart-grid">{grid}</g>')

    baseline_x = x_of(max(lo, 0.0) if zero_based else lo)
    for index, row in enumerate(rows):
        band_top = _PAD_TOP + index * per_row
        parts.append(
            f'<text class="dr-chart-cat" x="{_PAD_LEFT - 10}" '
            f'y="{_num(band_top + per_row / 2)}" text-anchor="end" '
            f'dominant-baseline="central">{escape(_clip(row.label, _LABEL_MAX_UNITS))}</text>'
        )
        for series, key in enumerate(columns):
            cell = row.cell(key)
            slot_top = band_top + (series * _ROW_HEIGHT if chart.form == "grouped_bar" else 0)
            centre = slot_top + _ROW_HEIGHT / 2
            colour = _colour(chart, table, row.label, series, len(columns))
            tip = _tooltip(row.label, table, key, cell)

            if cell.numeric is None:
                parts.append(
                    f'<text class="dr-chart-unreported" x="{_num(baseline_x + 6)}" '
                    f'y="{_num(centre)}" dominant-baseline="central">未报告</text>'
                )
                continue

            if chart.form == "dot":
                parts.append(
                    f"<g><title>{escape(tip)}</title>"
                    f'<circle class="dr-chart-dot" cx="{_num(x_of(cell.numeric))}" '
                    f'cy="{_num(centre)}" r="{_DOT_RADIUS}" fill="{colour}"/></g>'
                )
            else:
                thickness = min(_BAR_MAX_THICKNESS, _ROW_HEIGHT - _MARK_GAP * 2)
                top = centre - thickness / 2
                parts.append(
                    f"<g><title>{escape(tip)}</title>"
                    f'<path class="dr-chart-mark" fill="{colour}" '
                    f'd="{_bar_path(baseline_x, x_of(cell.numeric), top, thickness)}"/></g>'
                )
            # 柱与点都在末端直标数值——这是横向类别图的常规读法，且源表仍是权威。
            parts.append(
                f'<text class="dr-chart-value" x="{_num(x_of(cell.numeric) + 7)}" '
                f'y="{_num(centre)}" dominant-baseline="central">'
                f"{escape(cell.value or _num(cell.numeric))}</text>"
            )

    axis_y = height - _AXIS_BAND
    axis = (
        f'<line x1="{_num(plot_left)}" y1="{_num(axis_y)}" '
        f'x2="{_num(plot_right)}" y2="{_num(axis_y)}"/>'
    )
    parts.append(f'<g class="dr-chart-axis">{axis}</g>')
    parts.append(
        "".join(
            f'<text class="dr-chart-tick" x="{_num(x_of(t))}" y="{_num(axis_y + 15)}" '
            f'text-anchor="middle">{escape(_num(t))}</text>'
            for t in ticks
        )
    )
    axis_label = chart.y_label or _axis_label(table, columns)
    if axis_label:
        parts.append(
            f'<text class="dr-chart-tick" x="{_num((plot_left + plot_right) / 2)}" '
            f'y="{_num(axis_y + 30)}" text-anchor="middle">{escape(axis_label)}</text>'
        )
    return "".join(parts), height


def _bar_path(x0: float, x1: float, top: float, thickness: float) -> str:
    """圆角数据端 + 方角基线端。

    只有数据端圆角:圆角标记的是"值到这里",基线端是坐标原点,圆掉会让人以为
    那一侧也是数据。宽度不足两个半径时退化成矩形,避免路径自交。
    """
    left, right = min(x0, x1), max(x0, x1)
    width = right - left
    bottom = top + thickness
    r = min(_BAR_RADIUS, width / 2, thickness / 2)
    if width <= 0 or r <= 0:
        return f"M{_num(left)},{_num(top)} H{_num(right)} V{_num(bottom)} H{_num(left)} Z"
    # 数据端在右（值为正）时右侧圆角；值为负则左侧圆角。
    if x1 >= x0:
        return (
            f"M{_num(left)},{_num(top)} H{_num(right - r)} "
            f"Q{_num(right)},{_num(top)} {_num(right)},{_num(top + r)} "
            f"V{_num(bottom - r)} Q{_num(right)},{_num(bottom)} {_num(right - r)},{_num(bottom)} "
            f"H{_num(left)} Z"
        )
    return (
        f"M{_num(right)},{_num(top)} H{_num(left + r)} "
        f"Q{_num(left)},{_num(top)} {_num(left)},{_num(top + r)} "
        f"V{_num(bottom - r)} Q{_num(left)},{_num(bottom)} {_num(left + r)},{_num(bottom)} "
        f"H{_num(right)} Z"
    )


# --- 直角坐标图：scatter / line ------------------------------------------------


def _render_cartesian(
    chart: ChartBlock, table: TableBlock, columns: list[str]
) -> tuple[str, float]:
    x_key = chart.x_column
    x_values: list[float] = []
    for row in table.rows:
        cell = row.cell(x_key) if x_key else None
        if cell is not None and cell.numeric is not None:
            x_values.append(cell.numeric)
    # 没有数值横轴时退化成序号轴：光谱曲线一定有波长列，但方法演进这类图没有。
    if not x_values:
        x_values = [float(i) for i in range(len(table.rows))]

    y_values = _all_numbers(table.rows, columns)
    x_lo, x_hi = _value_range(x_values, zero_based=False)
    y_lo, y_hi = _value_range(y_values, zero_based=False)
    x_ticks, y_ticks = _ticks(x_lo, x_hi), _ticks(y_lo, y_hi)
    x_lo, x_hi = min(x_lo, x_ticks[0]), max(x_hi, x_ticks[-1])
    y_lo, y_hi = min(y_lo, y_ticks[0]), max(y_hi, y_ticks[-1])

    plot_left, plot_right = _CARTESIAN_PAD_LEFT, _WIDTH - 24
    plot_top = _PAD_TOP
    plot_bottom = _CARTESIAN_HEIGHT - _AXIS_BAND
    height = _CARTESIAN_HEIGHT

    def x_of(v: float) -> float:
        span = x_hi - x_lo
        return plot_left if span <= 0 else plot_left + (v - x_lo) / span * (plot_right - plot_left)

    def y_of(v: float) -> float:
        span = y_hi - y_lo
        if span <= 0:
            return plot_bottom
        return plot_bottom - (v - y_lo) / span * (plot_bottom - plot_top)

    parts: list[str] = []
    grid = "".join(
        f'<line x1="{_num(plot_left)}" y1="{_num(y_of(t))}" '
        f'x2="{_num(plot_right)}" y2="{_num(y_of(t))}"/>'
        for t in y_ticks
    )
    parts.append(f'<g class="dr-chart-grid">{grid}</g>')

    for series, key in enumerate(columns):
        colour = _colour(chart, table, "", series, len(columns))
        points: list[tuple[float, float, str]] = []
        for index, row in enumerate(table.rows):
            cell = row.cell(key)
            if cell.numeric is None:
                continue  # 缺值断开，不插值——插值是编数据
            xv = row.cell(x_key).numeric if x_key else float(index)
            if xv is None:
                continue
            points.append((x_of(xv), y_of(cell.numeric), _tooltip(row.label, table, key, cell)))
        if chart.form == "line" and len(points) > 1:
            path = "M" + " L".join(f"{_num(px)},{_num(py)}" for px, py, _ in points)
            parts.append(f'<path class="dr-chart-line" stroke="{colour}" d="{path}"/>')
        for px, py, tip in points:
            parts.append(
                f"<g><title>{escape(tip)}</title>"
                f'<circle class="dr-chart-dot" cx="{_num(px)}" cy="{_num(py)}" '
                f'r="{_DOT_RADIUS}" fill="{colour}"/></g>'
            )

    parts.append(
        f'<g class="dr-chart-axis">'
        f'<line x1="{_num(plot_left)}" y1="{_num(plot_bottom)}" '
        f'x2="{_num(plot_right)}" y2="{_num(plot_bottom)}"/>'
        f'<line x1="{_num(plot_left)}" y1="{_num(plot_top)}" '
        f'x2="{_num(plot_left)}" y2="{_num(plot_bottom)}"/></g>'
    )
    parts.append(
        "".join(
            f'<text class="dr-chart-tick" x="{_num(plot_left - 8)}" y="{_num(y_of(t))}" '
            f'text-anchor="end" dominant-baseline="central">{escape(_num(t))}</text>'
            for t in y_ticks
        )
    )
    parts.append(
        "".join(
            f'<text class="dr-chart-tick" x="{_num(x_of(t))}" y="{_num(plot_bottom + 16)}" '
            f'text-anchor="middle">{escape(_num(t))}</text>'
            for t in x_ticks
        )
    )
    return "".join(parts), height


# --- 共用工具 -----------------------------------------------------------------


def _assert_commensurable(chart: ChartBlock, table: TableBlock, columns: list[str]) -> None:
    """``value_columns`` 永远共用同一根数值轴，所以它们必须量纲相同。

    这是"绝不做双 Y 轴"那条规则的落地。把 PSNR（31–38 dB）与 SSIM（0.89–0.97）并排
    放到一根 0–40 的轴上，SSIM 会渲染成 8px 的残根——不是"不好看"，是那个系列的
    信息被完全抹掉了，而图看上去仍然人模人样。两个量纲就是两张图。

    ``x_column`` 不在此列：散点图的两根轴本来就是两个不同的度量，那是它的本意。
    """
    if len(columns) < 2:
        return
    # 列的存在性已由 _resolve_columns 校验过，这里只取单位。
    unit_by_key = {key: (column.unit if (column := table.column(key)) else "") for key in columns}
    if len(set(unit_by_key.values())) > 1:
        shown = "、".join(sorted(f"{key}({unit or '无单位'})" for key, unit in unit_by_key.items()))
        raise ChartDataError(
            f"图 {chart.id} 的系列量纲不一致（{shown}）。共用一根数值轴会让小量纲系列"
            "被压成看不见的残根，等同于双 Y 轴撒谎——请拆成两张图，或改用 scatter 让"
            "两个度量各占一轴"
        )


def _resolve_columns(chart: ChartBlock, table: TableBlock) -> list[str]:
    keys = chart.value_columns or [c.key for c in table.columns if c.numeric][:1]
    missing = [key for key in keys if table.column(key) is None]
    if missing:
        raise ChartDataError(f"图 {chart.id} 引用了源表不存在的列：{', '.join(missing)}")
    if not keys:
        raise ChartDataError(f"图 {chart.id} 没有可用的数值列")
    return keys


def _all_numbers(rows: list[TableRow], columns: list[str]) -> list[float]:
    return [
        cell.numeric
        for row in rows
        for key in columns
        if (cell := row.cell(key)).numeric is not None
    ]


def _value_range(values: list[float], *, zero_based: bool) -> tuple[float, float]:
    if not values:
        return (0.0, 1.0)
    lo, hi = min(values), max(values)
    if zero_based:
        lo = min(0.0, lo)
    elif hi > lo:
        pad = (hi - lo) * 0.08  # 留白，避免极值贴边
        lo, hi = lo - pad, hi + pad
    if hi <= lo:
        hi = lo + 1.0
    return (lo, hi)


def _ticks(lo: float, hi: float, target: int = 5) -> list[float]:
    """生成"干净数字"刻度：步长取 1 / 2 / 2.5 / 5 / 10 的 10 的幂倍。"""
    if hi <= lo or not all(math.isfinite(v) for v in (lo, hi)):
        return [0.0, 1.0]
    raw = (hi - lo) / max(target, 1)
    magnitude = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1.0
    for multiple in (1.0, 2.0, 2.5, 5.0, 10.0):
        step = multiple * magnitude
        if raw <= step:
            break
    start = math.floor(lo / step) * step
    out: list[float] = []
    value = start
    # +1e-9 吸收浮点误差，否则末刻度常常差一个 step 不出现
    while value <= hi + step * 1e-9 and len(out) < 24:
        out.append(round(value, 10))
        value += step
    return out or [lo, hi]


def _colour(chart: ChartBlock, table: TableBlock, row_label: str, series: int, total: int) -> str:
    """决定一个标记的颜色。

    单系列时所有标记同色（方法名是无序名义类别，按数值上色会把长度重复编码成色相）。
    指定 emphasis 时高亮那一行、其余转灰——"某一个是重点"用强调形式，不用分类色。
    """
    if chart.emphasis and total == 1:
        return "var(--dr-s1)" if row_label == chart.emphasis else "var(--dr-muted-mark)"
    return f"var(--dr-s{min(series, MAX_CHART_SERIES - 1) + 1})"


def _legend(chart: ChartBlock, table: TableBlock, columns: list[str]) -> str:
    """≥2 个系列必有图例；单系列不加——只有一种颜色时标题已经说明画的是什么。"""
    if len(columns) < 2:
        return ""
    items = []
    for series, key in enumerate(columns):
        column = table.column(key)
        label = column.label if column else key
        if column and column.unit:
            label = f"{label}（{column.unit}）"
        swatch = f"var(--dr-s{min(series, MAX_CHART_SERIES - 1) + 1})"
        items.append(
            f'<li><span class="dr-chart-swatch" style="background:{swatch}"></span>'
            f"{escape(label)}</li>"
        )
    return f'<ul class="dr-chart-legend">{"".join(items)}</ul>'


def _axis_label(table: TableBlock, columns: list[str]) -> str:
    if len(columns) != 1:
        return ""
    column = table.column(columns[0])
    if column is None:
        return ""
    return f"{column.label}（{column.unit}）" if column.unit else column.label


def _tooltip(row_label: str, table: TableBlock, key: str, cell: TableCell) -> str:
    column = table.column(key)
    label = column.label if column else key
    unit = f" {column.unit}" if column and column.unit else ""
    shown = cell.value or (_num(cell.numeric) if cell.numeric is not None else "未报告")
    citation = "".join(f"  [{n}]" for n in cell.citations)
    return f"{row_label} · {label}: {shown}{unit}{citation}"


def _caption(chart: ChartBlock, table: TableBlock) -> str:
    pieces: list[str] = []
    if chart.caption:
        pieces.append(escape(chart.caption))
    # 图注必须指回源表：读者要能去核每一个点，而不是只能相信这张图。
    pieces.append(f"数据取自：{escape(table.title or table.id)}（含逐格引用与口径脚注）")
    return f"<figcaption>{'；'.join(pieces)}</figcaption>"


def _describe(chart: ChartBlock, table: TableBlock, columns: list[str]) -> str:
    labels = []
    for key in columns:
        column = table.column(key)
        labels.append(column.label if column else key)
    return (
        f"{chart.form} 图，{len(table.rows)} 个对象，指标：{'、'.join(labels)}。"
        f"完整数值见源表 {table.title or table.id}。"
    )


def _display_width(text: str) -> int:
    """近似显示宽度，半角计 1、全角计 2。

    中文报告里"裁到 N 个字符"是错的口径：24 个汉字在 12px 下约 288px，而标签区
    只有 140px，文字会溢出 viewBox 往左跑。按显示宽度裁才对得上版面。
    """
    return sum(2 if ord(ch) > 0x2E7F else 1 for ch in text)


def _clip(text: str, max_units: int) -> str:
    """按显示宽度裁剪，超出部分用省略号。"""
    value = text.strip()
    if _display_width(value) <= max_units:
        return value
    out: list[str] = []
    used = 0
    for ch in value:
        step = 2 if ord(ch) > 0x2E7F else 1
        if used + step > max_units - 1:  # 为省略号留一格
            break
        out.append(ch)
        used += step
    return "".join(out) + "…"


def _num(value: float | None) -> str:
    """确定性数字格式化：去掉无意义的尾随零，保证同一输入的 SVG 逐字节一致。"""
    if value is None:
        return ""
    if not math.isfinite(value):
        return "0"
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text or "0"
