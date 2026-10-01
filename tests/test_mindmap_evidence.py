from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from xml.etree import ElementTree

from deep_research.agents.base import Blackboard, RunContext
from deep_research.models import Report, ResearchResult
from deep_research.observability import Tracer
from deep_research.persistence.repository import RunDetail
from deep_research.workbench.contract import CONTRACT_SCRATCH_KEY, build_contract
from deep_research.workbench.delivery.mindmap import (
    _bounds,
    _layout,
    render_mindmap_html,
    render_svg,
)
from deep_research.workbench.mindmap_contract import Mindmap, review_record, units
from deep_research.workbench.publish import build_bundle
from deep_research.workbench.support import (
    SupportDecisions,
    SupportReviewer,
    SupportUnit,
    evidence_records,
)
from deep_research.workbench.templates import get_template
from deep_research.workbench.writers import MindmapWriter, mindmap_to_markdown
from tests.fakes import FakeLLM, FakeSearch, verified_finding


def evidence():
    return [
        ResearchResult(
            sub_question="方法比较",
            findings=[
                verified_finding("两变量相关", evidence_quote="两变量存在相关，未证明因果关系。"),
                verified_finding(
                    "算法采用既有滤波器",
                    source_url="https://b.com",
                    evidence_quote="使用了已有滤波器，未提出新滤波器。",
                ),
            ],
        )
    ]


async def test_reviewer_batches_nodes_caches_unchanged_units_and_requires_every_decision():
    class Judgements(FakeLLM):
        calls = []

        async def parse(self, system, user, schema, **kwargs):
            data = json.loads(user)
            self.calls.append(data)
            # Omit the last unit to model a truncated/incomplete semantic response.
            return SupportDecisions(
                decisions=[
                    {
                        "unit_id": u["id"],
                        "verdict": "non_factual",
                        "reason": "组织概念",
                    }
                    for u in data["units"][:-1]
                ]
            )

    llm = Judgements()
    reviewer = SupportReviewer(llm, [], 50000)
    nodes = [SupportUnit(str(i), f"概念 {i}", kind="concept") for i in range(20)]
    decisions = await reviewer.review(nodes)
    assert len(llm.calls) == 2 and len(decisions) == 20
    assert decisions[15].verdict == decisions[-1].verdict == "uncertain"
    assert all(d.verdict == "non_factual" for d in decisions[:15])
    await reviewer.review(nodes)
    assert len(llm.calls) == 3
    assert [u["id"] for u in llm.calls[-1]["units"]] == ["15", "19"]
    nodes[0].text = "新概念"
    await reviewer.review(nodes)
    assert len(llm.calls) == 4


async def test_evidence_must_belong_to_the_specific_node_not_another_node_in_batch():
    class WrongSource(FakeLLM):
        async def parse(self, system, user, schema, **kwargs):
            other_id = evidence_records(evidence(), {"https://a.com": 1, "https://b.com": 2})[1][
                "id"
            ]
            return SupportDecisions(
                decisions=[
                    {
                        "unit_id": str(i),
                        "verdict": "supported",
                        "evidence_ids": [other_id],
                        "reason": "支持",
                    }
                    for i in range(2)
                ]
            )

    reviewer = SupportReviewer(
        WrongSource(), evidence_records(evidence(), {"https://a.com": 1, "https://b.com": 2}), 50000
    )
    decisions = await reviewer.review(
        [
            SupportUnit("0", "两变量相关", citations=[1]),
            SupportUnit("1", "使用既有算法", citations=[2]),
        ]
    )
    assert decisions[0].verdict == "uncertain" and decisions[1].verdict == "supported"


async def test_missing_evidence_and_model_failure_never_count_as_pass():
    class Failed(FakeLLM):
        async def parse(self, *args, **kwargs):
            raise TimeoutError("private provider payload")

    decisions = await SupportReviewer(Failed(), [], 50000).review(
        [
            SupportUnit("0", "无引文事实"),
            SupportUnit("1", "概念", kind="concept"),
        ]
    )
    assert [d.verdict for d in decisions] == ["unsupported", "uncertain"]
    assert "private provider payload" not in str(decisions)


async def test_verifier_outage_does_not_trigger_rewriting_and_charge_again(settings):
    class Writer(FakeLLM):
        writes = 0

        async def parse(self, system, user, schema, **kwargs):
            if schema is Mindmap:
                self.writes += 1
                return Mindmap(root="主题", branches=[{"label": "相关分析"}])
            raise TimeoutError("provider offline")

    llm = Writer()
    ctx = RunContext(llm=llm, search_tool=FakeSearch(), tracer=Tracer(), settings=settings)
    bb = await MindmapWriter().step(Blackboard(query="相关分析"), ctx)
    extras = bb.scratch["workbench"]["extras"]
    assert llm.writes == 1
    assert extras["node_review"]["status"] == "fail"
    assert extras["revision"]["attempts"] == 1


