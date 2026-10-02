from __future__ import annotations

from deep_research.agents.researcher import Researcher
from deep_research.guardrails import report_eligible
from deep_research.models import ExtractedFindingList, FindingContent
from deep_research.observability import Tracer
from deep_research.prompting import structured_system_prompt
from deep_research.workbench.intake import _FixedSources
from tests.fakes import FakeLLM

URL = "https://example.org/paper"
QUOTE = "Alpha has a measured value. Beta has another measured value."


def finding(statement="A", quote="Alpha has a measured value.", url=URL, **kwargs):
    return FindingContent(statement=statement, source_url=url, evidence_quote=quote, **kwargs)


class Semantic:
    def __init__(self):
        self.calls = []

    async def verify_batch(self, findings, llm, **kwargs):
        self.calls.append([item.statement for item in findings])
        return [
            item.model_copy(
                update={
                    "verification": item.verification.model_copy(
                        update={
                            "semantic_status": "unsupported"
                            if item.statement.startswith(("bad", "compound"))
                            else "supported",
                            "semantic_reason": "unsupported statement"
                            if item.statement.startswith(("bad", "compound"))
                            else "controlled support",
                            "semantic_confidence": 0.9,
                        }
                    )
                }
            )
            for item in findings
        ]


class Extractor(FakeLLM):
    def __init__(self, candidates, repairs):
        super().__init__()
        self.candidates, self.repairs = candidates, iter(repairs)
        self.requests = []

    async def parse(self, system, user, schema, **kwargs):
        assert schema is ExtractedFindingList
        self.requests.append((system, user))
        if len(self.requests) == 1:
            return ExtractedFindingList(findings=self.candidates)
        value = next(self.repairs)
        if isinstance(value, Exception):
            raise value
        return ExtractedFindingList.model_validate(value)


def repair(candidate="c1", findings=None, action="repair"):
    return {
        "repairs": [
            {
                "candidate_id": candidate,
                "action": action,
                "findings": findings or [],
                "reason": "use the supplied source",
            }
        ]
    }


async def run(settings, candidates, repairs, *, sources=None, rounds=1):
    from deep_research.models import Source

    settings.quality = {"extraction_max_revisions": rounds}
    llm, semantic = Extractor(candidates, repairs), Semantic()
    researcher = Researcher(
        llm,
        _FixedSources(sources or [Source(url=URL, content=QUOTE)]),
        Tracer(),
        settings,
        semantic_verifier=semantic,
    )
    result = await researcher.run("Compare A and B")
    return result, llm, semantic


async def test_repairs_failed_quote_only_and_reuses_the_same_prompt_prefix(settings):
    a, b = finding(), finding("B", "Beta has a DIFFERENT measured value.")
    result, llm, semantic = await run(
        settings,
        [a, b],
        [repair("c2", [finding("B", "Beta has another measured value.").model_dump()])],
    )
    assert all(report_eligible(item) for item in result.findings)
    assert [item.statement for item in result.findings] == ["A", "B"]
    assert semantic.calls == [["A"], ["B"]]
    assert llm.requests[0][0] == llm.requests[1][0]
    assert llm.requests[0][1].prefix == llm.requests[1][1].prefix
    assert '"candidate_id": "c2"' in llm.requests[1][1]
    assert '"candidate_id": "c1"' not in llm.requests[1][1]
    audit = result.extraction_audit
    assert audit.candidates[1].original.evidence_quote == b.evidence_quote
    assert audit.candidates[1].attempts[0].checks[0].problems == ["evidence_quote_not_found"]
    assert audit.candidates[1].accepted and len(audit.candidates[0].attempts) == 1
    assert audit.sources[0].content == QUOTE


async def test_numeric_rejection_is_not_sent_to_semantic_review_and_cannot_drop_quantity(settings):
    from deep_research.models import Source

    quote = "Alpha achieves PSNR 38.4 dB."
    original = finding("A", quote, quantity={"metric": "PSNR", "value": 99.0, "unit": "dB"})
    result, _, semantic = await run(
        settings,
        [original],
        [repair(findings=[finding("A", quote).model_dump()])],
        sources=[Source(url=URL, content=quote)],
    )
    assert not any(report_eligible(item) for item in result.findings)
    assert semantic.calls == []
    assert (
        "repair_removed_numeric_claim"
        in result.extraction_audit.candidates[0].attempts[1].checks[0].problems
    )


