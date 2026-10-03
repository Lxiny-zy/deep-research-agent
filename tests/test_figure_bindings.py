from __future__ import annotations

import json
from copy import deepcopy

from deep_research.workbench.figure_review import (
    check_figure,
    figure_signature,
    figure_units,
    review_figure,
)
from deep_research.workbench.figures import ConceptFigure
from deep_research.workbench.support import SupportDecisions, SupportReviewer, digest
from tests.fakes import FakeLLM


def test_figure_display_projects_citations_without_changing_checked_content():
    from deep_research.bibliography import Bibliography, ReferenceLocation
    from deep_research.workbench.figures import present_figure

    graph = diagram()
    graph.title = "流程 [29]"
    graph.caption = "输入 [1][29]；范围 [0,1]；公式 $x[29]$。"
    graph.nodes[0].label, graph.nodes[0].group = "输入 [29]", "分组 [29]"
    graph.edges[0].label = "投影 [29]"
    original = graph.model_dump(mode="json")
    catalog = Bibliography(
        locations=[
            ReferenceLocation(index=1, document=1, url="a"),
            ReferenceLocation(index=29, document=2, url="b"),
        ]
    )
    shown = present_figure(graph, catalog)
    assert shown.title == "流程 [2]"
    assert shown.caption == "输入 [1], [2]；范围 [0,1]；公式 $x[29]$。"
    assert shown.nodes[0].label == "输入 [2]" and shown.nodes[0].group == "分组 [2]"
    assert shown.edges[0].label == "投影 [2]"
    assert graph.model_dump(mode="json") == original
    assert shown.nodes[0].citations == graph.nodes[0].citations


def diagram():
    return ConceptFigure(
        title="流程",
        evidence_mode="scoped",
        nodes=[
            {"id": "a", "label": "输入", "citations": [1]},
            {"id": "b", "label": "投影", "citations": [1]},
        ],
        edges=[{"source": "a", "target": "b", "label": "输入", "citations": [1]}],
    )


def evidence():
    return [
        {
            "id": f"e{i}",
            "citation": i,
            "statement": f"记录{i}",
            "quote": f"SOURCE-{i}: " + "原文" * 600,
            "source": f"https://paper/{i}",
        }
        for i in range(1, 71)
    ]


class Judge(FakeLLM):
    def __init__(self):
        super().__init__()
        self.requests = []

    async def parse(self, system, user, schema, **kwargs):
        data = json.loads(user)
        self.requests.append(data)
        return SupportDecisions(
            decisions=[
                {
                    "unit_id": u["id"],
                    "verdict": "supported" if u["citations"] else "non_factual",
                    "evidence_ids": [
                        e["id"] for e in data["evidence"] if e["citation"] in u["citations"]
                    ],
                    "reason": "fixture",
                }
                for u in data["units"]
            ]
        )


async def test_claim_mislabelled_as_nonfactual_stays_rejected_but_can_be_rewritten():
    class TopicJudge(Judge):
        async def parse(self, system, user, schema, **kwargs):
            response = await super().parse(system, user, schema, **kwargs)
            for decision in response.decisions:
                if decision.unit_id == "figure-node-a":
                    decision.verdict = "non_factual"
                    decision.reason = "仅为主题标签"
                    decision.evidence_ids = []
            return response

    llm, graph = TopicJudge(), diagram()
    graph.nodes[0].kind = "claim"
    reviewed = await review_figure(graph, SupportReviewer(llm, evidence(), 50000))
    assert reviewed["status"] == "fail" and reviewed["can_revise"]
    assert check_figure(graph, evidence(), reviewed)
    assert len(llm.requests) == 1  # Repeating the same classification cannot repair the text.


