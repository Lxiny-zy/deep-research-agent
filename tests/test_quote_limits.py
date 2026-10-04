from __future__ import annotations

import hashlib
import json

import pytest

from deep_research.agents.researcher import Researcher
from deep_research.guardrails import EvidenceVerifier, report_eligible
from deep_research.models import (
    ExtractionAudit,
    ExtractionCandidate,
    RepairFindingContent,
    ResearchResult,
    Source,
)
from deep_research.observability import Tracer
from deep_research.workbench.intake import _FixedSources
from deep_research.workbench.quality import QualityPolicy, quality_schema
from deep_research.workbench.quote_repair import quote_options, resolve_quote
from tests.fakes import FakeLLM, FakeSearch, verified_finding
from tests.test_extraction_repair import URL, Extractor, Semantic, finding, repair

TARGET = "Alpha achieves an accuracy of 95 percent under the stated test conditions."
LONG = (
    "Background describes other experiments. " * 45 + TARGET + " Further discussion follows." * 65
)


def test_verbatim_match_rejects_an_entire_long_source():
    checked = EvidenceVerifier().verify(
        finding(TARGET, LONG).as_unverified(), Source(url=URL, content=LONG)
    )
    assert not checked.accepted
    assert checked.reason == "evidence_quote_too_long"


def test_raw_source_span_cannot_hide_excess_length_behind_normalization():
    quote = "Alpha " + "\n " * 400 + "uses spectral attention."
    checked = EvidenceVerifier().verify(
        finding("Alpha uses spectral attention", "Alpha uses spectral attention.").as_unverified(),
        Source(url=URL, content=quote),
    )
    assert not checked.accepted
    assert checked.reason == "evidence_quote_too_long"


def test_quote_limit_is_configurable_and_exposed_by_quality_settings():
    assert QualityPolicy().max_evidence_quote_chars == 600
    field = next(item for item in quality_schema() if item["key"] == "max_evidence_quote_chars")
    assert field["default"] == 600 and field["unit"] == "字"
    assert (
        EvidenceVerifier(max_quote_chars=1000)
        .verify(finding(quote="x" * 700).as_unverified(), Source(url=URL, content="x" * 700))
        .accepted
    )


def test_quote_options_offer_short_exact_spans_without_a_full_source_escape():
    source = Source(url=URL, content=LONG)
    candidate = ExtractionCandidate(id="c1", original=finding(TARGET, LONG))
    options = quote_options(candidate, [source], max_quote_chars=600)
    assert options and any(option.text == TARGET for option in options)
    for option in options:
        assert "-full-" not in option.id
        assert len(option.text) <= 600
        assert source.content[option.start : option.end] == option.text
        assert option.source_content_hash == hashlib.sha256(source.content.encode()).hexdigest()


def test_short_quote_does_not_add_the_rest_of_the_source_as_an_option():
    source = Source(url=URL, content=LONG)
    candidate = ExtractionCandidate(id="c1", original=finding(TARGET, TARGET))
    assert [option.text for option in quote_options(candidate, [source])] == [TARGET]


class SelectShortQuote(Extractor):
    async def parse(self, system, user, schema, **kwargs):
        if self.requests:
            candidate = json.loads(user.rsplit("\n", 1)[1])["candidates"][0]
            option = next(item for item in candidate["quote_options"] if item["text"] == TARGET)
            assert "full_source" not in user.rsplit("\n", 1)[1]
            self.repairs = iter(
                [repair(findings=[dict(finding(TARGET, "").model_dump(), quote_id=option["id"])])]
            )
        return await super().parse(system, user, schema, **kwargs)


