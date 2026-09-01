---
name: image-gen
description: Use for any figure whose content is drawn rather than computed — architecture/framework diagram, method schematic, taxonomy, mechanism or concept illustration, cover art — in a paper, a survey, a research report or any other written deliverable. One prompt, one call, one image. Do NOT use for charts computed from data (that is matplotlib, see experiment-suite) or for figures needing exact vector typography (that is TikZ/LaTeX).
---

# Image generation (one prompt, one image)

Unattended by design. You write one good prompt, you get one image, you check
it, you ship it. There is no candidate matrix, no audit stage, no revision
loop — those cost several images' worth of tokens per figure and need a human
in the loop to pay off.

## Is this the right route?

**The question is always the same: is this figure's content *computed* or
*drawn*?** Computed → code. Drawn → this skill.

| The ask | Route |
|---|---|
| bar/line/scatter/heatmap/timeline **computed from data** | matplotlib — `.claude/skills/experiment-suite` figure playbook |
| **architecture / framework / method diagram**, taxonomy tree, mechanism schematic, conceptual illustration | **this skill** |
| figure needing **exact vector typography** — journal final art, formulas typeset inside the figure — or a fallback when this route is down | TikZ/LaTeX standalone |

If the deliverable is "a picture of how this thing works" and nobody is
sitting there to choose between drafts, this skill is the route.

**This applies to every written deliverable, not just papers**: a literature
survey's taxonomy and mechanism figures, a research report's landscape map, an
article's explanatory schematic. The only paper-specific entry point is
`.claude/skills/paper-writer/scripts/figure-studio/generate_paper_framework_once.py`, which derives the
prompt from a finished manuscript PDF; when you already know what the figure
must show, come straight here. There is no per-deliverable cap on how many
figures use this route — the limit is **one call per figure**.

## Route configuration

Set by the deployment, not by you. All three come from the runtime environment:

- `OPENAI_BASE_URL` — the LLM gateway. **No default**; unset fails loudly.
- `OPENAI_API_KEY` — a gateway *virtual* key, scoped to the image model.