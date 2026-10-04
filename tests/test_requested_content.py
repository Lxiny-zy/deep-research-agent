"""User-named deliverables are independent of the template's default headings."""

import pytest

from deep_research.workbench.contract import TaskContract, build_contract
from deep_research.workbench.templates import get_template
from tests.test_content_revision import service as service

SC60_REQUEST = (
    "精读所附 DAUHST 完整论文，用中文形成有原文依据的精读结论报告。"
    "提炼贡献、可借鉴的设计、适用边界与待验证问题；未核实内容不能写成论文缺陷或普遍结论。"
)
SC32_REQUEST = (
    "仅根据上传的 MST、DGSMP、MST++ 三篇论文，写一份中文主题综述。"
    "对比表应覆盖三篇论文，写清输入与任务、核心机制、实验条件及证据。"
)
SC82_REQUEST = (
    "查阅公开学术文献，用中文说明 split conformal prediction 对单个未来样本的边际覆盖保证、"
    "交换性假设，以及它与给定特征值的条件覆盖有什么区别。三部分都要回答。"
)


def test_sc60_named_design_is_frozen_beyond_template_sections():
    contract = build_contract(get_template("paperRead"), SC60_REQUEST)
    assert any(item.label == "可借鉴的设计" for item in contract.requested_items)
    assert "可借鉴的设计" in contract.render()
    assert TaskContract.model_validate_json(contract.model_dump_json()) == contract


def test_sc32_freezes_table_fields_and_all_three_named_comparison_targets():
    contract = build_contract(get_template("litReview"), SC32_REQUEST, strategy="none")
    fields = {item.label for item in contract.requested_items if item.kind == "table_column"}
    assert {"核心机制", "实验条件"} <= fields
    targets = {
        item.label
        for item in contract.requested_items
        if item.kind == "comparison" and item.table_only
    }
    assert {"MST", "DGSMP", "MST++"} <= targets


def test_sc82_preserves_all_three_topics_without_inventing_a_q3_question_column():
    contract = build_contract(get_template("autoResearch"), SC82_REQUEST)
    labels = [item.label for item in contract.requested_items]
    assert any("边际覆盖保证" in label for label in labels)
    assert any("交换性假设" in label for label in labels)
    assert any("条件覆盖" in label for label in labels)
    assert not any(item.kind == "table_column" for item in contract.requested_items)


def test_negations_code_samples_and_urls_do_not_create_or_hide_positive_requirements():
    from deep_research.workbench.requested_content import extract_requested_items

    text = "请阅读 https://example.org/paper.pdf，列出贡献、局限。不要输出额外图示。\n```text\n列出伪要求\n```"
    labels = {item.label for item in extract_requested_items(text)}
    assert {"贡献", "局限"} <= labels
    assert "额外图示" not in labels and "伪要求" not in labels
    fields = extract_requested_items("表格列名：X、Y")
    assert {item.label for item in fields if item.kind != "overall"} == {"X", "Y"}


def test_table_fields_do_not_swallow_a_following_content_instruction():
    from deep_research.workbench.requested_content import extract_requested_items

    items = extract_requested_items("对比表列出核心机制、实验条件，并解释适用边界。")
    assert {item.label for item in items if item.kind == "table_column"} == {"核心机制", "实验条件"}
    assert any(item.label == "适用边界" and item.kind == "content" for item in items)


def test_sc82_quartile_table_missing_q3_is_flagged_without_confusing_research_questions():
    from deep_research.workbench.coverage_review import table_scope_issues

    table = ("表 1 四分位覆盖\n\n| 方法 | Q1 | Q2 | Q4 |\n"
             "|---|---|---|---|\n| SCP | 0.97 | 0.96 | 0.75 |")
    assert "Q3" in table_scope_issues(table)[0]
    assert not table_scope_issues(table.replace("四分位覆盖", "研究问题回答"))
    assert not table_scope_issues(table.replace("表 1 四分位覆盖", "表 1 仅比较 Q1、Q2、Q4 四分位"))


