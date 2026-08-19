"""训练 L2 意图分类器（TF-IDF + 多类逻辑回归），产出 query_intent.json。

纯标准库实现的批量梯度下降 + L2 正则。之所以不引 sklearn：模型要随
PyInstaller 桌面版打包，而这个规模的问题（百级样本、千级特征、6 类）
用纯 Python 训练也只要秒级。

运行：
    python -m scripts.train_intent_model              # 训练 + 写模型 + 打印评测
    python -m scripts.train_intent_model --dry-run    # 只评测不写文件

复现性：数据固定、切分种子固定、初始权重全零、迭代次数固定，
因此同一份数据集永远训出同一个模型文件（可 diff、可提交、可回归）。
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:  # 支持 `python scripts/train_intent_model.py` 直接运行
    sys.path.insert(0, str(_REPO_ROOT))

from deep_research.intent.dataset import QUERY_INTENT_DATA  # noqa: E402
from deep_research.intent.model import (  # noqa: E402
    QUERY_MODEL_PATH,
    IntentModelBundle,
    TextClassifier,
    extract_features,
)

SEED = 20260802
DEV_RATIO = 0.25
EPOCHS = 400
LEARNING_RATE = 1.2
L2_REGULARIZATION = 3e-4
MIN_DOCUMENT_FREQUENCY = 2  # 只出现一次的特征多为噪声，剔除可显著压缩模型体积


def stratified_split(
    data: list[tuple[str, str]], *, dev_ratio: float, seed: int
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """按标签分层切分，保证每个类在 dev 里都有样本（小数据集上尤其重要）。"""
    grouped: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for text, label in data:
        grouped[label].append((text, label))
    rng = random.Random(seed)
    train: list[tuple[str, str]] = []
    dev: list[tuple[str, str]] = []
    for label in sorted(grouped):
        items = sorted(grouped[label])
        rng.shuffle(items)
        cut = max(1, round(len(items) * dev_ratio))
        dev.extend(items[:cut])
        train.extend(items[cut:])
    rng.shuffle(train)
    rng.shuffle(dev)
    return train, dev


def build_idf(documents: list[dict[str, int]]) -> dict[str, float]:
    """平滑 IDF：log((1+N)/(1+df)) + 1，与 sklearn 的 smooth_idf 一致。"""
    total = len(documents)
    document_frequency = Counter()
    for counts in documents:
        document_frequency.update(counts.keys())
    return {
        feature: math.log((1 + total) / (1 + df)) + 1.0
        for feature, df in document_frequency.items()
        if df >= MIN_DOCUMENT_FREQUENCY
    }


def vectorize(counts: dict[str, int], idf: dict[str, float]) -> dict[str, float]:
    vector = {
        feature: (1.0 + math.log(count)) * idf[feature]
        for feature, count in counts.items()
        if feature in idf
    }
    norm = math.sqrt(sum(value * value for value in vector.values()))
    if norm <= 0:
        return {}
    return {key: value / norm for key, value in vector.items()}


def softmax(logits: list[float]) -> list[float]:
    peak = max(logits)
    exps = [math.exp(value - peak) for value in logits]
    total = sum(exps)
    return [value / total for value in exps]


def train(
    samples: list[tuple[dict[str, float], int]],
    labels: tuple[str, ...],
    features: list[str],
) -> tuple[dict[str, list[float]], list[float]]:
    """批量梯度下降训练 softmax 回归。权重零初始化 → 训练完全确定性。"""
    width = len(labels)
    weights: dict[str, list[float]] = {feature: [0.0] * width for feature in features}
    bias = [0.0] * width
    n = len(samples)
    if n == 0:
        return weights, bias

    for _ in range(EPOCHS):
        weight_gradients: dict[str, list[float]] = defaultdict(lambda: [0.0] * width)
        bias_gradients = [0.0] * width
        for vector, target in samples:
            logits = list(bias)
            for feature, value in vector.items():
                row = weights.get(feature)
                if row is None:
                    continue
                for index in range(width):
                    logits[index] += row[index] * value
            probabilities = softmax(logits)
            # 交叉熵对 logit 的梯度就是 (p - y)
            errors = [
                probabilities[index] - (1.0 if index == target else 0.0) for index in range(width)
            ]
            for index in range(width):
                bias_gradients[index] += errors[index]
            for feature, value in vector.items():
                if feature not in weights:
                    continue
                row = weight_gradients[feature]
                for index in range(width):
                    row[index] += errors[index] * value

        for index in range(width):
            bias[index] -= LEARNING_RATE * bias_gradients[index] / n
        for feature, gradient in weight_gradients.items():
            row = weights[feature]
            for index in range(width):
                # L2 正则只作用于权重不作用于偏置（标准做法：偏置不该被压向 0）
                row[index] -= LEARNING_RATE * (gradient[index] / n + L2_REGULARIZATION * row[index])
    return weights, bias


def evaluate(
    classifier: TextClassifier, data: list[tuple[str, str]]
) -> tuple[float, dict[str, dict[str, int]], list[tuple[str, str, str, float]]]:
    """返回 (准确率, 混淆矩阵, badcase 列表)。"""
    confusion: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    errors: list[tuple[str, str, str, float]] = []
    correct = 0
    for text, gold in data:
        predicted, confidence, _ = classifier.predict(text)
        confusion[gold][predicted] += 1
        if predicted == gold:
            correct += 1
        else:
            errors.append((text, gold, predicted, confidence))
    accuracy = correct / len(data) if data else 0.0
    return accuracy, {key: dict(value) for key, value in confusion.items()}, errors


def calibrate_threshold(
    classifier: TextClassifier, dev: list[tuple[str, str]]
) -> tuple[float, float, float]:
    """在 dev 上选阈值：取「采纳部分的精度 ≥ 0.9」中覆盖率最高的那个。

    这是级联的关键旋钮——阈值定高了大量样本白白升级到 LLM（贵），
    定低了错误路由流入下游（错）。用 dev 集显式校准，而不是拍脑袋。
    """
    predictions = [(classifier.predict(text), gold) for text, gold in dev]
    best = (0.55, 0.0, 0.0)
    for step in range(20, 96, 5):
        threshold = step / 100
        accepted = [(pred, gold) for pred, gold in predictions if pred[1] >= threshold]
        if not accepted:
            continue
        precision = sum(pred[0] == gold for pred, gold in accepted) / len(accepted)
        coverage = len(accepted) / len(predictions)
        if precision >= 0.9 and coverage > best[2]:
            best = (threshold, precision, coverage)
    return best


def main() -> int:
    parser = argparse.ArgumentParser(description="训练意图识别 L2 本地模型")
    parser.add_argument("--dry-run", action="store_true", help="只训练评测，不写模型文件")
    parser.add_argument("--output", default=str(QUERY_MODEL_PATH), help="模型输出路径")
    args = parser.parse_args()

    data = list(QUERY_INTENT_DATA)
    labels = tuple(sorted({label for _, label in data}))
    train_data, dev_data = stratified_split(data, dev_ratio=DEV_RATIO, seed=SEED)
    print(f"样本 {len(data)} 条（train {len(train_data)} / dev {len(dev_data)}），{len(labels)} 类")

    train_counts = [extract_features(text) for text, _ in train_data]
    idf = build_idf(train_counts)  # IDF 只用 train 统计，防止 dev 信息泄漏
    features = sorted(idf)
    print(f"特征 {len(features)} 维（df >= {MIN_DOCUMENT_FREQUENCY}）")

    label_index = {label: index for index, label in enumerate(labels)}
    samples = [
        (vectorize(counts, idf), label_index[label])
        for counts, (_, label) in zip(train_counts, train_data, strict=True)
    ]
    weights, bias = train(samples, labels, features)

    bundle = IntentModelBundle(
        labels=labels,
        idf=idf,
        weights={feature: tuple(row) for feature, row in weights.items()},
        bias=tuple(bias),
        trained_on=len(train_data),
    )
    classifier = TextClassifier(bundle)

    train_accuracy, _, _ = evaluate(classifier, train_data)
    dev_accuracy, confusion, errors = evaluate(classifier, dev_data)
    threshold, precision, coverage = calibrate_threshold(classifier, dev_data)
    print(f"train 准确率 {train_accuracy:.3f} / dev 准确率 {dev_accuracy:.3f}")
    print(f"校准阈值 {threshold:.2f}：采纳精度 {precision:.3f}，覆盖率 {coverage:.3f}")

    print("\n混淆矩阵（行=真实，列=预测）：")
    header = "".join(f"{label[:9]:>11}" for label in labels)
    print(f"{'':>18}{header}")
    for gold in labels:
        row = "".join(f"{confusion.get(gold, {}).get(pred, 0):>11}" for pred in labels)
        print(f"{gold:>18}{row}")

    if errors:
        print(f"\nbadcase（{len(errors)} 条）：")
        for text, gold, predicted, confidence in errors:
            print(f"  [{gold} → {predicted} p={confidence:.2f}] {text}")

    bundle = IntentModelBundle(
        labels=bundle.labels,
        idf=bundle.idf,
        weights=bundle.weights,
        bias=bundle.bias,
        min_confidence=threshold,
        trained_on=bundle.trained_on,
    )
    if args.dry_run:
        print("\n--dry-run：未写入模型文件")
        return 0

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(bundle.to_dict(), ensure_ascii=False, sort_keys=True, indent=1),
        encoding="utf-8",
    )
    size_kb = output.stat().st_size / 1024
    print(f"\n已写入 {output}（{size_kb:.1f} KB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
