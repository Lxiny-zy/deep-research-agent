from __future__ import annotations

from deep_research.dag import build_dag, detect_cycle, topo_layers


def test_build_dag_cleans_invalid_deps():
    # 自环(0)、越界(5,-1)、重复(1,1) 都应被剔除，保序
    assert build_dag([[0, 5, -1, 1, 1], []]) == {0: [1], 1: []}


def test_empty_dag():
    assert build_dag([]) == {}
    assert topo_layers({}) == []
    assert detect_cycle({}) is None


def test_topo_layers_linear_chain():
    # 0 <- 1 <- 2：严格串行，每层一个
    assert topo_layers(build_dag([[], [0], [1]])) == [[0], [1], [2]]


def test_topo_layers_diamond():
    # 0 → {1, 2} → 3
    assert topo_layers(build_dag([[], [0], [0], [1, 2]])) == [[0], [1, 2], [3]]


def test_topo_layers_no_deps_single_layer():
    # 无依赖 → 单层全并行
    assert topo_layers(build_dag([[], [], []])) == [[0, 1, 2]]


def test_detect_cycle_none_when_acyclic():
    assert detect_cycle(build_dag([[], [0], [1]])) is None


def test_detect_cycle_finds_cycle():
    # 0 ↔ 1 成环
    cycle = detect_cycle(build_dag([[1], [0]]))
    assert cycle is not None
    assert set(cycle) == {0, 1}
