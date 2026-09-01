"""发布方独立性：同一篇工作、同一个团队不能算成两个独立来源。

改造前按 registrable domain 判独立性，文献场景下会给出**错误结论**——一篇 CASSI
工作常同时存在 arXiv 预印本、期刊正式版与机构库副本，三个域一篇工作，"已交叉印证"
这个结论本身是假的。

测试分三层:

1. **归一化**——DOI / work_id / 标题 / 作者名各自的等价类边界；
2. **聚类**——五条合并规则各自生效，以及最关键的**单调性**（新逻辑恒不比旧逻辑
   给出更多独立来源，因此结构上不可能放宽门禁）；
3. **门禁衔接**——伪双源被降级为单源，且原因里写明"看到了第二个来源但同源"。
"""

from __future__ import annotations

import pytest

from deep_research.guardrails import _identity_for, publisher_identity
from deep_research.independence import (
    author_key,
    cluster_sources,
    normalize_doi,
    normalize_title,
    normalize_work_id,
)
from deep_research.models import (
    EvidenceVerification,
    Finding,
    ScholarlyMetadata,
    Source,
    SourceIdentity,
)


def _identity(
    *,
    doi: str = "",
    work_id: str = "",
    title: str = "",
    authors: list[str] | None = None,
    domain: str = "",
) -> SourceIdentity:
    return SourceIdentity(
        doi=doi, work_id=work_id, title=title, authors=authors or [], domain=domain
    )


# ── 归一化 ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw",
    [
        "10.1364/OE.456",
        "https://doi.org/10.1364/oe.456",
        "http://doi.org/10.1364/OE.456",
        "https://dx.doi.org/10.1364/oe.456",
        "doi:10.1364/OE.456",
        "  10.1364/oe.456  ",
        "10.1364/oe.456.",
    ],
)
def test_doi_variants_normalise_to_one_key(raw: str) -> None:
    """DOI 按规范大小写不敏感，且解析器前缀有多种写法。"""
    assert normalize_doi(raw) == "10.1364/oe.456"


def test_arxiv_versions_are_the_same_work() -> None:
    """v1 与 v3 是同一篇论文的两个版本，算成两个独立来源是错的。"""
    assert normalize_work_id("arxiv:2205.10102v1") == normalize_work_id("arxiv:2205.10102v3")
    assert normalize_work_id("arxiv:2205.10102") == "arxiv:2205.10102"


def test_openalex_url_prefix_is_stripped() -> None:
    assert normalize_work_id("https://openalex.org/W123") == "w123"
    assert normalize_work_id("W123") == "w123"


def test_title_normalisation_ignores_punctuation_case_and_wrapping() -> None:
    a = normalize_title("Mask-guided Spectral-wise Transformer for Efficient Reconstruction")
    b = normalize_title("mask guided spectral wise transformer   for efficient\nreconstruction")

    assert a == b and a


def test_short_or_empty_titles_are_not_usable_as_identity() -> None:
    """守卫退化合并:若一批来源标题都为空，空标题会把它们全并成一簇，
    独立来源数塌成 1，严格门禁下报告会没有素材——指标彻底失去意义。
    """
    assert normalize_title("") == ""
    assert normalize_title("MST") == ""
    assert normalize_title("   ...   ") == ""


def test_cjk_titles_survive_normalisation() -> None:
    assert normalize_title("基于衍射光学元件的快照式高光谱成像") != ""


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Yuanhao Cai", "cai_y"),
        ("Y. Cai", "cai_y"),
        ("Cai, Yuanhao", "cai_y"),
        ("  yuanhao   cai  ", "cai_y"),
        ("David Brady", "brady_d"),
        ("蔡远昊", "蔡远昊"),  # 连写姓名整体作键
        ("Madonna", "madonna"),
        ("", ""),
        ("   ", ""),
    ],
)
def test_author_key_folds_common_name_forms(name: str, expected: str) -> None:
    """刻意偏向合并:``Y. Cai`` 与 ``Yuanhao Cai`` 归一到同一键。

    偏向合并 = 把两个人当成一个 = 低估独立来源数 = 失败关闭。
    """
    assert author_key(name) == expected


def test_multi_word_surnames_fold_consistently() -> None:
    """ "van der Berg" 取末词作姓——只要前后一致，判定就稳定。"""
    assert author_key("Jan van der Berg") == author_key("J. van der Berg")


# ── 聚类:五条合并规则 ──────────────────────────────────────────────────────


def test_same_doi_across_two_domains_is_one_publisher() -> None:
    """预印本域与期刊域同 DOI —— 这正是伪双源的典型形态。"""
    graph = cluster_sources(
        {
            "preprint": _identity(doi="10.1364/oe.1", domain="arxiv.org"),
            "journal": _identity(doi="https://doi.org/10.1364/OE.1", domain="opg.optica.org"),
        }
    )

    assert graph.same_publisher("preprint", "journal")
    assert graph.count(["preprint", "journal"]) == 1
    assert graph.explain("preprint", "journal") == "same_doi"


