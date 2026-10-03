from __future__ import annotations

import io
import json
import shutil
import subprocess

import pytest
from PIL import Image

from deep_research.workbench.figures import ConceptFigure
from deep_research.workbench.graphviz_figure import dot_source, render_grouped_png


def figure():
    return ConceptFigure(
        title='Structured graph <literal> "title"',
        evidence_mode="scoped",
        nodes=[
            {"id": str(i), "label": f"Node {i}", "group": "Measurement" if i < 4 else "Analysis"}
            for i in range(8)
        ],
        edges=[
            {"source": str(a), "target": str(b), "label": f"Link {i}"}
            for i, (a, b) in enumerate(
                [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (5, 6), (5, 7), (0, 5)]
            )
        ],
    )


@pytest.mark.skipif(not shutil.which("dot"), reason="Graphviz runtime is installed by Dockerfile")
def test_graphviz_preserves_topology_and_literal_labels():
    graph = figure()
    graph.nodes[1].label = 'Literal "; injected -> bad; <tag>'
    original = graph.model_dump()
    source = dot_source(graph)
    result = subprocess.run(
        [shutil.which("dot"), "-Tjson"],
        input=source.encode(),
        capture_output=True,
        check=True,
    )
    data = json.loads(result.stdout)
    nodes = [item for item in data["objects"] if "pos" in item]
    assert len(nodes) == 8 and len(data["edges"]) == 8
    label = next(n["label"] for n in nodes if n["name"] == "n1")
    displayed = "".join(label.replace("\\n", "").split())
    assert displayed == "".join(graph.nodes[1].label.split())
    png = render_grouped_png(graph)
    assert png is not None and graph.model_dump() == original
    with Image.open(io.BytesIO(png)) as image:
        assert image.width > 400 and image.height > 400


def test_missing_graphviz_and_math_labels_keep_builtin_rendering_available(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _: None)
    assert render_grouped_png(figure()) is None
    monkeypatch.setattr(shutil, "which", lambda _: "unavailable-binary")
    graph = figure()
    graph.nodes[0].label = "$x^2$"
    assert render_grouped_png(graph) is None


def test_incomplete_external_layout_is_rejected(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _: "dot")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=b'{"objects":[],"edges":[]}'),
    )
    with pytest.raises(ValueError, match="完整节点"):
        render_grouped_png(figure())
