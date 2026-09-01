from __future__ import annotations

import pytest

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


# ── 伪双源：同一篇工作 / 同一团队不构成独立印证 ─────────────────────────────


def _scholarly(
    statement: str,
    url: str,
    *,
    doi: str = "",
    work_id: str = "",
    title: str = "",
    authors: list[str] | None = None,
) -> Finding:
    """带发布方身份的 finding，模拟 EvidenceVerifier 在验证时刻盖的章。"""
    from deep_research.models import SourceIdentity

    return Finding(
        statement=statement,
        source_url=url,
        evidence_quote=statement,
        verification=EvidenceVerification(
            status="verified",
            method="normalized_quote",
            semantic_status="supported",
            source_identity=SourceIdentity(
                doi=doi,
                work_id=work_id,
                title=title,
                authors=authors or [],
                domain=guardrails.publisher_identity(url),
            ),
        ),
    )


@pytest.mark.asyncio
async def test_the_same_work_on_two_domains_is_not_a_double_source():
    """一篇工作的 arXiv 预印本与期刊正式版是两个域、一篇工作。

    按域名判会得出"已交叉印证"——那个结论本身是假的，而这正是改造前的行为。
    """
    findings = [
        _scholarly("该方法达到 38.36 dB", "https://arxiv.org/abs/2205.10102v3", doi="10.1364/oe.1"),
        _scholarly(
            "该方法达到 38.36 dB",
            "https://opg.optica.org/oe/abstract.cfm?uri=oe-1",
            doi="https://doi.org/10.1364/OE.1",
        ),
    ]

    checked = await ClaimConsistencyVerifier().verify_batch(
        findings, RelationshipLLM(corroborations=[(0, 1, 0.95)])
    )

    for finding in checked:
        assert finding.verification.corroboration_status == "single_source"
        assert finding.verification.independent_source_count == 1
        # 必须说明"看到了第二个来源但同源"，否则读者以为系统只找到一个
        assert "同源来源被驳回" in finding.verification.corroboration_reason
        assert "same_doi" in finding.verification.corroboration_reason
    assert not report_eligible(checked[0], require_corroboration=True)


@pytest.mark.asyncio
async def test_two_papers_from_the_same_group_are_not_a_double_source():
    """本领域课题组高度集中，同组两篇论文互相印证不构成独立验证。"""
    findings = [
        _scholarly(
            "深度展开优于端到端",
            "https://arxiv.org/abs/1",
            doi="10.1/mst",
            authors=["Yuanhao Cai", "Jing Lin"],
        ),
        _scholarly(
            "深度展开优于端到端",
            "https://openreview.net/forum?id=2",
            doi="10.2/dauhst",
            authors=["Y. Cai", "Xiaowan Hu"],
        ),
    ]

    checked = await ClaimConsistencyVerifier().verify_batch(
        findings, RelationshipLLM(corroborations=[(0, 1, 0.95)])
    )

    assert checked[0].verification.corroboration_status == "single_source"
    assert "shared_authors" in checked[0].verification.corroboration_reason


@pytest.mark.asyncio
async def test_two_independent_groups_still_corroborate():
    """门禁不能把一切都合并——真正独立的两个团队必须仍构成双源。"""
    findings = [
        _scholarly(
            "CASSI 可用单次曝光重建光谱",
            "https://opg.optica.org/oe/1",
            doi="10.1364/oe.1",
            title="Single disperser coded aperture snapshot spectral imaging",
            authors=["Ashwin Wagadarikar", "David Brady"],
        ),
        _scholarly(
            "CASSI 可用单次曝光重建光谱",
            "https://ieeexplore.ieee.org/document/2",
            doi="10.1109/cvpr.2",
            title="Mask-guided spectral-wise transformer for reconstruction",
            authors=["Yuanhao Cai", "Jing Lin"],
        ),
    ]

    checked = await ClaimConsistencyVerifier().verify_batch(
        findings, RelationshipLLM(corroborations=[(0, 1, 0.95)])
    )

    for finding in checked:
        assert finding.verification.corroboration_status == "corroborated"
        assert finding.verification.independent_source_count == 2
    assert report_eligible(checked[0], require_corroboration=True)


@pytest.mark.asyncio
async def test_a_third_independent_source_lifts_a_pseudo_double_source():
    """两个同源 + 一个真独立 = 2 个独立发布方，不是 3 个。"""
    findings = [
        _scholarly("论断 X", "https://arxiv.org/abs/1", doi="10.1/same"),
        _scholarly("论断 X", "https://optica.org/1", doi="10.1/same"),
        _scholarly(
            "论断 X",
            "https://ieee.org/2",
            doi="10.9/other",
            title="An entirely unrelated independent study",
            authors=["Zoe Zhang"],
        ),
    ]

    checked = await ClaimConsistencyVerifier().verify_batch(
        findings, RelationshipLLM(corroborations=[(0, 1, 0.95), (0, 2, 0.95), (1, 2, 0.95)])
    )

    assert checked[0].verification.corroboration_status == "corroborated"
    assert checked[0].verification.independent_source_count == 2


@pytest.mark.asyncio
async def test_legacy_findings_keep_the_previous_domain_based_verdict():
    """旧记录没有 source_identity：判定退回按域名，与改造前逐条一致。"""
    findings = [
        _supported("论断 Y", "https://a.example.com/1"),
        _supported("论断 Y", "https://b.example.org/2"),
    ]

    checked = await ClaimConsistencyVerifier().verify_batch(
        findings, RelationshipLLM(corroborations=[(0, 1, 0.95)])
    )

    assert checked[0].verification.corroboration_status == "corroborated"
    assert checked[0].verification.independent_source_count == 2