async def test_only_explicit_figure_sources_are_sent_and_stored_decisions_cannot_borrow_others():
    llm = Judge()
    graph, records = diagram(), evidence()
    checked = await review_figure(graph, SupportReviewer(llm, records, 10000))
    assert checked["status"] == "pass" and checked["version"] == 2
    assert len(llm.requests) == 1
    assert [e["citation"] for e in llm.requests[0]["evidence"]] == [1]
    assert not check_figure(graph, records, checked)
    forged = deepcopy(checked)
    forged["decisions"][-1]["evidence_ids"] = ["e2"]
    assert check_figure(graph, records, forged)


async def test_edge_does_not_automatically_inherit_evidence_from_its_endpoints():
    graph = diagram()
    graph.edges[0].citations = []
    record = await review_figure(graph, SupportReviewer(Judge(), evidence(), 10000))
    assert record["status"] == "fail"
    assert any("没有可用的引用证据" in issue for issue in record["issues"])


async def test_unknown_reference_and_binding_downgrade_cannot_reuse_a_good_record():
    graph, records = diagram(), evidence()
    checked = await review_figure(graph, SupportReviewer(Judge(), records, 10000))
    unknown = graph.model_copy(deep=True)
    unknown.nodes[0].citations = [999]
    assert check_figure(unknown, records, checked)
    downgraded = graph.model_copy(deep=True)
    downgraded.evidence_mode = "legacy"
    for item in [*downgraded.nodes, *downgraded.edges]:
        item.citations = []
    assert check_figure(downgraded, records, checked)
    # Even a matching signature and forged non-factual decision cannot excuse an unknown ID.
    unknown_record = await review_figure(unknown, SupportReviewer(Judge(), records, 10000))
    unknown_record.update(status="pass", issues=[])
    for decision in unknown_record["decisions"]:
        if decision["verdict"] == "unsupported":
            decision.update(verdict="non_factual", evidence_ids=[])
    assert check_figure(unknown, records, unknown_record)


async def test_edge_reordering_keeps_unchanged_semantic_checks_cached():
    graph = diagram()
    graph.nodes.append(
        graph.nodes[1].model_copy(update={"id": "c", "label": "去噪", "citations": [2]})
    )
    graph.edges.append(graph.edges[0].model_copy(update={"target": "c", "citations": [2]}))
    llm = Judge()
    reviewer = SupportReviewer(llm, evidence(), 50000)
    await review_figure(graph, reviewer)
    ids = {u.id for u in figure_units(graph, []) if u.id.startswith("figure-edge-")}
    graph.edges.reverse()
    assert {u.id for u in figure_units(graph, []) if u.id.startswith("figure-edge-")} == ids
    llm.requests.clear()
    result = await review_figure(graph, reviewer)
    assert result["status"] == "pass"
    assert [u["id"] for request in llm.requests for u in request["units"]] == ["figure-caption"]


def test_legacy_signature_matches_the_original_payload_without_new_defaults():
    raw = {
        "title": "旧图",
        "caption": "",
        "layout": "flow",
        "nodes": [
            {"id": "a", "label": "输入", "group": ""},
            {"id": "b", "label": "输出", "group": ""},
        ],
        "edges": [{"source": "a", "target": "b", "label": "过程"}],
    }
    from deep_research.workbench.support import SUPPORT_POLICY_VERSION

    records = evidence()[:1]
    assert figure_signature(ConceptFigure.model_validate(raw), records) == digest(
        {
            "version": 1,
            "policy": SUPPORT_POLICY_VERSION,
            "figure": raw,
            "evidence": records,
        }
    )


async def test_group_claim_is_visible_and_unreferenced_evidence_changes_do_not_invalidate_review():
    graph, records = diagram(), evidence()
    graph.nodes[0].group = "受控实验"
    assert "所属分组：受控实验" in figure_units(graph, [1])[1].text
    checked = await review_figure(graph, SupportReviewer(Judge(), records, 10000))
    records[-1]["quote"] = "An unrelated source changed."
    assert not check_figure(graph, records, checked)
    graph.nodes[0].group = "所有场景成立"
    assert check_figure(graph, records, checked)
