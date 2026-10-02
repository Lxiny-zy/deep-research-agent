from __future__ import annotations

import json
from copy import deepcopy

import pytest

from deep_research.prompting import structured_system_prompt
from deep_research.workbench.support import (
    SupportDecisions,
    SupportReviewer,
    SupportUnit,
    compact_evidence,
)


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


class RecordingJudge:
    def __init__(self):
        self.requests = []

    async def parse(self, system, user, schema, **kwargs):
        data = json.loads(user)
        self.requests.append((system, user, data))
        return SupportDecisions(
            decisions=[
                {
                    "unit_id": unit["id"],
                    "verdict": "supported",
                    "evidence_ids": [
                        item["id"]
                        for item in data["evidence"]
                        if item["citation"] in unit["citations"]
                    ],
                    "reason": "fixture support",
                }
                for unit in data["units"]
            ]
        )


def interleaved_units():
    # Every old batch includes a broad heading, even though most paragraphs
    # cite only one paper. Each unit has its own explicit discourse context.
    return [
        SupportUnit(
            f"unit-{i}",
            f"Paragraph {i}",
            context=f"Section {i // 16}; previous paragraph {i - 1}",
            kind="summary" if i % 16 == 0 else "prose",
            citations=[1, 2] if i % 16 == 0 else [1 if i < 32 else 2],
        )
        for i in range(64)
    ]


async def test_batching_reduces_repeated_corpus_with_full_scope_and_stable_decisions():
    original, units = evidence(), interleaved_units()
    before = deepcopy((original, units))
    judge = RecordingJudge()
    reviewer = SupportReviewer(judge, original, 50000)
    baseline = sum(len(reviewer._prompt(units[i : i + 16])) for i in range(0, len(units), 16))
    decisions = await reviewer.review(units)
    assert len(judge.requests) == 4
    assert sum(len(user) for _, user, _ in judge.requests) < baseline
    assert [d.unit_id for d in decisions] == [unit.id for unit in units]
    assert all(d.verdict == "supported" for d in decisions)
    assert (original, units) == before
    sent = []
    for _, _, request in judge.requests:
        sent.extend(request["units"])
        citations = {c for unit in request["units"] for c in unit["citations"]}
        assert restore(request["evidence"]) == [
            item for item in original if item["citation"] in citations
        ]
    assert sorted(sent, key=lambda unit: unit["id"]) == sorted(
        [vars(unit) for unit in units], key=lambda unit: unit["id"]
    )
    assert len({unit["id"] for unit in sent}) == len(units)
    # A different batch arrangement does not invalidate unchanged unit evidence.
    assert await reviewer.review(units) == decisions
    assert len(judge.requests) == 4


@pytest.mark.parametrize("capacity", [6000, 10000, 50000])
async def test_grouped_batches_respect_capacity_and_never_add_calls_or_input(capacity):
    original, units = evidence(), interleaved_units()
    judge = RecordingJudge()
    reviewer = SupportReviewer(judge, original, capacity)
    system_size = len(structured_system_prompt(reviewer.system, SupportDecisions))
    fits = [u for u in units if system_size + len(reviewer._prompt([u])) <= capacity]
    baseline = []
    batch = []
    for unit in fits:
        trial = [*batch, unit]
        if batch and (len(trial) > 16 or system_size + len(reviewer._prompt(trial)) > capacity):
            baseline.append(reviewer._prompt(batch))
            batch = []
        batch.append(unit)
    if batch:
        baseline.append(reviewer._prompt(batch))
    decisions = await reviewer.review(units)
    assert len(judge.requests) <= len(baseline)
    assert sum(system_size + len(user) for _, user, _ in judge.requests) <= sum(
        system_size + len(prompt) for prompt in baseline
    )
    for _, user, request in judge.requests:
        assert system_size + len(user) <= capacity
        assert len(request["units"]) <= 16
    sent = {unit["id"] for _, _, request in judge.requests for unit in request["units"]}
    assert sent == {unit.id for unit in fits}
    assert all(
        decision.verdict == ("supported" if unit.id in sent else "uncertain")
        for unit, decision in zip(units, decisions, strict=True)
    )


async def test_regrouping_does_not_allow_evidence_from_another_unit_or_poison_reuse():
    class WrongSource(RecordingJudge):
        async def parse(self, system, user, schema, **kwargs):
            result = await super().parse(system, user, schema, **kwargs)
            for decision in result.decisions:
                if decision.unit_id == "unit-1":
                    decision.evidence_ids = ["c"]  # unit-1 only cites paper 1.
            return result

    reviewer = SupportReviewer(WrongSource(), evidence(), 50000)
    units = interleaved_units()
    decisions = await reviewer.review(units)
    assert decisions[1].verdict == "uncertain"
    assert all(d.verdict == "supported" for i, d in enumerate(decisions) if i != 1)
    requests = len(reviewer.llm.requests)
    await reviewer.review(units)
    assert all(
        [unit["id"] for unit in data["units"]] == ["unit-1"]
        for _, _, data in reviewer.llm.requests[requests:]
    )


async def test_changed_evidence_rechecks_only_units_bound_to_that_citation():
    original, units = evidence(), interleaved_units()
    judge = RecordingJudge()
    reviewer = SupportReviewer(judge, original, 50000)
    await reviewer.review(units)
    count = len(judge.requests)
    original[0]["quote"] += " Changed source condition."
    await reviewer.review(units)
    new_requests = [data for _, _, data in judge.requests[count:]]
    assert {unit["id"] for data in new_requests for unit in data["units"]} == {
        unit.id for unit in units if 1 in unit.citations
    }
    assert any(
        item.get("quote", "").endswith("Changed source condition.")
        for data in new_requests
        for item in data["evidence"]
    )
