"""Relations express checked directions, not topic labels or untracked cross-links."""

from copy import deepcopy
from xml.etree import ElementTree

import pytest

from deep_research.workbench.mindmap_contract import (
    Mindmap,
    checked_review,
    review_record,
    structural_issues,
    units,
)
from deep_research.workbench.support import SupportReviewer, evidence_records
from deep_research.workbench.writers import mindmap_to_markdown
from tests.fakes import FakeLLM, verified_finding


def graph(**link):
    return Mindmap.model_validate(
        {
            "root": "两种方法的关系",
            "branches": [{"label": "基础方法"}, {"label": "改进方法"}],
            "links": [{"source": "1", "target": "0", "relation": "改进", "citations": [1], **link}],
        }
    )


@pytest.mark.parametrize("relation", ["数据来源", "配置", "内部结构", "实验设置", "观察结果"])
def test_sc56_topic_labels_are_rejected_as_relations(relation):
    model = Mindmap(root="方法", branches=[{"label": "模型结构", "relation": relation}])
    assert any("关系词表" in issue for issue in structural_issues(model, 0))


def test_relation_vocabulary_is_exposed_to_structured_generation():
    schema = Mindmap.model_json_schema()
    vocabulary = schema["$defs"]["MindmapNode"]["properties"]["relation"].get("enum", [])
    assert set(vocabulary) == {"包含", "导致", "依赖", "对比", "改进", "前提", "应用于"}


def test_factual_hierarchical_relation_cannot_be_exempted_as_a_concept():
    model = Mindmap(root="输入误差", branches=[{"label": "输出偏差", "relation": "导致"}])
    relation = next((u for u in units(model) if u.id == "relation:0"), None)
    assert relation is not None and relation.kind == "claim"
    assert "输入误差" in relation.text and "导致" in relation.text and "输出偏差" in relation.text
    assert any("关系" in issue and "引用" in issue for issue in structural_issues(model, 0))


@pytest.mark.parametrize(
    "link",
    [
        {"source": "99"},
        {"target": "1"},
        {"relation": "性能结果"},
        {"citations": []},
        {"citations": [2]},
    ],
)
def test_cross_links_require_existing_distinct_nodes_typed_relations_and_evidence(link):
    assert structural_issues(graph(**link), 1)


def test_cross_links_are_limited_and_cannot_repeat_an_edge():
    model = graph()
    raw = model.model_dump(mode="json")
    assert raw.get("links")
    raw["links"] *= 9
    assert any("跨分支" in issue for issue in structural_issues(Mindmap.model_validate(raw), 1))


async def checked_graph():
    from deep_research.models import ResearchResult

    model = graph()
    results = [
        ResearchResult(
            sub_question="方法比较",
            findings=[
                verified_finding(
                    "改进方法改进基础方法",
                    evidence_quote="The revised method improves the baseline.",
                ),
            ],
        )
    ]
    citations = ["https://a.com"]
    checker = SupportReviewer(FakeLLM(), evidence_records(results, {citations[0]: 1}), 50000)
    decisions = await checker.review(units(model))
    record = review_record(model.model_dump(), citations, results, decisions)
    return model, results, citations, record


async def test_cross_link_is_a_separately_reviewed_claim_and_binds_direction():
    model, results, citations, record = await checked_graph()
    assert any(
        d["unit_id"] == "link:0" and d["verdict"] == "supported" for d in record["decisions"]
    )
    assert checked_review(
        model.model_dump(), citations, results, record, mindmap_to_markdown(model)
    ) == (True, [])
    raw = model.model_dump()
    raw["links"][0].update(source="0", target="1")
    assert checked_review(
        raw, citations, results, record, mindmap_to_markdown(Mindmap.model_validate(raw))
    )[1]
    incomplete = deepcopy(record)
    incomplete["decisions"] = [d for d in incomplete["decisions"] if d["unit_id"] != "link:0"]
    assert checked_review(
        model.model_dump(), citations, results, incomplete, mindmap_to_markdown(model)
    )[1]


def test_cross_link_is_visible_in_markdown_svg_and_html_with_evidence():
    from deep_research.workbench.delivery.mindmap import render_mindmap_html, render_svg

    model = graph()
    body = mindmap_to_markdown(model)
    assert "跨分支关联" in body and "改进方法 —改进→ 基础方法 [1]" in body
    raw = model.model_dump()
    svg = ElementTree.fromstring(render_svg(raw))
    edges = [e for e in svg.iter() if e.get("class") == "cross-edge"]
    assert len(edges) == 1 and edges[0].get("data-source") == "1"
    assert edges[0].get("data-target") == "0"
    html = render_mindmap_html(
        {**raw, "sources": [{"index": 1, "url": "https://a.com"}]}, title="方法"
    )
    assert "跨分支关联" in html and 'href="#source-1"' in html


