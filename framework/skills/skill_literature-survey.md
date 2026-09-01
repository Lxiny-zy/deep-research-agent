---
name: literature-survey
description: Use when the user wants a comprehensive literature survey on a specific research topic. Outputs a complete PDF survey (6–20 pages, 60+ real citations, 100+ recommended) with LaTeX source, taxonomy figures, and a classified literature table. Single-stage, no Python runtime.
---

# Literature Survey

## Overview

End-to-end literature survey builder. **Single stage, full quality from the start.** The agent (Claude Code / Cursor / Aider / Codex / …) does the entire build using its own tools (WebFetch, WebSearch, Write, Bash). This SKILL is procedure + reference playbooks + LaTeX template — no Python runtime, no LLM SDK.

The substantive work is decomposed into reference playbooks under `references/`:

| Reference | Topic |
|---|---|
| `references/00-incremental-execution.md` | how to actually do this without losing work: batch sizes, persistence, resume — **read first** |
| `references/01-bibliography-expansion.md` | grow `bibliography.bib` to 60+ real entries (100+ recommended) via WebFetch (no memory) |
| `references/02-survey-figures.md` | taxonomy / timeline / coverage-matrix / area-map figures |
| `references/03-survey-section-playbook.md` | per-section structure for survey-shaped papers |
| `references/04-layout-discipline.md` | tables, figures, floats, cross-refs, author + disclosure footnote |
| `references/05-quality-gate.md` | self-check before delivery |
| `.claude/skills/_shared/references/academic-register.md` | formal Chinese/English titles and prose; colloquial-language exclusions |

**Read the relevant reference _before_ writing, not after.** The full pass does not fit in a single turn — `references/00-incremental-execution.md` is the only execution mode that completes.

## When to Use

- User asks for a "survey" / "review" / "文献综述" on a specific topic.
- User has a research topic and wants a structured map of the field with citations.
- User needs background reading curated for a thesis chapter or grant section.

## When NOT to Use

- User wants original research with experiments → `paper-writer`.
- User wants only an outline / topic exploration → `research-explorer`.
- User wants experiment code → `experiment-suite`.
- Topic is too broad (e.g., "all of AI") — narrow it before starting.

## Workflow