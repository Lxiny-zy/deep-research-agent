from __future__ import annotations

import json
from copy import deepcopy

from deep_research.workbench.support import SupportReviewer, SupportUnit, compact_evidence


def evidence():
    quote = 'Table\nModel A 33.18; Model B 32.67. "原始文本" α \\beta\n' * 25
    return [
        {
            "id": "a",
            "citation": 1,
            "source": "https://paper.test/one",
            "statement": "A",
            "quote": quote,
        },
        {
            "id": "b",
            "citation": 1,
            "source": "https://paper.test/one",
            "statement": "B",
            "quote": quote,
        },
        {
            "id": "c",
            "citation": 2,
            "source": "https://paper.test/two",
            "statement": "C",
            "quote": quote,
        },
        {
            "id": "d",
            "citation": 2,
            "source": "https://paper.test/two",
            "statement": "D",
            "quote": quote,
        },
    ]


def restore(records):
    known, result = {}, []
    for raw in records:
        item = dict(raw)
        if "quote_from" in item:
            reference = item.pop("quote_from")
            assert reference in known and reference != item["id"]
            earlier = known[reference]
            assert (earlier["citation"], earlier["source"]) == (item["citation"], item["source"])
            item["quote"] = earlier["quote"]
        known[item["id"]] = item
        result.append(item)
    return result


def test_quote_transport_is_lossless_and_does_not_mutate_canonical_evidence():
    original = evidence()
    before = deepcopy(original)
    packed = compact_evidence(original)
    assert original == before and restore(packed) == before
    assert packed[1]["quote_from"] == "a" and packed[3]["quote_from"] == "c"
    assert "quote" in packed[2]  # A different paper/citation never borrows a source identity.
    assert len(json.dumps(packed, ensure_ascii=False)) < len(
        json.dumps(original, ensure_ascii=False)
    )


def test_citation_subset_and_protocol_retry_have_no_dangling_quote_references():
    original = evidence()
    reviewer = SupportReviewer(None, original, 50000)
    units = [SupportUnit("unit", "A statement", citations=[2])]
    first = json.loads(reviewer._prompt(units))
    retry = json.loads(reviewer._prompt(units, {"unit": "invalid mapping"}))
    assert first["evidence"] == retry["evidence"]
    assert restore(first["evidence"]) == original[2:]
    assert {r["id"] for r in first["evidence"]} == {"c", "d"}


def test_a_changed_quote_is_not_served_from_an_old_transport_copy():
    original = evidence()
    reviewer = SupportReviewer(None, original, 50000)
    units = [SupportUnit("unit", "A statement", citations=[1])]
    assert "quote_from" in json.loads(reviewer._prompt(units))["evidence"][1]
    original[0]["quote"] += " Changed."
    packed = json.loads(reviewer._prompt(units))["evidence"]
    assert all("quote" in item for item in packed)
    assert restore(packed) == original[:2]


def test_short_quotes_ambiguous_ids_and_nonidentical_text_stay_inline():
    small = [
        {"id": "a" * 64, "citation": 1, "quote": "short"},
        {"id": "b" * 64, "citation": 1, "quote": "short"},
    ]
    assert compact_evidence(small) == small
    duplicate = evidence()[:2]
    duplicate[1]["id"] = duplicate[0]["id"]
    assert compact_evidence(duplicate) == duplicate
    changed = evidence()[:2]
    changed[1]["quote"] += " "
    assert compact_evidence(changed) == changed
