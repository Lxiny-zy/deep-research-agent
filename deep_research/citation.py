"""学术引用渲染。

单点实现的理由：参考来源列表、表格脚注与 CSV 导出引用的是同一条来源，三处各
渲染一遍必然漂移（尤其是「作者截断到几位」「撤稿怎么标」这类口径）。

渲染规则只有一条：**只用已经抽到的字段**。缺作者就不写作者，缺年份就不写年份，
绝不用「n.d.」「Anonymous」这类占位符——占位符会让缺失看起来像已知。

URL 一律放在行尾且不带尾随标点。这是与前端引用回退解析器
（``frontend/src/lib/evidence.ts`` 的 ``resolveCitationTargets``）之间的格式契约：
流式阶段还没有 ``report.citations`` 时，前端要从「## 参考来源」里把 URL 抠出来。
"""

from __future__ import annotations

from .models import ScholarlyMetadata

# 作者列表截断阈值。学术惯例是 3 位以内全列、超出用「等」，与期刊风格一致，
# 也避免 30 位作者的高能物理式署名把参考来源列表撑爆。
_MAX_AUTHORS = 3


def format_reference(url: str, meta: ScholarlyMetadata | None, *, title: str = "") -> str:
    """渲染一条学术参考来源；没有学术元数据时返回空串。

    空串是明确的「无学术引用可用」信号，调用方据此回退到裸 URL
    （``Synthesizer._finalize`` 就是这么做的）。这样通用网页调研报告的参考来源
    段落与改造前**逐字节一致**——学术元数据是增量能力，不该改变既有产物。
    """
    if meta is None:
        return ""

    parts: list[str] = []
    if meta.authors:
        shown = [a.strip() for a in meta.authors[:_MAX_AUTHORS] if a.strip()]
        if shown:
            authors = ", ".join(shown)
            if len(meta.authors) > len(shown):
                authors += " 等"
            parts.append(authors)

    if title.strip():
        parts.append(title.strip())

    venue_year = ", ".join(x for x in (meta.venue.strip(), _year(meta)) if x)
    if venue_year:
        parts.append(venue_year)

    # URL 本身就是 DOI 解析地址时不再单列 doi: 段——同一个标识印两遍只是噪声。
    # 反过来，arXiv 记录的 URL 是 abs 页而 DOI 指向期刊版，这时 doi: 是额外信息，要留。
    bare = _bare_doi(meta.doi)
    if bare and url.strip().casefold() != f"https://doi.org/{bare}".casefold():
        parts.append(f"doi:{bare}")

    for flag in _status_flags(meta):
        parts.append(flag)

    body = ". ".join(parts)
    return f"{body}. {url}" if body else url


def _year(meta: ScholarlyMetadata) -> str:
    return str(meta.year) if meta.year else ""


def _bare_doi(doi: str) -> str:
    """去掉 DOI 的解析器前缀，只留 ``10.xxxx/yyyy`` 本体。"""
    value = doi.strip()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if value.casefold().startswith(prefix):
            value = value[len(prefix) :]
            break
    return value


def _status_flags(meta: ScholarlyMetadata) -> list[str]:
    """需要读者立刻看到的状态标记。

    撤稿必须出现在引用里而不只在审计事件里：报告是给人看的最终产物，一条来自
    撤稿论文的引用若在正文中与其他引用长得一样，读者没有任何机会发现。
    """
    flags: list[str] = []
    if meta.retracted:
        flags.append("【已撤稿】")
    if meta.peer_reviewed is False:
        version = f" {meta.version}" if meta.version else ""
        flags.append(f"【预印本{version}】")
    return flags
