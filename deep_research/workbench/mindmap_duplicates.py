"""Conservative text-similarity candidates; never merge or delete scientific claims."""

from __future__ import annotations

import itertools
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any

_ENTITY = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[-+][A-Za-z0-9]+|\+)*")
_NUMBER = re.compile(r"(?<![\w.])[+−-]?\d+(?:\.\d+)?(?:[eE][+−-]?\d+)?")
_SPLIT = re.compile(r"训练集|验证集|测试集|training|validation|test", re.I)
_NEGATIVE = re.compile(r"不可|不能|不|没有|未|无|\bnot\b|\bwithout\b", re.I)
_CAUSAL = re.compile(r"因果|导致|causal", re.I)
_CORRELATION = re.compile(r"相关|关联|correlat\w*", re.I)


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()
    # Source numbers are metadata, not measured values.
    text = re.sub(r"\[\d+(?:\s*[,，]\s*\d+)*\]", "", text)
    text = text.replace("对比", "比较").replace("数值", "数字")
    return re.sub(r"[^\w+−-]", "", text)


def _grams(text: str) -> set[str]:
    return {text[i : i + 2] for i in range(len(text) - 1)}


def duplicate_nodes(model: Any) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []

    def walk(children: Any, prefix: str, ancestors: str) -> None:
        for index, node in enumerate(children):
            path = f"{prefix}.{index}" if prefix else str(index)
            context = ancestors + " / " + node.label
            nodes.append(
                {
                    "id": path,
                    "parent": prefix,
                    "label": node.label,
                    "text": _plain(node.label),
                    "kind": node.kind,
                    "citations": set(node.citations),
                    "entities": {v.casefold() for v in _ENTITY.findall(context)},
                    "numbers": {v.replace("−", "-") for v in _NUMBER.findall(node.label)},
                    "splits": set(_SPLIT.findall(context.casefold())),
                    "causal": bool(_CAUSAL.search(node.label)),
                    "correlation": bool(_CORRELATION.search(node.label)),
                }
            )
            walk(node.children, path, context)

    walk(model.branches, "", "")
    duplicates = []
    for left, right in itertools.combinations(nodes, 2):
        if left["parent"] == right["parent"]:
            continue  # The existing sibling check owns this case.
        if left["id"].startswith(right["id"] + ".") or right["id"].startswith(left["id"] + "."):
            continue  # A child can elaborate its parent.
        if left["kind"] != right["kind"] or any(
            left[key] != right[key] for key in ("entities", "numbers", "splits")
        ):
            continue
        if left["causal"] != right["causal"] and (left["correlation"] or right["correlation"]):
            continue
        if (
            left["citations"]
            and right["citations"]
            and not left["citations"].intersection(right["citations"])
        ):
            continue
        a, b = left["text"], right["text"]
        if min(len(a), len(b)) < 8:
            continue
        if a != b and _NEGATIVE.sub("", a) == _NEGATIVE.sub("", b):
            continue
        ga, gb = _grams(a), _grams(b)
        overlap = len(ga & gb) / max(1, min(len(ga), len(gb)))
        sequence = SequenceMatcher(None, a, b, autojunk=False).ratio()
        similar = a == b or (
            min(len(a), len(b)) >= 20
            and len(ga & gb) >= 9
            and overlap >= 0.4
            and (sequence >= 0.45 or overlap >= 0.58)
        )
        if similar:
            duplicates.append(
                {
                    "left": left["id"],
                    "right": right["id"],
                    "labels": [left["label"], right["label"]],
                    "exact": a == b,
                    "similarity": round(max(overlap, sequence), 3),
                }
            )
    return duplicates
