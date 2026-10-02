from __future__ import annotations

import hashlib
from copy import deepcopy

from deep_research.agents.base import Blackboard, RunContext
from deep_research.models import ExtractedFindingList, FindingContent, ResearchResult
from deep_research.observability import Tracer
from deep_research.workbench.attachments import Attachment, AttachmentChunk
from deep_research.workbench.contract import CONTRACT_SCRATCH_KEY, build_contract
from deep_research.workbench.paper_evidence import PaperEvidenceSelection
from deep_research.workbench.review_coverage import ReviewEvidenceCoverage, coverage_issues
from deep_research.workbench.templates import LIT_REVIEW
from tests.fakes import FakeLLM, FakeSearch, verified_finding


def material(rounds=1):
    attachment = Attachment(
        id="a" * 24,
        filename="method.txt",
        kind="text",
        size=200,
        char_count=200,
        chunks=[
            AttachmentChunk(
                ordinal=0,
                locator="Introduction",
                content="The paper proposes a learned image prior.",
            ),
            AttachmentChunk(
                ordinal=1,
                locator="Core method",
                content="The network stages correspond to optimization iterations.",
            ),
            AttachmentChunk(
                ordinal=2, locator="References", content="Unrelated bibliographic references."
            ),
        ],
    )
    sources = attachment.sources()
    finding = verified_finding(sources[0].content, sources[0].url, sources[0].content)
    finding.verification.source_content_hash = hashlib.sha256(
        sources[0].content.encode()
    ).hexdigest()
    contract = build_contract(
        LIT_REVIEW,
        "Compare the core method and its trainable components",
        strategy="none",
        quality={"review_evidence_rounds": rounds},
    )
    bb = Blackboard(
        query=contract.original_request,
        results=[ResearchResult(sub_question="initial read", findings=[finding])],
        scratch={
            CONTRACT_SCRATCH_KEY: contract.model_dump(),
            "attachments": [attachment.model_dump()],
        },
    )
    return bb, sources


class Reader(FakeLLM):
    def __init__(self, sources):
        super().__init__()
        self.sources = sources
        self.plans = 0
        self.reads = []
        self.unknown = False

    async def parse(self, system, user, schema, **kwargs):
        if schema is PaperEvidenceSelection:
            self.plans += 1
            sufficient = self.sources[1].content in user
            return PaperEvidenceSelection(
                sufficient=sufficient,
                finding_ids=["e1"],
                missing_topics=[]
                if sufficient
                else ["Missing the algorithm stages and computation steps"],
                source_urls=[]
                if sufficient
                else ["https://outside.test/other" if self.unknown else self.sources[1].url],
            )
        if schema is ExtractedFindingList:
            selected = [s for s in self.sources if s.url in user]
            self.reads.append([s.url for s in selected])
            # The initial read omits the central method; focused rereading recovers it.
            source = self.sources[0] if len(selected) > 1 else selected[0]
            return ExtractedFindingList(
                findings=[
                    FindingContent(
                        statement=source.content,
                        source_url=source.url,
                        evidence_quote=source.content,
                    )
                ]
            )
        return await super().parse(system, user, schema, **kwargs)


class NoSearch(FakeSearch):
    async def search(self, *args, **kwargs):
        raise AssertionError("Closed reviews must not use open search")


async def test_recovery_reads_only_missing_method_and_reuses_a_bound_success(settings):
    bb, sources = material()
    llm = Reader(sources)
    ctx = RunContext(llm=llm, search_tool=NoSearch(), tracer=Tracer(), settings=settings)
    old = deepcopy(bb.results[0].findings[0].model_dump())
    await ReviewEvidenceCoverage().step(bb, ctx)
    assert llm.reads == [[sources[1].url]] and llm.plans == 2
    assert not coverage_issues(bb.scratch, bb.results)
    assert bb.results[0].findings[0].statement == old["statement"]
    assert bb.results[0].findings[0].evidence_quote == old["evidence_quote"]
    await ReviewEvidenceCoverage().step(bb, ctx)
    assert llm.plans == 2 and len(llm.reads) == 1


