"""把 catalog（DB 中的角色卡片 / 模型档案）桥接到工作流引擎运行期。

提供两个解析器，注入到 WorkflowEngine / RunContext：
  - agent_resolver(name)：优先用 DB 角色卡片（CardAgent），否则回退代码注册表
  - llm_resolver(name)  ：按角色卡片绑定的模型档案构造专属 LLM，否则回退默认

构造 LLM 有缓存：同一档案在一次运行内只建一个 client，避免每步重复开连接池。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from ..agents.base import Agent
from ..agents.card_agent import CardAgent
from ..config import Settings
from ..llm import LLM
from ..observability import Tracer
from ..registry import create as registry_create
from ..security import validate_provider_url_resolved
from ..tools.base import SearchTool
from .dto import (
    AgentCardSnapshot,
    AgentCardView,
    CatalogRuntimeSnapshot,
    ModelProfileFull,
    ModelProfileSnapshot,
    SearchProfileView,
    WorkflowDefView,
)
from .search import SearchRuntime, default_search_ids, freeze_search_profiles


class CatalogSource(Protocol):
    """load_catalog_runtime / 编排器所需的最小 catalog 仓储接口（鸭子类型，避免硬依赖）。"""

    async def list_agents(self) -> list[AgentCardView]: ...
    async def get_profile_full(self, profile_id: str) -> ModelProfileFull | None: ...
    async def get_default_profile(self) -> ModelProfileFull | None: ...
    async def get_workflow_def(self, name: str) -> WorkflowDefView | None: ...


_BUILTIN_TERMINAL_ROLES = {"synthesizer", "aggregator"}


async def _validate_profile_endpoints(
    profiles: Iterable[ModelProfileFull], settings: Settings
) -> None:
    for profile in profiles:
        await validate_provider_url_resolved(
            profile.base_url,
            allow_private=settings.allow_private_provider_urls,
            allowlist=settings.provider_host_allowlist,
        )


def terminal_roles_for_cards(cards: Iterable[AgentCardView]) -> set[str]:
    """Return report-producing names after enabled catalog cards override built-ins."""
    overrides = {card.name: card for card in cards if card.enabled}
    terminals = _BUILTIN_TERMINAL_ROLES - overrides.keys()
    terminals.update(card.name for card in overrides.values() if card.behavior == "synthesize")
    return terminals


async def create_catalog_runtime_snapshot(
    catalog_repo: CatalogSource,
    required_roles: Iterable[str],
    settings: Settings | None = None,
) -> CatalogRuntimeSnapshot:
    """Capture non-secret catalog semantics for the roles a workflow may use."""
    required = set(required_roles)
    effective_settings = settings or Settings()
    search_defaults = default_search_ids(effective_settings)
    cards = await catalog_repo.list_agents()
    enabled = {card.name: card for card in cards if card.enabled}
    default_profile = await catalog_repo.get_default_profile()
    profile_ids = {
        card.model_profile_id
        for name, card in enabled.items()
        if name in required and card.model_profile_id
    }
    if default_profile is not None:
        profile_ids.add(default_profile.id)
    profiles: list[ModelProfileFull] = []
    for profile_id in sorted(profile_ids):
        if default_profile is not None and profile_id == default_profile.id:
            profiles.append(default_profile)
            continue
        profile = await catalog_repo.get_profile_full(profile_id)
        if profile is not None:
            profiles.append(profile)
    terminal_roles = terminal_roles_for_cards(cards)

    return CatalogRuntimeSnapshot(
        default_search_profile_ids=search_defaults,
        cards=[
            AgentCardSnapshot(
                name=card.name,
                behavior=card.behavior,
                system_prompt=card.system_prompt,
                prompt_mode=card.prompt_mode,
                search_profile_ids=card.search_profile_ids,
                model_profile_id=card.model_profile_id,
            )
            for name, card in sorted(enabled.items())
            if name in required
        ],
        profiles=[_profile_snapshot(profile) for profile in profiles],
        default_profile_id=default_profile.id if default_profile is not None else None,
        terminal_roles=sorted(terminal_roles.intersection(required | _BUILTIN_TERMINAL_ROLES)),
        search_profiles=await freeze_search_profiles(
            catalog_repo,
            effective_settings,
            {
                pid
                for name, card in enabled.items()
                if name in required
                for pid in (card.search_profile_ids or [])
            }
            | set(search_defaults),
        ),
    )


class CatalogRuntime:
    """一次运行的 catalog 解析上下文：持有 DB 快照（角色卡片 + 档案），按需建 LLM。"""

    def __init__(
        self,
        *,
        cards: list[AgentCardView],
        profiles: dict[str, ModelProfileFull],
        default_profile: ModelProfileFull | None,
        tracer: Tracer,
        settings: Settings,
        terminal_roles: set[str] | None = None,
        search_profiles: list[SearchProfileView] | None = None,
        catalog_repo: CatalogSource | None = None,
        default_search_profile_ids: list[str] | None = None,
    ) -> None:
        self._cards = {c.name: c for c in cards if c.enabled}
        self._profiles = profiles  # profile_id -> full
        self._default_profile = default_profile
        self._terminal_roles = (
            set(terminal_roles)
            if terminal_roles is not None
            else terminal_roles_for_cards(self._cards.values())
        )
        self._tracer = tracer
        self._settings = settings
        self._llm_cache: dict[str, LLM] = {}  # profile_id -> LLM（运行内复用）
        self.search_runtime = SearchRuntime(search_profiles or [], catalog_repo, settings, tracer)
        self._default_search_ids = (
            list(settings.search_profile_ids)
            if default_search_profile_ids is None
            else default_search_profile_ids
        )

    async def resolve_search(
        self, agent_name: str, *, include_default: bool = True
    ) -> SearchTool | None:
        card = self._cards.get(agent_name)
        if not include_default and (card is None or card.search_profile_ids is None):
            return None
        ids = (
            card.search_profile_ids
            if card and card.search_profile_ids is not None
            else self._default_search_ids
        )
        if not ids:
            return None
        tool = await self.search_runtime.resolve(ids)
        self._tracer.emit(
            "RESEARCHER",
            "info",
            f"角色 {agent_name} 使用检索服务：{tool.backend_name}",
            data={"category": "search_binding", "role": agent_name, "profile_ids": ids},
        )
        return tool

    # ── 角色解析 ────────────────────────────────────────────────────────
    def resolve_agent(self, name: str) -> Agent:
        card = self._cards.get(name)
        if card is not None:
            return CardAgent(
                name=card.name,
                behavior=card.behavior,
                system_prompt=card.system_prompt,
                prompt_mode=card.prompt_mode,
            )
        return registry_create(name)  # 回退内置注册表

    @property
    def terminal_roles(self) -> set[str]:
        return set(self._terminal_roles)

    def snapshot(self, required_roles: Iterable[str]) -> CatalogRuntimeSnapshot:
        """Serialize this runtime's role semantics without profile credentials."""
        required = set(required_roles)
        return CatalogRuntimeSnapshot(
            default_search_profile_ids=list(self._default_search_ids),
            cards=[
                AgentCardSnapshot(
                    name=card.name,
                    behavior=card.behavior,
                    system_prompt=card.system_prompt,
                    prompt_mode=card.prompt_mode,
                    search_profile_ids=card.search_profile_ids,
                    model_profile_id=card.model_profile_id,
                )
                for name, card in sorted(self._cards.items())
                if name in required
            ],
            profiles=[_profile_snapshot(profile) for _, profile in sorted(self._profiles.items())]
            + (
                [_profile_snapshot(self._default_profile)]
                if self._default_profile is not None
                and self._default_profile.id not in self._profiles
                else []
            ),
            default_profile_id=(
                self._default_profile.id if self._default_profile is not None else None
            ),
            terminal_roles=sorted(
                self._terminal_roles.intersection(required | _BUILTIN_TERMINAL_ROLES)
            ),
            search_profiles=list(self.search_runtime.profiles.values()),
        )

    @property
    def has_default_profile(self) -> bool:
        """Whether this runtime can replace the process-wide LLM fallback."""
        return self._default_profile is not None

    # ── 模型解析 ────────────────────────────────────────────────────────
    def resolve_llm(self, agent_name: str) -> LLM | None:
        """角色卡片绑定了档案 → 用该档案的 LLM；否则 None（由 RunContext 回退默认）。"""
        card = self._cards.get(agent_name)
        profile: ModelProfileFull | None = None
        if card is not None and card.model_profile_id:
            profile = self._profiles.get(card.model_profile_id)
        if profile is None:
            profile = self._default_profile  # 卡片未绑则用全局默认档案
        if profile is None:
            return None
        return self._build_llm(profile)

    def _build_llm(self, profile: ModelProfileFull) -> LLM:
        cached = self._llm_cache.get(profile.id)
        if cached is not None:
            return cached
        llm = LLM.from_params(
            self._tracer,
            api_key=profile.api_key,
            base_url=profile.base_url,
            model=profile.model,
            timeout=self._settings.request_timeout,
            user_agent=self._settings.llm_user_agent,
            temperature=profile.temperature,
            parameter_mode=profile.parameter_mode,
            reasoning_effort=profile.reasoning_effort,
            allow_private_provider_urls=self._settings.allow_private_provider_urls,
        )
        self._llm_cache[profile.id] = llm
        return llm

    async def aclose(self) -> None:
        """释放本运行期新建的所有 LLM client 连接池。"""
        errors: list[Exception] = []
        try:
            await self.search_runtime.aclose()
        except Exception as exc:
            errors.append(exc)
        for llm in self._llm_cache.values():
            try:
                await llm.aclose()
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise errors[0]