class CoverageJudge:
    def __init__(self, *, force=None, basis_ids=()):
        self.force, self.basis_ids = force, list(basis_ids)
        self.calls = []

    async def parse(self, system, user, schema, **kwargs):
        import json

        from deep_research.workbench.coverage_review import CoverageDecisions

        assert schema is CoverageDecisions
        data = json.loads(user)
        self.calls.append(data)
        decisions = []
        for item in data["requirements"]:
            location = None
            for region in data["regions"]:
                if item["kind"] == "table_column" and region["kind"] == "table":
                    for column, header in enumerate(region["rows"][0]):
                        if item["label"] in header and len(region["rows"]) > 1:
                            location = dict(
                                region_id=region["id"],
                                column=column,
                                quote=region["rows"][1][column],
                            )
                elif item["table_only"] and region["kind"] == "table":
                    for row, cells in enumerate(region["rows"][1:], 1):
                        if item["label"] in " | ".join(cells):
                            location = dict(
                                region_id=region["id"], row=row, quote=" | ".join(cells)
                            )
                elif (
                    item["kind"] in {"section", "branch"}
                    and region["kind"] == item["kind"]
                    and item["label"] in region["title"]
                ):
                    if region["text"]:
                        location = dict(region_id=region["id"], quote=region["text"])
                elif (
                    item["kind"] not in {"section", "branch"}
                    and not item["table_only"]
                    and region["kind"] != "table"
                    and item["label"] in region["text"]
                ):
                    location = dict(region_id=region["id"], quote=region["text"])
                if location:
                    break
            if item["kind"] == "overall" and data["regions"]:
                region = next((r for r in data["regions"] if r["text"]), data["regions"][0])
                if region["text"]:
                    location = dict(region_id=region["id"], quote=region["text"])
            if self.force and data["regions"]:
                region = next((r for r in data["regions"] if r["text"]), data["regions"][0])
                location = dict(region_id=region["id"], quote=region["text"])
            decisions.append(
                dict(
                    requirement_id=item["id"],
                    status=self.force or ("covered" if location else "missing"),
                    locations=[location] if location else [],
                    basis_ids=self.basis_ids,
                    reason="controlled coverage assessment",
                )
            )
        return CoverageDecisions(decisions=decisions)


@pytest.mark.asyncio
async def test_named_content_is_missing_even_when_default_headings_are_present():
    from deep_research.workbench.coverage_review import CoverageReviewer
    from deep_research.workbench.gates import structure_gate

    template = get_template("paperRead")
    contract = build_contract(template, "请提炼可借鉴的设计。")
    body = "\n\n".join(f"## {section.title}\n已有方法事实与结果。" for section in template.sections)
    assert structure_gate(body, template).status == "pass"
    record = await CoverageReviewer(CoverageJudge(), contract, 50000).review(body, [])
    assert record["status"] == "fail" and "可借鉴的设计" in " ".join(record["issues"])
    body += "\n\n## 可迁移思路\n可借鉴的设计包括将数据保真项与先验解耦。"
    assert (await CoverageReviewer(CoverageJudge(), contract, 50000).review(body, []))[
        "status"
    ] == "pass"


@pytest.mark.asyncio
async def test_sc32_missing_table_fields_cannot_be_satisfied_by_other_prose():
    from deep_research.workbench.coverage_review import CoverageReviewer

    contract = build_contract(get_template("litReview"), SC32_REQUEST, strategy="none")
    body = ("核心机制与实验条件将在正文讨论。\n\n| 方法 | 输入与任务 | 证据 |\n"
            "|---|---|---|\n| MST | CASSI | 来源一 |\n| DGSMP | CASSI | 来源二 |\n"
            "| MST++ | RGB重建 | 来源三 |")
    record = await CoverageReviewer(CoverageJudge(), contract, 50000).review(body, [])
    assert record["status"] == "fail"
    assert "核心机制" in " ".join(record["issues"]) and "实验条件" in " ".join(record["issues"])
    forged = await CoverageReviewer(CoverageJudge(force="covered"), contract, 50000).review(
        body, []
    )
    assert forged["status"] == "fail"


@pytest.mark.asyncio
async def test_empty_named_section_and_unjustified_material_gap_do_not_pass():
    from deep_research.workbench.coverage_review import CoverageReviewer

    section = build_contract(get_template("paperRead"), "请增加章节「可借鉴的设计」。")
    assert (
        await CoverageReviewer(CoverageJudge(), section, 50000).review("## 可借鉴的设计\n", [])
    )["status"] == "fail"
    contract = build_contract(get_template("autoResearch"), "请说明实验条件。")
    body = "实验条件目前无法确认：附件 A 读取失败。"
    assert (
        await CoverageReviewer(CoverageJudge(force="insufficient"), contract, 50000).review(
            body, []
        )
    )["status"] == "fail"
    bases = [dict(id="failed-A", kind="input_unavailable", reason="A 读取失败", source="A")]
    result = await CoverageReviewer(
        CoverageJudge(force="insufficient", basis_ids=["failed-A"]), contract, 50000
    ).review(body, bases)
    assert result["status"] == "pass"


