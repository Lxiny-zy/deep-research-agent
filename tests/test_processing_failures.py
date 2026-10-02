from __future__ import annotations

from datetime import UTC, datetime

from deep_research.agents.base import Blackboard, RunContext
from deep_research.agents.researcher import Researcher
from deep_research.llm import InputCapacityError
from deep_research.models import ExtractedFindingList, ResearchPlan, SubQuestion
from deep_research.observability import Tracer
from deep_research.orchestrator import create_initial_execution
from deep_research.persistence.memory_repository import InMemoryRepository
from deep_research.persistence.repository import RunDetail
from deep_research.report.service import ReportService
from deep_research.workbench.contract import CONTRACT_SCRATCH_KEY, build_contract
from deep_research.workbench.extraction import processing_failures
from deep_research.workbench.publish import build_bundle
from deep_research.workbench.templates import AUTO_RESEARCH
from deep_research.workbench.writers import ResearchWriter
from tests.fakes import FakeLLM, FakeSearch


async def test_all_failed_search_queries_are_retained_as_failure_not_absent_evidence(settings):
    class FailedSearch(FakeSearch):
        async def search(self, query, **kwargs):
            raise TimeoutError("provider-private-message")

    result = await Researcher(FakeLLM(), FailedSearch(), Tracer(), settings).run(
        "完整研究问题", search_queries=["first", "second"]
    )
    assert result is not None and result.extraction_audit is not None
    assert result.extraction_audit.issues == ["retrieval_call_failed:TimeoutError"]
    assert "检索调用未完成" in processing_failures([result])[0]
    assert "provider-private-message" not in result.model_dump_json()


async def test_failed_extraction_keeps_sources_and_blocks_official_delivery(settings):
    class Failed(FakeLLM):
        async def parse(self, system, user, schema, **kwargs):
            if schema is ExtractedFindingList:
                raise InputCapacityError("provider-private-message")
            return await super().parse(system, user, schema, **kwargs)

        async def stream(self, *args, **kwargs):
            raise AssertionError("An incomplete input must not be rewritten into a report")
            yield ""  # pragma: no cover

    tracer, llm = Tracer(), Failed()
    ctx = RunContext(llm=llm, search_tool=FakeSearch(), tracer=tracer, settings=settings)
    bb = Blackboard(
        query="研究原始问题",
        plan=ResearchPlan(
            interpretation="范围", sub_questions=[SubQuestion(question="未处理完的关键方法")]
        ),
        scratch={
            CONTRACT_SCRATCH_KEY: build_contract(
                AUTO_RESEARCH, "研究原始问题", strategy="quick"
            ).model_dump()
        },
    )
    await Researcher().step(bb, ctx)
    assert len(bb.results) == 1
    audit = bb.results[0].extraction_audit
    assert audit and audit.sources and not audit.candidates
    assert audit.issues == ["extraction_call_failed:InputCapacityError"]
    assert "provider-private-message" not in bb.model_dump_json() + str(tracer.events)
    await ResearchWriter().step(bb, ctx)
    assert "材料处理未完成" in bb.report.markdown and llm.stream_calls == 0
    execution = create_initial_execution(bb.query, "research_quick", settings)
    execution.checkpoint.update(bb.model_dump(mode="json"))
    detail = RunDetail(
        id="failed-input",
        query=bb.query,
        status="done",
        created_at=datetime.now(UTC),
        orchestration=execution,
        results=bb.results,
        report=bb.report,
    )
    bundle = build_bundle(detail)
    assert bundle.status == "fail" and {f.format for f in bundle.files} == {"md"}
    assert next(g for g in bundle.gates if g.name == "source_processing").status == "fail"

    class Store:
        async def get_run(self, run_id):
            return detail

        async def get_events(self, run_id, **kwargs):
            return []

    document = await ReportService(Store()).document(detail.id)
    assert document.final_validation.scope == "source_processing"
    assert document.final_validation.support_status == "fail"
    repo = InMemoryRepository()
    run_id = await repo.create_run(bb.query)
    await repo.save_result(run_id, bb.results[0])
    retained = await repo.get_run(run_id)
    assert retained.results[0].extraction_audit.sources == audit.sources
