"""多检索后端并发合并。

设计要点，都是为了不破坏既有的证据链语义：

* **并发而非串行**：墙钟 ≈ 最慢的一个后端，而不是求和；
* **部分失败不阻断**：一个后端挂掉只记审计事件，仍返回其余后端的结果。全部失败
  才向上抛——那才是「这次检索没拿到东西」；
* **归一化去重**：同一 URL 被多个后端返回时只保留一份。去重发生在**进入来源策略
  门禁之前**，因此不会让同一个页面在交叉印证时被当成两个独立发布方——独立发布方
  的判定仍由 guardrails 按 registrable domain 进行，本模块不碰那套规则。
"""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlsplit, urlunsplit

from ..models import Source
from ..observability import Tracer
from .base import SearchTool

logger = logging.getLogger(__name__)

# 不影响定位、只影响去重的查询参数（跟踪码）。剥掉它们能让同一页面的不同
# 来路链接归并成一条，否则会凭空多出「独立来源」。
_TRACKING_PARAMS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "gclid",
        "fbclid",
        "ref",
        "ref_src",
        "spm",
    }
)


def normalize_url(url: str) -> str:
    """URL 归一化，仅用于**去重**，不改写实际访问与引用的 URL。

    只做无争议的等价变换：小写 scheme/host、去掉默认端口、剥离跟踪参数与 fragment。
    路径大小写与末尾斜杠保持原样——它们在部分站点上是有意义的。
    """
    try:
        parts = urlsplit(url.strip())
        host = parts.hostname or ""
        port = parts.port
    except ValueError:
        return url.strip()
    if not parts.scheme or not parts.netloc:
        return url.strip()
    default_port = {"http": 80, "https": 443}.get(parts.scheme.lower())
    netloc = host.lower()
    if ":" in netloc and not netloc.startswith("["):
        netloc = f"[{netloc}]"
    if port is not None and port != default_port:
        netloc = f"{netloc}:{port}"
    query = "&".join(
        piece
        for piece in parts.query.split("&")
        if piece and piece.split("=", 1)[0].lower() not in _TRACKING_PARAMS
    )
    return urlunsplit((parts.scheme.lower(), netloc, parts.path, query, ""))


class MultiBackendSearch(SearchTool):
    """并发查询多个后端并合并去重。"""

    def __init__(self, backends: list[SearchTool], *, tracer: Tracer | None = None) -> None:
        if not backends:
            raise ValueError("MultiBackendSearch 至少需要一个后端")
        self._backends = list(backends)
        self._tracer = tracer

    @property
    def backend_name(self) -> str:
        # 进 run manifest：可复现实验必须能区分「单后端跑的」与「双后端跑的」。
        return "+".join(backend.backend_name for backend in self._backends)

    async def search(self, query: str, *, max_results: int = 5) -> list[Source]:
        if max_results <= 0:
            return []
        results = await asyncio.gather(
            *(backend.search(query, max_results=max_results) for backend in self._backends),
            return_exceptions=True,
        )
        merged: dict[str, Source] = {}
        failures: list[str] = []
        for backend, result in zip(self._backends, results, strict=True):
            if isinstance(result, BaseException):
                if isinstance(result, asyncio.CancelledError):
                    raise result
                failures.append(backend.backend_name)
                logger.warning(
                    "search backend %s failed: %s", backend.backend_name, result, exc_info=result
                )
                self._emit(f"检索后端 {backend.backend_name} 失败，已跳过：{result}")
                continue
            for source in result:
                key = normalize_url(source.url) or source.url.strip()
                # 先到先得：同一 URL 保留最先返回的那份内容，避免同页多份摘要
                # 在证据验证时相互干扰。
                if key not in merged:
                    merged[key] = source
        if failures and len(failures) == len(self._backends):
            raise RuntimeError(f"所有检索后端均失败：{', '.join(failures)}")
        return list(merged.values())

    def _emit(self, message: str) -> None:
        if self._tracer is not None:
            self._tracer.emit("RESEARCHER", "error", message)

    async def aclose(self) -> None:
        for backend in self._backends:
            try:
                await backend.aclose()
            except Exception:
                logger.exception("failed to close search backend %s", backend.backend_name)
