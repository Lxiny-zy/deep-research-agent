# Research Quality Upgrade

## Objective

Turn the project from a feature-rich research agent into a measurable research runtime whose
results can be reproduced, audited, and compared across workflow and model versions.

## Delivered: Phase 1

1. **Run manifest** — durable workflow/query/catalog hashes, non-secret behavior settings, model
   identity, sanitized endpoint, and retrieval backend.
2. **Retrieval snapshots** — every unique URL/content version returned by search is persisted
   before source-policy filtering and LLM extraction, under the same execution lease as the
   workflow. Transient persistence failures are audited without discarding successful retrievals.
3. **Deterministic metrics** — evidence verification, semantic support, report eligibility,
   corroboration/conflict counts, citation snapshot coverage, source diversity, policy blocks,
   tokens, and latency.
4. **API contract** — run detail returns `manifest`, `sources`, `events`, and `metrics` alongside
   existing artifacts.

## Delivered: Phase 2 Benchmark Matrix

- The same versioned dataset runs across `quick`, `deep`, `reviewed`, `auto`, and `teams`.
- Every benchmark cell is persisted as a normal research run and linked by `run_id`.
- Markdown output combines judge dimensions, deterministic evidence metrics, cost, and latency.
- JSON output includes manifests, metrics, summaries, and a SHA-256 over detail rows.
- `--no-persist-runs` remains available for disposable local experiments.

## Delivered: Regression Gates

- Configurable minimum citation-snapshot coverage.
- Maximum conflict and unsupported-claim rates for every benchmark cell.
- Candidate-to-baseline judge score and token-cost comparisons.
- Fail-closed handling for missing metrics, missing cells, and dataset drift.
- Exit code `2` when any gate fails, suitable for CI release checks.

## Baseline Workflow

1. Run the full matrix on the protected branch and review the linked run IDs.
2. Copy the approved JSON to `eval/baselines/main.json`.
3. Run candidate branches with `--baseline eval/baselines/main.json`.
4. Update the baseline only when the quality/cost tradeoff is intentionally accepted.

## Phase 3: Evidence Provenance

- Classify primary, secondary, official, academic, news, and community sources.
- Detect syndicated or near-duplicate pages using normalized content fingerprints.
- Link secondary reporting to original publications when citations are discoverable.
- Apply query-aware freshness checks for “latest”, “current”, and date-bounded claims.

## Acceptance Criteria

- Every newly persisted run has a manifest and source snapshots when search returns results.
- No secret is serialized into the manifest.
- Exact repeated snapshots do not create duplicate rows; changed content for the same URL is kept.
- Strict corroboration changes report-eligibility metrics deterministically.
- Benchmark reports can trace every score back to a durable run ID and manifest.
- Regression gates fail when evidence quality, judge score, or cost exceeds policy tolerances.