@pytest.mark.asyncio
async def test_coverage_binding_rejects_changed_body_or_changed_frozen_requirements():
    from deep_research.workbench.coverage_review import CoverageReviewer, coverage_issues

    contract = build_contract(get_template("paperRead"), "请说明可借鉴的设计。")
    body = "可借鉴的设计包括将数据项与先验分离。"
    record = await CoverageReviewer(CoverageJudge(), contract, 50000).review(body, [])
    assert record["status"] == "pass"
    assert coverage_issues(contract, "内容已经删除。", record, [])
    changed = contract.model_copy(update={"requested_items": []})
    assert coverage_issues(changed, body, record, [])


def test_pasted_source_is_not_reinterpreted_as_user_requirements():
    request = (
        "Abstract\n"
        + "We describe a method, include additional experiments and discuss limitations. " * 5
    )
    contract = build_contract(get_template("paperRead"), request)
    assert contract.requested_items == [] and contract.request_instructions == ""
    mixed = "请说明贡献与局限。\n论文正文如下：\n" + request
    contract = build_contract(get_template("paperRead"), mixed)
    assert {item.label for item in contract.requested_items if item.kind != "overall"} == {
        "贡献",
        "局限",
    }
    assert "We describe" not in contract.request_instructions


@pytest.mark.asyncio
async def test_named_mindmap_branches_need_more_than_their_titles():
    from deep_research.workbench.coverage_review import CoverageReviewer

    contract = build_contract(get_template("mindmap"), "分支包括方法、局限。")
    assert {item.kind for item in contract.requested_items} == {"branch", "overall"}
    empty = "# 主题\n\n- 方法\n- 局限"
    assert (await CoverageReviewer(CoverageJudge(), contract, 50000).review(empty, []))[
        "status"
    ] == "fail"
    full = ("# 主题\n\n- 方法\n  - 使用已知数据与先验分离的方式。\n"
            "- 局限\n  - 适用范围需要受测试条件约束。")
    assert (await CoverageReviewer(CoverageJudge(), contract, 50000).review(full, []))[
        "status"
    ] == "pass"


@pytest.mark.asyncio
@pytest.mark.parametrize("revisions", [0, 1])
async def test_writer_repairs_named_content_and_delivery_blocks_an_unresolved_gap(
    settings, revisions
):
    from deep_research.agents.base import Blackboard, RunContext
    from deep_research.models import ResearchResult
    from deep_research.observability import Tracer
    from deep_research.orchestrator import create_initial_execution
    from deep_research.persistence.repository import RunDetail
    from deep_research.workbench.contract import CONTRACT_SCRATCH_KEY
    from deep_research.workbench.coverage_review import CoverageDecisions
    from deep_research.workbench.publish import build_bundle
    from deep_research.workbench.writers import ResearchWriter
    from tests.fakes import FakeLLM, FakeSearch, verified_finding

    query = "请说明可借鉴的设计。"
    body = (
        "## 摘要\n\n研究关注变量关系。\n\n## 分析\n\n"
        + ("观测数据支持相关关系，解释时应保留研究条件与适用范围。" * 28)
        + " [1]。\n\n## 结论\n\n目前结论只涉及相关关系 [1]。"
    )

    class WriterLLM(FakeLLM):
        def __init__(self):
            super().__init__()
            self.prompts = []
            self.coverage = CoverageJudge()

        async def parse(self, system, user, schema, **kwargs):
            if schema is CoverageDecisions:
                return await self.coverage.parse(system, user, schema, **kwargs)
            return await super().parse(system, user, schema, **kwargs)

        async def stream(self, system, user, **kwargs):
            self.prompts.append(user)
            yield body + (
                "\n\n可借鉴的设计是将观测结果与因果解释分开，以保留适用边界 [1]。"
                if len(self.prompts) > 1
                else ""
            )

    settings.quality = {
        "research_min_citations": 1,
        "max_revisions": revisions,
        "register_check": False,
    }
    contract = build_contract(get_template("autoResearch"), query, quality=settings.quality)
    bb = Blackboard(
        query=query,
        results=[ResearchResult(sub_question="q", findings=[verified_finding()])],
        scratch={CONTRACT_SCRATCH_KEY: contract.model_dump(mode="json")},
    )
    llm = WriterLLM()
    ctx = RunContext(llm=llm, search_tool=FakeSearch(), tracer=Tracer(), settings=settings)
    bb = await ResearchWriter().step(bb, ctx)
    assert len(llm.prompts) == revisions + 1
    if revisions:
        assert "可借鉴的设计" in llm.prompts[1]
    execution = create_initial_execution(query, "research_quick", settings)
    execution.checkpoint = bb.model_dump(mode="json")
    detail = RunDetail(
        id="required-content",
        query=query,
        status="done",
        results=bb.results,
        report=bb.report,
        orchestration=execution,
    )
    bundle = build_bundle(detail)
    assert next(g for g in bundle.gates if g.name == "prose_evidence").status == "pass"
    gate = next(g for g in bundle.gates if g.name == "user_requirements")
    assert (gate.status == "pass") == bool(revisions)
    if not revisions:
        assert {file.format for file in bundle.files} == {"md"}


