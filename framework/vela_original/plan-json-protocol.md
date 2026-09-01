# .vela/plan.json — planner-driven agent contract

When this repo is invoked by a **planner-driven** Vela agent
(`agents.mode = 'planner-driven'`), the agent's only step (the
"planner") must write a file at `.vela/plan.json` in the workspace.
Vela then dynamically executes the steps listed there.

## Schema

```json
{
  "title": "≤40-char human label for the overall run",
  "steps": [
    {
      "id": "kebab-case-unique-id",
      "name": "≤20 字 中文步骤名 (UI 显示)",
      "prompt": "完整 prompt; 下一个 step 直接收到这串",
      "reset": true,
      "status": "pending"
    }
  ]
}
```

### Field rules

| field | type | required | notes |
|---|---|---|---|
| `title` | string | optional | overall task label |
| `steps[].id` | string | yes | kebab-case, unique across plan |
| `steps[].name` | string | yes | ≤20 chars, Chinese OK, displayed in UI |
| `steps[].prompt` | string | yes | full prompt verbatim |
| `steps[].reset` | bool | optional, default `true` | session reset between steps |
| `steps[].status` | string | optional, default `"pending"` | one of `pending`/`running`/`done`/`partial`/`failed`/`skipped` |

Vela worker invariants:
- duplicate `id` rejected → task fails fast with `PlanError`
- `status` outside the allowed set rejected
- empty / missing required fields rejected
- malformed JSON rejected

## Authoring guidelines

### Step count

Default range: **3–8 steps**. Push higher only when the user explicitly
asks for iteration (e.g. "跑 20 轮实验"). Each step must have one clear,
independently checkable goal; do not split steps merely to mirror timeout
windows. A step gets up to three 4-hour execution windows. At the first and
second timeout Vela saves progress and asks the agent to converge; after the
third window the step becomes `partial` and later steps continue. The whole
task is capped at 48 hours, with the last 4 hours reserved for a truthful final
report. Plans should therefore create useful persisted results incrementally,
not depend on every planned step succeeding before anything is deliverable.

### Paper by default (do not silently skip it)

If the plan contains experiment / training / evaluation steps, **append
paper-writer steps even when the user did not mention a paper** — real
research that produced results is worth writing up, and users repeatedly
report "experiments done, no paper" as a defect. Omit the paper stage
only when (a) the user explicitly declined one, or (b) the run is not
research-shaped (pure ops, data preparation, tooling). When you do omit
it, say so in the final step's deliverable summary so the omission is a
visible decision, not a silent gap.

### Reset semantics

Default `reset: true` for each step. Use `reset: false` only when the
next step *must* see the prior session's in-memory context. For most
research workflows the artefacts on disk are the contract; sessions
should be fresh.

### Status field

Authors should leave `status: "pending"` on every step in the freshly
written plan. The worker (or the step itself, when run) updates it.

If a step wants to declare itself complete it may overwrite its own
entry to `"done"`. The worker also applies a safety-net `"done"` after
each step in case the agent forgot (also applied if status was left as
`"running"`).

When useful work exists but the step cannot be completed, set `"partial"` and
record what is complete, the exact gap, and the relevant artefact paths. It is
a terminal step status, but the worker continues with later steps. Use
`"skipped"` only when the step should not run. Research limitations should not
be reported as infrastructure `"failed"`; authentication, storage, scheduling,
and similar execution failures are classified by the worker and may still fail
the task.

### Machine boundaries & GPU steps (read before splitting steps)

**Steps do NOT run on the same machine
…[truncated]