from __future__ import annotations

import deep_research.guardrails as guardrails
from deep_research.agents.synthesizer import Synthesizer
from deep_research.config import Settings
from deep_research.guardrails import (
    ClaimConsistencyReport,
    ClaimConsistencyVerifier,
    report_eligible,
)
from deep_research.models import EvidenceVerification, Finding, ResearchResult
from deep_research.observability import Tracer
from tests.fakes import FakeLLM


def _supported(statement: str, source_url: str) -> Finding:
    return Finding(
        statement=statement,
        source_url=source_url,
        evidence_quote=statement,
        verification=EvidenceVerification(
            status="verified",
            method="normalized_quote",
            semantic_status="supported",
        ),
    )


def _claim_ids(user: str) -> list[str]:
    return [
        line.removeprefix("Claim ID: ")
        for line in user.splitlines()
        if line.startswith("Claim ID: ")
    ]


class RelationshipLLM:
    def __init__(
        self,
        *,
        corroborations: list[tuple[int, int, float]] | None = None,
        contradictions: list[tuple[int, int, float]] | None = None,
        fail: bool = False,
    ) -> None:
        self.corroborations = corroborations or []
        self.contradictions = contradictions or []
        self.fail = fail

    async def parse(self, system, user, schema, *, temperature=0.0, retries=2):
        if self.fail:
            raise RuntimeError("relationship verifier unavailable")
        assert schema is ClaimConsistencyReport
        ids = _claim_ids(user)
        return ClaimConsistencyReport(
            contradictions=[
                {
                    "left_claim_id": ids[left],
                    "right_claim_id": ids[right],
                    "confidence": confidence,
                    "reason": "claims conflict",
                }
                for left, right, confidence in self.contradictions
            ],
            corroborations=[
                {
                    "left_claim_id": ids[left],
                    "right_claim_id": ids[right],
                    "confidence": confidence,
                    "reason": "same fact from independent publishers",
                }
                for left, right, confidence in self.corroborations
            ],
        )


def test_public_suffix_extractor_is_offline_and_cacheless() -> None:
    assert guardrails._TLD_EXTRACT.suffix_list_urls == ()
    assert not guardrails._TLD_EXTRACT._cache.enabled


async def test_independent_publishers_corroborate_both_claims() -> None:
    findings = [
        _supported("The policy starts in 2027.", "https://agency.gov.example/policy"),
        _supported("The policy takes effect in 2027.", "https://journal.example.org/report"),
    ]

    checked = await ClaimConsistencyVerifier().verify_batch(
        findings,
        RelationshipLLM(corroborations=[(0, 1, 0.94)]),
    )

    for finding, other in zip(checked, reversed(checked), strict=True):
        verification = finding.verification
        assert verification.corroboration_status == "corroborated"
        assert verification.independent_source_count == 2
        assert verification.corroborates_claim_ids == [other.verification.claim_id]
        assert report_eligible(finding, require_corroboration=True)


async def test_same_registrable_domain_cannot_self_corroborate() -> None:
    findings = [
        _supported("Revenue was $10m.", "https://news.example.co.uk/a"),
        _supported("Revenue reached $10m.", "https://data.example.co.uk/b"),
    ]

    checked = await ClaimConsistencyVerifier().verify_batch(
        findings,
        RelationshipLLM(corroborations=[(0, 1, 0.99)]),
    )

    assert [f.verification.corroboration_status for f in checked] == [
        "single_source",
        "single_source",
    ]
    assert [f.verification.independent_source_count for f in checked] == [1, 1]
    assert all(not f.verification.corroborates_claim_ids for f in checked)
    assert all(not report_eligible(f, require_corroboration=True) for f in checked)


async def test_idna_aliases_cannot_self_corroborate() -> None:
    findings = [
        _supported("Publisher report", "https://b\u00fccher.de/a"),
        _supported("Publisher mirror", "https://xn--bcher-kva.de/b"),
    ]

    checked = await ClaimConsistencyVerifier().verify_batch(
        findings,
        RelationshipLLM(corroborations=[(0, 1, 0.99)]),
    )

    assert [f.verification.corroboration_status for f in checked] == [
        "single_source",
        "single_source",
    ]
    assert [f.verification.independent_source_count for f in checked] == [1, 1]
    assert all(not report_eligible(f, require_corroboration=True) for f in checked)


