---
name: pdf
description: Use this skill whenever the user wants to do anything with PDF files. This includes reading or extracting text/tables from PDFs, combining or merging multiple PDFs into one, splitting PDFs apart, rotating pages, adding watermarks, creating new PDFs, filling PDF forms, encrypting/decrypting PDFs, extracting images, and OCR on scanned PDFs to make them searchable. If the user mentions a .pdf file or asks to produce one, use this skill.
license: Proprietary. LICENSE.txt has complete terms
---

# PDF Processing Guide

## 本仓路由:交付用的 PDF 不在这里生成

这份 skill 管的是**处理**已有 PDF(读、抽、合、拆、加水印、OCR)。要**交付**一份 PDF 时,
它的样式由上游决定,不许在这里从 Markdown 直接转:

| 交付物 | 由谁生成 |
|---|---|
| 报告/分析/方案/指南/备忘 | 定稿 DOCX(Apevon 基底)→ `soffice --headless --convert-to pdf` |
| 论文 / 综述 | `paper-writer` / `literature-survey` 的 LaTeX 模板,`compile.sh`(xelatex) |

机械门:`python3 .claude/skills/_shared/scripts/style_gate.py <out>.pdf`(论文/综述加 `--kind paper`)
—— 它查 `/Producer`,报告类必须是 LibreOffice、论文类必须是 TeX。详见
`.claude/skills/_shared/references/delivery-contract.md`「样式起点」。

## Overview

This guide covers essential PDF processing operations using Python libraries and command-line tools. For advanced features, JavaScript libraries, and detailed examples, see REFERENCE.md. If you need to fill out a PDF form, read FORMS.md and follow its instructions.

## Quick Start

```python
from pypdf import PdfReader, PdfWriter

# Read a PDF
reader = PdfReader("document.pdf")
print(f"Pages: {len(reader.pages)}")

# Extract text
text = ""
for page in reader.pages:
    text += page.extract_text()
```