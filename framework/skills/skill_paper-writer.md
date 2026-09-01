---
name: paper-writer
description: Use when the user wants a complete, publication-grade research paper on a specific topic — produces 60–120 real citations (each one discussed in prose), 4–8 publication-grade figures, and 7 sections of substantive prose compiled to PDF in one pass. No skeleton stage.
---

# Paper Writer

## Overview

End-to-end research paper builder. **Single stage, full quality from the start** — there is no skeleton phase to enrich later. The agent (Claude Code / Cursor / Aider / Codex / …) does the writing using its own tools (WebFetch, WebSearch, Write, Bash). This SKILL has no Python runtime; it is purely a procedure + reference playbooks + a LaTeX template.

The substantive work is decomposed into reference playbooks under `references/`:

| Reference | Topic |
|---|---|
| `references/00-incremental-execution.md` | how to actually do this without losing work: batch sizes, persistence, resume — **read first** |
| `references/01-bibliography-expansion.md` | grow `bibliography.bib` to 60–120 real entries via WebFetch/WebSearch |
| `references/02-figures-publication-grade.md` | the two figure routes (image-2 for drawn, matplotlib/seaborn for computed), TikZ for true vector, multi-panel recipes |
| `references/03-section-playbook.md` | per-section structure, length, citation density |
| `references/04-layout-discipline.md` | tables, figures, floats, cross-refs, author + disclosure footnote |
| `references/05-quality-gate.md` | self-check before delivery (G1–G8 hard, S1–S5 soft) |
| `references/06-experiment-provenance.md` | honest provenance for every number (measured / simulated / illustrative) |
| `.claude/skills/_shared/references/academic-register.md` | formal Chinese/English titles and prose; colloquial-language exclusions |

**Read the relevant reference _before_ writing, not after.**

The full pass does not fit in a single turn. The bibliography is built across ~20+ small WebFetch/WebSearch batches; sections are drafted one per turn; figures are generated one at a time. **Read `references/00-incremental-execution.md` before starting** — it is the only execution mode that actually completes without losing work.

## When to Use

- User asks to "write a paper" / "写论文" on a specific topic.
- User wants Abstract + Introduction + Related Work + Method + Experiment + Results + Conclusion.
- User has experiment results (a `results.json`) and wants them formatted into a paper.

## When NOT to Use

- User wants only a literature survey → `literature-survey`.
- User wants only the experiment package → `experiment-suite`.
- User wants only direction/topic exploration → `research-explorer`.
- User wants the full multi-skill pipeline → `ai4s-agent` (which invokes this SKILL as one stage).