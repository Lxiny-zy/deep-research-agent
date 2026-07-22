"""Versioned workflow graph contract and legacy linear-step adapters."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class NodePosition(BaseModel):
    x: float = 0
    y: float = 0


class WorkflowNode(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    type: str = "step"
    position: NodePosition = Field(default_factory=NodePosition)
    step: dict = Field(default_factory=dict)
    join_mode: Literal["any", "all", "success_all"] = "any"


class WorkflowEdge(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    source: str
    target: str
    condition: str | None = None


class WorkflowViewport(BaseModel):
    x: float = 0
    y: float = 0
    zoom: float = Field(1, ge=0.1, le=4)
    input_position: NodePosition | None = None
    output_position: NodePosition | None = None


def steps_to_graph(steps: list[dict]) -> tuple[list[WorkflowNode], list[WorkflowEdge]]:
    nodes = [
        WorkflowNode(
            id=f"node-{index + 1}",
            position=NodePosition(x=160, y=80 + index * 150),
            step=step,
        )
        for index, step in enumerate(steps)
    ]
    edges = [
        WorkflowEdge(id=f"edge-{index + 1}", source=nodes[index].id, target=nodes[index + 1].id)
        for index in range(max(0, len(nodes) - 1))
    ]
    return nodes, edges


def graph_to_steps(nodes: list[WorkflowNode], edges: list[WorkflowEdge]) -> list[dict]:
    """Convert a single-path DAG to legacy steps, rejecting ambiguous graphs for now."""
    if not nodes:
        return []
    by_id = {node.id: node for node in nodes}
    if len(by_id) != len(nodes):
        raise ValueError("节点 id 重复")
    if len({edge.id for edge in edges}) != len(edges):
        raise ValueError("边 id 重复")
    outgoing: dict[str, list[str]] = {node_id: [] for node_id in by_id}
    incoming: dict[str, int] = {node_id: 0 for node_id in by_id}
    for edge in edges:
        if edge.source not in by_id or edge.target not in by_id:
            raise ValueError(f"边 {edge.id} 引用了不存在的节点")
        outgoing[edge.source].append(edge.target)
        incoming[edge.target] += 1
    has_branch = any(len(targets) > 1 for targets in outgoing.values())
    has_merge = any(value > 1 for value in incoming.values())
    if has_branch or has_merge:
        raise ValueError("当前运行时仅支持单路径图；分支与聚合将在图调度器阶段启用")
    roots = [node_id for node_id, count in incoming.items() if count == 0]
    if len(roots) != 1:
        raise ValueError("工作流图必须有且只有一个起点")
    ordered: list[dict] = []
    visited: set[str] = set()
    current: str | None = roots[0]
    while current is not None:
        if current in visited:
            raise ValueError("工作流图存在循环")
        visited.add(current)
        ordered.append(by_id[current].step)
        targets = outgoing[current]
        current = targets[0] if targets else None
    if len(visited) != len(nodes):
        raise ValueError("工作流图包含未连接节点")
    return ordered


def graph_topo_layers(
    nodes: list[WorkflowNode], edges: list[WorkflowEdge]
) -> list[list[WorkflowNode]]:
    """Validate a DAG and return deterministic parallel execution layers."""
    if not nodes:
        return []
    by_id = {node.id: node for node in nodes}
    if len(by_id) != len(nodes):
        raise ValueError("节点 id 重复")
    edge_ids = {edge.id for edge in edges}
    if len(edge_ids) != len(edges):
        raise ValueError("边 id 重复")
    order = {node.id: index for index, node in enumerate(nodes)}
    outgoing: dict[str, list[str]] = {node_id: [] for node_id in by_id}
    incoming: dict[str, int] = {node_id: 0 for node_id in by_id}
    for edge in edges:
        if edge.source not in by_id or edge.target not in by_id:
            raise ValueError(f"边 {edge.id} 引用了不存在的节点")
        outgoing[edge.source].append(edge.target)
        incoming[edge.target] += 1
    if len(nodes) > 1:
        neighbors: dict[str, set[str]] = {node_id: set() for node_id in by_id}
        for edge in edges:
            neighbors[edge.source].add(edge.target)
            neighbors[edge.target].add(edge.source)
        connected: set[str] = set()
        queue = [nodes[0].id]
        while queue:
            node_id = queue.pop()
            if node_id in connected:
                continue
            connected.add(node_id)
            queue.extend(neighbors[node_id] - connected)
        if len(connected) != len(nodes):
            raise ValueError("工作流图包含未连接节点")
    ready = sorted(
        (node_id for node_id, count in incoming.items() if count == 0),
        key=lambda node_id: order[node_id],
    )
    if not ready:
        raise ValueError("工作流图没有起点，可能存在循环")
    layers: list[list[WorkflowNode]] = []
    visited = 0
    while ready:
        current = ready
        layers.append([by_id[node_id] for node_id in current])
        ready = []
        for node_id in current:
            visited += 1
            for target in outgoing[node_id]:
                incoming[target] -= 1
                if incoming[target] == 0:
                    ready.append(target)
        ready.sort(key=lambda node_id: order[node_id])
    if visited != len(nodes):
        raise ValueError("工作流图存在循环")
    return layers


def graph_topological_steps(nodes: list[WorkflowNode], edges: list[WorkflowEdge]) -> list[dict]:
    return [node.step for layer in graph_topo_layers(nodes, edges) for node in layer]
