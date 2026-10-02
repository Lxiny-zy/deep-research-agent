from __future__ import annotations

import io
import json
from copy import deepcopy

import pytest
from PIL import Image

from deep_research.models import Report, ResearchResult
from deep_research.persistence.repository import RunDetail
from deep_research.workbench.figure_review import check_figure, review_figure
from deep_research.workbench.figures import ConceptFigure, render_concept_png
from deep_research.workbench.publish import build_bundle
from deep_research.workbench.support import SupportDecisions, SupportReviewer, evidence_records
from tests.fakes import FakeLLM, verified_finding
from tests.test_workbench import _execution


def figure(reverse=False):
    return ConceptFigure(
        title="方案比较",
        nodes=[{"id": "camera", "label": "相机阵列"}, {"id": "filter", "label": "滤光片阵列"}],
        edges=[
            {
                "source": "filter" if reverse else "camera",
                "target": "camera" if reverse else "filter",
                "label": "优于",
            }
        ],
    )


class Judge(FakeLLM):
    async def parse(self, system, user, schema, **kwargs):
        data = json.loads(user)
        return SupportDecisions(
            decisions=[
                {
                    "unit_id": u["id"],
                    "verdict": "unsupported"
                    if "滤光片阵列 --优于--> 相机阵列" in u["text"]
                    else "supported",
                    "reason": "比较方向相反"
                    if "滤光片阵列 --优于--> 相机阵列" in u["text"]
                    else "fixture",
                    "evidence_ids": [e["id"] for e in data["evidence"]],
                }
                for u in data["units"]
            ]
        )


async def test_directed_comparison_and_record_binding_are_checked():
    results = [
        ResearchResult(
            sub_question="q",
            findings=[
                verified_finding("相机阵列优于滤光片阵列", evidence_quote="相机阵列优于滤光片阵列")
            ],
        )
    ]
    evidence = evidence_records(results, {"https://a.com": 1})
    reviewer = SupportReviewer(Judge(), evidence, 50000)
    rejected = await review_figure(figure(True), reviewer)
    assert rejected["status"] == "fail"
    accepted = await review_figure(figure(), reviewer)
    assert accepted["status"] == "pass" and not check_figure(figure(), evidence, accepted)
    assert check_figure(figure(True), evidence, accepted)
    incomplete = deepcopy(accepted)
    incomplete["decisions"].pop()
    assert check_figure(figure(), evidence, incomplete)


def test_unreviewed_optional_figure_is_omitted_instead_of_silently_published(settings):
    _, execution = _execution("q", "autoResearch", settings)
    execution.checkpoint["scratch"]["workbench"] = {
        "template": "autoResearch",
        "extras": {"concept_figure": figure(True).model_dump()},
    }
    detail = RunDetail(
        id="figure-check",
        query="q",
        status="done",
        orchestration=execution,
        report=Report(query="q", markdown="## 结论\n\n正文", citations=[]),
    )
    bundle = build_bundle(detail)
    assert not any(file.name.startswith("figures/") for file in bundle.files)
    assert next(g for g in bundle.gates if g.name == "figure_evidence").status == "warn"
    assert {"md", "html", "docx", "pdf"}.issubset({file.format for file in bundle.files})


def test_long_flow_uses_report_width_instead_of_shrinking_a_many_column_canvas():
    graph = ConceptFigure(
        title="长流程",
        nodes=[{"id": str(i), "label": f"第{i}个流程节点"} for i in range(8)],
        edges=[{"source": str(i), "target": str(i + 1), "label": "下一步"} for i in range(7)],
    )
    with Image.open(io.BytesIO(render_concept_png(graph))) as image:
        assert image.width < 1600 and image.height > image.width


def test_feedback_cycle_and_downstream_stages_keep_their_flow_order():
    from deep_research.workbench.figures import _levels

    graph = ConceptFigure(
        title="Feedback flow",
        nodes=[{"id": key, "label": key} for key in ["input", "a", "b", "c", "output", "note"]],
        edges=[
            {"source": a, "target": b}
            for a, b in [("input", "a"), ("a", "b"), ("b", "c"), ("c", "a"), ("c", "output")]
        ],
    )
    rows = [[node.id for node in group] for group in _levels(graph)]
    assert rows == [["input", "note"], ["a", "b", "c"], ["output"]]
    assert render_concept_png(graph) == render_concept_png(graph)


def test_dense_edge_labels_do_not_overlap_each_other_or_nodes(monkeypatch):
    from matplotlib.figure import Figure
    from matplotlib.patches import FancyBboxPatch

    pairs = [
        (0, 1),
        (1, 2),
        (2, 3),
        (2, 4),
        (2, 5),
        (5, 6),
        (5, 7),
        (7, 8),
        (8, 9),
        (9, 7),
        (9, 10),
        (10, 11),
    ]
    graph = ConceptFigure(
        title="关系标签排版",
        nodes=[
            {"id": str(i), "label": "光谱角距离 SAD" if i == 11 else f"节点{i}"} for i in range(12)
        ],
        edges=[
            {"source": str(a), "target": str(b), "label": f"关系说明{i}"}
            for i, (a, b) in enumerate(pairs)
        ],
    )
    save = Figure.savefig
    checked = []

    def inspect(fig, *args, **kwargs):
        fig.canvas.draw()
        renderer = fig.canvas.get_renderer()
        ax = fig.axes[0]
        labels = [text for text in ax.texts if text.get_fontsize() == 8.5]
        assert len(labels) == len(pairs)
        boxes = [text.get_window_extent(renderer) for text in labels]
        nodes = [p.get_window_extent(renderer) for p in ax.patches if isinstance(p, FancyBboxPatch)]
        assert not any(a.overlaps(b) for i, a in enumerate(boxes) for b in boxes[i + 1 :])
        assert not any(a.overlaps(b) for a in boxes for b in nodes)
        assert any("SAD" in text.get_text() for text in ax.texts)
        checked.append(True)
        return save(fig, *args, **kwargs)

    monkeypatch.setattr(Figure, "savefig", inspect)
    assert render_concept_png(graph).startswith(b"\x89PNG") and checked


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("review_status", "body", "expected_calls"),
    [("fail", "结论X [1]。", 0), ("pass", "结果为 999 [1]。", 0), ("pass", "结论X [1]。", 1)],
)
async def test_optional_figure_waits_for_accepted_prose(
    settings, monkeypatch, review_status, body, expected_calls
):
    from deep_research.agents.base import Blackboard, RunContext
    from deep_research.models import ResearchResult
    from deep_research.observability import Tracer
    from deep_research.workbench.prose_review import ProseReviewer
    from deep_research.workbench.writers import TemplateWriter
    from tests.fakes import FakeLLM, FakeSearch, verified_finding

    async def review(self, body):
        return {"status": review_status, "issues": [] if review_status == "pass" else ["bad"]}

    monkeypatch.setattr(ProseReviewer, "review", review)

    class Writer(TemplateWriter):
        name = "research_writer"
        calls = 0

        async def _write_checked(self, *args, **kwargs):
            return body, None

        async def concept_figure(self, *args, **kwargs):
            self.calls += 1
            return None

    bb = Blackboard(
        query="Q",
        results=[ResearchResult(sub_question="Q", findings=[verified_finding("结论X")])],
    )
    ctx = RunContext(llm=FakeLLM(), search_tool=FakeSearch(), tracer=Tracer(), settings=settings)
    writer = Writer()
    await writer.step(bb, ctx)
    assert writer.calls == expected_calls
    assert ("concept_figure_skipped" in bb.scratch["workbench"]["extras"]) == (expected_calls == 0)
