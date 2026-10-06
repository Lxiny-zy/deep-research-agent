"""Short indexes retain complete, reviewed meaning in every usable mindmap view."""

import io
import json
from copy import deepcopy

import httpx
import pytest
from PIL import Image

from deep_research.workbench.delivery.mindmap import render_mindmap_html
from deep_research.workbench.mindmap_contract import (
    MINDMAP_REVIEW_VERSION,
    Mindmap,
    composition,
    input_hash,
    semantic_payload,
    structural_issues,
    units,
)
from deep_research.workbench.mindmap_delivery import (
    MindmapImageLimit,
    branch_markdown,
    branch_png_pages,
    delivery_index,
    graph_version,
    overview_pages,
)
from deep_research.workbench.mindmap_duplicates import duplicate_nodes
from deep_research.workbench.support import SUPPORT_POLICY_VERSION, SupportReviewer, digest
from deep_research.workbench.writers import mindmap_to_markdown
from tests.test_support_alignment import SelectedJudge, item
from tests.test_workbench import api_repo as api_repo


def graph():
    return {
        "schema_version": 2,
        "root": "重建方法与适用边界",
        "branches": [
            {
                "label": "方法结构",
                "details": "按已有证据组织方法。",
                "children": [
                    {
                        "label": "室内实验",
                        "details": "Alpha 在室内数据上的得分是 95；不推广为室外结果。",
                        "kind": "claim",
                        "citations": [1],
                    },
                    {
                        "label": "关键公式",
                        "details": r"重建关系为 $x=\frac{1}{2}y$，系数不能省略。",
                        "kind": "claim",
                        "citations": [1],
                    },
                ],
            },
            {
                "label": "比较边界",
                "details": "相同条件才比较。",
                "children": [
                    {
                        "label": "室外实验",
                        "details": "Beta 在室外数据上的得分是 90。",
                        "kind": "claim",
                        "citations": [2],
                    },
                ],
            },
        ],
        "links": [{"source": "0.0", "target": "1.0", "relation": "对比", "citations": [1, 2]}],
        "sources": [
            {"index": 1, "url": "https://example.org/alpha", "quotes": ["Alpha scored 95."]},
            {"index": 2, "url": "https://example.org/beta", "quotes": ["Beta scored 90."]},
        ],
    }


def test_details_are_present_in_scientific_units_markdown_and_fingerprint():
    model = Mindmap.model_validate(graph())
    checked = next(unit for unit in units(model) if unit.id == "0.0")
    assert "95" in checked.text and "不推广" in checked.text
    markdown = mindmap_to_markdown(model)
    assert "95" in markdown and r"\frac{1}{2}" in markdown
    assert "L1（0.0 → 1.0）" in markdown
    old = input_hash(model.model_dump(), ["url-1", "url-2"], [])
    model.branches[0].children[0].details = "Alpha 得分为 100。"
    assert input_hash(model.model_dump(), ["url-1", "url-2"], []) != old


def test_diagram_index_covers_every_node_and_displayed_citations_remain_versioned():
    raw = graph()
    index = delivery_index(raw)
    pictured = {
        path
        for branch in index["branches"]
        for group in branch["diagram_groups"]
        for path in [group["parent"], *group["children"]]
    }
    assert pictured == {node["path"] for node in index["nodes"]}
    version = graph_version(raw)
    raw["generated_index"] = index
    assert graph_version(raw) == version  # No recursive hash over generated metadata.
    raw["branches"][1]["children"][0]["display_citations"] = [1]
    assert graph_version(raw) != version
    assert "90。 [1]" in branch_markdown(raw, 1)
    raw_index = delivery_index(raw)
    node = next(item for item in raw_index["nodes"] if item["path"] == "1.0")
    assert node["citations"] == [2] and node["display_citations"] == [1]


async def test_numeric_claim_hidden_in_details_cannot_bypass_evidence_check():
    model = Mindmap(
        root="实验",
        branches=[
            {"label": "结果", "details": "Alpha scored 100 [1].", "kind": "claim", "citations": [1]}
        ],
    )
    reviewer = SupportReviewer(SelectedJudge(["e"]), [item("e", "Alpha scored 95")], 50000)
    decision = (await reviewer.review([unit for unit in units(model) if unit.id == "0"]))[0]
    assert decision.verdict == "unsupported"