def test_same_work_id_across_versions_is_one_publisher() -> None:
    graph = cluster_sources(
        {
            "v1": _identity(work_id="arxiv:2205.10102v1", domain="arxiv.org"),
            "v3": _identity(work_id="arxiv:2205.10102v3", domain="export.arxiv.org"),
        }
    )

    assert graph.count(["v1", "v3"]) == 1
    assert graph.explain("v1", "v3") == "same_work_id"


def test_same_title_catches_a_preprint_without_the_journal_doi() -> None:
    """预印本常没有期刊 DOI，标题是补这个漏的判据。"""
    title = "Degradation-Aware Unfolding Half-Shuffle Transformer for Spectral Imaging"
    graph = cluster_sources(
        {
            "arxiv": _identity(title=title, domain="arxiv.org"),
            "proceedings": _identity(doi="10.1/x", title=title.upper(), domain="neurips.cc"),
        }
    )

    assert graph.count(["arxiv", "proceedings"]) == 1
    assert graph.explain("arxiv", "proceedings") == "same_title"


def test_shared_author_makes_two_different_works_one_publisher() -> None:
    """同一课题组的两篇不同论文互相印证不构成独立验证。

    这一条同时补了"标题在预印本与终稿间被改过"的漏——作者不变。
    """
    graph = cluster_sources(
        {
            "mst": _identity(
                doi="10.1/mst",
                title="Mask-guided Spectral-wise Transformer",
                authors=["Yuanhao Cai", "Jing Lin"],
                domain="arxiv.org",
            ),
            "dauhst": _identity(
                doi="10.2/dauhst",
                title="Degradation-Aware Unfolding Half-Shuffle Transformer",
                authors=["Y. Cai", "Xiaowan Hu"],
                domain="openreview.net",
            ),
        }
    )

    assert graph.count(["mst", "dauhst"]) == 1
    assert graph.explain("mst", "dauhst").startswith("shared_authors:cai_y")


def test_same_domain_still_merges_preserving_the_previous_behaviour() -> None:
    """规则 5 保留旧行为作为下界，这是单调性的来源。"""
    graph = cluster_sources(
        {
            "a": _identity(title="A completely different paper title", domain="example.com"),
            "b": _identity(title="Another entirely unrelated title", domain="example.com"),
        }
    )

    assert graph.count(["a", "b"]) == 1
    assert graph.explain("a", "b") == "same_publisher_domain"


def test_genuinely_independent_works_stay_independent() -> None:
    """门禁不能把一切都合并——真正独立的两篇必须仍算两个来源。"""
    graph = cluster_sources(
        {
            "duke": _identity(
                doi="10.1364/oe.1",
                title="Single disperser design for coded aperture snapshot imaging",
                authors=["Ashwin Wagadarikar", "David Brady"],
                domain="opg.optica.org",
            ),
            "tsinghua": _identity(
                doi="10.1109/cvpr.2",
                title="Mask-guided Spectral-wise Transformer for reconstruction",
                authors=["Yuanhao Cai", "Jing Lin"],
                domain="ieee.org",
            ),
        }
    )

    assert not graph.same_publisher("duke", "tsinghua")
    assert graph.count(["duke", "tsinghua"]) == 2
    assert graph.explain("duke", "tsinghua") == ""


# ── 聚类:传递性与单调性 ────────────────────────────────────────────────────


def test_author_overlap_is_transitive_across_the_collaboration_graph() -> None:
    """A–B 共享一位作者、B–C 共享另一位 → A/B/C 同簇。

    传递闭包在极端情况下会过度合并，但那是失败关闭:低估独立来源数只造成
    可用性损失，不会放行伪双源。
    """
    graph = cluster_sources(
        {
            "a": _identity(doi="10.1/a", authors=["Alice Adams"], domain="a.test"),
            "b": _identity(doi="10.1/b", authors=["Alice Adams", "Bob Brown"], domain="b.test"),
            "c": _identity(doi="10.1/c", authors=["Bob Brown"], domain="c.test"),
        }
    )

    assert graph.count(["a", "b", "c"]) == 1


def test_cluster_count_never_exceeds_the_distinct_domain_count() -> None:
    """**单调性**:新逻辑恒不比旧逻辑给出更多独立来源。

    旧逻辑 = 按 registrable domain 去重。新逻辑保留"同域名即同发布方"作为规则之一，
    只是额外增加了合并条件，所以簇数 ≤ 域名数。这条性质意味着本次改动在结构上
    不可能放宽交叉印证门禁——不依赖任何一条规则的正确性。
    """
    cases: list[dict[str, SourceIdentity]] = [
        {
            "a": _identity(doi="10.1/x", domain="arxiv.org"),
            "b": _identity(doi="10.1/x", domain="optica.org"),
            "c": _identity(doi="10.2/y", domain="ieee.org"),
        },
        {
            "a": _identity(authors=["Alice Adams"], domain="a.test"),
            "b": _identity(authors=["Alice Adams"], domain="b.test"),
            "c": _identity(authors=["Zoe Zhang"], domain="c.test"),
            "d": _identity(authors=["Zoe Zhang"], domain="c.test"),
        },
        {
            "a": _identity(title="A sufficiently long and distinctive title", domain="x.test"),
            "b": _identity(title="A sufficiently long and distinctive title", domain="y.test"),
        },
        {
            "a": _identity(domain="only.test"),
            "b": _identity(domain="other.test"),
        },
    ]
    for identities in cases:
        keys = list(identities)
        domains = {i.domain for i in identities.values() if i.domain}
        assert cluster_sources(identities).count(keys) <= len(domains)


