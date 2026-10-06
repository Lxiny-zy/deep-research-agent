"""Offline calibration labels; semantic labels require an independent reviewer.

Run ``python -m eval.scientific_support_cases`` for the deterministic layer.
Passing an anchor check is deliberately not counted as semantic verification.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SupportCase:
    name: str
    category: str
    text: str
    quotes: tuple[tuple[int, str], ...]
    alignment: str
    semantic: str


CASES = (
    SupportCase(
        "number_match",
        "number",
        "Alpha error is 0.001 [1].",
        ((1, "Alpha error is 1e-3."),),
        "pass",
        "supported",
    ),
    SupportCase(
        "number_mismatch",
        "number",
        "Alpha error is 0.01 [1].",
        ((1, "Alpha error is 1e-3."),),
        "fail",
        "unsupported",
    ),
    SupportCase(
        "name_match",
        "proper_name",
        "Alpha uses sparse coding [1].",
        ((1, "Alpha uses sparse coding."),),
        "pass",
        "supported",
    ),
    SupportCase(
        "name_mismatch",
        "proper_name",
        "Alpha uses sparse coding [1].",
        ((1, "Beta uses sparse coding."),),
        "fail",
        "unsupported",
    ),
    SupportCase(
        "condition_kept",
        "condition",
        "Alpha scores 95 on indoor data [1].",
        ((1, "Alpha scores 95 on indoor data; outdoor data were not evaluated."),),
        "pass",
        "supported",
    ),
    SupportCase(
        "condition_swapped",
        "condition",
        "Alpha scores 95 on outdoor data [1].",
        ((1, "Alpha scores 95 on indoor data; outdoor data were not evaluated."),),
        "pass",
        "unsupported",
    ),
    SupportCase(
        "correlation_kept",
        "causality",
        "Alpha is correlated with lower error [1].",
        ((1, "Alpha is correlated with lower error; causality was not tested."),),
        "pass",
        "supported",
    ),
    SupportCase(
        "correlation_to_causation",
        "causality",
        "Alpha causes lower error [1].",
        ((1, "Alpha is correlated with lower error; causality was not tested."),),
        "pass",
        "unsupported",
    ),
    SupportCase(
        "separate_sources",
        "multiple_sources",
        "Alpha scored 95 [1]. Beta scored 90 [2].",
        ((1, "Alpha scored 95."), (2, "Beta scored 90.")),
        "pass",
        "supported",
    ),
    SupportCase(
        "source_numbers_swapped",
        "multiple_sources",
        "Alpha scored 90 [1]. Beta scored 95 [2].",
        ((1, "Alpha scored 95."), (2, "Beta scored 90.")),
        "fail",
        "unsupported",
    ),
    SupportCase(
        "conditional_causality",
        "condition_causality",
        "Alpha may reduce error if calibration holds [1].",
        ((1, "Alpha may reduce error if calibration holds; this has not been tested."),),
        "pass",
        "supported",
    ),
    SupportCase(
        "hypothesis_to_fact",
        "condition_causality",
        "Alpha reduces error [1].",
        ((1, "Alpha may reduce error if calibration holds; this has not been tested."),),
        "pass",
        "unsupported",
    ),
)


def evidence_for(case: SupportCase) -> list[dict]:
    return [
        {
            "id": f"e{index}",
            "citation": citation,
            "quote": quote,
            "source": f"fixture://{case.name}/{citation}",
        }
        for index, (citation, quote) in enumerate(case.quotes)
    ]


def evaluate() -> dict:
    from deep_research.workbench.support_alignment import alignment_diagnostics

    results = []
    for case in CASES:
        evidence = evidence_for(case)
        diagnostic = alignment_diagnostics(
            case.text,
            sorted({x[0] for x in case.quotes}),
            [item["id"] for item in evidence],
            evidence,
        )
        results.append(
            {
                "name": case.name,
                "category": case.category,
                "alignment_expected": case.alignment,
                "alignment_actual": diagnostic["status"],
                "alignment_matches_label": diagnostic["status"] == case.alignment,
                "semantic_label": case.semantic,
                "semantic_measured": False,
            }
        )
    return {
        "cases": results,
        "mechanical_passed": all(x["alignment_matches_label"] for x in results),
        "semantic_quality_claim": "not measured by this offline mechanical evaluation",
    }


if __name__ == "__main__":
    import json

    report = evaluate()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["mechanical_passed"] else 1)
