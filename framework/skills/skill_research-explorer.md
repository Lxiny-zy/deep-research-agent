---
name: research-explorer
description: Use when the user has a vague research direction and wants to explore feasible specific topics. Outputs a structured analysis with candidate topics, innovation/feasibility scoring, and a pre-survey of 20–30 representative works. Single-stage, no Python runtime.
---

# Research Explorer

## Overview

Research-topic exploration SKILL. Takes a broad direction, performs multi-dimensional web research with the agent's own WebSearch / WebFetch tools, and produces three structured Markdown deliverables. **Single stage, full quality from the start.** No Python runtime, no LLM SDK.

## When to Use

- User says "I want to research X" / "我对 X 感兴趣" without a specific topic.
- User wants to know "what are the hot topics in X".
- User needs help narrowing a broad field into 5–10 candidate topics.
- User asks for "research landscape overview" / "选题分析".

## When NOT to Use

- User already has a specific research question → use `literature-survey` or `paper-writer`.
- User wants a quick fact-check → use WebSearch directly.

## Workflow

### Step 1 — Understand the direction

Settle these from the step prompt, the attachments and the upstream artefacts —
**not by asking**. This skill runs unattended (repo-root `AGENTS.md` § 运行环境):
nobody will answer. Where the input is silent, take the stated default, write the
choice and its reason into the run's first artefact, and keep going:

- **Direction** — the broad area of interest (e.g., "federated learning", "NLP for healthcare").
- **Constraints** — theory vs. applied, specific methods, target venue, compute budget, time horizon.
- **Language** — reports in Chinese unless the input asks otherwise.

### Step 2 — Set up the run directory

```bash
DIRECTION="<direction>"