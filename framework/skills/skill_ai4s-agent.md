---
name: ai4s-agent
description: Use when the user wants an end-to-end AI4S research pipeline — broad direction or specific topic in, full research package out (exploration + literature survey + experiment + paper). Meta-SKILL that chains the four downstream skills in order. Pure markdown — no Python runtime.
---

# AI4S Agent (meta-SKILL)

## Overview

Top-level entry point for the AI4S research stack. This SKILL contains **no work of its own** — its only job is to call four downstream SKILLs in the right order, with the right slug, and reuse intermediate artefacts by path convention.

```
direction → research-explorer → topic
topic     → literature-survey  (60+ real bib, 100+ recommended)
topic     → experiment-suite   (design + code + results + figures)
topic     → paper-writer       (assembles into a 60–120-cite PDF)
```

Each downstream skill is **already single-stage and self-sufficient**: its agent loads that skill's SKILL.md and produces the full final-quality artefact directly. There is no skeleton/enrichment split. This meta-SKILL only handles ordering, the path convention, and disclosure consistency.

## When to Use

- User asks for "a paper on X" or "research package on X" and wants the whole stack run end-to-end.
- User wants to compare what each skill produces — useful for development/debugging the pipeline itself.

## When NOT to Use

- User wants to enrich only one stage (e.g., only the literature survey) → invoke that skill's SKILL directly.
- User wants only topic exploration → invoke `research-explorer` directly.

## The slug contract

Every skill computes the same slug from the same topic string:

```python
import re, hashlib
def slug(t):
    n = re.sub(r'[\s_]+', '-', re.sub(r'[^\w\s-]', '', t.lower().strip())).strip('-')[:40].rstrip('-')
    h = hashlib.sha1(t.encode()).hexdigest()[:8]
    return f"{n}-{h}"