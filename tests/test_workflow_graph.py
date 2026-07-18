from __future__ import annotations

import pytest

from deep_research.orchestration import WorkflowEdge, WorkflowNode, graph_to_steps, steps_to_graph

STEPS = [
    {"kind": "agent", "agent": "planner"},
    {"kind": "agent", "agent": "researcher"},
    {"kind": "agent", "agent": "synthesizer"},
]


def test_linear_steps_graph_roundtrip() -> None:
    nodes, edges = steps_to_graph(STEPS)
    assert [node.id for node in nodes] == ["node-1", "node-2", "node-3"]
    assert [(edge.source, edge.target) for edge in edges] == [
        ("node-1", "node-2"),
        ("node-2", "node-3"),
    ]
    assert graph_to_steps(nodes, edges) == STEPS


def test_graph_rejects_cycle() -> None:
    nodes, edges = steps_to_graph(STEPS)
    edges.append(WorkflowEdge(id="cycle", source="node-3", target="node-1"))
    with pytest.raises(ValueError, match="起点|循环"):
        graph_to_steps(nodes, edges)


def test_graph_topology_supports_branch() -> None:
    from deep_research.orchestration import graph_topo_layers

    nodes = [
        WorkflowNode(id="a", step=STEPS[0]),
        WorkflowNode(id="b", step=STEPS[1]),
        WorkflowNode(id="c", step=STEPS[2]),
    ]
    edges = [
        WorkflowEdge(id="ab", source="a", target="b"),
        WorkflowEdge(id="ac", source="a", target="c"),
    ]
    layers = graph_topo_layers(nodes, edges)
    assert [[node.id for node in layer] for layer in layers] == [["a"], ["b", "c"]]