@pytest.mark.asyncio
async def test_long_quote_is_reselected_and_semantically_verified(settings):
    llm, semantic = SelectShortQuote([finding(TARGET, LONG)], []), Semantic()
    researcher = Researcher(
        llm,
        _FixedSources([Source(url=URL, content=LONG)]),
        Tracer(),
        settings,
        semantic_verifier=semantic,
    )
    result = await researcher.run("What is Alpha's measured accuracy?")
    assert len(llm.requests) == 2
    assert semantic.calls == [[TARGET]]
    assert len(result.findings) == 1 and report_eligible(result.findings[0])
    assert result.findings[0].evidence_quote == TARGET
    candidate = result.extraction_audit.candidates[0]
    assert candidate.original.evidence_quote == LONG
    assert candidate.attempts[0].checks[0].problems == ["evidence_quote_too_long"]
    assert len(candidate.attempts) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("late_settings", [False, True])
async def test_configured_quote_limit_controls_both_verification_and_prompt(
    settings, late_settings
):
    settings.quality = {"max_evidence_quote_chars": 1000}
    quote = "An exact source quotation. " * 30
    llm = Extractor([finding("Alpha description", quote)], [])
    researcher = Researcher(
        llm,
        _FixedSources([Source(url=URL, content=quote)]),
        Tracer(),
        None if late_settings else settings,
        semantic_verifier=Semantic(),
    )
    researcher.settings = settings
    result = await researcher.run("Describe Alpha")
    assert len(llm.requests) == 1 and "最多 1000 字" in llm.requests[0][0]
    assert len(result.findings[0].evidence_quote) > 600
    assert report_eligible(result.findings[0])


def test_a_saved_quote_option_cannot_bypass_the_current_length_limit():
    quote = "An exact source quotation. " * 30
    source = Source(url=URL, content=quote)
    candidate = ExtractionCandidate(id="old", original=finding("Alpha", quote))
    candidate.quote_options = quote_options(candidate, [source], max_quote_chars=1000)
    proposal = RepairFindingContent(
        statement="Alpha",
        source_url=URL,
        quote_id=candidate.quote_options[0].id,
    )
    assert resolve_quote(proposal, candidate, [source])[1] == ["evidence_quote_too_long"]


def legacy_result():
    source = Source(url=URL, content=LONG)
    old = verified_finding(TARGET, URL, LONG)
    old.verification.source_content_hash = hashlib.sha256(LONG.encode()).hexdigest()
    result = ResearchResult(
        sub_question="Alpha accuracy",
        findings=[old],
        extraction_audit=ExtractionAudit(question="Alpha accuracy", sources=[source]),
    )
    return result, source


@pytest.mark.asyncio
@pytest.mark.parametrize("stored_copy", [False, True])
async def test_restored_long_quote_reuses_frozen_source_and_preserves_unaffected_findings(
    settings, stored_copy
):
    from deep_research.workbench.quote_recovery import repair_long_quotes

    result, source = legacy_result()
    unchanged = verified_finding("A short independent finding", URL, TARGET)
    result.findings.append(unchanged)
    llm = SelectShortQuote([], [])
    llm.requests.append(("prior extraction", ""))  # recovery starts at repair, not extraction
    semantic = Semantic()
    researcher = Researcher(llm, None, Tracer(), settings, semantic_verifier=semantic)
    extra = (
        [source.model_copy(update={"content_hash": hashlib.sha256(LONG.encode()).hexdigest()})]
        if stored_copy
        else []
    )
    recovered = await repair_long_quotes(result, researcher, extra)
    assert len(llm.requests) == 2 and semantic.calls == [[TARGET]]
    assert [f.evidence_quote for f in recovered.findings] == [TARGET, TARGET]
    assert recovered.findings[1] == unchanged
    assert result.findings[0].evidence_quote == LONG
    assert [(s.url, s.content) for s in recovered.extraction_audit.sources] == [
        (source.url, source.content)
    ]
    assert recovered.extraction_audit.candidates[-1].original.evidence_quote == LONG
    assert await repair_long_quotes(recovered, researcher) == recovered
    assert len(llm.requests) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("changed_source", [False, True])
