"""Search service definitions and run-scoped, credential-free configuration snapshots."""

from __future__ import annotations

from typing import Any

from ..config import Settings
from ..observability import Tracer
from ..security import validate_provider_url
from ..tools.base import SearchTool
from ..tools.composite import MultiBackendSearch
from ..tools.key_pool import ApiKeyPoolSearch
from .dto import SearchProfileView

BUILTIN_NAMES = {
    "tavily": "Tavily",
    "brave": "Brave",
    "serper": "Serper",
    "grok": "Grok Web Search",
    "openalex": "OpenAlex",
    "arxiv": "arXiv",
}


def default_search_ids(settings: Settings) -> list[str]:
    return list(settings.search_profile_ids) or [f"builtin:{p}" for p in settings.search_backends]


def builtin_search_profiles(settings: Settings) -> list[SearchProfileView]:
    return [
        SearchProfileView(
            id=f"builtin:{provider}",
            name=name,
            provider=provider,
            builtin=True,
            endpoint=settings.xai_base_url if provider == "grok" else "",
            model=settings.xai_model if provider == "grok" else "",
        )
        for provider, name in BUILTIN_NAMES.items()
    ]


async def list_search_profiles(catalog: Any, settings: Settings) -> list[SearchProfileView]:
    stored = (
        await catalog.list_search_profiles() if hasattr(catalog, "list_search_profiles") else []
    )
    return builtin_search_profiles(settings) + stored


async def validate_search_bindings(catalog: Any, settings: Settings, ids: list[str]) -> None:
    profiles = {p.id: p for p in await list_search_profiles(catalog, settings)}
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("请选择至少一个检索档案，且不能重复")
    for profile_id in ids:
        if profile_id not in profiles or not profiles[profile_id].enabled:
            raise ValueError(f"检索档案不存在或已停用：{profile_id}")


async def freeze_search_profiles(
    catalog: Any, settings: Settings, ids: set[str]
) -> list[SearchProfileView]:
    if not ids:
        return []
    await validate_search_bindings(catalog, settings, sorted(ids))
    profiles = {p.id: p for p in await list_search_profiles(catalog, settings)}
    keys = await catalog.list_keys() if hasattr(catalog, "list_keys") else []
    frozen = []
    for profile_id in sorted(ids):
        profile = profiles[profile_id].model_copy(deep=True)
        if profile.key_ids is None:
            profile.key_ids = [k.id for k in keys if k.provider == profile.provider and k.enabled]
        else:
            priority_order = {k.id: i for i, k in enumerate(keys)}
            if any(key_id not in priority_order for key_id in profile.key_ids):
                raise ValueError(f"检索档案“{profile.name}”引用的 Key 不存在")
            profile.key_ids.sort(key=lambda key_id: priority_order[key_id])
        profile.key_order_frozen = True
        frozen.append(profile)
    return frozen


async def build_profile_tool(
    profile: SearchProfileView,
    catalog: Any,
    settings: Settings,
    *,
    only_key: str | None = None,
    tracer: Tracer | None = None,
) -> SearchTool:
    """No implicit provider fallback for a user-defined service."""
    if not profile.enabled:
        raise ValueError(f"检索档案已停用：{profile.name}")
    provider = profile.provider
    if provider == "openalex":
        from ..tools.openalex import OpenAlexSearch

        return OpenAlexSearch(
            mailto=settings.openalex_mailto,
            fulltext=settings.fulltext_enabled,
            fulltext_max_chars=settings.fulltext_max_chars,
            timeout=settings.request_timeout,
        )
    if provider == "arxiv":
        from ..tools.arxiv_search import ArxivSearch

        return ArxivSearch(
            fulltext=settings.fulltext_enabled,
            fulltext_max_chars=settings.fulltext_max_chars,
            timeout=settings.request_timeout,
        )
    if only_key is not None:
        keys = [only_key]
    elif profile.key_ids is not None:
        keys = (
            await catalog.selected_key_secrets(
                provider, profile.key_ids, preserve_order=profile.key_order_frozen
            )
            if profile.key_ids
            else []
        )
    else:
        keys = await catalog.active_keys(provider) if catalog is not None else []
    if not keys and profile.builtin and not profile.key_ids:
        field = "xai_api_key" if provider == "grok" else f"{provider}_api_key"
        fallback = getattr(settings, field, "")
        if fallback:
            keys = [fallback]
    if not keys:
        raise ValueError(f"检索档案“{profile.name}”没有可用 Key，请在角色广场添加或启用凭据")

    def factory(key: str) -> SearchTool:
        if provider == "tavily":
            from ..tools.tavily_search import TavilySearch

            return TavilySearch(key)
        if provider == "brave":
            from ..tools.brave_search import BraveSearch

            return BraveSearch(key, timeout=settings.request_timeout)
        if provider == "serper":
            from ..tools.serper_search import SerperSearch

            return SerperSearch(key, timeout=settings.request_timeout)
        if provider in {"responses", "chat_search", "grok"}:
            from ..tools.model_search import ModelSearch

            endpoint = profile.endpoint or settings.xai_base_url
            model = profile.model or settings.xai_model
            validate_provider_url(
                endpoint,
                allow_private=settings.allow_private_provider_urls,
                allowlist=settings.provider_host_allowlist,
            )
            return ModelSearch(
                key,
                endpoint=endpoint,
                model=model,
                protocol="chat_search" if provider == "chat_search" else "responses",
                timeout=settings.request_timeout,
                allow_private=settings.allow_private_provider_urls,
                tracer=tracer,
                max_input_chars=settings.llm_max_input_chars,
                max_output_tokens=settings.llm_max_output_tokens,
            )
        raise ValueError(f"不支持的检索协议：{provider}")

    return ApiKeyPoolSearch(
        provider,
        keys,
        factory,
        display_name=f"{profile.name} ({profile.id})",
        tracer=tracer,
        namespace=profile.endpoint,
    )


class MissingSearchTool(SearchTool):
    async def search(self, query: str, *, max_results: int = 5) -> list:
        raise ValueError("未配置默认检索服务，请选择检索档案或为研究角色绑定专属档案")


class SearchRuntime:
    """Own each service once, even when several roles share it."""

    def __init__(
        self,
        profiles: list[SearchProfileView],
        catalog: Any,
        settings: Settings,
        tracer: Tracer | None = None,
    ) -> None:
        self.profiles = {p.id: p for p in profiles}
        self.catalog = catalog
        self.settings = settings
        self.tracer = tracer
        self._tools: dict[str, SearchTool] = {}
        self._combinations: dict[tuple[str, ...], SearchTool] = {}
        import asyncio

        self._lock = asyncio.Lock()

    async def resolve(self, ids: list[str]) -> SearchTool:
        identity = tuple(ids)
        async with self._lock:
            if identity in self._combinations:
                return self._combinations[identity]
            selected = []
            for profile_id in ids:
                if profile_id not in self.profiles:
                    raise ValueError(f"运行快照缺少检索档案：{profile_id}")
                if profile_id not in self._tools:
                    self._tools[profile_id] = await build_profile_tool(
                        self.profiles[profile_id], self.catalog, self.settings, tracer=self.tracer
                    )
                selected.append(self._tools[profile_id])
            tool = (
                selected[0]
                if len(selected) == 1
                else MultiBackendSearch(selected, tracer=self.tracer)
            )
            self._combinations[identity] = tool
            return tool

    async def aclose(self) -> None:
        errors = []
        for tool in self._tools.values():
            try:
                await tool.aclose()
            except Exception as exc:
                errors.append(exc)
        self._tools.clear()
        self._combinations.clear()
        if errors:
            raise errors[0]
