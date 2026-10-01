from __future__ import annotations

import json

from deep_research.guardrails import EvidenceVerifier, report_eligible
from deep_research.models import (
    EvidenceVerification,
    ExtractedFindingList,
    Finding,
    FindingContent,
    FindingList,
    Source,
)


def test_extraction_schema_excludes_program_fields_but_retains_measurements():
    raw = ExtractedFindingList.model_json_schema()
    properties = raw["$defs"]["FindingContent"]["properties"]
    assert set(properties) == {
        "statement",
        "source_url",
        "evidence_quote",
        "confidence",
        "entity",
        "quantity",
        "conditions",
    }
    assert "EvidenceVerification" not in raw["$defs"] and "SourceIdentity" not in raw["$defs"]
    assert "ExperimentConditions" in raw["$defs"] and "Quantity" in raw["$defs"]
    old = FindingList.model_json_schema()
    assert "verification" in old["$defs"]["Finding"]["properties"]
    assert len(json.dumps(raw)) < len(json.dumps(old))


def test_model_supplied_verification_is_discarded_even_for_adapter_subclasses():
    claimed = Finding(
        statement="s",
        source_url="https://a.com",
        evidence_quote="quoted evidence",
        verification=EvidenceVerification(
            status="verified",
            semantic_status="supported",
            source_content_hash="spoof",
            independent_source_count=99,
        ),
    )
    decoded = ExtractedFindingList.model_validate({"findings": [claimed.model_dump()]})
    for item in (decoded.findings[0], claimed):
        clean = item.as_unverified()
        assert clean.statement == "s" and clean.evidence_quote == "quoted evidence"
        assert clean.verification == EvidenceVerification()
        assert not report_eligible(clean)


def test_numerical_and_condition_evidence_checks_still_apply():
    quote = "MST reaches PSNR 38.4 dB on CAVE."
    raw = FindingContent(
        statement="MST reaches PSNR 38.4 dB on CAVE.",
        source_url="https://a.com",
        evidence_quote=quote,
        entity="MST",
        quantity={"metric": "PSNR", "value": 38.4, "unit": "dB", "rendered": "38.4"},
        conditions={"dataset": "CAVE"},
    )
    candidate = raw.as_unverified()
    assert candidate.quantity == raw.quantity and candidate.conditions == raw.conditions
    source = Source(title="measurement", url="https://a.com", content=quote)
    check = EvidenceVerifier().verify(candidate, source)
    assert check.accepted and check.finding.verification.quantity_status == "verified"
    assert check.finding.verification.semantic_status == "not_checked"
    assert not report_eligible(check.finding)
    candidate.quantity.value = 88.4
    wrong = EvidenceVerifier().verify(candidate, source)
    assert wrong.finding.verification.quantity_status == "unsupported"
    wrong.finding.verification.semantic_status = "supported"
    assert not report_eligible(wrong.finding)