def test_legacy_empty_details_preserve_original_review_identity():
    model = Mindmap(root="主题", branches=[{"label": "旧节点"}])
    legacy = {
        "root": "主题",
        "branches": [
            {
                "label": "旧节点",
                "kind": "concept",
                "relation": "包含",
                "citations": [],
                "children": [],
            }
        ],
        "links": [],
    }
    expected = digest(
        {
            "version": MINDMAP_REVIEW_VERSION,
            "policy": SUPPORT_POLICY_VERSION,
            "mindmap": legacy,
            "citations": [],
            "results": [],
        }
    )
    assert semantic_payload(model) == legacy
    assert input_hash(legacy, [], []) == expected
    assert mindmap_to_markdown(model) == "# 主题\n\n- 旧节点\n"


def test_soft_label_budget_does_not_delete_facts_or_merge_different_conditions():
    model = Mindmap(
        root="条件",
        branches=[
            {
                "label": "讨论" * 30,
                "children": [
                    {
                        "label": "结果",
                        "details": "Alpha training score 95",
                        "kind": "claim",
                        "citations": [1],
                    },
                    {
                        "label": "结果",
                        "details": "Alpha test score 90",
                        "kind": "claim",
                        "citations": [1],
                    },
                ],
            }
        ],
    )
    original = model.model_dump()
    metrics, advice = composition(model)
    assert metrics["long_labels"] == 1 and advice
    assert not structural_issues(model, 1) and not duplicate_nodes(model)
    assert model.model_dump() == original


def test_duplicate_candidates_are_inspectable_without_deleting_nodes():
    label = "需要比较不同研究方案的测量条件与适用边界"
    raw = {
        "root": "比较",
        "branches": [
            {"label": "方法", "children": [{"label": label}]},
            {"label": "实验", "children": [{"label": label}]},
        ],
    }
    before = deepcopy(raw)
    index = delivery_index(raw)
    assert index["duplicate_candidates"]
    html = render_mindmap_html(raw, title="待核对重复候选")
    assert 'href="#outline-0.0"' in html and 'href="#outline-1.0"' in html
    assert "保留原节点" in html and index["node_count"] == 4
    assert raw == before


def test_html_contains_complete_details_math_relationships_and_inspection_index():
    raw = graph()
    html = render_mindmap_html(raw, title="方法导图")
    assert graph_version(raw) in html
    assert "不推广为室外结果" in html and "node-inspector" in html
    assert "math-svg" in html and 'id="outline-0.1"' in html
    assert "关系与重复检查" in html and 'id="mindmap-index"' in html
    assert "showNode" in html and "keydown" in html
    dangerous = deepcopy(raw)
    dangerous["branches"][0]["details"] = "</script><script>BAD()</script>"
    safe = render_mindmap_html(dangerous, title="safe")
    assert "<script>BAD()" not in safe and "\\u003c/script>" in safe


def test_png_overview_and_branch_pages_share_identity_and_keep_cross_branch_index():
    raw = graph()
    version = graph_version(raw)
    overview = list(overview_pages(raw))
    pages = list(branch_png_pages(raw, 0))
    assert overview[0][0] == "-mindmap.png" and pages
    for _, data in [*overview, *pages]:
        with Image.open(io.BytesIO(data)) as image:
            metadata = json.loads(image.info["deep-research-mindmap"])
            assert metadata["mindmap_version"] == version
            assert image.width * image.height <= 2_500_000
    with Image.open(io.BytesIO(pages[0][1])) as image:
        metadata = json.loads(image.info["deep-research-mindmap"])
        assert metadata["cross_link_ids"] == ["L1"]
        assert metadata["branch_node_paths"] == ["0", "0.0", "0.1"]
    body = branch_markdown(raw, 0)
    assert "节点 0.0 —对比→ 节点 1.0" in body and "95" in body


def test_long_branch_paginates_without_scaling_away_node_text():
    raw = {
        "root": "完整说明",
        "branches": [
            {
                "label": "复杂分支",
                "children": [
                    {
                        "label": f"观察项 {i}",
                        "details": ("保留适用条件、数据来源和必要说明。" * 25) + f"结束标记 {i}",
                    }
                    for i in range(8)
                ],
            }
        ],
    }
    pages = list(branch_png_pages(raw, 0))
    assert len(pages) > 1
    starts = set()
    for _, data in pages:
        with Image.open(io.BytesIO(data)) as image:
            meta = json.loads(image.info["deep-research-mindmap"])
            starts.update(meta["page_node_starts"])
            assert 118 <= image.info["dpi"][0] <= 121
    assert starts == {"0", *(f"0.{i}" for i in range(8))}
    with pytest.raises(MindmapImageLimit):
        list(branch_png_pages(raw, 0, max_pages=1))


