"""Historical admission must not bypass the current finding-level policy."""

import hashlib

import pytest

from deep_research.agents.base import RunContext
from deep_research.agents.researcher import Researcher
from deep_research.guardrails import SemanticEvidenceDecisionList, SemanticEvidenceVerifier
from deep_research.models import ExtractedFindingList
from deep_research.observability import Tracer
from deep_research.persistence.repository import LeaseLostError
from deep_research.prompting import EVIDENCE_MODALITY_RULES
from deep_research.workbench.paper_evidence import PaperEvidenceSelection, current_findings
from deep_research.workbench.qa import answer_question
from deep_research.workbench.qa_cache import PaperEvidenceCache, evidence_cache_key
from tests.fakes import FakeSearch, verified_finding
from tests.test_evidence_modality import (
    QUOTE,
    REJECTED,
    SUPPORTED,
    URL,
    ModalityJudge,
    Sc82Search,
)


class HistoricalJudge(ModalityJudge):
    def __init__(self):
        super().__init__()
        self.extractions = 0

    async def parse(self, system, user, schema, **kwargs):
        if schema is ExtractedFindingList:
            self.extractions += 1
        if schema is PaperEvidenceSelection:
            return PaperEvidenceSelection(sufficient=True, finding_ids=["e1"])
        return await super().parse(system, user, schema, **kwargs)


async def inputs(settings, model=None):
    sources = await Sc82Search().search("paper")
    candidates = [verified_finding(text, URL, QUOTE) for text in (REJECTED, SUPPORTED)]
    for finding in candidates:
        finding.verification.source_content_hash = hashlib.sha256(QUOTE.encode()).hexdigest()
    judge = model or HistoricalJudge()
    researcher = Researcher(llm=judge, settings=settings)
    return sources, candidates, researcher


async def test_historical_supported_verdict_is_rechecked_under_current_modality_rules(settings):
    sources, candidates, researcher = await inputs(settings)
    old = [finding.model_dump(mode="json") for finding in candidates]
    admitted = await current_findings(candidates, sources, researcher)
    assert [finding.statement for finding in admitted] == [SUPPORTED]
    assert researcher.llm.evidence_systems
    assert admitted[0].verification.quote_start == 0
    assert admitted[0].verification.quote_end == len(QUOTE)
    assert [finding.model_dump(mode="json") for finding in candidates] == old


async def test_sc82_historical_rejection_cannot_reappear_in_fallback_or_exact_cache(settings):
    sources, candidates, researcher = await inputs(settings)
    ctx = RunContext(
        llm=researcher.llm, search_tool=FakeSearch(), tracer=Tracer(), settings=settings
    )
    cache = PaperEvidenceCache()
    for _ in range(2):
        result = await answer_question(
            "单一全局阈值是否意味着必须按组标定？",
            history=[],
            ctx=ctx,
            paper_sources=sources,
            paper_evidence=candidates,
            paper_cache=cache,
            cache_scope="sc82/task",
        )
        assert result.fallback and SUPPORTED in result.answer
        assert REJECTED not in result.answer
        assert [finding.statement for finding in result.findings] == [SUPPORTED]
    assert researcher.llm.extractions == 0
    assert len(researcher.llm.evidence_systems) == 1


@pytest.mark.parametrize("error_type", [TimeoutError, LeaseLostError])
async def test_historical_verifier_failure_propagates_without_using_old_verdict(
    settings, error_type
):
    class Failure(HistoricalJudge):
        async def parse(self, system, user, schema, **kwargs):
            if schema is SemanticEvidenceDecisionList:
                raise error_type("verification unavailable")
            return await super().parse(system, user, schema, **kwargs)

    sources, candidates, researcher = await inputs(settings, Failure())
    with pytest.raises(error_type, match="verification unavailable"):
        await current_findings(candidates, sources, researcher)


async def test_cache_key_changes_with_finding_verifier_policy(settings, monkeypatch):
    sources, _, researcher = await inputs(settings)
    before = evidence_cache_key("scope", "question", sources, researcher)
    monkeypatch.setattr(
        researcher.semantic_verifier,
        "_SYSTEM",
        researcher.semantic_verifier._SYSTEM + " Updated modality rule.",
    )
    assert evidence_cache_key("scope", "question", sources, researcher) != before


async def test_cache_key_changes_with_quote_admission_limit(settings):
    sources, _, researcher = await inputs(settings)
    before = evidence_cache_key("scope", "question", sources, researcher)
    researcher.evidence_verifier.max_quote_chars = 100
    assert evidence_cache_key("scope", "question", sources, researcher) != before


async def test_policy_upgrade_invalidates_exact_answer_and_pooled_evidence(settings, monkeypatch):
    sources, candidates, researcher = await inputs(settings)
    current_policy = SemanticEvidenceVerifier._SYSTEM
    ctx = RunContext(
        llm=researcher.llm, search_tool=FakeSearch(), tracer=Tracer(), settings=settings
    )
    cache = PaperEvidenceCache()

    async def ask():
        return await answer_question(
            "单一全局阈值是否意味着必须按组标定？",
            history=[],
            ctx=ctx,
            paper_sources=sources,
            paper_evidence=candidates,
            paper_cache=cache,
            cache_scope="sc82/task",
        )

    monkeypatch.setattr(
        SemanticEvidenceVerifier, "_SYSTEM", current_policy.replace(EVIDENCE_MODALITY_RULES, "")
    )
    assert REJECTED in (await ask()).answer
    monkeypatch.setattr(SemanticEvidenceVerifier, "_SYSTEM", current_policy)
    updated = await ask()
    assert updated.fallback and REJECTED not in updated.answer and SUPPORTED in updated.answer
    assert len(researcher.llm.evidence_systems) == 2 and researcher.llm.extractions == 0


async def test_new_uncertain_verdict_does_not_reuse_historical_supported_status(settings):
    class Uncertain(HistoricalJudge):
        async def parse(self, system, user, schema, **kwargs):
            response = await super().parse(system, user, schema, **kwargs)
            if schema is SemanticEvidenceDecisionList:
                for decision in response.decisions:
                    decision.verdict = "uncertain"
            return response

    sources, candidates, researcher = await inputs(settings, Uncertain())
    assert await current_findings(candidates, sources, researcher) == []
