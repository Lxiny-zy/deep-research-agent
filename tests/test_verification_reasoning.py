"""Routine checks lower per-call reasoning without changing shared model settings."""

import asyncio
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from deep_research.guardrails import SemanticEvidenceDecisionList, SemanticEvidenceVerifier
from deep_research.llm import LLM, ModelOutputTruncated, verification_generation_options
from deep_research.observability import Tracer
from deep_research.orchestrator import _LazyOwnedLLM
from deep_research.workbench.fulltext_review import FullTextChecks, FullTextTarget
from deep_research.workbench.support import SupportDecisions, SupportReviewer, SupportUnit
from tests.fakes import FakeLLM, verified_finding
from tests.test_fulltext_absence import FullTextJudge, make_corpus
from tests.test_llm_usage import FakeStream


class Reply(BaseModel):
    value: int


@pytest.mark.parametrize("mode", ["reasoning", "temperature"])
async def test_parse_effort_override_is_request_local_and_keeps_output_cap(settings, mode):
    settings.llm_api_key = "test-key"
    settings.llm_max_output_tokens = 20000
    llm = LLM(settings, Tracer())
    await llm.client.close()
    llm.parameter_mode, llm.reasoning_effort = mode, "medium"
    requests = []
    both_started = asyncio.Event()

    async def create(**kwargs):
        requests.append(kwargs)
        if len(requests) == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), 1)
        result = FakeStream()
        result.chunks = [SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content='{"value": 1}'),
                                     finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=20, completion_tokens=10, total_tokens=30),
        )]
        return result

    llm.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    lazy = _LazyOwnedLLM(lambda: llm)
    results = await asyncio.gather(
        lazy.parse("system", "verify", Reply, **verification_generation_options(lazy)),
        llm.parse("system", "write", Reply),
    )
    assert [result.value for result in results] == [1, 1]
    assert len(requests) == 2 and llm.reasoning_effort == "medium"
    cap_key = "max_completion_tokens" if mode == "reasoning" else "max_tokens"
    assert all(request[cap_key] == 20000 for request in requests)
    if mode == "reasoning":
        assert [request["reasoning_effort"] for request in requests] == ["low", "medium"]
        assert all("temperature" not in request for request in requests)
    else:
        assert all("reasoning_effort" not in request for request in requests)
    usage = [event.data["llm_usage"] for event in llm.tracer.events
             if event.data and "llm_usage" in event.data]
    if mode == "reasoning":
        assert sorted(row["reasoning_effort"] for row in usage) == ["low", "medium"]
    else:
        assert [row.get("reasoning_effort") for row in usage] == [None, None]


async def test_low_effort_truncation_does_not_retry_or_increase_cap(settings):
    settings.llm_api_key = "test-key"
    settings.llm_max_output_tokens = 20000
    llm = LLM(settings, Tracer())
    await llm.client.close()
    llm.parameter_mode, llm.reasoning_effort = "reasoning", "medium"
    requests = []

    async def create(**kwargs):
        requests.append(kwargs)
        result = FakeStream()
        result.chunks = [SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content=None), finish_reason="length")],
            usage=SimpleNamespace(prompt_tokens=3287, completion_tokens=20000, total_tokens=23287),
        )]
        return result

    llm.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    with pytest.raises(ModelOutputTruncated):
        await llm.parse("system", "verify", Reply, reasoning_effort="low")
    assert len(requests) == 1 and requests[0]["max_completion_tokens"] == 20000
    assert llm.reasoning_effort == "medium"


@pytest.mark.parametrize("mode", ["reasoning", "temperature"])
async def test_semantic_support_and_fulltext_checks_use_only_supported_effort(mode):
    calls = []

    class Judge(FakeLLM):
        parameter_mode = mode
        reasoning_effort = "medium"

        async def parse(self, system, user, schema, **kwargs):
            calls.append((schema, kwargs))
            delegate = {key: value for key, value in kwargs.items() if key != "reasoning_effort"}
            return await super().parse(system, user, schema, **delegate)

    llm = Judge()
    finding = verified_finding()
    admitted = await SemanticEvidenceVerifier().verify_batch([finding], llm)
    assert admitted[0].verification.semantic_status == "supported"
    decision = (await SupportReviewer(
        llm, [{"id": "e", "citation": 1, "statement": finding.statement,
               "quote": finding.evidence_quote, "source": finding.source_url}],
        20000, check_fulltext=False, check_formulas=False,
    ).review([SupportUnit("u", finding.statement + " [1]", citations=[1])]))[0]
    assert decision.verdict == "supported"

    class Fulltext(FullTextJudge):
        parameter_mode = mode
        reasoning_effort = "medium"

        async def parse(self, system, user, schema, **kwargs):
            calls.append((schema, kwargs))
            return await super().parse(system, user, schema, **kwargs)

    _, corpus = make_corpus(complete=True)
    fulltext = Fulltext()
    checked = (await SupportReviewer(fulltext, [], 20000, fulltext_corpus=corpus).review([
        SupportUnit("absence", "本次取得的全文文本中未见 dropout 设置。", kind="prose"),
    ]))[0]
    assert checked.verdict == "supported"
    assert {schema for schema, _ in calls} == {
        SemanticEvidenceDecisionList, SupportDecisions, FullTextTarget, FullTextChecks,
    }
    for _, options in calls:
        if mode == "reasoning":
            assert options["reasoning_effort"] == "low"
        else:
            assert "reasoning_effort" not in options
    assert llm.reasoning_effort == fulltext.reasoning_effort == "medium"
