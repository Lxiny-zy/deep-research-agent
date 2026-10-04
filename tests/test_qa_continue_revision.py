"""Explicit continuation reuses a stored draft, evidence, and positive verdicts."""

import json

import pytest

from deep_research.agents.base import RunContext
from deep_research.guardrails import SemanticEvidenceDecisionList
from deep_research.models import ExtractedFindingList
from deep_research.observability import Tracer
from deep_research.workbench.prose_edit import ProseEdits
from deep_research.workbench.qa import answer_question
from tests.test_prose_review import CorrelationSearch, Judge
from tests.test_qa_requests import stores as stores

GOOD = "变量存在相关关系 [1]。"
BAD = "变量已经证明因果关系 [1]。"
FIXED = "不能从相关关系证明因果关系 [1]。"


class ContinueModel(Judge):
    def __init__(self):
        super().__init__()
        self.extractions = 0
        self.evidence_checks = 0
        self.edits = []

    async def stream(self, *args, **kwargs):
        self.stream_calls += 1
        yield GOOD + "\n\n" + BAD

    async def parse(self, system, user, schema, **kwargs):
        if schema is ExtractedFindingList:
            self.extractions += 1
        if schema is SemanticEvidenceDecisionList:
            self.evidence_checks += 1
        if schema is ProseEdits:
            data = json.loads(user.split("【只修订以下段落】\n", 1)[1])
            self.edits.append(data["paragraphs"])
            return ProseEdits(
                edits=[
                    {"unit_id": part["unit_id"], "replacement": FIXED}
                    for part in data["paragraphs"]
                ]
            )
        return await super().parse(system, user, schema, **kwargs)


async def initial(settings):
    settings.quality = {"max_revisions": 0, "qa_claim_max_revisions": 0}
    model = ContinueModel()
    ctx = RunContext(llm=model, search_tool=CorrelationSearch(), tracer=Tracer(), settings=settings)
    answer = await answer_question("解释变量关系", history=[], ctx=ctx, include_web=True)
    return answer, model, ctx


async def test_continuation_preserves_good_text_without_search_extraction_or_full_generation(
    settings,
):
    first, model, ctx = await initial(settings)
    assert first.fallback and first.revision_state is not None
    settings.quality = {"max_revisions": 1, "qa_claim_max_revisions": 1}
    second = await answer_question(
        "解释变量关系",
        history=[],
        ctx=ctx,
        revision_seed=first.revision_state,
    )
    assert not second.fallback and second.answer == GOOD + "\n\n" + FIXED
    assert model.stream_calls == 1 and model.extractions == 1 and model.evidence_checks == 1
    assert len(model.edits) == 1 and [part["text"] for part in model.edits[0]] == [BAD]
    assert second.revision_state is None


async def test_changed_verification_policy_rechecks_evidence_before_continuing(
    settings, monkeypatch
):
    from deep_research.guardrails import SemanticEvidenceVerifier

    first, model, ctx = await initial(settings)
    monkeypatch.setattr(
        SemanticEvidenceVerifier, "_SYSTEM", SemanticEvidenceVerifier._SYSTEM + " New rule."
    )
    settings.quality = {"max_revisions": 1, "qa_claim_max_revisions": 1}
    second = await answer_question(
        "解释变量关系", history=[], ctx=ctx, revision_seed=first.revision_state
    )
    assert not second.fallback and model.evidence_checks == 2 and model.extractions == 1


async def test_corrupted_revision_snapshot_is_rejected_before_generation(settings):
    first, model, ctx = await initial(settings)
    changed = {**first.revision_state, "draft": "伪造的新稿 [1]。"}
    with pytest.raises(ValueError, match="修订"):
        await answer_question("解释变量关系", history=[], ctx=ctx, revision_seed=changed)
    assert model.stream_calls == 1 and model.extractions == 1


async def test_private_revision_state_survives_durable_storage_and_reload(settings, stores):
    from deep_research.workbench.qa_revision_state import (
        PRIVATE_REVISION_TOOL,
        stored_revision_state,
    )
    from deep_research.workbench.qa_store import message_payload

    first, model, ctx = await initial(settings)
    store, jobs, other, cid = stores
    await jobs.reserve(cid, "original-request", "hash", {"query": "解释变量关系"})
    assert await jobs.claim(cid, "original-request", "writer", 90)
    assert await jobs.update(
        cid,
        "original-request",
        "writer",
        result={
            "answer": first.answer,
            "status": "fallback",
            "tokens": 0,
            "thoughts": [{"tool": PRIVATE_REVISION_TOOL, "state": first.revision_state}],
        },
    )
    restored = await other.get(cid, "original-request")
    assert message_payload(restored)["revision"]["available"]
    assert not message_payload(restored)["thoughts"]
    settings.quality = {"max_revisions": 1, "qa_claim_max_revisions": 1}
    second = await answer_question(
        "解释变量关系",
        history=[],
        ctx=ctx,
        revision_seed=stored_revision_state(restored.thoughts),
    )
    assert not second.fallback and model.stream_calls == 1 and len(model.edits) == 1


async def test_legacy_reconstruction_cannot_overwrite_known_retraction_with_old_metadata(settings):
    from deep_research.models import ScholarlyMetadata, Source
    from deep_research.workbench.qa_revision_state import legacy_revision_state
    from deep_research.workbench.qa_store import QaMessage

    first, model, ctx = await initial(settings)
    source = Source.model_validate(first.revision_state["sources"][0])
    retracted = source.model_copy(update={"scholarly": ScholarlyMetadata(retracted=True)})
    parent = QaMessage(
        id="old",
        position=0,
        query="解释变量关系",
        answer=first.answer,
        citations=first.citations,
        thoughts=first.thoughts,
        status="fallback",
        evidence=[
            {
                "statement": f.statement,
                "source_url": f.source_url,
                "evidence_quote": f.evidence_quote,
            }
            for f in first.findings
        ],
    )
    restored = legacy_revision_state(parent, sources=[retracted])
    second = await answer_question("解释变量关系", history=[], ctx=ctx, revision_seed=restored)
    assert second.fallback and not second.findings
    assert model.extractions == 1 and model.evidence_checks == 1
