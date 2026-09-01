---
name: pptx
description: "Use this skill any time a .pptx file is involved in any way — as input, output, or both. This includes: creating slide decks, pitch decks, or presentations on top of eight complete visual design systems with a mechanical overflow gate; reading, parsing, or extracting text from any .pptx file (even if the extracted content will be used elsewhere, like in an email or summary); editing, modifying, or updating existing presentations; combining or splitting slide files; working with templates, layouts, speaker notes, or comments. Trigger whenever the user mentions \"deck,\" \"slides,\" \"presentation,\" or references a .pptx filename, regardless of what they plan to do with the content afterward. If a .pptx file needs to be opened, created, or touched, use this skill."
license: Proprietary. LICENSE.txt has complete terms
---

# PPTX

一份 `.pptx` 是 OOXML 部件的 ZIP 容器（ISO/IEC 29500）：`ppt/slides/` 下每页一个 XML，外加版式、母版、媒体与关系文件。用对象模型搭页，API 覆盖不到的才下到 XML。

## 先认路:三个入口

| 你要做的 | 走哪条 | 往下读 |
|---|---|---|
| **从零做一份 deck**（最常见） | 美学模板 + `deckkit` + `fitcheck` | 本文件下面全部 |
| **读/ 抽取**已有 pptx 的内容 | `markitdown` / `thumbnail.py` / `office/unpack.py` | 本文件末「读与改已有文件」 |
| **改**一份已有 deck、或套用户给的模板 | unpack → 改 → clean → pack | `editing.md` |
| 给 deck **配图**（封面视觉、示意图、图表） | 代码画图表 / image-2 出示意与封面 | `images.md` |

从零做的分工是：**你负责内容与构图决策，美学系统负责颜色字体比例，确定性程序负责验收。**

```
选一套美学模板  →  用 deckkit 搭页(样式 token 自动生效)
      ↓  fitcheck.py     机械门:算文字宽度查溢出、查中文缺 <a:ea>、查越界
      ↓  style_gate.py   机械门:每页背景必须来自 styles.json 里**同一套**模板
      ↓  逐页渲染        soffice(首选) 或 pptx_preview(无 LibreOffice 时)
      ↓  眼睛真看每一页   机械门看不见"擦边""撞色""留白失衡"
交付 .pptx (+ .pdf)
```

两道机械门都必须退出 0:

```bash
python3 .claude/skills/pptx/scripts/fitcheck.py deck.pptx
python3 .claude/skills/_shared/scripts/style_gate.py deck.pptx
```

## 硬规则

**一、必须选一套美学模板，不许自己配色。** 先读 `.claude/skills/pptx/styles/_index.md` 选定，再读那一份 `styles/<id>.md`。**只读选中的那一份**——八套是互斥的完整系统，读两份只会让你把它们混起来。