def test_claim_proportion_is_measured_as_advice_without_blocking_content():
    from deep_research.workbench.gate_classification import classify_gates
    from deep_research.workbench.gates import mindmap_composition_gate

    model = Mindmap(
        root="方法",
        branches=[
            {
                "label": f"主题{i}",
                "children": [
                    {"label": f"结果{i}-{j}", "kind": "claim", "citations": [1]} for j in range(4)
                ],
            }
            for i in range(4)
        ],
    )
    gate = mindmap_composition_gate(model)
    assert gate.metrics["claims"] == 16 and gate.metrics["nodes"] == 20
    assert gate.metrics["claim_ratio"] == 0.8 and gate.issues
    classify_gates([gate])
    assert gate.advisories and not gate.blocking_issues


async def test_endpoint_concepts_cannot_exempt_the_factual_relation():
    from deep_research.workbench.support import SupportDecision, SupportDecisions

    class Exempt(FakeLLM):
        async def parse(self, system, user, schema, **kwargs):
            import json

            return SupportDecisions(
                decisions=[
                    SupportDecision(unit_id=u["id"], verdict="non_factual", reason="concept")
                    for u in json.loads(user)["units"]
                ]
            )

    model = graph()
    decisions = await SupportReviewer(Exempt(), [], 50000).review(units(model))
    relation = next(d for d in decisions if d.unit_id == "link:0")
    assert relation.verdict in {"unsupported", "uncertain"}


async def test_writer_freezes_cross_links_and_retries_png_without_rejudging(
    settings,
    tmp_path,
    monkeypatch,
):
    from datetime import UTC, datetime

    from deep_research.agents.base import Blackboard, RunContext
    from deep_research.observability import Tracer
    from deep_research.orchestrator import create_initial_execution
    from deep_research.persistence.repository import RunDetail
    from deep_research.workbench.delivery import mindmap as renderer
    from deep_research.workbench.delivery_store import build_or_load, load_version, retry_format
    from deep_research.workbench.publish import build_bundle
    from deep_research.workbench.templates import get_template
    from deep_research.workbench.writers import MindmapWriter
    from tests.fakes import FakeSearch

    model, results, _, _ = await checked_graph()

    class Writer(FakeLLM):
        async def parse(self, system, user, schema, **kwargs):
            if schema is Mindmap:
                return model.model_copy(deep=True)
            return await super().parse(system, user, schema, **kwargs)

    llm = Writer()
    settings.quality = {"max_revisions": 0, "register_check": False, "require_limitations": False}
    bb = Blackboard(query="比较两种方法", results=results)
    await MindmapWriter().step(
        bb,
        RunContext(llm=llm, search_tool=FakeSearch(), tracer=Tracer(), settings=settings),
    )
    assert bb.scratch["workbench"]["extras"]["node_review"]["status"] == "pass"
    template = get_template("mindmap")
    execution = create_initial_execution(bb.query, template.workflow, settings)
    execution.checkpoint["scratch"] = bb.scratch.copy()
    detail = RunDetail(
        id="relation-map",
        query=bb.query,
        status="done",
        created_at=datetime.now(UTC),
        report=bb.report,
        results=bb.results,
        orchestration=execution,
    )
    calls = llm.parse_calls

    def failure(*args, **kwargs):
        raise ValueError("temporary renderer failure")

    with monkeypatch.context() as patch:
        patch.setattr(renderer, "render_mindmap_png", failure)
        first = build_or_load(detail, str(tmp_path), None, build_bundle)
    assert next(g for g in first.gates if g.name == "node_evidence").status == "pass"
    assert first.render_context["extras"]["mindmap"]["links"] == model.model_dump()["links"]
    second = retry_format(
        detail, str(tmp_path), None, first.content_version, "png", "relation-retry"
    )
    assert llm.parse_calls == calls
    assert any(f.format == "png" for f in second.files) and not second.failures
    assert load_version(detail, str(tmp_path), first.content_version).registry() == first.registry()
    html = next(f.data for f in first.files if f.format == "html")
    assert next(f.data for f in second.files if f.format == "html") == html
    assert b"cross-edge" in html


def test_legacy_map_without_review_cannot_export_unknown_topic_relations(settings):
    from datetime import UTC, datetime

    from deep_research.models import Report
    from deep_research.orchestrator import create_initial_execution
    from deep_research.persistence.repository import RunDetail
    from deep_research.workbench.publish import build_bundle
    from deep_research.workbench.templates import get_template

    model = Mindmap(root="方法", branches=[{"label": "输入数据", "relation": "数据来源"}])
    template = get_template("mindmap")
    execution = create_initial_execution("方法", template.workflow, settings)
    execution.checkpoint["scratch"]["workbench"] = {
        "template": "mindmap", "extras": {"mindmap": model.model_dump()},
    }
    detail = RunDetail(
        id="legacy-relation", query="方法", status="done", created_at=datetime.now(UTC),
        report=Report(query="方法", markdown=mindmap_to_markdown(model)), orchestration=execution,
    )
    bundle = build_bundle(detail)
    assert bundle.render_context["blocked"]
    assert {f.format for f in bundle.files} == {"md"}