async def test_contradiction_wins_over_corroboration_for_same_pair() -> None:
    findings = [
        _supported("The drug is safe.", "https://study.example/a"),
        _supported("The drug is unsafe.", "https://regulator.example/b"),
    ]

    checked = await ClaimConsistencyVerifier().verify_batch(
        findings,
        RelationshipLLM(
            corroborations=[(0, 1, 0.95)],
            contradictions=[(0, 1, 0.95)],
        ),
    )

    for finding in checked:
        verification = finding.verification
        assert verification.consistency_status == "conflicted"
        assert verification.corroboration_status == "disputed"
        assert verification.independent_source_count == 1
        assert verification.corroborates_claim_ids == []
        assert not report_eligible(finding, require_corroboration=True)


async def test_conflicted_claim_cannot_corroborate_another_claim() -> None:
    findings = [
        _supported("The policy starts in 2027.", "https://agency.example/a"),
        _supported("The policy takes effect in 2027.", "https://journal.example/b"),
        _supported("The policy was cancelled.", "https://regulator.example/c"),
    ]

    checked = await ClaimConsistencyVerifier().verify_batch(
        findings,
        RelationshipLLM(
            corroborations=[(0, 1, 0.95)],
            contradictions=[(0, 2, 0.95)],
        ),
    )

    assert [f.verification.corroboration_status for f in checked] == [
        "disputed",
        "single_source",
        "disputed",
    ]
    assert checked[1].verification.corroborates_claim_ids == []
    assert all(not report_eligible(f, require_corroboration=True) for f in checked)


async def test_low_confidence_corroboration_is_not_promoted() -> None:
    findings = [
        _supported("Demand increased.", "https://one.example/a"),
        _supported("Demand grew.", "https://two.example/b"),
    ]

    checked = await ClaimConsistencyVerifier().verify_batch(
        findings,
        RelationshipLLM(corroborations=[(0, 1, 0.59)]),
    )

    assert all(f.verification.corroboration_status == "single_source" for f in checked)


async def test_relationship_verifier_failure_is_fail_closed_in_strict_mode() -> None:
    findings = [
        _supported("Demand increased.", "https://one.example/a"),
        _supported("Demand grew.", "https://two.example/b"),
    ]

    checked = await ClaimConsistencyVerifier().verify_batch(
        findings,
        RelationshipLLM(fail=True),
    )

    assert all(f.verification.corroboration_status == "not_checked" for f in checked)
    assert all(f.verification.independent_source_count == 0 for f in checked)
    assert all(not report_eligible(f, require_corroboration=True) for f in checked)


def test_strict_synthesizer_excludes_single_source_material() -> None:
    single = _supported("Single-source claim", "https://single.example/a")
    single.verification.corroboration_status = "single_source"
    single.verification.independent_source_count = 1
    corroborated = _supported("Corroborated claim", "https://two.example/b")
    corroborated.verification.corroboration_status = "corroborated"
    corroborated.verification.independent_source_count = 2
    corroborated.verification.consistency_status = "clear"
    corroborated.verification.corroborates_claim_ids = ["supporting-claim"]
    result = ResearchResult(sub_question="Q", findings=[single, corroborated])
    synthesizer = Synthesizer(
        FakeLLM(),
        Tracer(),
        Settings(require_corroboration=True),
    )

    material, citations = synthesizer._material([result])

    assert "Single-source claim" not in material
    assert "Corroborated claim" in material
    assert citations == {"https://two.example/b": 1}


def test_strict_gate_rejects_incomplete_persisted_corroboration() -> None:
    finding = _supported("Claim", "https://single.example/a")
    finding.verification.corroboration_status = "corroborated"
    finding.verification.corroborates_claim_ids = ["supporting-claim"]

    assert not report_eligible(finding, require_corroboration=True)

    finding.verification.independent_source_count = 2
    assert not report_eligible(finding, require_corroboration=True)

    finding.verification.consistency_status = "clear"
    assert report_eligible(finding, require_corroboration=True)


async def test_strict_synthesizer_skips_model_when_gate_filters_all() -> None:
    finding = _supported("Single-source claim", "https://single.example/a")
    finding.verification.corroboration_status = "single_source"
    finding.verification.independent_source_count = 1
    llm = FakeLLM()
    synthesizer = Synthesizer(
        llm,
        Tracer(),
        Settings(require_corroboration=True),
    )

    report = await synthesizer.run(
        "Q",
        [ResearchResult(sub_question="Q", findings=[finding])],
    )

    assert llm.stream_calls == 0
    assert report.citations == []
    assert "Single-source claim" not in report.markdown
    assert "[1]" not in report.markdown


def test_default_mode_remains_backward_compatible() -> None:
    finding = _supported("Single-source claim", "https://single.example/a")
    finding.verification.corroboration_status = "single_source"
    finding.verification.independent_source_count = 1

    assert report_eligible(finding)
    assert not report_eligible(finding, require_corroboration=True)
