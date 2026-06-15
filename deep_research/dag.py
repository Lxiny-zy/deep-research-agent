"""子问题依赖图（DAG）：构建、环检测、拓扑分层。

Planner 产出的每个子问题可声明 `depends_on`（前驱序号）。本模块把这些声明
规整成 DAG，并提供：
  - build_dag    ：清洗依赖（剔除越界/自环/重复）得到邻接表
  - detect_cycle ：环检测（用于编排器破环降级，保证永不死锁）
  - topo_layers  ：拓扑分层（Kahn）——同层可并行检索，层间串行（后层可用前层发现作上下文）

依赖方向约定：dag[node] 是 node 的「前驱集合」，即这些前驱必须先于 node 完成。
"""

from __future__ import annotations


def build_dag(deps: list[list[int]]) -> dict[int, list[int]]:
    """把每个节点的 depends_on 列表规整成 {节点: [合法前驱...]} 邻接表。

    deps[i] 为节点 i 声明的前驱序号列表；返回时剔除越界、自环与重复（保序）。
    """
    n = len(deps)
    dag: dict[int, list[int]] = {}
    for i in range(n):
        seen: set[int] = set()
        preds: list[int] = []
        for d in deps[i]:
            if 0 <= d < n and d != i and d not in seen:
                seen.add(d)
                preds.append(d)
        dag[i] = preds
    return dag


def topo_layers(dag: dict[int, list[int]]) -> list[list[int]]:
    """Kahn 拓扑分层。每层内节点的依赖均已在更早的层中满足，可安全并行。

    若图中存在环，环上（及被环阻塞的）节点无法进入任何层，会被排除——
    调用方应先 detect_cycle 并破环后再分层。
    """
    in_degree: dict[int, int] = {node: len(preds) for node, preds in dag.items()}
    successors: dict[int, list[int]] = {node: [] for node in dag}
    for node, preds in dag.items():
        for p in preds:
            successors[p].append(node)

    ready = sorted(node for node, deg in in_degree.items() if deg == 0)
    layers: list[list[int]] = []
    while ready:
        layers.append(ready)
        nxt: list[int] = []
        for node in ready:
            for s in successors[node]:
                in_degree[s] -= 1
                if in_degree[s] == 0:
                    nxt.append(s)
        ready = sorted(nxt)
    return layers


def detect_cycle(dag: dict[int, list[int]]) -> list[int] | None:
    """检测是否存在环。返回无法被拓扑排序的节点（环及其下游），无环则返回 None。"""
    layered = {n for layer in topo_layers(dag) for n in layer}
    remaining = [n for n in sorted(dag) if n not in layered]
    return remaining or None
