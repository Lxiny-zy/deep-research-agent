"""Detect repeated claims across branches without conflating scientific conditions."""

import pytest

from deep_research.workbench.mindmap_contract import Mindmap, structural_issues

# Paired claims in the retained sc56 Markdown, lines 10/48, 12/49 and 14/50.
SC56_PAIRS = [
    (
        "数据集的 RGB 由真值 HSI 经变换矩阵生成，并注入散粒噪声以模拟真实相机",
        "MST++ 的 RGB 输入由真值 HSI 经变换矩阵生成，并注入散粒噪声以模拟真实相机情形，"
        "而非实拍图像",
        "RGB 到高光谱重建（MST++）",
    ),
    (
        "输入模态与数据来源不同，两文的性能数值不可直接对比",
        "两文的数据集、波长范围与评价指标不同，跨文性能数字不可直接比较",
        "研究任务与输入",
    ),
    (
        "共同动机：两文都从 HSI 特性出发沿光谱维做自注意力——MST++ 表述为 HSI 空间稀疏、"
        "光谱高度自相似，建模空间依赖不如捕捉光谱间相关性划算；MST 表述为由 HSI 特性驱动、"
        "捕捉光谱间相似性与依赖",
        "两文共享的前提是 HSI 光谱自相似：MST 以捕捉光谱间相似性与依赖为动机，"
        "MST++ 表述为空间稀疏、光谱高度自相似",
        "共同的光谱注意力思路",
    ),
]


def pair_graph(left, right, parent="方法背景", *, left_cites=(1,), right_cites=(1,)):
    return Mindmap(
        root="方法综述",
        branches=[
            {
                "label": parent,
                "children": [
                    {"label": left, "kind": "claim", "citations": list(left_cites)},
                ],
            },
            {
                "label": "适用边界",
                "children": [
                    {"label": right, "kind": "claim", "citations": list(right_cites)},
                ],
            },
        ],
    )


@pytest.mark.parametrize("left,right,parent", SC56_PAIRS)
def test_sc56_cross_branch_repetitions_are_reported(left, right, parent):
    issues = structural_issues(pair_graph(left, right, parent), 1)
    assert any("跨分支" in issue and "重复节点" in issue for issue in issues)


def test_global_duplicates_include_normalized_text_and_stable_paths():
    graph = pair_graph("光谱 自注意力保持全局感受野。", "光谱自注意力保持全局感受野")
    issues = structural_issues(graph, 1)
    assert any("0.0" in issue and "1.0" in issue for issue in issues)


@pytest.mark.parametrize(
    "left,right",
    [
        ("MST 的方法结构保持全局光谱注意力", "MST++ 的方法结构保持全局光谱注意力"),
        ("该模型的误差均值为 -0.5，采用相同评估数据", "该模型的误差均值为 0.5，采用相同评估数据"),
        ("使用 28 个波长执行光谱重建并评估效果", "使用 31 个波长执行光谱重建并评估效果"),
        ("该方法在统一条件下可以直接比较性能", "该方法在统一条件下不可以直接比较性能"),
        ("训练集上的模型平均误差已稳定收敛", "测试集上的模型平均误差已稳定收敛"),
    ],
)
def test_different_models_values_polarity_or_splits_are_not_duplicates(left, right):
    assert not structural_issues(pair_graph(left, right), 1)


def test_same_short_label_under_different_sources_is_not_collapsed():
    graph = pair_graph("采用光谱注意力", "采用光谱注意力", left_cites=(1,), right_cites=(2,))
    assert not structural_issues(graph, 2)


def test_questions_are_not_collapsed_into_claims():
    graph = pair_graph("光谱注意力能否适用于大规模数据？", "光谱注意力适用于大规模数据")
    graph.branches[0].children[0].kind = "question"
    assert not structural_issues(graph, 1)
