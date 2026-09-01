---
name: experiment-suite
description: Use when the user has a research question and needs a complete experiment package — design document, runnable code, results (measured or simulated with honest provenance), publication-grade figures, structured report. Single-stage, no Python runtime in the skill.
---

# Experiment Suite

## Overview

End-to-end experiment package builder. **Single stage, full quality from the start.** The agent (Claude Code / Cursor / Aider / Codex / …) writes everything directly using its own tools (Write, Bash, WebFetch, …). This skill contains procedure + reference playbooks + figure-example scripts — no Python runtime, no LLM SDK.

The substantive work is decomposed into reference playbooks under `references/`:

| Reference | Topic |
|---|---|
| `references/00-incremental-execution.md` | how to do this without losing work: batches, persistence, resume — **read first** |
| `references/01-design-depth.md` | what a real experiment design contains (motivation → hypothesis → datasets → baselines → metrics → ablations → budget) |
| `references/01a-data-contract.md` | runtime dataset binding: source, access route, version, split, reuse boundary — **and mandatory download-route rules (fast mirrors only, bandwidth floor, download on CPU before any GPU step)** |
| `references/02-code-quality.md` | code-skeleton standards — runnable `model.py`, `data.py`, `train.py`, `evaluate.py` |
| `references/03-results-protocol.md` | `results.json` schema; `measured` / `simulated` / `illustrative` provenance |
| `references/04-publication-figures.md` | publication-grade charts, multi-panel layouts, taste rules |
| `references/04a-figure-contract.md` | figure logic before plotting: conclusion, panel map, reviewer risk |
| `references/04b-figure-qa.md` | export bundle, editable text, statistics and image-integrity QA |
| `references/05-report-structure.md` | structured `experiment_report.md` (problem → design → method → results → analysis → limitations) |
| `references/06-quality-gate.md` | self-check before delivery |

Also: `figure_examples/` — publication-style matplotlib scripts plus a shared style kit the agent can use as starting points.

**Read the relevant reference _before_ writing, not after.** The full pass does not fit in a single turn — `references/00-incremental-execution.md` is the only execution mode that completes.

## When to Use

- User wants to "design an experiment" for a research question.
- User needs runnable code for a specific task (classification / forecasting / detection / …).
- User wants to compare methods and have a structured report at the end.
- User needs publication-quality figures of experimental results.

## When NOT to Use

- User only wants a quick code snippet (write code directly).