def test_clustering_is_deterministic_regardless_of_insertion_order() -> None:
    """判定必须可复现:同一组来源换个顺序不能得出不同的独立来源数。"""
    base = {
        "a": _identity(doi="10.1/x", domain="arxiv.org"),
        "b": _identity(doi="10.1/x", domain="optica.org"),
        "c": _identity(doi="10.2/y", domain="ieee.org"),
    }
    reversed_order = dict(reversed(list(base.items())))

    assert cluster_sources(base).count(list(base)) == cluster_sources(reversed_order).count(
        list(reversed_order)
    )
    assert cluster_sources(base).cluster_of == cluster_sources(reversed_order).cluster_of


# ── 不可识别的来源 ─────────────────────────────────────────────────────────


def test_a_source_with_no_identity_signal_cannot_corroborate() -> None:
    """沿用"域名解析不出来就不参与印证"的语义。"""
    graph = cluster_sources(
        {
            "known": _identity(doi="10.1/x", domain="arxiv.org"),
            "nameless": _identity(),
        }
    )

    assert "nameless" in graph.unidentifiable
    assert not graph.same_publisher("known", "nameless")
    # 不可识别的来源既不合并也不计数
    assert graph.count(["known", "nameless"]) == 1


def test_empty_titles_do_not_collapse_everything_into_one_cluster() -> None:
    """退化合并的守卫:三个无标题但域名不同的来源仍是三个发布方。"""
    graph = cluster_sources(
        {
            "a": _identity(title="", domain="a.test"),
            "b": _identity(title="   ", domain="b.test"),
            "c": _identity(title="MST", domain="c.test"),
        }
    )

    assert graph.count(["a", "b", "c"]) == 3


# ── 与证据门禁的衔接 ───────────────────────────────────────────────────────


def test_verifier_stamps_the_source_identity() -> None:
    """身份必须在验证时刻抓取——那是唯一同时握有 Finding 与 Source 的时刻。"""
    from deep_research.guardrails import EvidenceVerifier

    source = Source(
        title="Mask-guided Spectral-wise Transformer",
        url="https://arxiv.org/abs/2205.10102v3",
        content="We report a PSNR of 35.18 dB on the KAIST benchmark.",
        scholarly=ScholarlyMetadata(
            doi="10.1109/cvpr.1", work_id="arxiv:2205.10102", authors=["Yuanhao Cai", "Jing Lin"]
        ),
    )
    candidate = Finding(
        statement="达到 35.18 dB",
        source_url=source.url,
        evidence_quote="PSNR of 35.18 dB on the KAIST benchmark",
    )

    check = EvidenceVerifier().verify(candidate, source)

    assert check.finding is not None
    identity = check.finding.verification.source_identity
    assert identity is not None
    assert identity.doi == "10.1109/cvpr.1"
    assert identity.work_id == "arxiv:2205.10102"
    assert identity.authors == ["Yuanhao Cai", "Jing Lin"]
    assert identity.domain == "arxiv.org"
    assert identity.title == "Mask-guided Spectral-wise Transformer"


def test_a_legacy_finding_falls_back_to_domain_only() -> None:
    """旧记录退回只按域名——本项目把"判定可从存下来的输入复现"当硬性质，
    所以宁可让旧数据保持旧口径，也不借 source_title 做局部升级。
    """
    legacy = Finding(
        statement="旧论断",
        source_url="https://arxiv.org/abs/1234.5678",
        evidence_quote="q",
        verification=EvidenceVerification(source_title="某个标题够长可以当判据的论文"),
    )

    identity = _identity_for(legacy)

    assert identity.domain == "arxiv.org"
    assert identity.doi == "" and identity.work_id == "" and identity.authors == []
    # 关键:标题没有被借用，否则旧 run 重新判定会得出与已落库不同的结论
    assert identity.title == ""


def test_web_only_sources_reproduce_the_previous_domain_semantics() -> None:
    """没有学术元数据的通用网页来源:行为必须与改造前完全一致。"""
    graph = cluster_sources(
        {
            "a": _identity(domain=publisher_identity("https://blog.example.com/a")),
            "b": _identity(domain=publisher_identity("https://news.example.com/b")),
            "c": _identity(domain=publisher_identity("https://other.test/c")),
        }
    )

    # example.com 的两个子域仍是同一发布方（原有行为），other.test 独立
    assert graph.count(["a", "b", "c"]) == 2
