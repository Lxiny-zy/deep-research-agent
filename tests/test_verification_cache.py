"""Repeated evidence checks reuse one request without weakening admission checks."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from deep_research.guardrails import (
    ClaimConsistencyReport,
    ClaimConsistencyVerifier,
    SemanticEvidenceVerifier,
)
from deep_research.llm import LLM, ModelOutputTruncated
from deep_research.observability import Tracer
from deep_research.token_budget import TokenBudget
from deep_research.verification_cache import VerificationCache
from tests.fakes import verified_finding


@pytest.fixture
async def model(settings):
    llm = LLM(replace(settings, llm_api_key="synthetic-cache-key"), Tracer())
    calls = []
    replies = []

    async def create(**request):
        calls.append(request)
        reply = replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        if isinstance(reply, asyncio.Event):
            await reply.wait()
            reply = {}

        async def stream():
            yield SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(content=json.dumps(reply)),
                        finish_reason="stop",
                    )
                ],
                usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            )

        return stream()

    llm.client.chat.completions.create = create
    try:
        yield llm, calls, replies
    finally:
        await llm.aclose()


def findings():
    return [
        verified_finding(source_url="https://one.example/paper"),
        verified_finding(source_url="https://two.example/paper"),
    ]


async def test_recreated_verifier_and_role_clients_reuse_identical_consistency(model):
    llm, calls, replies = model
    replies.append({})
    original = findings()
    first = await ClaimConsistencyVerifier().verify_batch(
        original, llm.for_role("evidence_verifier")
    )
    second = await ClaimConsistencyVerifier().verify_batch(first, llm.for_role("evidence_verifier"))
    assert first == second and len(calls) == 1
    assert llm.tracer.total_tokens == 15
    assert any((event.data or {}).get("verification_reuse") for event in llm.tracer.events)


async def test_lazy_role_keeps_the_current_turn_cache_and_attribution(model):
    from deep_research.orchestrator import _LazyOwnedLLM

    llm, calls, replies = model
    original_tracer = llm.tracer
    current_tracer = Tracer()
    current_tracer.budget = TokenBudget()
    replies.append({})
    lazy = _LazyOwnedLLM(lambda: llm)
    for _ in range(2):
        bound = lazy.for_role("evidence_verifier")
        bound.tracer = current_tracer
        await ClaimConsistencyVerifier().verify_batch(findings(), bound)
    assert len(calls) == 1 and llm.tracer is original_tracer
    assert not original_tracer.verification_cache.entries
    assert current_tracer.total_tokens == 15
    assert any(
        (e.data or {}).get("verification_reuse", {}).get("role") == "evidence_verifier"
        for e in current_tracer.events
    )


async def test_concurrent_checks_share_one_call_and_keep_result_copies_independent(model):
    llm, calls, replies = model
    gate = asyncio.Event()
    replies.append(gate)
    first = asyncio.create_task(llm.parse_verification("s", "u", ClaimConsistencyReport))
    second = asyncio.create_task(llm.parse_verification("s", "u", ClaimConsistencyReport))
    try:
        for _ in range(200):
            if calls and next(iter(llm.tracer.verification_cache.entries.values())).users == 2:
                break
            await asyncio.sleep(0.001)
        else:
            pytest.fail("duplicate requests did not join the same cache entry")
        assert len(calls) == 1
        gate.set()
        a, b = await asyncio.wait_for(asyncio.gather(first, second), 2)
        assert a == b and a is not b
        a.contradictions.append("changed by caller")
        assert not b.contradictions
        assert not (await llm.parse_verification("s", "u", ClaimConsistencyReport)).contradictions
    finally:
        first.cancel()
        second.cancel()
        await asyncio.gather(first, second, return_exceptions=True)


async def test_incomplete_semantic_decisions_are_not_cached(model):
    llm, calls, replies = model
    replies.extend(
        [
            {"decisions": []},
            {"decisions": [{"index": 0, "verdict": "supported", "confidence": 0.9}]},
        ]
    )
    original = findings()[:1]
    rejected = await SemanticEvidenceVerifier().verify_batch(original, llm)
    assert rejected[0].verification.semantic_reason == "semantic_indices_invalid"
    accepted = await SemanticEvidenceVerifier().verify_batch(original, llm)
    assert accepted[0].verification.semantic_status == "supported"
    assert await SemanticEvidenceVerifier().verify_batch(original, llm) == accepted
    assert len(calls) == 2


@pytest.mark.parametrize("change", ["quote", "snapshot", "question", "model", "effort", "scope"])
async def test_changed_evidence_model_or_turn_invalidates_reuse(model, change):
    llm, calls, replies = model
    llm.parameter_mode = "reasoning"
    replies.extend([{}, {}])
    original = findings()
    await ClaimConsistencyVerifier().verify_batch(original, llm)
    if change == "quote":
        original[0].evidence_quote += " Additional source condition."
    elif change == "snapshot":
        original[0].verification.source_content_hash = "different-snapshot"
    elif change == "question":
        original[0].statement += " With a different scope."
    elif change == "model":
        llm.model = "different-model"
    elif change == "scope":
        llm.tracer.request_id = "another-turn"
    else:
        # Semantic checks explicitly request low effort; test a different
        # explicit override rather than changing an unused profile hint.
        replies[:] = [{}, {}]
        await llm.parse_verification("s", "u", ClaimConsistencyReport, reasoning_effort="low")
        await llm.parse_verification("s", "u", ClaimConsistencyReport, reasoning_effort="high")
        assert len(calls) == 3
        return
    await ClaimConsistencyVerifier().verify_batch(original, llm)
    assert len(calls) == 2


async def test_cancelled_duplicate_does_not_cancel_the_running_request(model):
    llm, calls, replies = model
    gate = asyncio.Event()
    replies.append(gate)
    owner = asyncio.create_task(llm.parse_verification("s", "u", ClaimConsistencyReport))
    waiter = asyncio.create_task(llm.parse_verification("s", "u", ClaimConsistencyReport))
    try:
        for _ in range(200):
            if calls and next(iter(llm.tracer.verification_cache.entries.values())).users == 2:
                break
            await asyncio.sleep(0.001)
        else:
            pytest.fail("duplicate did not join")
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert not owner.done()
        gate.set()
        await asyncio.wait_for(owner, 2)
        await llm.parse_verification("s", "u", ClaimConsistencyReport)
        assert len(calls) == 1
    finally:
        owner.cancel()
        waiter.cancel()
        await asyncio.gather(owner, waiter, return_exceptions=True)


async def test_failed_requests_are_not_cached_and_ordinary_parse_never_reuses(model):
    llm, calls, replies = model
    replies.extend([ModelOutputTruncated(10), {}, {}, {}])
    with pytest.raises(ModelOutputTruncated):
        await llm.parse_verification("s", "u", ClaimConsistencyReport)
    assert not llm.tracer.verification_cache.entries
    await llm.parse_verification("s", "u", ClaimConsistencyReport)
    await llm.parse("s", "u", ClaimConsistencyReport)
    await llm.parse("s", "u", ClaimConsistencyReport)
    assert len(calls) == 4


async def test_cache_is_bounded():
    cache = VerificationCache(maximum=2)

    async def compute():
        return ClaimConsistencyReport()

    for index in range(10):
        await cache.get_or_compute(str(index), compute, lambda: None)
    assert list(cache.entries) == ["8", "9"]