async def test_recovery_without_matching_source_blocks_reuse_without_provider_calls(
    settings,
    changed_source,
):
    from deep_research.workbench.quote_recovery import quote_length_issues, repair_long_quotes

    result, _ = legacy_result()
    result.extraction_audit.sources = (
        [Source(url=URL, content=LONG + " changed")] if changed_source else []
    )
    llm = Extractor([], [])
    researcher = Researcher(llm, None, Tracer(), settings, semantic_verifier=Semantic())
    recovered = await repair_long_quotes(result, researcher)
    assert not llm.requests
    assert not any(report_eligible(f) for f in recovered.findings)
    assert quote_length_issues([recovered], 600)


def test_old_verified_long_quotes_cannot_pass_delivery_length_gate():
    from deep_research.workbench.quote_recovery import quote_length_issues

    result, _ = legacy_result()
    assert quote_length_issues([result], 600)
    assert not quote_length_issues([result], 5000)


@pytest.mark.asyncio
async def test_recovery_cannot_keep_old_support_when_short_quote_fails_semantic_review(settings):
    from deep_research.workbench.quote_recovery import quote_length_issues, repair_long_quotes

    class RejectShort(Semantic):
        async def verify_batch(self, findings, llm, **kwargs):
            checked = await super().verify_batch(findings, llm, **kwargs)
            for item in checked:
                item.verification.semantic_status = "unsupported"
                item.verification.semantic_reason = "Necessary condition omitted"
            return checked

    result, _ = legacy_result()
    llm = SelectShortQuote([], [])
    llm.requests.append(("prior extraction", ""))
    researcher = Researcher(llm, None, Tracer(), settings, semantic_verifier=RejectShort())
    recovered = await repair_long_quotes(result, researcher)
    assert not any(report_eligible(item) for item in recovered.findings)
    assert quote_length_issues([recovered], 600)


