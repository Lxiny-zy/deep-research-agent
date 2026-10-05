from __future__ import annotations

import json
from copy import deepcopy

import pytest
from pydantic import ValidationError

from deep_research.agents.base import Blackboard, RunContext
from deep_research.models import ResearchResult
from deep_research.observability import Tracer
from deep_research.workbench.mindmap_contract import Mindmap, checked_review, review_record
from deep_research.workbench.mindmap_edit import NodeEdits, prime_review, repair_nodes, review_units
from deep_research.workbench.support import SupportDecisions, SupportReviewer, evidence_records
from deep_research.workbench.writers import MindmapWriter, mindmap_to_markdown
from tests.fakes import FakeLLM, FakeSearch, verified_finding


def material():
    return [
        ResearchResult(
            sub_question="方法",
            findings=[
                verified_finding("A 使用光谱注意力", "https://a.com", "A uses spectral attention."),
                verified_finding("B 使用光谱注意力", "https://b.com", "B uses spectral attention."),
            ],
        )
    ]


def graph():
    return Mindmap(
        root="两种方法",
        branches=[
            {
                "label": "共同方法",
                "children": [
                    {"label": "A 使用光谱注意力", "kind": "claim", "citations": [1]},
                    {"label": "B 使用光谱注意力", "kind": "claim", "citations": [2]},
                ],
            },
            {
                "label": "待研究问题",
                "children": [{"label": "适用范围是否可扩展？", "kind": "question"}],
            },
        ],
    )


class Judge(FakeLLM):
    def __init__(self):
        super().__init__()
        self.edited = []
        self.judged = []
        self.writes = 0
        self.mode = "valid"

    async def parse(self, system, user, schema, **kwargs):
        if schema is Mindmap:
            self.writes += 1
            return graph()
        if schema is NodeEdits:
            payload = json.loads(user.split("【仅修订指定节点】\n", 1)[1])
            self.edited.extend(payload["nodes"])
            edits = []
            for item in payload["nodes"]:
                node = item["node"]
                edit = {
                    "unit_id": item["unit_id"],
                    "label": node["label"],
                    "kind": "claim",
                    "relation": node["relation"],
                    "citations": [1, 2],
                }
                if self.mode == "foreign":
                    edit["unit_id"] = "99"
                if self.mode == "children":
                    edit["children"] = []
                if self.mode == "citation":
                    edit["citations"] = [99]
                if self.mode == "unchanged":
                    edit.update({k: v for k, v in node.items() if k != "children"})
                edits.append(edit)
            return NodeEdits.model_validate({"edits": edits})
        if schema is SupportDecisions:
            payload = json.loads(user)
            self.judged.extend(u["id"] for u in payload["units"])
            decisions = []
            for unit in payload["units"]:
                missing = unit["text"] == "共同方法" and set(unit["citations"]) != {1, 2}
                decisions.append(
                    {
                        "unit_id": unit["id"],
                        "verdict": "unsupported"
                        if missing
                        else "supported"
                        if unit["citations"]
                        else "non_factual",
                        "evidence_ids": [
                            e["id"]
                            for e in payload["evidence"]
                            if e["citation"] in unit["citations"]
                        ],
                        "reason": "共同性需要两篇文献的证据" if missing else "fixture",
                    }
                )
            return SupportDecisions(decisions=decisions)
        return await super().parse(system, user, schema, **kwargs)


async def setup_review():
    llm, model, results = Judge(), graph(), material()
    citations = ["https://a.com", "https://b.com"]
    checker = SupportReviewer(
        llm, evidence_records(results, {u: i for i, u in enumerate(citations, 1)}), 50000
    )
    decisions = await checker.review(review_units(model, "比较两种方法"))
    record = review_record(model.model_dump(mode="json"), citations, results, decisions)
    return llm, model, results, citations, checker, record


async def test_repair_binds_parent_evidence_explicitly_and_keeps_all_children():
    llm, model, results, citations, checker, record = await setup_review()
    before = model.model_dump(mode="json")
    assert record["status"] == "fail" and model.branches[0].citations == []
    resumed = SupportReviewer(llm, checker.evidence, 50000)
    assert prime_review(resumed, model, "比较两种方法", citations, results, record)
    patched, paths = await repair_nodes(
        llm, resumed, model, "比较两种方法", citations, results, record
    )
    assert paths == ["0"] and model.model_dump(mode="json") == before
    assert patched.branches[0].children == model.branches[0].children
    assert patched.branches[1] == model.branches[1]
    assert patched.branches[0].citations == [1, 2] and patched.branches[0].kind == "claim"
    llm.judged.clear()
    decisions = await resumed.review(review_units(patched, "比较两种方法"))
    assert set(llm.judged) == {"root", "0"}
    audit = review_record(patched.model_dump(mode="json"), citations, results, decisions)
    assert audit["status"] == "pass"
    assert checked_review(
        patched.model_dump(mode="json"), citations, results, audit, mindmap_to_markdown(patched)
    ) == (True, [])


