from __future__ import annotations

import json

import pytest

from deep_research.workbench.figure_edit import FigureEdits, repair_figure
from deep_research.workbench.figure_review import check_figure, review_figure
from deep_research.workbench.support import SupportReviewer
from tests.test_figure_bindings import Judge, diagram, evidence


class RepairJudge(Judge):
    mode = "valid"

    async def parse(self, system, user, schema, **kwargs):
        if schema is FigureEdits:
            payload = json.loads(user.split("【指定修订】\n", 1)[1])
            uid = payload["edges"][0]["unit_id"]
            edge = {
                "unit_id": uid,
                "source": "a",
                "target": "b",
                "label": "输入",
                "kind": "claim",
                "citations": [1],
            }
            if self.mode == "unknown":
                edge["target"] = "new-node"
            if self.mode == "type_only":
                edge.update(source="b", target="a", kind="concept")
            if self.mode == "extra":
                edge["unit_id"] = "other-edge"
            return FigureEdits.model_validate({"edges": [edge]})
        value = await super().parse(system, user, schema, **kwargs)
        data = json.loads(user)
        invalid = {u["id"] for u in data["units"] if u["text"] == "投影 --输入--> 输入"}
        for decision in value.decisions:
            if decision.unit_id in invalid:
                decision.verdict = "unsupported"
                decision.reason = "关系方向相反"
        return value


async def case():
    graph = diagram()
    graph.edges[0].source, graph.edges[0].target = "b", "a"
    llm = RepairJudge()
    reviewer = SupportReviewer(llm, evidence(), 50000)
    record = await review_figure(graph, reviewer)
    assert record["status"] == "fail"
    return graph, llm, reviewer, record


async def test_only_rejected_edge_changes_and_other_node_reviews_are_reused():
    graph, llm, reviewer, record = await case()
    before = graph.model_dump(mode="json")
    updated = await repair_figure(llm, reviewer, graph, record)
    assert updated is not None and graph.model_dump(mode="json") == before
    assert updated.nodes == graph.nodes and updated.title == graph.title
    assert updated.edges[0].source == "a" and updated.edges[0].target == "b"
    llm.requests.clear()
    accepted = await review_figure(updated, reviewer)
    assert accepted["status"] == "pass" and not check_figure(updated, evidence(), accepted)
    checked = [u["id"] for r in llm.requests for u in r["units"]]
    assert len(checked) == 2 and "figure-caption" in checked
    assert not any(uid.startswith("figure-node") for uid in checked)


@pytest.mark.parametrize("mode", ["unknown", "type_only", "extra"])
async def test_invalid_patch_does_not_replace_the_existing_graph(mode):
    graph, llm, reviewer, record = await case()
    before = graph.model_dump(mode="json")
    llm.mode = mode
    with pytest.raises(ValueError):
        await repair_figure(llm, reviewer, graph, record)
    assert graph.model_dump(mode="json") == before


async def test_stale_review_is_not_used_to_select_repair_targets():
    graph, llm, reviewer, record = await case()
    graph.nodes[0].label = "另一种输入"
    assert await repair_figure(llm, reviewer, graph, record) is None


class CaptionJudge(Judge):
    mode = "valid"

    async def parse(self, system, user, schema, **kwargs):
        if schema is FigureEdits:
            payload = json.loads(user.split("【指定修订】\n", 1)[1])
            assert payload["caption_problem"] and not payload["nodes"] and not payload["edges"]
            caption = {"title": "流程", "caption": "输入投影 [1]。", "citations": [1]}
            if self.mode == "unknown":
                caption["citations"] = [999]
            if self.mode == "unchanged":
                caption["caption"] = "所有场景均成立 [1]。"
            value = {"caption": caption}
            if self.mode == "extra":
                value["nodes"] = [
                    {
                        "unit_id": "figure-node-a",
                        "label": "改写",
                        "kind": "concept",
                        "citations": [1],
                    }
                ]
            if self.mode == "missing":
                value = {}
            return FigureEdits.model_validate(value)
        result = await super().parse(system, user, schema, **kwargs)
        payload = json.loads(user)
        for unit, decision in zip(payload["units"], result.decisions, strict=True):
            if unit["id"] == "figure-caption" and "所有场景" in unit["text"]:
                decision.verdict = "unsupported"
                decision.reason = "限定场景不能外推为全部场景"
        return result


async def caption_case():
    graph = diagram()
    graph.caption, graph.citations = "所有场景均成立 [1]。", [1]
    llm = CaptionJudge()
    reviewer = SupportReviewer(llm, evidence(), 50000)
    record = await review_figure(graph, reviewer)
    assert record["status"] == "fail"
    return graph, llm, reviewer, record


async def test_caption_only_repair_preserves_graph_and_reuses_node_and_edge_checks():
    graph, llm, reviewer, record = await caption_case()
    before = graph.model_dump(mode="json")
    updated = await repair_figure(llm, reviewer, graph, record)
    assert updated is not None and graph.model_dump(mode="json") == before
    assert updated.nodes == graph.nodes and updated.edges == graph.edges
    llm.requests.clear()
    checked = await review_figure(updated, reviewer)
    assert checked["status"] == "pass" and not check_figure(updated, evidence(), checked)
    assert [u["id"] for r in llm.requests for u in r["units"]] == ["figure-caption"]


@pytest.mark.parametrize("mode", ["unknown", "unchanged", "extra", "missing"])
async def test_invalid_caption_edit_preserves_original(mode):
    graph, llm, reviewer, record = await caption_case()
    before = graph.model_dump(mode="json")
    llm.mode = mode
    with pytest.raises(ValueError):
        await repair_figure(llm, reviewer, graph, record)
    assert graph.model_dump(mode="json") == before
