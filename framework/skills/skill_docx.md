---
name: docx
license: MIT
metadata:
  version: "2.1.0"
  category: document-processing
  author: "MiniMaxAI foundation; customized for Apevon"
  derived-from: "minimax-docx@1.1.0"
  sources:
    - "ECMA-376 Office Open XML File Formats"
    - "GB/T 9704-2012 Layout Standard for Official Documents"
    - "IEEE / ACM / APA / MLA / Chicago / Turabian Style Guides"
    - "Springer LNCS / Nature / HBR Document Templates"
description: >
  Create, edit, and format professional DOCX files without .NET, using docx-js for new
  documents and Python/direct OOXML for safe package editing. Includes a customized Chinese
  report profile with Songti fallback, Times New Roman for non-Chinese text, spacious
  no-callout layouts, WPS/Pages-safe tables, and built-in Apevon Science branding and assets.
  Use for Chinese reports, formal Word deliverables, template restyling, contracts, proposals,
  tracked edits, or when the user explicitly requests docx-skill.
---

# docx-skill

Create, edit, and format DOCX files without .NET. Use `docx-js` for new documents and Python standard-library OOXML package editing for existing documents and templates.

## Runtime

In Codex desktop, first load the bundled workspace dependencies. Use the returned Node.js executable, Node modules path, and Python executable; do not install a second runtime when the bundle is available.

Before the first DOCX operation in a session, run:

```bash
DOCX_SKILL_NODE="<bundled-node>" \
DOCX_SKILL_NODE_MODULES="<bundled-node_modules>" \
DOCX_SKILL_PYTHON="<bundled-python>" \
bash scripts/env_check.sh
```

Missing .NET must never block this skill; the package contains no .NET backend. Outside Codex, `bash scripts/setup.sh` or `scripts/setup.ps1` can create a local `docx-js` runtime.