async def test_source_bound_open_question_is_rechecked_and_corrected_from_the_paper():
    from deep_research.workbench.fulltext_review import FullTextChecks, FullTextTarget
    from tests.test_fulltext_absence import FullTextJudge, make_corpus, research_material

    answer = "The model calculates self-attention along the spectral dimension."
    sources, corpus = make_corpus(first=answer, second="The detailed derivation is " + answer)
    results = research_material(sources)
    citations = [sources[0].url]
    model = Mindmap(root="光谱注意力方法", branches=[
        {"label": "模型采用什么维度的注意力？", "kind": "question", "citations": [1]},
        {"label": "后续应用方向", "children": [
            {"label": "可否拓展到更大规模？", "kind": "question"},
        ]},
    ])

    class Repair(FakeLLM):
        def __init__(self):
            super().__init__()
            self.fulltext = FullTextJudge(answer)

        async def parse(self, system, user, schema, **kwargs):
            if schema in {FullTextTarget, FullTextChecks}:
                return await self.fulltext.parse(system, user, schema, **kwargs)
            if schema is NodeEdits:
                payload = json.loads(user.split("【仅修订指定节点】\n", 1)[1])
                assert answer in payload["nodes"][0]["problem"]
                return NodeEdits.model_validate({"edits": [{
                    "unit_id": "0", "label": "模型沿光谱维度计算自注意力", "kind": "claim",
                    "relation": "包含", "citations": [1],
                }]})
            return await super().parse(system, user, schema, **kwargs)

    llm = Repair()
    checker = SupportReviewer(
        llm, evidence_records(results, {citations[0]: 1}), 50000, fulltext_corpus=corpus,
    )
    decisions = await checker.review(review_units(model, "解释模型的注意力机制"))
    record = review_record(model.model_dump(), citations, results, decisions)
    assert record["status"] == "fail" and llm.fulltext.parts
    patched, paths = await repair_nodes(
        llm, checker, model, "解释模型的注意力机制", citations, results, record,
    )
    assert paths == ["0"] and patched.branches[1] == model.branches[1]
    decisions = await checker.review(review_units(patched, "解释模型的注意力机制"))
    repaired = review_record(patched.model_dump(), citations, results, decisions)
    assert repaired["status"] == "pass"
    assert checked_review(
        patched.model_dump(), citations, results, repaired, mindmap_to_markdown(patched),
        corpus=corpus, query="解释模型的注意力机制",
    ) == (True, [])


async def test_cross_branch_duplicate_requests_whole_tree_revision():
    from tests.test_mindmap_duplicates import pair_graph

    llm = FakeLLM()
    model = pair_graph("模型利用光谱间相似性进行特征恢复", "模型利用光谱间相似性进行特征恢复")
    results, citations = material(), ["https://a.com", "https://b.com"]
    checker = SupportReviewer(
        llm, evidence_records(results, {u: i + 1 for i, u in enumerate(citations)}), 50000,
    )
    decisions = await checker.review(review_units(model, "分析方法"))
    record = review_record(model.model_dump(), citations, results, decisions)
    assert await repair_nodes(llm, checker, model, "分析方法", citations, results, record) is None


@pytest.mark.parametrize("mode", ["foreign", "children", "citation", "unchanged"])
async def test_bad_patch_does_not_change_the_original_map(mode):
    llm, model, results, citations, checker, record = await setup_review()
    before = deepcopy(model)
    llm.mode = mode
    with pytest.raises((ValueError, ValidationError)):
        await repair_nodes(llm, checker, model, "比较两种方法", citations, results, record)
    assert model == before


async def test_stale_or_incomplete_review_cannot_seed_cache_or_trigger_edits():
    llm, model, results, citations, checker, record = await setup_review()
    changed = deepcopy(results)
    changed[0].findings[0].evidence_quote = "Changed source"
    assert not prime_review(checker, model, "比较两种方法", citations, changed, record)
    assert (
        await repair_nodes(llm, checker, model, "比较两种方法", citations, changed, record) is None
    )
    broken = deepcopy(record)
    broken["decisions"].pop()
    assert not prime_review(checker, model, "比较两种方法", citations, results, broken)
    assert not llm.edited


async def test_writer_repairs_one_node_instead_of_regenerating_the_whole_tree(settings):
    llm = Judge()
    bb = Blackboard(query="比较两种方法", results=material())
    ctx = RunContext(llm=llm, search_tool=FakeSearch(), tracer=Tracer(), settings=settings)
    result = await MindmapWriter().step(bb, ctx)
    extras = result.scratch["workbench"]["extras"]
    assert llm.writes == 1 and len(llm.edited) == 1
    assert extras["node_repairs"] == [["0"]]
    assert extras["node_review"]["status"] == "pass" and not extras["revision"]["remaining"]
