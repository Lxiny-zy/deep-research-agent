"""发布方独立性判定：同一篇工作、同一个团队，不能算成两个独立来源。

## 现有逻辑为什么在科学场景下是错的

交叉印证门禁原先按 registrable domain 判独立发布方。通用网页调研里这个近似够用，
但在文献场景下它会给出**错误结论**：

1. **同一篇工作分散在多个域**。一篇 CASSI 工作常同时存在 arXiv 预印本、
   Optics Express / CVPR 正式版、机构库副本——三个域，一篇工作，被算成"三个独立
   来源"。于是"已交叉印证"这个结论本身是假的。
2. **同一课题组的不同论文**。本领域课题组高度集中，同组两篇论文互相印证不构成
   独立验证，但它们可能落在两个不同域（arxiv.org 与 openreview.net）。
3. **聚合站镜像**。ResearchGate / Semantic Scholar 页面是同一篇工作的副本。

## 判定单位是"团队"，不是"域名"

科学意义上的独立印证是：**两组独立的研究，各自得出了同一结论**。所以

* 同一篇工作 → 1 个来源；
* 不同工作、同一团队 → 1 个来源；
* 不同工作、不同团队 → 2 个来源。

## 为什么需要并查集而不是一个分组键

独立性由**重叠关系**定义，不是相等关系：A 与 B 共享一位作者，B 与 C 共享另一位
作者，则 A/B/C 归为同一团队簇。这种传递闭包只能用并查集表达，用"作者集合当键"
会把 A 与 B 判成不同键。

传递性在极端情况下会过度合并（A 与 C 可能确无关系），但那是**失败关闭**的方向：
独立来源数被低估，最坏结果是一条真实的双源论断被降级为单源（可用性损失），
而不是一条伪双源被放行（安全性损失）。这与本项目"意图判定只能收紧"同一原则。

## 合并规则（层叠，任一命中即视为同一发布方）

1. **同一 DOI**（归一化后）——最强的同一工作证据；
2. **同一 work_id**（OpenAlex / arXiv，剥掉版本号）；
3. **同一归一化标题**——补 1/2 的漏：预印本可能没有期刊 DOI；
4. **作者重叠**——补 3 的漏：预印本与终稿标题常有改动，但作者不变。同时这一条
   本身就是"同一团队"的判据；
5. **同一 registrable domain**——保留原有行为作为下界。

规则 5 是关键：它保证新逻辑**只可能比旧逻辑给出更少或相同的独立来源数**，
永不更多。因此这次改动在结构上不可能放宽门禁（见测试里的单调性断言）。

## 刻意不做机构重叠

同一机构不等于同一团队——大学里两个互不相关的组报同一个数，那是真的独立验证。
把机构当合并依据会把它们错误合并，而"同一团队"这个真正的信号已由规则 4 覆盖。

## 已知残留缺口（不假装解决）

**benchmark 数值传抄**：论文 B 从论文 A 的表里抄了 baseline 的 PSNR，两篇是不同
工作、不同团队，因此本模块判它们独立——但 B 并不是独立测量，只是转录。这在 CASSI
类 benchmark 里很普遍。元数据里没有任何信号能区分"独立复现"与"转录"，所以本模块
不猜；"≥2 个独立发布方"的含义仅限于"两个独立团队各自发表了这个说法"，不等于
"两次独立测量"。
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field

from .models import SourceIdentity

# 归一化标题短于此长度时不作为同一工作的判据。
# 这条守卫针对的是**退化合并**：若一批来源标题都为空，空标题会把它们全部并成
# 一簇，独立来源数塌成 1，整个指标失去意义（严格门禁下报告会没有素材）。
_MIN_TITLE_KEY_CHARS = 12

_DOI_PREFIXES = ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "doi:")
_OPENALEX_PREFIXES = ("https://openalex.org/", "http://openalex.org/")
_ARXIV_VERSION_RE = re.compile(r"v\d+$")


def normalize_doi(doi: str) -> str:
    """DOI 归一化。DOI 按规范大小写不敏感，所以统一折叠。"""
    value = unicodedata.normalize("NFKC", doi or "").strip()
    lowered = value.casefold()
    for prefix in _DOI_PREFIXES:
        if lowered.startswith(prefix):
            value = value[len(prefix) :]
            break
    return value.strip().strip(".,;").casefold()


def normalize_work_id(work_id: str) -> str:
    """work_id 归一化：剥 OpenAlex URL 前缀，剥 arXiv 版本号。

    ``arxiv:2205.10102v1`` 与 ``…v3`` 是同一篇工作的两个版本，必须归一到一起——
    否则同一篇论文的两个版本会被算成两个独立来源。
    """
    value = unicodedata.normalize("NFKC", work_id or "").strip().casefold()
    for prefix in _OPENALEX_PREFIXES:
        if value.startswith(prefix):
            value = value[len(prefix) :]
            break
    if value.startswith("arxiv:"):
        return "arxiv:" + _ARXIV_VERSION_RE.sub("", value[len("arxiv:") :])
    return value


def normalize_title(title: str) -> str:
    """标题归一化成同一工作的判据键。

    只保留字母数字（``str.isalnum`` 对中日韩字符同样为真），因此标点、空白、
    大小写、连字符与换行差异都不影响判定。返回空串表示"不可用作判据"。
    """
    folded = unicodedata.normalize("NFKC", title or "").casefold()
    key = "".join(ch for ch in folded if ch.isalnum())
    return key if len(key) >= _MIN_TITLE_KEY_CHARS else ""


def author_key(name: str) -> str:
    """作者名归一化成"姓 + 名首字母"。

    这是书目学的常规做法，且刻意偏向**合并**：``Y. Cai`` 与 ``Yuanhao Cai`` 归一到
    同一键。偏向合并意味着误判方向是"把两个人当成一个"→ 低估独立来源数 → 失败关闭。

    单词名（如中日韩姓名连写、单名）整体作为键。返回空串表示不可用。
    """
    value = unicodedata.normalize("NFKC", name or "").strip()
    if not value:
        return ""
    surname, given = "", ""
    if "," in value:
        # "Cai, Yuanhao" —— 逗号前是姓
        head, _, tail = value.partition(",")
        surname, given = head, tail
    else:
        tokens = [token for token in value.split() if token]
        if len(tokens) >= 2:
            surname, given = tokens[-1], tokens[0]
        elif tokens:
            surname = tokens[0]
    surname_key = "".join(ch for ch in surname.casefold() if ch.isalnum())
    if not surname_key:
        return ""
    initial = next((ch for ch in given.casefold() if ch.isalnum()), "")
    return f"{surname_key}_{initial}" if initial else surname_key


def author_keys(names: list[str]) -> set[str]:
    return {key for name in names if (key := author_key(name))}


@dataclass
class _UnionFind:
    parent: dict[str, str] = field(default_factory=dict)

    def add(self, item: str) -> None:
        self.parent.setdefault(item, item)

    def find(self, item: str) -> str:
        root = item
        while self.parent[root] != root:
            root = self.parent[root]
        # 路径压缩
        while self.parent[item] != root:
            self.parent[item], item = root, self.parent[item]
        return root

    def union(self, left: str, right: str) -> bool:
        left_root, right_root = self.find(left), self.find(right)
        if left_root == right_root:
            return False
        self.parent[right_root] = left_root
        return True


@dataclass(frozen=True)
class IndependenceGraph:
    """发布方独立性聚类结果。"""

    cluster_of: dict[str, int]
    # 直接合并的原因，供审计说明"这两条为什么不算两个独立来源"。
    # 传递合并没有直接原因，``explain`` 会如实说明是聚类传递而来。
    merge_reasons: dict[frozenset[str], str]
    # 没有任何可用身份信号的 key：它们不能参与交叉印证（沿用原有的
    # "域名解析不出来就不能印证"语义）。
    unidentifiable: frozenset[str]

    def same_publisher(self, left: str, right: str) -> bool:
        if left in self.unidentifiable or right in self.unidentifiable:
            return False
        return self.cluster_of.get(left, -1) == self.cluster_of.get(right, -2)

    def count(self, keys: list[str]) -> int:
        """一组 key 覆盖多少个独立发布方。不可识别的 key 不计入。"""
        clusters = {
            self.cluster_of[key]
            for key in keys
            if key in self.cluster_of and key not in self.unidentifiable
        }
        return len(clusters)

    def explain(self, left: str, right: str) -> str:
        direct = self.merge_reasons.get(frozenset((left, right)))
        if direct:
            return direct
        if self.same_publisher(left, right):
            return "same_publisher_cluster"
        return ""


def cluster_sources(identities: Mapping[str, SourceIdentity]) -> IndependenceGraph:
    """按"是否同一发布方"把来源聚成簇。

    合并规则见模块 docstring。规则包含"同一 registrable domain"，因此本函数给出的
    独立来源数**恒不大于**按域名去重得到的数（单调性，有测试断言）。
    """
    union = _UnionFind()
    for key in identities:
        union.add(key)

    reasons: dict[frozenset[str], str] = {}
    # 每种判据各自建索引：首个持有该判据的 key 作为代表，后来者与它合并。
    buckets: list[tuple[str, dict[str, str]]] = [
        ("same_doi", {}),
        ("same_work_id", {}),
        ("same_title", {}),
        ("same_publisher_domain", {}),
    ]

    unidentifiable: set[str] = set()
    for key, identity in identities.items():
        signals = {
            "same_doi": normalize_doi(identity.doi),
            "same_work_id": normalize_work_id(identity.work_id),
            "same_title": normalize_title(identity.title),
            "same_publisher_domain": identity.domain.strip().casefold(),
        }
        keys_seen = author_keys(identity.authors)
        if not any(signals.values()) and not keys_seen:
            unidentifiable.add(key)
            continue
        for reason, index in buckets:
            value = signals[reason]
            if not value:
                continue
            representative = index.setdefault(value, key)
            if representative != key and union.union(representative, key):
                reasons.setdefault(frozenset((representative, key)), reason)

    # 作者重叠：不是相等关系，只能两两比对。findings 规模是十量级，O(n²) 无妨。
    author_index = {
        key: author_keys(identity.authors)
        for key, identity in identities.items()
        if key not in unidentifiable
    }
    items = [(key, keys) for key, keys in author_index.items() if keys]
    for i, (left, left_keys) in enumerate(items):
        for right, right_keys in items[i + 1 :]:
            shared = left_keys & right_keys
            if not shared:
                continue
            if union.union(left, right):
                reasons.setdefault(
                    frozenset((left, right)),
                    f"shared_authors:{','.join(sorted(shared))}",
                )

    # 稳定编号：按 key 排序后首次出现的簇拿 0、1、2…，保证同一输入下结果可复现。
    cluster_of: dict[str, int] = {}
    numbering: dict[str, int] = {}
    for key in sorted(identities):
        if key in unidentifiable:
            continue
        root = union.find(key)
        if root not in numbering:
            numbering[root] = len(numbering)
        cluster_of[key] = numbering[root]

    return IndependenceGraph(
        cluster_of=cluster_of,
        merge_reasons=reasons,
        unidentifiable=frozenset(unidentifiable),
    )