async def test_small_relevant_mindmap_does_not_need_six_branches(settings):
    class Small(FakeLLM):
        async def parse(self, system, user, schema, **kwargs):
            if schema is Mindmap:
                return Mindmap(
                    root="统计方法", branches=[{"label": "相关分析"}, {"label": "差异检验"}]
                )
            return await super().parse(system, user, schema, **kwargs)

    ctx = RunContext(llm=Small(), search_tool=FakeSearch(), tracer=Tracer(), settings=settings)
    bb = await MindmapWriter().step(Blackboard(query="仅梳理相关与差异这两个概念"), ctx)
    extras = bb.scratch["workbench"]["extras"]
    assert extras["stats"]["branches"] == 2
    assert extras["revision"]["attempts"] == 1 and extras["node_review"]["status"] == "pass"


def test_deep_wide_and_long_labels_are_complete_and_boxes_do_not_overlap():
    deep = {"label": "第五层完整文字-" + "长中文与English" * 10}
    for i in range(4):
        deep = {"label": f"深度{i}", "children": [deep]}
    tree = {
        "root": "完整导图",
        "branches": [
            {
                "label": "一级",
                "children": [
                    {"label": "二级", "children": [{"label": f"孙节点{i}"} for i in range(7)]},
                    deep,
                ],
            }
        ],
    }
    nodes, _ = _layout(tree)
    assert len(nodes) == 15
    assert max(n["depth"] for n in nodes) == 6
    root = ElementTree.fromstring(render_svg(tree))
    text = "".join(root.itertext())
    assert "孙节点6" in text and "第五层完整文字-" + "长中文与English" * 10 in text
    x0, y0, x1, y1 = _bounds(nodes)
    for i, a in enumerate(nodes):
        assert a["x"] - a["width"] / 2 >= x0 and a["x"] + a["width"] / 2 <= x1
        assert a["y"] - a["height"] / 2 >= y0 and a["y"] + a["height"] / 2 <= y1
        for b in nodes[i + 1 :]:
            assert (
                abs(a["x"] - b["x"]) >= (a["width"] + b["width"]) / 2
                or abs(a["y"] - b["y"]) >= (a["height"] + b["height"]) / 2
            )


def test_html_contains_full_claim_citations_relations_and_safe_sources():
    html = render_mindmap_html(
        {
            "root": "主题",
            "branches": [
                {
                    "label": "两变量相关",
                    "kind": "claim",
                    "relation": "观察结果",
                    "citations": [1],
                }
            ],
            "sources": [
                {"index": 1, "url": "https://a.com", "title": "真实来源"},
                {"index": 2, "url": "javascript:alert(1)", "title": "<img>"},
            ],
        },
        title="导图",
    )
    assert "【结论】" in html and "观察结果" in html and "[1]" in html
    assert 'href="https://a.com"' in html and 'href="javascript:' not in html
    assert 'href="#source-1"' in html
    assert "&lt;img&gt;" in html


async def test_delivery_rejects_stale_or_failed_node_review(settings):
    from deep_research.orchestrator import create_initial_execution

    template = get_template("mindmap")
    execution = create_initial_execution("主题", template.workflow, settings)
    execution.checkpoint["scratch"][CONTRACT_SCRATCH_KEY] = build_contract(
        template, "主题"
    ).model_dump(mode="json")
    model = Mindmap(
        root="主题", branches=[{"label": "变量相关", "kind": "claim", "citations": [1]}]
    )
    raw = model.model_dump(mode="json")
    findings, citations = evidence(), ["https://a.com", "https://b.com"]
    decisions = await SupportReviewer(
        FakeLLM(), evidence_records(findings, dict(zip(citations, [1, 2], strict=True))), 50000
    ).review(units(model))
    extras = {
        "mindmap": raw,
        "node_review": review_record(raw, citations, findings, decisions),
        "stats": {"branches": 1},
    }
    execution.checkpoint["scratch"]["workbench"] = {"template": "mindmap", "extras": extras}
    detail = RunDetail(
        id="map-test",
        query="主题",
        status="done",
        created_at=datetime.now(UTC),
        orchestration=execution,
        results=findings,
        report=Report(query="主题", markdown=mindmap_to_markdown(model), citations=citations),
    )
    saved = build_bundle(detail)
    assert any(f.name.endswith("-mindmap.html") for f in saved.files)
    html = next(f.data.decode() for f in saved.files if f.format == "html")
    assert "两变量存在相关，未证明因果关系。" in html
    assert "<blockquote>" in html and 'href="#source-1"' in html
    changed = deepcopy(detail)
    changed.results[0].findings[0].evidence_quote = "原证据已经变更"
    rejected = build_bundle(changed)
    assert len(rejected.files) == 1 and rejected.files[0].status == "fail"
    assert any(g.name == "node_evidence" and g.status == "fail" for g in rejected.gates)
