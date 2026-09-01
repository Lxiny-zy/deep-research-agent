# Benchmark Baselines

Store reviewed benchmark JSON files here. A baseline is not generated automatically because it
represents an explicit quality/cost decision rather than merely the latest run.

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
