"""OpenAlex 检索实现：学术元数据主干。

选它当主干而不是当「又一个后端」，理由有三条，都直接服务于既有的证据门禁：

1. **它给 DOI 与 work_id**。交叉印证门禁要判「≥2 个独立发布方」，而同一篇工作的
   预印本 / 期刊版 / 机构库是三个不同域——按域名判会算出伪双源。OpenAlex 的
   ``id`` 是跨库聚类后的同一工作标识，是修这个判定的前提。
2. **它给作者机构**。本项目面向的领域课题组高度集中，同一课题组的两篇论文不构成
   独立印证。机构信息在通用网页检索里根本拿不到。
3. **它给撤稿标记与开放全文位置**。前者是科学场景必须有的硬门禁输入，后者决定
   全文解析阶段能不能合法拿到 PDF。

无需 API key，但**有每日免费配额**（响应头 `x-ratelimit-limit` 约 1000 次/日，按 IP 计），
配额耗尽会返回 429 并在 UTC 午夜重置。一次研究只消耗个位数请求，正常用量远够；但共享
出口 IP（NAT / 云主机）可能被同 IP 的其他调用提前耗尽。配额耗尽抛
``OpenAlexQuotaExceeded`` 而不是裸 429——错误信息里带重置时间，否则运维看到的只是一个
不知何时会恢复的 HTTP 错误。按 ``OPENALEX_MAILTO`` 进礼貌池可换更稳定的调度。

多后端场景下这个失败是被隔离的：``MultiBackendSearch`` 只记审计事件并继续用其余后端，
只有全部后端都失败才向上抛。

直接用 httpx 调 REST 端点，不引入新依赖——本仓库用带哈希的锁文件，加依赖要重新
生成两个 lock，为一个 JSON 客户端付这个代价不值得。
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from ..models import ScholarlyMetadata, Source
from .base import SearchTool
from .oa_pdf_fulltext import OaPdfFetcher, OaPdfFulltextError

logger = logging.getLogger(__name__)

_ENDPOINT = "https://api.openalex.org/works"
_MAX_RESULTS = 50
# 只取用得到的字段。OpenAlex 的完整 work 对象很大（含全部 concepts / 全部 location），
# 不裁剪会让每次检索多传几百 KB，而这些字段没有任何下游消费者。
_SELECT = ",".join(
    (
        "id",
        "doi",
        "display_name",
        "abstract_inverted_index",
        "publication_year",
        "primary_location",
        "best_oa_location",
        "authorships",
        "cited_by_count",
        "is_retracted",
        "type",
    )
)


class OpenAlexQuotaExceeded(RuntimeError):
    """OpenAlex 每日免费配额耗尽（HTTP 429）。

    单独立一个异常类型而不是让 ``HTTPStatusError`` 冒出去，是因为这两种失败的处置
    完全不同：普通 HTTP 错误值得重试或排查，配额耗尽只能等 UTC 午夜重置或改用别的
    后端——把重置时间带进错误信息，运维才不用去翻响应头。
    """


class OpenAlexSearch(SearchTool):
    def __init__(
        self,
        *,
        mailto: str = "",
        timeout: float = 30.0,
        fulltext: bool = False,
        fulltext_max_chars: int = 12_000,
        pdf_fetcher: OaPdfFetcher | None = None,
    ) -> None:
        self._mailto = mailto.strip()
        headers = {"Accept": "application/json"}
        if self._mailto:
            # OpenAlex 明确建议在 UA 里带联系方式；这不是伪装，是进礼貌池的凭据。
            headers["User-Agent"] = f"deep-research-agent (+{self._mailto})"
        self._client = httpx.AsyncClient(timeout=timeout, headers=headers)
        self._fulltext_max_chars = fulltext_max_chars
        self._pdf_fetcher = pdf_fetcher or (OaPdfFetcher(timeout=timeout) if fulltext else None)

    @property
    def backend_name(self) -> str:
        return "OpenAlexSearch"

    async def search(self, query: str, *, max_results: int = 5) -> list[Source]:
        if max_results <= 0:
            return []
        requested = min(max_results, _MAX_RESULTS)
        params: dict[str, str | int] = {
            "search": query,
            "per-page": requested,
            "select": _SELECT,
        }
        if self._mailto:
            params["mailto"] = self._mailto
        response = await self._client.get(_ENDPOINT, params=params)
        if response.status_code == 429:
            raise OpenAlexQuotaExceeded(_quota_message(response))
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("OpenAlex returned a non-object JSON payload")
        results = payload.get("results") or []
        sources: list[Source] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            source = _to_source(item)
            if source is not None:
                if self._pdf_fetcher is not None:
                    try:
                        sources.extend(await self._fulltext_sources(source, query))
                    except (OaPdfFulltextError, RuntimeError) as exc:
                        # OA metadata/abstract remains a valid source when the
                        # optional PDF dependency or remote document is unavailable.
                        logger.warning("OA PDF unavailable for %s: %s", source.url, exc)
                        sources.append(source)
                else:
                    sources.append(source)
            if len(sources) >= requested:
                break
        return sources[:requested]

    async def aclose(self) -> None:
        await self._client.aclose()
        if self._pdf_fetcher is not None:
            await self._pdf_fetcher.aclose()

    async def _fulltext_sources(self, source: Source, query: str) -> list[Source]:
        fetcher = self._pdf_fetcher
        if fetcher is None or source.scholarly is None or not source.scholarly.oa_pdf_url:
            return [source]
        if source.scholarly.work_id.casefold().startswith("arxiv:"):
            # arXiv has a lossless LaTeX e-print path; let ArxivSearch own it
            # instead of extracting a lower-fidelity PDF copy here.
            return [source]
        required: set[str] = set()
        query_lower = query.casefold()
        if any(
            token in query_lower
            for token in ("psnr", "ssim", "benchmark", "accuracy", "指标", "结果")
        ):
            required.add("results")
        elif any(
            token in query_lower for token in ("method", "approach", "protocol", "方法", "实验")
        ):
            required.update(("method", "experiment"))
        return await fetcher.sections(
            source,
            query,
            max_chars=self._fulltext_max_chars,
            required=required,
        )


def _to_source(item: dict[str, Any]) -> Source | None:
    doi = _text(item.get("doi"))
    primary = _dict(item.get("primary_location"))
    landing = _text(primary.get("landing_page_url"))
    work_id = _text(item.get("id"))
    # 引用 URL 的优先级：DOI 最稳（永久标识、可解析到出版方当前地址），
    # 其次是落地页，最后才是 OpenAlex 自己的 work URL。
    url = _doi_url(doi) or landing or work_id
    if not url:
        return None

    abstract = _abstract_from_inverted_index(item.get("abstract_inverted_index"))
    authors, affiliations = _authorships(item.get("authorships"))
    best_oa = _dict(item.get("best_oa_location"))

    meta = ScholarlyMetadata(
        doi=_bare_doi(doi),
        work_id=work_id,
        authors=authors,
        affiliations=affiliations,
        venue=_venue(primary),
        year=_year(item.get("publication_year")),
        peer_reviewed=_peer_reviewed(primary),
        retracted=bool(item.get("is_retracted")) if "is_retracted" in item else None,
        citation_count=_int_or_none(item.get("cited_by_count")),
        oa_pdf_url=_text(best_oa.get("pdf_url")),
    )
    return Source(
        title=_text(item.get("display_name"))[:200],
        url=url,
        content=abstract,
        scholarly=meta,
    )


def _abstract_from_inverted_index(index: Any) -> str:
    """把 OpenAlex 的倒排索引摘要还原成连续文本。

    OpenAlex 出于版权考虑不直接给摘要原文，而给 ``{词: [位置…]}``。按位置排序即可
    重建词序。

    重建结果的空格与标点未必与出版方渲染完全一致，但这不影响证据链的自洽性：
    模型看到的、被哈希留证的、以及逐字校验匹配的，都是这同一份重建文本
    （``EvidenceVerifier`` 匹配的对象就是 ``Source.content``），且校验前会做
    空白归一化。也就是说这里的重建误差不会造成「引用明明在原文里却验不过」。
    """
    if not isinstance(index, dict):
        return ""
    positions: list[tuple[int, str]] = []
    for word, spots in index.items():
        if not isinstance(word, str) or not isinstance(spots, list):
            continue
        for spot in spots:
            if isinstance(spot, int) and not isinstance(spot, bool):
                positions.append((spot, word))
    positions.sort(key=lambda pair: pair[0])
    return " ".join(word for _, word in positions)


def _authorships(raw: Any) -> tuple[list[str], list[str]]:
    """抽作者名与机构名。机构去重但保持首次出现顺序，用于后续独立性判定。"""
    if not isinstance(raw, list):
        return [], []
    authors: list[str] = []
    affiliations: dict[str, None] = {}
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        author = entry.get("author")
        if isinstance(author, dict):
            name = _text(author.get("display_name"))
            if name:
                authors.append(name)
        for institution in entry.get("institutions") or []:
            if not isinstance(institution, dict):
                continue
            institution_name = _text(institution.get("display_name"))
            if institution_name:
                affiliations.setdefault(institution_name, None)
    return authors[:32], list(affiliations)[:32]


def _venue(primary: dict[str, Any]) -> str:
    container = primary.get("source")
    if isinstance(container, dict):
        return _text(container.get("display_name"))[:200]
    return ""


def _peer_reviewed(primary: dict[str, Any]) -> bool | None:
    """由 OpenAlex 的 ``version`` 推断，而不是由「有没有期刊名」推断。

    ``submittedVersion`` 就是投稿版（预印本），``acceptedVersion`` / ``publishedVersion``
    已过评审。字段缺失时返回 None——未知就是未知，不猜。
    """
    version = _text(primary.get("version")).casefold()
    if version in {"publishedversion", "acceptedversion"}:
        return True
    if version == "submittedversion":
        return False
    return None


def _doi_url(doi: str) -> str:
    bare = _bare_doi(doi)
    return f"https://doi.org/{bare}" if bare else ""


def _bare_doi(doi: str) -> str:
    value = doi.strip()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if value.casefold().startswith(prefix):
            return value[len(prefix) :]
    return value


def _year(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _quota_message(response: httpx.Response) -> str:
    """把 429 的配额头翻译成一句可直接处置的说明。

    只读响应头与已知 JSON 字段，任何缺失都退化成不带该信息的短句——不能因为
    错误响应的格式变化而在错误处理路径上再抛一个异常。
    """
    reset = response.headers.get("x-ratelimit-reset") or response.headers.get("retry-after") or ""
    limit = response.headers.get("x-ratelimit-limit") or ""
    detail = ""
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if isinstance(payload, dict):
        detail = _text(payload.get("message"))

    parts = ["OpenAlex 每日免费配额已耗尽（HTTP 429）"]
    if limit:
        parts.append(f"日配额 {limit} 次")
    if reset.isdigit():
        parts.append(f"约 {int(reset) // 3600} 小时后（UTC 午夜）重置")
    parts.append("可改用其他检索后端，或配置 OPENALEX_MAILTO 进礼貌池")
    message = "；".join(parts)
    return f"{message}。上游说明：{detail}" if detail else message


def _dict(value: Any) -> dict[str, Any]:
    """把可能缺失/类型不符的嵌套对象收敛成空字典。

    OpenAlex 的 ``primary_location`` 等字段在某些记录上是 null，逐处写 isinstance
    既啰嗦又容易漏一处——统一在入口收敛，后续访问就都是安全的。
    """
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""
