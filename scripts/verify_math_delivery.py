"""Generate a reproducible mathematical review sample without model calls."""

from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pymupdf
from PIL import Image, ImageDraw

from deep_research.workbench.delivery.docx import render_docx
from deep_research.workbench.delivery.html import render_html
from deep_research.workbench.delivery.pdf import render_pdf

SAMPLE = r"""# 估计误差与 $L_2$ 正则化：综述排版验收样稿

本文件使用合成内容检查交付格式，不代表真实研究结论。

## 摘要

比较均方误差、线性表示与分段函数的数学表达。行内分式
$\frac{a+b}{c+d}$ 与上下标 $x_i^2$ 应与正文基线衔接，中文标注应完整可见。

## 指标与表达式

均方误差用于汇总逐项误差，具体推论仍需数据与证据支持 [1]。

$$
\text{误差}=\frac{1}{N}\sum_{i=1}^{N}(y_i-\hat{y}_i)^2
$$

矩阵表示与多行推导：

\[
\begin{bmatrix}a&b\\c&d\end{bmatrix}
\begin{bmatrix}x\\y\end{bmatrix}
=\begin{bmatrix}ax+by\\cx+dy\end{bmatrix}
\]

$$
\begin{aligned}y&=Ax+b\\z&=Cy+d\end{aligned}
$$

## 分段表达与中文下标

```math
f(x)=\begin{cases}x^2&x>0\\0&x\le0\end{cases}
```

中文下标 $x_{\text{训练}}$ 和 $x_{\text{验证}}$ 应保持区分。
向量 $v=[1,2]$ 的方括号是公式的一部分；本句文献编号为 [1]。

## 代码与货币

Cost $5 and $10. 行内代码 `$x_i$` 与以下代码均应原样保留：

```python
values = [1, 2]
formula = r"\frac{a}{b}"
```

## 跨页公式表格

表 1 合成指标及其表达式

| 指标 | 数学表达式 | 说明 |
|---|---|---|
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    destination = parser.parse_args().output
    destination.mkdir(parents=True, exist_ok=True)
    rows = [
        rf"| 指标 {i} | $e_{{{i}}}=\frac{{a+b+c+d}}{{N}}$ | 合成表达式，用于检查分页与清晰度。 |"
        for i in range(1, 36)
    ]
    body = SAMPLE + "\n".join(rows)
    body += "\n\n![图 1 合成趋势示意](trend.png)\n\n"
    body += "## 结论与适用范围\n\n公式结构与引用编号应在全部交付格式中保持一致。\n\n"
    body += "## 参考来源\n\n[1] 格式验收用合成材料，不对应外部研究文献。\n"
    figure = Image.new("RGB", (640, 220), "white")
    draw = ImageDraw.Draw(figure)
    draw.line([(30, 20), (30, 190), (610, 190)], fill="#334155", width=2)
    draw.line(
        [(30, 155), (130, 130), (230, 145), (360, 65), (480, 80), (600, 30)],
        fill="#2563eb",
        width=4,
    )
    image = io.BytesIO()
    figure.save(image, format="PNG")
    images = {"trend.png": image.getvalue()}
    title = "综述数学排版验收"
    pdf = render_pdf(body, title=title, images=images)
    word = render_docx(body, title=title, images=images)
    (destination / "review.md").write_text(body, encoding="utf-8")
    (destination / "review.html").write_text(
        render_html(body, title=title, images=images), encoding="utf-8"
    )
    (destination / "review.pdf").write_bytes(pdf)
    (destination / "review.docx").write_bytes(word)
    root = ET.fromstring(ZipFile(io.BytesIO(word)).read("word/document.xml"))
    math_count = len(
        root.findall(".//{http://schemas.openxmlformats.org/officeDocument/2006/math}oMath")
    )
    with pymupdf.open(stream=pdf, filetype="pdf") as document:
        text = "".join(page.get_text() for page in document)
        assert "x_{\\text{训练}}" in text and "x_{\\text{验证}}" in text
        for i in range(1, 36):
            assert text.count(rf"e_{{{i}}}=\frac{{a+b+c+d}}{{N}}") == 1
        raster_count = sum(len(page.get_image_info()) for page in document)
        assert raster_count == 1
        for i in range(len(document)):
            document[i].get_pixmap(matrix=pymupdf.Matrix(1.4, 1.4)).save(
                destination / f"page-{i + 1}.png"
            )
        summary = {
            "pages": len(document),
            "word_equations": math_count,
            "pdf_raster_figures": raster_count,
            "table_equations_preserved": 35,
            "model_calls": 0,
        }
    (destination / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
