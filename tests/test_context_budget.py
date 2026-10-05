"""Conservative estimates and exact-source batching share one admission calculation."""

import hashlib
import re
from types import SimpleNamespace

import pytest

from deep_research.agents.researcher import Researcher
from deep_research.context_budget import (
    ContextBudget,
    TokenEstimator,
    estimate_tokens,
    prompt_tokens,
)
from deep_research.guardrails import SemanticEvidenceDecisionList
from deep_research.llm import LLM, InputCapacityError, ModelOutputTruncated
from deep_research.models import ExtractedFindingList, ExtractionAudit, Source
from deep_research.observability import Tracer
from deep_research.prompting import structured_system_prompt


def test_estimate_is_unicode_aware_and_independent_of_stream_chunk_boundaries():
    text = "long_ascii_name, 中文与符号 ± λ 42. \n https://example.org/paper"
    estimator = TokenEstimator()
    for char in text:
        estimator.feed(char)
    assert estimator.total == estimate_tokens(text)
    assert estimate_tokens("研究依据") == 8
    assert estimate_tokens("a" * 30) < estimate_tokens("论" * 30)


def test_output_and_framing_are_reserved_even_for_provider_default_output():
    budget = ContextBudget.for_limits(32000, None)
    assert budget.output_tokens == 8000
    assert budget.input_tokens + budget.output_tokens == 32000
    assert budget.remaining("system", "question") == budget.input_tokens - prompt_tokens(
        "system", "question"
    )
    # Legacy callers receive a safe character hint, modern callers can use actual text.
    text = "a" * (budget.input_capacity_chars + 100)
    assert budget.fits("system", text)
    legacy = ContextBudget.for_limits(None, None, 100)
    assert not legacy.enforced and not legacy.fits("system", "a" * 100)


async def test_unicode_overflow_is_rejected_before_transport_with_output_cap_unchanged(settings):
    settings.llm_api_key = "test-key"
    settings.llm_max_output_tokens = 512
    llm = LLM(settings, Tracer())
    llm.context_window_tokens = 2000
    try:
        text = "文" * 800
        # This fit the previous 2 * token-budget character check.
        assert len(text) < 2 * (2000 - 512)
        with pytest.raises(InputCapacityError, match="保守估算"):
            llm._reserve("system", text)
        assert settings.llm_max_output_tokens == 512
    finally:
        await llm.aclose()


class Sources:
    def __init__(self, sources):
        self.sources = sources

    async def search(self, query, **kwargs):
        return self.sources


class Extractor:
    context_window_tokens = 24000
    input_capacity_chars = 100000
    parameter_mode = "temperature"

    def __init__(self, fail_at=None):
        self.settings = SimpleNamespace(llm_max_output_tokens=1024)
        self.prompts = []
        self.fail_at = fail_at

    async def parse(self, system, user, schema, **kwargs):
        budget = ContextBudget.from_model(self)
        assert budget.fits(structured_system_prompt(system, schema), user)
        if schema is SemanticEvidenceDecisionList:
            return schema(decisions=[
                {"index": int(index), "verdict": "supported", "confidence": 1,
                 "reason": "The fixture statement exactly repeats its source quote."}
                for index in re.findall(r"^Index: (\d+)$", user, re.M)
            ])
        assert schema is ExtractedFindingList
        self.prompts.append(user)
        if self.fail_at == len(self.prompts):
            raise ModelOutputTruncated(1024)
        records = re.findall(r"URL: ([^\n]+)\n内容: ([\s\S]*?)\n<<<来源 \d+ 结束>>>", user)
        return schema(findings=[
            {"statement": text[:50], "source_url": url, "evidence_quote": text[:50]}
            for url, text in records if text.strip()
        ])


def large_sources():
    return [Source(
        url=f"https://example.org/paper-{index}", title=f"原文 {index}",
        content=(f"第{index}种方法记录了实验结果。" * 750),
    ) for index in range(3)]


async def test_large_web_sources_are_partitioned_with_original_hash_and_complete_coverage(settings):
    sources = large_sources()
    llm = Extractor()
    result = await Researcher(llm, Sources(sources), Tracer(), settings).run("方法记录了什么？")
    audit = result.extraction_audit
    assert audit is not None and len(audit.context_batches) == len(llm.prompts) > 1
    assert all(batch["status"] == "checked" for batch in audit.context_batches)
    assert {source.url: source.content for source in audit.sources} == {
        source.url: source.content for source in sources
    }
    for source in sources:
        expected_hash = hashlib.sha256(source.content.encode()).hexdigest()
        windows = [window for batch in audit.context_batches for window in batch["sources"]
                   if window["source_url"] == source.url]
        position = 0
        for window in sorted(windows, key=lambda value: value["start"]):
            assert window["start"] <= position < window["end"]
            assert window["source_content_hash"] == expected_hash
            position = window["end"]
        assert position == len(source.content)
        matching = [finding for finding in result.findings if finding.source_url == source.url]
        assert matching
        assert all(
            finding.verification.source_content_hash == expected_hash for finding in matching
        )
        assert all(finding.evidence_quote in source.content for finding in matching)
    ids = [candidate.id for candidate in audit.candidates]
    assert len(ids) == len(set(ids))
    assert any("字符区间左闭右开" in prompt for prompt in llm.prompts)


async def test_batch_output_failure_retains_prior_work_and_does_not_retry_remaining_sources(
    settings,
):
    llm, sources = Extractor(fail_at=2), large_sources()
    result = await Researcher(llm, Sources(sources), Tracer(), settings).run("方法记录了什么？")
    audit = result.extraction_audit
    assert audit is not None and result.findings and len(llm.prompts) == 2
    assert audit.context_batches[0]["status"] == "checked"
    assert audit.context_batches[1]["status"] == "error"
    assert all(batch["status"] == "not_attempted" for batch in audit.context_batches[2:])
    assert "extraction_call_failed:ModelOutputTruncated" in audit.issues
    assert len(audit.sources) == len(sources)


async def test_insufficient_fixed_prompt_space_is_distinct_from_output_truncation(settings):
    llm = Extractor()
    llm.context_window_tokens = 1200
    sources = large_sources()[:1]
    result = await Researcher(llm, Sources(sources), Tracer(), settings).run("问题" * 200)
    assert not llm.prompts and result.extraction_audit is not None
    assert result.extraction_audit.issues == ["extraction_call_failed:InputCapacityError"]
    assert result.extraction_audit.sources[0].content == sources[0].content


def test_empty_partition_metadata_preserves_legacy_audit_serialization():
    assert "context_batches" not in ExtractionAudit(question="q").model_dump()