@pytest.mark.asyncio
async def test_legacy_contract_freezes_and_malformed_requirements_fail(
    settings,
):
    from deep_research.agents.base import Blackboard, RunContext
    from deep_research.observability import Tracer
    from deep_research.workbench.contract import CONTRACT_SCRATCH_KEY, contract_from_scratch
    from deep_research.workflow import Step, Workflow, WorkflowEngine
    from tests.fakes import FakeLLM, FakeSearch

    contract = build_contract(get_template("autoResearch"), "请说明实验条件。")
    old = contract.model_dump(mode="json")
    for key in (
        "requested_items",
        "requirements_version",
        "requested_input_hash",
        "request_instructions",
    ):
        old.pop(key)

    class Observe:
        async def step(self, bb, ctx):
            current = contract_from_scratch(bb.scratch)
            assert current.requirements_version == 1 and current.requested_items
            return bb

    ctx = RunContext(llm=FakeLLM(), search_tool=FakeSearch(), tracer=Tracer(), settings=settings)
    workflow = Workflow(name="requirements", steps=[Step(agent="observe")])
    engine = WorkflowEngine(ctx, resolver=lambda _: Observe())
    await engine.run(
        workflow, Blackboard(query=contract.original_request, scratch={CONTRACT_SCRATCH_KEY: old})
    )
    assert (
        engine.runtime.run.checkpoint["scratch"][CONTRACT_SCRATCH_KEY]["requirements_version"] == 1
    )
    broken = contract.model_dump(mode="json")
    broken["requested_items"] = [{"kind": "content"}]
    with pytest.raises(ValueError, match="契约无法解析"):
        await WorkflowEngine(ctx, resolver=lambda _: Observe()).run(
            workflow, Blackboard(query="q", scratch={CONTRACT_SCRATCH_KEY: broken})
        )


@pytest.mark.asyncio
async def test_table_field_with_an_empty_required_cell_is_not_complete():
    from deep_research.workbench.coverage_review import CoverageReviewer

    contract = build_contract(get_template("litReview"), "对比表列出核心机制。")
    body = "| 方法 | 核心机制 |\n|---|---|\n| A | 光谱注意力 |\n| B | — |"
    record = await CoverageReviewer(CoverageJudge(), contract, 50000).review(body, [])
    assert record["status"] == "fail" and "未填" in " ".join(record["issues"])


@pytest.mark.asyncio
async def test_late_named_content_in_a_long_report_is_not_dropped():
    from deep_research.workbench.coverage_review import CoverageReviewer, coverage_issues

    contract = build_contract(get_template("paperRead"), "请说明可借鉴的设计。")
    body = "\n\n".join("这是已知背景材料，用来说明研究范围。" * 30 for _ in range(30))
    body += "\n\n可借鉴的设计是模块化地分离数据与先验。"
    judge = CoverageJudge()
    record = await CoverageReviewer(judge, contract, 7000).review(body, [])
    assert record["status"] == "pass" and len(judge.calls) > 1
    assert not coverage_issues(contract, body, record, [])


@pytest.mark.asyncio
async def test_api_freezes_requested_items_before_queue_execution(service):
    client, repo, settings = service
    response = await client.post(
        "/api/runs",
        json={
            "query": "请说明实验条件，并提炼可借鉴的设计。",
            "template": "autoResearch",
            "strategy": "quick",
            "clarified": True,
        },
    )
    assert response.status_code == 202, response.text
    detail = await repo.get_run(response.json()["run_id"])
    frozen = detail.orchestration.checkpoint["scratch"]["task_contract"]
    assert frozen["requirements_version"] == 1
    assert {item["label"] for item in frozen["requested_items"] if item["kind"] != "overall"} == {
        "实验条件",
        "可借鉴的设计",
    }
    before = frozen["requested_input_hash"]
    settings.quality = {"max_revisions": 3}
    assert (await repo.get_run(detail.id)).orchestration.checkpoint["scratch"]["task_contract"][
        "requested_input_hash"
    ] == before