async def test_wrong_source_is_not_admitted_even_when_its_quote_is_valid(settings):
    from deep_research.models import Source

    result, _, semantic = await run(
        settings,
        [finding("bad", "not in source")],
        [
            repair(
                findings=[
                    finding(
                        "B", "Valid other source quotation", "https://example.org/other"
                    ).model_dump()
                ]
            )
        ],
        sources=[
            Source(url=URL, content=QUOTE),
            Source(url="https://example.org/other", content="Valid other source quotation"),
        ],
    )
    assert not result.findings and not semantic.calls
    assert (
        "repair_changed_source"
        in result.extraction_audit.candidates[0].attempts[1].checks[0].problems
    )


async def test_repair_cannot_replace_scientific_value_with_mantissa(settings):
    from deep_research.models import Source

    quote = "Our method's EOG is 2.996 × 108."
    original = finding(
        "EOG 2.996 × 108", quote, quantity={"value": 299600000, "rendered": "2.996 × 108"}
    )
    wrong = original.model_copy(deep=True)
    wrong.quantity.value = 2.996
    result, _, semantic = await run(
        settings,
        [original],
        [repair(findings=[wrong.model_dump()])],
        sources=[Source(url=URL, content=quote)],
    )
    assert not any(report_eligible(item) for item in result.findings)
    assert semantic.calls == []
    assert not result.extraction_audit.candidates[0].accepted
    assert (
        "ambiguous_scientific"
        in result.extraction_audit.candidates[0].attempts[1].checks[0].problems[0]
    )


async def test_unchanged_claim_cannot_reroll_a_failed_semantic_judgement(settings):
    original = finding("bad claim")
    result, llm, semantic = await run(
        settings, [original], [repair(findings=[finding("bad claim.").model_dump()])], rounds=3
    )
    assert len(llm.requests) == 2 and len(semantic.calls) == 1
    assert not result.extraction_audit.candidates[0].accepted
    assert (
        "unchanged_candidate"
        in result.extraction_audit.candidates[0].attempts[1].checks[0].problems
    )


async def test_split_repair_reuses_previously_accepted_parts(settings):
    result, llm, semantic = await run(
        settings,
        [finding("compound")],
        [
            repair(
                findings=[
                    finding("A").model_dump(),
                    finding("bad-B", "Beta has another measured value.").model_dump(),
                ]
            ),
            repair(
                findings=[
                    finding("A").model_dump(),
                    finding("B", "Beta has another measured value.").model_dump(),
                ]
            ),
        ],
        rounds=2,
    )
    assert [item.statement for item in result.findings] == ["A", "B"]
    assert semantic.calls == [["compound"], ["A", "bad-B"], ["B"]]
    assert result.extraction_audit.candidates[0].attempts[2].checks[0].reused
    assert result.extraction_audit.candidates[0].accepted and len(llm.requests) == 3


async def test_zero_finding_results_keep_original_candidate_and_drop_reason(settings):
    result, _, _ = await run(
        settings, [finding("missing", "not in source")], [repair(action="drop")]
    )
    assert result.findings == []
    assert result.extraction_audit.candidates[0].original.statement == "missing"
    assert result.extraction_audit.candidates[0].attempts[-1].action == "drop"


async def test_malformed_ids_stop_repair_without_restarting_extraction(settings):
    result, llm, _ = await run(
        settings, [finding("bad")], [repair("not-a-candidate", [finding().model_dump()])], rounds=4
    )
    assert len(llm.requests) == 2
    assert result.extraction_audit.candidates[0].attempts[-1].action == "error"
    assert not any(report_eligible(item) for item in result.findings)


async def test_zero_rounds_preserve_diagnostics_without_extra_call(settings):
    result, llm, _ = await run(settings, [finding("missing", "no original quote")], [], rounds=0)
    assert len(llm.requests) == 1 and len(result.extraction_audit.candidates) == 1