async def load_catalog_runtime(
    catalog_repo: CatalogSource | None,
    tracer: Tracer,
    settings: Settings,
    *,
    snapshot: CatalogRuntimeSnapshot | dict[str, object] | None = None,
) -> CatalogRuntime | None:
    """从 CatalogRepository 拉取快照，构造 CatalogRuntime；repo 为空或无数据时返回 None。"""
    if snapshot is not None:
        if not isinstance(snapshot, CatalogRuntimeSnapshot):
            snapshot = CatalogRuntimeSnapshot.model_validate(snapshot)
        cards = [
            AgentCardView(
                id=f"snapshot:{card.name}",
                name=card.name,
                behavior=card.behavior,
                system_prompt=card.system_prompt,
                prompt_mode=card.prompt_mode,
                search_profile_ids=card.search_profile_ids,
                enabled=True,
                model_profile_id=card.model_profile_id,
            )
            for card in snapshot.cards
        ]
        profile_ids = {card.model_profile_id for card in snapshot.cards if card.model_profile_id}
        if snapshot.default_profile_id:
            profile_ids.add(snapshot.default_profile_id)
        frozen_profiles = {profile.id: profile for profile in snapshot.profiles}
        snapshot_profiles: dict[str, ModelProfileFull] = {}
        if catalog_repo is not None:
            for profile_id in profile_ids:
                live_profile = await catalog_repo.get_profile_full(profile_id)
                frozen_profile = frozen_profiles.get(profile_id)
                if live_profile is not None and frozen_profile is not None:
                    snapshot_profiles[profile_id] = ModelProfileFull(
                        **frozen_profile.model_dump(), api_key=live_profile.api_key
                    )
                elif live_profile is not None:
                    snapshot_profiles[live_profile.id] = live_profile
        default_profile = (
            snapshot_profiles.get(snapshot.default_profile_id)
            if snapshot.default_profile_id is not None
            else None
        )
        await _validate_profile_endpoints(snapshot_profiles.values(), settings)
        search_defaults = snapshot.default_search_profile_ids
        search_profiles = snapshot.search_profiles
        if search_defaults is None:
            # Pre-upgrade checkpoints contain no search definitions. Retain their
            # legacy provider configuration instead of adopting newly selected profiles.
            search_defaults = [f"builtin:{p}" for p in settings.search_backends]
            legacy = await freeze_search_profiles(catalog_repo, settings, set(search_defaults))
            search_profiles = search_profiles + [
                p for p in legacy if p.id not in {s.id for s in search_profiles}
            ]
        return CatalogRuntime(
            cards=cards,
            profiles=snapshot_profiles,
            default_profile=default_profile,
            tracer=tracer,
            settings=settings,
            terminal_roles=set(snapshot.terminal_roles),
            search_profiles=search_profiles,
            catalog_repo=catalog_repo,
            default_search_profile_ids=search_defaults,
        )

    if catalog_repo is None:
        if settings.search_profile_ids:
            return CatalogRuntime(
                cards=[],
                profiles={},
                default_profile=None,
                tracer=tracer,
                settings=settings,
                search_profiles=await freeze_search_profiles(
                    None, settings, set(settings.search_profile_ids)
                ),
            )
        return None
    cards = await catalog_repo.list_agents()
    search_defaults = default_search_ids(settings)
    # A global default profile is useful even when no custom role cards exist:
    # it supplies the model for built-in roles without requiring LLM_API_KEY.
    default_profile = await catalog_repo.get_default_profile()
    # 收集所有被引用的档案 + 默认档案
    profiles: dict[str, ModelProfileFull] = {}
    for c in cards:
        if c.enabled and c.model_profile_id and c.model_profile_id not in profiles:
            full = await catalog_repo.get_profile_full(c.model_profile_id)
            if full is not None:
                profiles[full.id] = full
    validation_profiles = list(profiles.values())
    if default_profile is not None and default_profile.id not in profiles:
        validation_profiles.append(default_profile)
    await _validate_profile_endpoints(validation_profiles, settings)
    return CatalogRuntime(
        cards=cards,
        profiles=profiles,
        default_profile=default_profile,
        tracer=tracer,
        settings=settings,
        search_profiles=await freeze_search_profiles(
            catalog_repo,
            settings,
            {pid for card in cards if card.enabled for pid in (card.search_profile_ids or [])}
            | set(search_defaults),
        ),
        catalog_repo=catalog_repo,
        default_search_profile_ids=search_defaults,
    )


def _profile_snapshot(profile: ModelProfileFull) -> ModelProfileSnapshot:
    return ModelProfileSnapshot(
        id=profile.id,
        name=profile.name,
        base_url=profile.base_url,
        model=profile.model,
        temperature=profile.temperature,
        parameter_mode=profile.parameter_mode,
        reasoning_effort=profile.reasoning_effort,
        is_default=profile.is_default,
    )
