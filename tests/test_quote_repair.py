from __future__ import annotations

import json

from deep_research.agents.researcher import Researcher
from deep_research.guardrails import report_eligible
from deep_research.models import ExtractionCandidate, RepairFindingContent, Source
from deep_research.observability import Tracer
from deep_research.workbench.intake import _FixedSources
from deep_research.workbench.quote_repair import quote_options, resolve_quote
from tests.test_extraction_repair import URL, Extractor, Semantic, finding, repair

OPENING = "Traditional features are concentrated in strongly textured areas."
ENDING = "Only a few feature points can be detected in weakly textured regions."
QUOTE = OPENING + "\n" + ENDING
SOURCE = OPENING + "\nJournal 2022, 14. https://doi.org/10.1234/paper\n2 of 20\n" + ENDING


async def test_model_selects_exact_cross_page_quote_without_retyping_metadata(settings):
    class Select(Extractor):
        async def parse(self, system, user, schema, **kwargs):
            if self.requests:
                candidate = json.loads(user.rsplit("\n", 1)[1])["candidates"][0]
                option = candidate["quote_options"][0]
                assert option["text"] == SOURCE
                self.repairs = iter(
                    [
                        repair(
                            findings=[
                                dict(
                                    finding("Features depend on texture", "").model_dump(),
                                    quote_id=option["id"],
                                )
                            ]
                        )
                    ]
                )
            return await super().parse(system, user, schema, **kwargs)

    llm, semantic = Select([finding("Features depend on texture", QUOTE)], []), Semantic()
    researcher = Researcher(
        llm,
        _FixedSources([Source(url=URL, content=SOURCE)]),
        Tracer(),
        settings,
        semantic_verifier=semantic,
    )
    result = await researcher.run("Feature matching")
    assert semantic.calls == [["Features depend on texture"]]
    assert len(result.findings) == 1 and report_eligible(result.findings[0])
    assert result.findings[0].evidence_quote == SOURCE
    audit = result.extraction_audit.candidates[0]
    assert audit.original.evidence_quote == QUOTE
    assert audit.attempts[-1].proposals[0].evidence_quote == ""
    assert audit.attempts[-1].quote_ids == [audit.quote_options[0].id]
    assert audit.attempts[-1].checks[0].finding.evidence_quote == SOURCE
    assert llm.requests[0][1].prefix == llm.requests[1][1].prefix


def test_formula_control_glyph_is_retained_in_source_span_and_resolved_quote():
    quote = "The spectral correction is defined by the following equation: Y = X + sum. " + ENDING
    source = Source(url=URL, content=quote.replace("sum", "\b sum"))
    candidate = ExtractionCandidate(id="c1", original=finding("Formula", quote))
    candidate.quote_options = quote_options(candidate, [source])
    assert len(candidate.quote_options) == 1
    resolved, issues = resolve_quote(
        RepairFindingContent(
            statement="Formula", source_url=URL, quote_id=candidate.quote_options[0].id
        ),
        candidate,
        [source],
    )
    assert not issues and "\b" in resolved.evidence_quote
    assert resolved.evidence_quote == source.content


def test_nonunique_or_reversed_anchors_do_not_produce_a_guessed_span():
    candidate = ExtractionCandidate(id="c1", original=finding("Feature", QUOTE))
    assert not quote_options(candidate, [Source(url=URL, content=SOURCE + "\n" + SOURCE)])
    assert not quote_options(candidate, [Source(url=URL, content=ENDING + "\n" + OPENING)])
    assert not quote_options(candidate, [Source(url=URL, content="unrelated original source")])


def test_span_id_is_bound_to_candidate_source_hash_and_original_offsets():
    source = Source(url=URL, content=SOURCE)
    candidate = ExtractionCandidate(id="c1", original=finding("Feature", QUOTE))
    candidate.quote_options = quote_options(candidate, [source])
    proposal = RepairFindingContent(
        statement="Feature", source_url=URL, quote_id=candidate.quote_options[0].id
    )
    assert resolve_quote(proposal, candidate, [source])[1] == []
    assert resolve_quote(proposal, candidate, [source.model_copy(update={"content": "changed"})])[
        1
    ] == ["repair_quote_snapshot_mismatch"]
    other = candidate.model_copy(update={"id": "c2", "quote_options": []})
    assert resolve_quote(proposal, other, [source])[1] == ["repair_quote_id_not_allowed"]
    assert resolve_quote(
        proposal.model_copy(update={"evidence_quote": QUOTE}), candidate, [source]
    )[1] == ["repair_quote_reference_conflict"]
    candidate.quote_options[0].end += 1
    assert resolve_quote(proposal, candidate, [source])[1] == ["repair_quote_snapshot_mismatch"]


async def test_selecting_a_span_still_requires_semantic_support(settings):
    class Select(Extractor):
        async def parse(self, system, user, schema, **kwargs):
            if self.requests:
                candidate = json.loads(user.rsplit("\n", 1)[1])["candidates"][0]
                self.repairs = iter(
                    [
                        repair(
                            findings=[
                                dict(
                                    finding("bad unsupported inference", "").model_dump(),
                                    quote_id=candidate["quote_options"][0]["id"],
                                )
                            ]
                        )
                    ]
                )
            return await super().parse(system, user, schema, **kwargs)

    llm, semantic = Select([finding("bad unsupported inference", QUOTE)], []), Semantic()
    researcher = Researcher(
        llm,
        _FixedSources([Source(url=URL, content=SOURCE)]),
        Tracer(),
        settings,
        semantic_verifier=semantic,
    )
    result = await researcher.run("q")
    assert semantic.calls == [["bad unsupported inference"]]
    assert not any(report_eligible(f) for f in result.findings)