async def test_repair_transport_failure_does_not_restart_or_erase_valid_findings(settings):
    result, llm, _ = await run(
        settings, [finding(), finding("bad")], [TimeoutError("provider detail")], rounds=4
    )
    assert len(llm.requests) == 2
    assert result.extraction_audit.candidates[0].accepted
    assert (
        result.extraction_audit.candidates[1].attempts[-1].reason
        == "repair_call_failed:TimeoutError"
    )


async def test_diagnostics_do_not_change_existing_prose_signature(settings):
    from deep_research.models import ExtractionAudit, ResearchResult
    from deep_research.workbench.prose_review import ProseReviewer
    from tests.fakes import verified_finding

    result = ResearchResult(sub_question="q", findings=[verified_finding()])
    mapping = {result.findings[0].source_url: 1}
    before = ProseReviewer.research(None, [result], mapping, 0).signature("body")
    result.extraction_audit = ExtractionAudit(question="q", issues=["diagnostic only"])
    assert ProseReviewer.research(None, [result], mapping, 0).signature("body") == before


async def test_capacity_fallback_keeps_the_complete_failed_source(settings):
    from deep_research.models import Source

    class Capacity(Extractor):
        async def parse(self, system, user, schema, **kwargs):
            if not self.requests:
                self.input_capacity_chars = (
                    len(structured_system_prompt(system, schema)) + len(user) + 100
                )
            return await super().parse(system, user, schema, **kwargs)

    settings.quality = {"extraction_max_revisions": 1}
    source = Source(url=URL, content="Full failed source contains END-ORIGINAL-MARKER.")
    other = Source(url="https://example.org/large", content="UNRELATED MATERIAL " * 600)
    llm = Capacity(
        [finding("A", "bad quote")], [repair(findings=[finding("A", source.content).model_dump()])]
    )
    researcher = Researcher(
        llm, _FixedSources([other, source]), Tracer(), settings, semantic_verifier=Semantic()
    )
    result = await researcher.run("q")
    assert result.extraction_audit.candidates[0].accepted
    assert "END-ORIGINAL-MARKER" in llm.requests[1][1]
    assert "UNRELATED MATERIAL" not in llm.requests[1][1]


async def test_scheduler_keeps_zero_finding_diagnostics():
    from deep_research.models import ExtractionAudit, ResearchResult, SubQuestion
    from deep_research.scheduler import research_dag

    audit = ExtractionAudit(question="q", issues=["rejected candidate"])

    async def execute(question, context):
        return ResearchResult(sub_question=question, extraction_audit=audit)

    results = await research_dag([SubQuestion(question="q", rationale="r")], execute, Tracer())
    assert len(results) == 1 and results[0].extraction_audit == audit


async def test_duplicate_repairs_cannot_inflate_verified_finding_count(settings):
    response = {
        "repairs": [
            {
                "candidate_id": candidate,
                "action": "repair",
                "findings": [finding("A").model_dump()],
                "reason": "supported part",
            }
            for candidate in ("c1", "c2")
        ]
    }
    result, _, semantic = await run(settings, [finding("bad-A"), finding("bad-B")], [response])
    assert sum(report_eligible(item) for item in result.findings) == 1
    assert semantic.calls == [["bad-A", "bad-B"], ["A"]]


async def test_accepted_split_parts_survive_a_later_transport_failure(settings):
    result, _, _ = await run(
        settings,
        [finding("compound")],
        [
            repair(findings=[finding("A").model_dump(), finding("bad-B").model_dump()]),
            TimeoutError("not logged"),
        ],
        rounds=2,
    )
    assert [finding.statement for finding in result.findings if report_eligible(finding)] == ["A"]
    assert not result.extraction_audit.candidates[0].accepted


async def test_word_boundaries_are_not_erased_when_checking_meaningful_edits(settings):
    from deep_research.workbench.extraction import _content_key

    assert _content_key(finding("notable")) != _content_key(finding("not able"))
    assert _content_key(finding("value 1.2")) != _content_key(finding("value 12"))
