"""Expected credential waits must survive search fallback and subquestion layers."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from deep_research.agents.researcher import Researcher
from deep_research.execution_policy import resource_busy, transient_failure
from deep_research.models import Source, SubQuestion
from deep_research.observability import Tracer
from deep_research.provider_limits import ProviderBusyError
from deep_research.scheduler import research_dag
from deep_research.tools.base import SearchTool
from deep_research.tools.composite import MultiBackendSearch
from deep_research.tools.key_pool import ApiKeyPoolSearch
from tests.fakes import FakeLLM, verified_finding
from tests.test_composite_search import FakeBackend


class BusyLLM(FakeLLM):
    async def parse(self, *args, **kwargs):
        raise ProviderBusyError(15)


@pytest.mark.parametrize("wrapped", [False, True])
async def test_busy_subquestion_cancels_peers_and_does_not_admit_successors(wrapped):
    started, cancelled = asyncio.Event(), asyncio.Event()
    calls = []

    async def work(question, context):
        calls.append(question)
        if question == "busy":
            await started.wait()
            try:
                raise ProviderBusyError(12)
            except ProviderBusyError as exc:
                if wrapped:
                    raise RuntimeError("role interrupted") from exc
                raise
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    with pytest.raises(RuntimeError) as failure:
        await asyncio.wait_for(
            research_dag(
                [
                    SubQuestion(question="busy"),
                    SubQuestion(question="peer"),
                    SubQuestion(question="successor", depends_on=[0]),
                ],
                work,
                Tracer(),
            ),
            2,
        )
    assert resource_busy(failure.value).retry_after == 12
    assert cancelled.is_set() and "successor" not in calls


async def test_busy_search_is_not_converted_to_an_empty_research_result(settings):
    search = FakeBackend("busy", [], fail=ProviderBusyError(15))
    researcher = Researcher(FakeLLM(), search, Tracer(), settings)
    with pytest.raises(ProviderBusyError):
        await researcher.run("question", search_queries=["first", "second"])
    assert search.queries == ["first"]


async def test_busy_extraction_does_not_finish_with_a_failed_audit(settings):
    settings.intent_source_screening = False
    search = FakeBackend("available", [Source(url="https://example.com/a", content="source text")])
    researcher = Researcher(BusyLLM(), search, Tracer(), settings)
    assert not researcher.raise_extraction_errors
    with pytest.raises(ProviderBusyError):
        await researcher.run("question")


@pytest.mark.parametrize("stage", ["semantic", "consistency"])
async def test_busy_verification_does_not_label_good_evidence_as_unchecked(stage):
    from deep_research.guardrails import ClaimConsistencyVerifier, SemanticEvidenceVerifier

    findings = [
        verified_finding(source_url="https://first.example/a"),
        verified_finding(source_url="https://second.example/b"),
    ]
    verifier = SemanticEvidenceVerifier() if stage == "semantic" else ClaimConsistencyVerifier()
    with pytest.raises(ProviderBusyError):
        await verifier.verify_batch(findings, BusyLLM())


async def test_busy_prose_support_check_does_not_become_a_quality_failure():
    from deep_research.workbench.support import SupportReviewer, SupportUnit

    reviewer = SupportReviewer(
        BusyLLM(),
        [
            {
                "id": "e",
                "citation": 1,
                "statement": "Fact",
                "quote": "Fact",
                "source": "https://example.com",
            }
        ],
        20000,
        check_fulltext=False,
        check_formulas=False,
    )
    with pytest.raises(ProviderBusyError):
        await reviewer.review([SupportUnit("u", "Fact [1]", citations=[1])])


async def test_recreated_key_pool_retains_safe_backpressure_without_reissuing_requests():
    calls = []
    keys = ["synthetic-first-secret", "synthetic-second-secret"]

    class Busy(SearchTool):
        def __init__(self, key):
            self.key = key

        async def search(self, query, *, max_results=5):
            calls.append(self.key)
            raise ProviderBusyError(20)

    for _ in range(2):
        with pytest.raises(ProviderBusyError) as failure:
            await ApiKeyPoolSearch("backpressure", keys, Busy).search("query")
        assert 18 <= failure.value.retry_after <= 20
        assert not any(key in str(failure.value) for key in keys)
    assert calls == keys


async def test_invalid_credentials_alone_stay_terminal():
    request = httpx.Request("GET", "https://invalid.example")
    error = httpx.HTTPStatusError(
        "private-key",
        request=request,
        response=httpx.Response(401, request=request),
    )
    pool = ApiKeyPoolSearch(
        "invalid", ["private-key"], lambda _: FakeBackend("invalid", [], fail=error)
    )
    with pytest.raises(RuntimeError) as failure:
        await pool.search("query")
    assert not transient_failure(failure.value)
    assert "private-key" not in str(failure.value)


@pytest.mark.parametrize("other_error", [None, ValueError("backend unavailable")])
async def test_composite_preserves_backpressure_when_no_alternative_sources_exist(other_error):
    search = MultiBackendSearch(
        [
            FakeBackend("busy", [], fail=ProviderBusyError(17)),
            FakeBackend("other", [], fail=other_error),
        ]
    )
    with pytest.raises(ProviderBusyError) as failure:
        await search.search("query")
    assert failure.value.retry_after == 17


async def test_busy_provider_does_not_block_a_healthy_fallback():
    source = Source(url="https://available.example", content="available evidence")
    pool = ApiKeyPoolSearch(
        "fallback",
        ["busy", "healthy"],
        lambda key: FakeBackend(
            key,
            [source] if key == "healthy" else [],
            fail=ProviderBusyError(20) if key == "busy" else None,
        ),
    )
    assert await pool.search("query") == [source]
    combined = MultiBackendSearch(
        [
            FakeBackend("busy", [], fail=ProviderBusyError(20)),
            FakeBackend("healthy", [source]),
        ]
    )
    assert await combined.search("query") == [source]
