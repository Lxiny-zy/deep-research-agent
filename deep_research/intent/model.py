"""L2 本地统计模型：TF-IDF 特征 + 多类逻辑回归，纯 Python 推理，零依赖零 token。

**为什么不用 sklearn/torch**：本项目要打包成单文件桌面版（PyInstaller），
再引入几十 MB 的科学计算栈只为跑一次稀疏点积不划算。模型参数本身就是
一个 JSON（词表 + IDF + 权重矩阵），推理是一次哈希查表加乘加，用标准库足够。

**为什么是字符 n-gram**：中文没有空格分词，引入分词器又是一个重依赖。
字符 2/3-gram 对中文天然稳健（「怎么对比」「有什么区别」这类模式直接成为特征），
对英文则退化为子串匹配，因此额外补一路英文词 unigram。这也顺带缓解了
零宽字符/变体拆词的干扰——归一化在特征提取前统一做掉。

训练脚本见 ``scripts/train_intent_model.py``；模型文件是
``deep_research/intent/models/query_intent.json``，随包分发。
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

_MODEL_DIR = Path(__file__).resolve().parent / "models"
QUERY_MODEL_PATH = _MODEL_DIR / "query_intent.json"

# 与 SourcePolicy._ZERO_WIDTH_TRANSLATION 同源：特征提取前剥掉零宽字符，
# 避免「意​图」这类拆词把 n-gram 打碎导致模型看不见本该命中的模式。
_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿­"))
_WORD_RE = re.compile(r"[a-z0-9]+")
_CJK_RE = re.compile(r"[一-鿿]")


def normalize(text: str) -> str:
    """特征提取前的统一归一化：剥零宽 → NFKC → 小写 → 折叠空白。"""
    stripped = text.translate(_ZERO_WIDTH)
    folded = unicodedata.normalize("NFKC", stripped).casefold()
    return " ".join(folded.split())


def extract_features(text: str, *, char_ngrams: tuple[int, ...] = (2, 3)) -> dict[str, int]:
    """把一段文本变成 {特征名: 出现次数}。

    三路特征并集：
      - ``w:<token>``  英文/数字词 unigram
      - ``c2:``/``c3:`` 字符 n-gram（覆盖中文，且对空格不敏感）
      - ``len:<bucket>`` 长度桶（短 query 更可能是单点查询，是个弱但稳定的信号）
    """
    normalized = normalize(text)
    counts: dict[str, int] = {}

    for token in _WORD_RE.findall(normalized):
        counts[f"w:{token}"] = counts.get(f"w:{token}", 0) + 1

    # 字符 n-gram 只在去空格后的串上取，避免把分隔空白编进特征。
    compact = normalized.replace(" ", "")
    for n in char_ngrams:
        if len(compact) < n:
            continue
        for i in range(len(compact) - n + 1):
            key = f"c{n}:{compact[i : i + n]}"
            counts[key] = counts.get(key, 0) + 1

    bucket = min(len(compact) // 10, 6)
    counts[f"len:{bucket}"] = 1
    if _CJK_RE.search(compact):
        counts["lang:cjk"] = 1
    return counts


def _l2_normalize(vector: dict[str, float]) -> dict[str, float]:
    norm = math.sqrt(sum(value * value for value in vector.values()))
    if norm <= 0:
        return vector
    return {key: value / norm for key, value in vector.items()}


def _softmax(logits: list[float]) -> list[float]:
    if not logits:
        return []
    peak = max(logits)
    exps = [math.exp(value - peak) for value in logits]  # 减最大值防溢出
    total = sum(exps)
    if total <= 0:
        return [1.0 / len(logits)] * len(logits)
    return [value / total for value in exps]


@dataclass(frozen=True)
class IntentModelBundle:
    """训练产物：词表 IDF + 每类权重 + 偏置 + 标签顺序。"""

    labels: tuple[str, ...]
    idf: dict[str, float]
    weights: dict[str, tuple[float, ...]]
    bias: tuple[float, ...]
    # 低于该值视为模型弃权，交给下一级；由训练脚本按验证集校准写入。
    min_confidence: float = 0.55
    trained_on: int = 0
    version: int = 1

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> IntentModelBundle:
        labels = tuple(str(label) for label in raw["labels"])
        width = len(labels)
        weights: dict[str, tuple[float, ...]] = {}
        for feature, values in raw.get("weights", {}).items():
            row = tuple(float(value) for value in values)
            if len(row) != width:
                raise ValueError(f"权重维度与标签数不符：{feature}")
            weights[str(feature)] = row
        bias = tuple(float(value) for value in raw.get("bias", [0.0] * width))
        if len(bias) != width:
            raise ValueError("偏置维度与标签数不符")
        return cls(
            labels=labels,
            idf={str(key): float(value) for key, value in raw.get("idf", {}).items()},
            weights=weights,
            bias=bias,
            min_confidence=float(raw.get("min_confidence", 0.55)),
            trained_on=int(raw.get("trained_on", 0)),
            version=int(raw.get("version", 1)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "labels": list(self.labels),
            "min_confidence": self.min_confidence,
            "trained_on": self.trained_on,
            "idf": self.idf,
            "weights": {key: list(value) for key, value in self.weights.items()},
            "bias": list(self.bias),
        }


class TextClassifier:
    """稀疏逻辑回归推理器：TF-IDF 向量 × 权重矩阵 → softmax。"""

    def __init__(self, bundle: IntentModelBundle) -> None:
        self.bundle = bundle

    def vectorize(self, text: str) -> dict[str, float]:
        """TF（对数缩放）× IDF，再 L2 归一化。

        训练期未见过的特征直接丢弃（不做 OOV 桶）——推理时词表是封闭的，
        保留未知特征只会引入无权重的噪声维度。
        """
        counts = extract_features(text)
        vector = {
            feature: (1.0 + math.log(count)) * self.bundle.idf[feature]
            for feature, count in counts.items()
            if feature in self.bundle.idf
        }
        return _l2_normalize(vector)

    def predict(self, text: str) -> tuple[str, float, dict[str, float]]:
        """返回 (最佳标签, 置信度, 各类概率)。词表全未命中时置信度为 0。"""
        vector = self.vectorize(text)
        if not vector:
            return self.bundle.labels[0], 0.0, {}
        logits = list(self.bundle.bias)
        for feature, value in vector.items():
            row = self.bundle.weights.get(feature)
            if row is None:
                continue
            for index, weight in enumerate(row):
                logits[index] += weight * value
        probabilities = _softmax(logits)
        scores = dict(zip(self.bundle.labels, probabilities, strict=True))
        best_label = max(scores, key=lambda label: scores[label])
        return best_label, scores[best_label], scores


@lru_cache(maxsize=4)
def load_bundled_model(path: str | None = None) -> TextClassifier | None:
    """加载随包分发的模型；文件缺失或损坏时返回 None（级联自动跳过 L2）。

    结果缓存：模型文件在进程生命周期内不变，每次分类都读盘解析 JSON 会
    把 L2 「比 LLM 快几个数量级」的优势吃掉。
    """
    target = Path(path) if path else QUERY_MODEL_PATH
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
        return TextClassifier(IntentModelBundle.from_dict(raw))
    except (OSError, ValueError, KeyError):
        return None