async def test_unknown_source_never_triggers_extra_reading(settings):
    bb, sources = material()
    llm = Reader(sources)
    llm.unknown = True
    await ReviewEvidenceCoverage().step(
        bb, RunContext(llm=llm, search_tool=NoSearch(), tracer=Tracer(), settings=settings)
    )
    assert coverage_issues(bb.scratch, bb.results) and not llm.reads
    assert len(bb.results) == 1


async def test_zero_recovery_rounds_still_checks_and_reports_missing_evidence(settings):
    bb, sources = material(rounds=0)
    llm = Reader(sources)
    await ReviewEvidenceCoverage().step(
        bb, RunContext(llm=llm, search_tool=NoSearch(), tracer=Tracer(), settings=settings)
    )
    assert llm.plans == 1 and not llm.reads
    assert "Missing the algorithm" in coverage_issues(bb.scratch, bb.results)[0]


async def test_changed_source_or_request_invalidates_coverage(settings):
    bb, sources = material()
    await ReviewEvidenceCoverage().step(
        bb,
        RunContext(llm=Reader(sources), search_tool=NoSearch(), tracer=Tracer(), settings=settings),
    )
    assert not coverage_issues(bb.scratch, bb.results)
    changed = deepcopy(bb.scratch)
    changed["attachments"][0]["chunks"][0]["content"] += " Different text."
    assert coverage_issues(changed, bb.results)
    changed = deepcopy(bb.scratch)
    changed[CONTRACT_SCRATCH_KEY]["original_request"] = "Compare the hardware measurements instead"
    assert coverage_issues(changed, bb.results)


async def test_consistency_removal_cannot_leave_a_stale_positive_coverage(settings, monkeypatch):
    from deep_research.workbench import review_coverage

    async def remove_new(results, *args, **kwargs):
        results[-1].findings.clear()

    monkeypatch.setattr(review_coverage, "verify_claim_consistency", remove_new)
    bb, sources = material()
    await ReviewEvidenceCoverage().step(
        bb,
        RunContext(llm=Reader(sources), search_tool=NoSearch(), tracer=Tracer(), settings=settings),
    )
    assert coverage_issues(bb.scratch, bb.results)
    assert len(bb.scratch["review_coverage"]["documents"][0]["checks"]) == 3


async def test_closed_workflow_recovers_omitted_method_before_writing(settings):
    from deep_research.orchestrator import DeepResearchAgent, create_initial_execution
    from deep_research.persistence.memory_repository import InMemoryRepository

    bb, sources = material()

    class Writer(Reader):
        async def stream(self, system, user, **kwargs):
            assert sources[1].content in user
            yield (
                "## 摘要\nThe review describes a learned prior and iterative network.\n\n"
                + "\n\n".join(
                    f"## {title}\n{sources[1].content} [2]"
                    for title in ("引言", "主题综述", "方法对比", "开放问题", "结论")
                )
            )

    llm = Writer(sources)
    execution = create_initial_execution(bb.query, "lit_review_provided", settings)
    execution.checkpoint["scratch"].update(bb.scratch)
    repo = InMemoryRepository()
    run_id = await repo.create_run(bb.query, execution=execution)
    agent = DeepResearchAgent(
        settings,
        llm=llm,
        search_tool=NoSearch(),
        workflow="lit_review_provided",
        repo=repo,
        run_id=run_id,
        initial_execution=execution,
    )
    await agent.run(bb.query)
    detail = await repo.get_run(run_id)
    assert detail is not None
    assert llm.reads == [[s.url for s in sources], [sources[1].url]]
    assert not coverage_issues(detail.orchestration.checkpoint["scratch"], detail.results)
    from deep_research.workbench.publish import build_bundle

    bundle = build_bundle(detail)
    assert next(g for g in bundle.gates if g.name == "review_coverage").status == "pass"
    detail.orchestration.checkpoint["scratch"]["review_coverage"]["documents"][0]["status"] = "fail"
    blocked = build_bundle(detail)
    assert next(g for g in blocked.gates if g.name == "review_coverage").status == "fail"
    assert {file.format for file in blocked.files} == {"md"} and blocked.files[0].status == "fail"