class RecoveryLLM(FakeLLM):
    repairs = 0

    async def parse(self, system, user, schema, **kwargs):
        from deep_research.models import ExtractedFindingList

        if schema is ExtractedFindingList:
            data = json.loads(user.rsplit("\n", 1)[1])
            candidate = data["candidates"][0]
            option = next(item for item in candidate["quote_options"] if item["text"] == TARGET)
            self.repairs += 1
            return ExtractedFindingList.model_validate(
                repair(
                    candidate["candidate_id"],
                    [dict(finding(TARGET, "").model_dump(), quote_id=option["id"])],
                )
            )
        return await super().parse(system, user, schema, **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("graph", [False, True])
@pytest.mark.parametrize("audited", [False, True])
async def test_workflow_restores_short_quotes_before_downstream_work(settings, graph, audited):
    from deep_research.agents.base import Blackboard, RunContext
    from deep_research.workflow import Step, Workflow, WorkflowEngine

    result, source = legacy_result()
    if not audited:
        result.extraction_audit = None
    bb = Blackboard(query="Alpha accuracy", results=[result])
    llm = RecoveryLLM()
    ctx = RunContext(
        llm=llm,
        search_tool=FakeSearch(),
        tracer=Tracer(),
        settings=settings,
        evidence_sources=[source],
    )

    class Observer:
        async def step(self, bb, ctx):
            assert bb.results[0].findings[0].evidence_quote == TARGET
            assert report_eligible(bb.results[0].findings[0])
            return bb

    wf = Workflow(
        name="quote-recovery",
        steps=[] if graph else [Step(agent="observer")],
        nodes=[{"id": "check", "step": {"agent": "observer"}}] if graph else [],
    )
    engine = WorkflowEngine(ctx, resolver=lambda _: Observer())
    restored = await engine.run(wf, bb)
    assert restored.results[0].findings[0].evidence_quote == TARGET
    assert engine.runtime.run.checkpoint["results"][0]["findings"][0]["evidence_quote"] == TARGET
    assert llm.repairs == 1


@pytest.mark.asyncio
async def test_subquestion_progress_rewrites_long_quotes_without_new_retrieval(tmp_path):
    from deep_research.agents.base import Blackboard
    from deep_research.artifacts import ArtifactStore
    from deep_research.models import ResearchPlan, SubQuestion
    from tests.test_research_progress import context

    old, _ = legacy_result()
    initial = Blackboard(
        query="q",
        plan=ResearchPlan(
            interpretation="q", sub_questions=[SubQuestion(question=old.sub_question)]
        ),
    )

    class Legacy(Researcher):
        async def run(self, *args, **kwargs):
            return old

    class Resumed(Researcher):
        async def run(self, *args, **kwargs):
            raise AssertionError("Frozen research must not trigger a new search/extraction")

    store = ArtifactStore(tmp_path)
    await Legacy().step(initial.model_copy(deep=True), context(store))
    ctx = context(store)
    ctx.llm = RecoveryLLM()
    restored = await Resumed().step(initial.model_copy(deep=True), ctx)
    assert restored.results[0].findings[0].evidence_quote == TARGET
    assert ctx.llm.repairs == 1
    again = await Resumed().step(initial.model_copy(deep=True), ctx)
    assert again.results[0].findings[0].evidence_quote == TARGET
    assert ctx.llm.repairs == 1


def test_delivery_blocks_legacy_long_quotes_before_rendering_formal_formats():
    from deep_research.models import Report
    from deep_research.persistence.repository import RunDetail
    from deep_research.workbench.publish import build_bundle

    result, source = legacy_result()
    detail = RunDetail(
        id="legacy-long",
        query="Research Alpha",
        status="done",
        results=[result],
        sources=[source],
        report=Report(query="Research Alpha", markdown=TARGET + " [1]", citations=[URL]),
    )
    bundle = build_bundle(detail)
    gate = next(g for g in bundle.gates if g.name == "evidence_quote_length")
    assert gate.status == "fail"
    assert {file.format for file in bundle.files} == {"md"}


@pytest.mark.asyncio
@pytest.mark.parametrize("graph", [False, True])
async def test_failed_workflow_resume_repairs_completed_research_without_reexecuting_it(
    settings,
    graph,
):
    from deep_research.agents.base import Blackboard, RunContext
    from deep_research.workflow import Step, Workflow, WorkflowEngine

    old, source = legacy_result()

    class Producer:
        async def step(self, bb, ctx):
            bb.results = [old]
            return bb

    class Interrupted:
        async def step(self, bb, ctx):
            raise RuntimeError("process interrupted before writing")

    steps = [Step(agent="producer"), Step(agent="writer", failure_policy="fail_fast")]
    wf = Workflow(
        name="resume-old-quotes",
        steps=[] if graph else steps,
        nodes=[{"id": step.agent, "step": step.model_dump()} for step in steps] if graph else [],
        edges=[{"id": "p-w", "source": "producer", "target": "writer"}] if graph else [],
    )
    ctx = RunContext(llm=FakeLLM(), search_tool=FakeSearch(), tracer=Tracer(), settings=settings)
    first = WorkflowEngine(
        ctx, resolver=lambda role: Producer() if role == "producer" else Interrupted()
    )
    with pytest.raises(RuntimeError, match="process interrupted"):
        await first.run(wf, Blackboard(query="Alpha"))
    saved = first.runtime.run.model_copy(deep=True)
    assert saved.checkpoint["results"][0]["findings"][0]["evidence_quote"] == LONG

    class Writer:
        async def step(self, bb, ctx):
            assert bb.results[0].findings[0].evidence_quote == TARGET
            return bb

    def resolve(role):
        assert role != "producer", "completed research must remain committed"
        return Writer()

    llm = RecoveryLLM()
    resumed_ctx = RunContext(
        llm=llm,
        search_tool=FakeSearch(),
        tracer=Tracer(),
        settings=settings,
        evidence_sources=[source],
    )
    resumed = WorkflowEngine(resumed_ctx, resolver=resolve, resume_run=saved)
    completed = await resumed.run(wf, Blackboard.model_validate(saved.checkpoint))
    assert completed.results[0].findings[0].evidence_quote == TARGET
    assert llm.repairs == 1