async def test_writer_to_frozen_delivery_publishes_index_and_branch_files(
    settings, tmp_path, api_repo
):
    from deep_research.agents.base import Blackboard, RunContext
    from deep_research.observability import Tracer
    from deep_research.orchestrator import create_initial_execution
    from deep_research.workbench.delivery_store import build_or_load, load_version
    from deep_research.workbench.publish import build_bundle
    from deep_research.workbench.templates import get_template
    from deep_research.workbench.writers import MindmapWriter
    from tests.fakes import FakeLLM, FakeSearch
    from tests.test_mindmap_relations import checked_graph

    model, results, _, _ = await checked_graph()
    model.branches[0].details = "该节点用于组织基础方法，不作为实验结果。"

    class Writer(FakeLLM):
        async def parse(self, system, user, schema, **kwargs):
            if schema is Mindmap:
                assert "details" in system
                return model.model_copy(deep=True)
            return await super().parse(system, user, schema, **kwargs)

    settings.quality = {"max_revisions": 0, "register_check": False, "require_limitations": False}
    llm = Writer()
    board = Blackboard(query="比较方法", results=results)
    await MindmapWriter().step(
        board, RunContext(llm=llm, search_tool=FakeSearch(), tracer=Tracer(), settings=settings)
    )
    raw = board.scratch["workbench"]["extras"]["mindmap"]
    assert raw["schema_version"] == 2 and raw["branches"][0]["details"]
    execution = create_initial_execution(board.query, get_template("mindmap").workflow, settings)
    execution.checkpoint["scratch"] = board.scratch.copy()
    api, repo = api_repo
    settings.artifact_root = str(tmp_path)
    api.app.state.settings = settings
    run_id = await repo.create_run(board.query, execution=execution)
    await repo.save_report(run_id, board.report)
    for result in board.results:
        await repo.save_result(run_id, result)
    await repo.set_status(run_id, "done")
    detail = await repo.get_run(run_id)
    bundle = build_or_load(detail, str(tmp_path), None, build_bundle)
    assert not bundle.failures, bundle.failures
    index_file = next(file for file in bundle.files if file.name.endswith("-mindmap-index.json"))
    index = json.loads(index_file.data)
    assert index["node_count"] == 2 and index["links"][0]["id"] == "L1"
    assert sum("-mindmap-b" in file.name for file in bundle.files) == 2
    assert "不作为实验结果" in next(
        file.data.decode() for file in bundle.files if file.format == "md"
    )
    for file in bundle.files:
        if file.format == "png":
            with Image.open(io.BytesIO(file.data)) as image:
                assert (
                    json.loads(image.info["deep-research-mindmap"])["mindmap_version"]
                    == index["mindmap_version"]
                )
    restored = load_version(detail, str(tmp_path), bundle.content_version)
    assert {file.name: file.sha256 for file in restored.files} == {
        file.name: file.sha256 for file in bundle.files
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test"
    ) as client:
        registry = await client.get(
            f"/api/runs/{run_id}/deliverables?version={bundle.content_version}"
        )
        assert registry.status_code == 200
        for file in bundle.files:
            response = await client.get(
                f"/api/runs/{run_id}/deliverables/{file.name}?version={bundle.content_version}"
            )
            assert response.status_code == 200 and response.content == file.data
            assert response.headers["x-content-version"] == bundle.content_version
            assert response.headers["x-content-sha256"] == file.sha256
def test_pdf_node_headings_accept_unicode_spaces_without_matching_path_prefixes():
    from deep_research.workbench.mindmap_delivery import _node_heading_paths

    text = "节点\u00a00\u00a0·\u00a0根分支\n节点\u00a00.10\u00a0·\u00a0完整标签\n"
    assert _node_heading_paths(text) == {"0", "0.10"}
    assert "0.1" not in _node_heading_paths(text)
    assert _node_heading_paths("跨分支链接：节点 0.1 · 不冒充标题") == set()
    assert _node_heading_paths("节点 0.1 · 已有标题\n节点\n0.2\n· 换行标题") == {"0.1", "0.2"}
