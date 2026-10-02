from __future__ import annotations

import io
import json
from copy import deepcopy

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
