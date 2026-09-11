# Benchmark Baselines

Store reviewed benchmark JSON files here. A baseline is not generated automatically because it
represents an explicit quality/cost decision rather than merely the latest run.

The current judge protocol is `source-snapshots-v2`. Each benchmark JSON freezes the report
Markdown, source snapshots and their content hashes, in addition to run IDs and deterministic
metrics. The judge receives bounded source content and reports source counts and truncation.
When no snapshot exists, `groundedness` is `null` and the mean only includes the other dimensions;
missing evidence must not produce a fabricated evidence-support score. A score based on truncated
snapshots is still a judgment of that limited context, not a proof of every report claim.

Regression comparison rejects differing judge protocols. Regenerate old candidates under v2
before comparing, and review the resulting reports and frozen sources again. Newly generated
files have `review_status: "unreviewed"`; generation or a passing automated gate does not mark
them as reviewed. Use repeated runs and more than one model when establishing a release baseline.

Recommended flow (the list may include internal compatibility strategies for
benchmark comparison; the product template gallery intentionally exposes only
`deep`, `quick`, and `hsi_review`):

```bash
python -m eval.run_eval --workflows deep,quick,reviewed,auto,teams \
  --output eval/results/candidate.md

# After reviewing every linked run ID and accepting the result:
cp eval/results/candidate.json eval/baselines/main.json

# Candidate/CI gate:
python -m eval.run_eval --workflows deep,quick,reviewed,auto,teams \
  --output eval/results/candidate.md --baseline eval/baselines/main.json
```

Do not approve a baseline only because the gate passes. Inspect report quality, evidence snapshots,
conflicts, token use, and latency before replacing a protected baseline.

`hsi_gold.json` is a separate deterministic HSI annotation fixture. Its current
`annotation_status` is `curated_draft`: every value carries arXiv/DOI, section, and quote
provenance, but it is intentionally not a release gold set until a second annotator reviews it
and additional papers are added.
