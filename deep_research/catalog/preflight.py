"""Read-only resource checks. Never send a provider request or expose secrets."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..config import Settings
from .dto import CatalogRuntimeSnapshot
from .search import build_profile_tool


class ResourcePreflight(BaseModel):
    ok: bool = True
    workflow: str = ""
    roles: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


async def inspect_snapshot(
    catalog: Any,
    settings: Settings,
    snapshot: CatalogRuntimeSnapshot,
    required_roles: set[str],
    *,
    workflow: str = "",
) -> ResourcePreflight:
    result = ResourcePreflight(workflow=workflow)
    cards = {card.name: card for card in snapshot.cards}
    profiles = {profile.id: profile for profile in snapshot.search_profiles}
    models = {profile.id: profile for profile in snapshot.profiles}
    checked: dict[str, str | None] = {}
    key_counts: dict[str, int] = {}
    for role in sorted(required_roles):
        card = cards.get(role)
        research = card.behavior == "research" if card else role == "researcher"
        model_id = (
            card.model_profile_id if card and card.model_profile_id else snapshot.default_profile_id
        )
        model = models.get(model_id) if model_id else None
        ids = (
            (
                card.search_profile_ids
                if card and card.search_profile_ids is not None
                else snapshot.default_search_profile_ids or []
            )
            if research
            else []
        )
        row: dict[str, Any] = {
            "role": role,
            "model": model.model if model else settings.llm_model,
            "model_profile": model.name if model else "全局默认模型",
            "search_profiles": [],
            "inherits_search": card is None or card.search_profile_ids is None,
        }
        if research and not ids:
            result.errors.append(f"角色 {role} 未配置检索服务")
        for profile_id in ids:
            profile = profiles.get(profile_id)
            if profile_id not in checked:
                error = None
                if profile is None:
                    error = f"检索档案不存在：{profile_id}"
                else:
                    try:
                        # Construction resolves live credentials; clients are lazy
                        # or closed immediately, without a search/network probe.
                        tool = await build_profile_tool(profile, catalog, settings)
                        key_counts[profile_id] = getattr(tool, "credential_count", 0)
                        await tool.aclose()
                    except ValueError as exc:
                        error = str(exc)
                checked[profile_id] = error
                if error and profile is not None and profile.builtin:
                    # Built-in profiles retain the legacy environment fallback;
                    # an empty catalog pool is therefore a warning rather than
                    # a new submission blocker.
                    result.warnings.append(
                        f"内置档案 {profile.name} 未找到 Catalog Key，将使用环境配置。"
                    )
                    checked[profile_id] = None
            row["search_profiles"].append(
                {
                    "id": profile_id,
                    "name": profile.name if profile else profile_id,
                    "ready": checked[profile_id] is None,
                    "key_count": key_counts.get(profile_id, 0),
                }
            )
            if checked[profile_id]:
                result.errors.append(f"角色 {role}：{checked[profile_id]}")
        result.roles.append(row)
    result.errors = list(dict.fromkeys(result.errors))
    result.ok = not result.errors
    result.warnings.append("配置检查不发起联网请求；端点连通性和账号额度请在检索档案中测试。")
    return result


async def preflight_workflow(
    catalog: Any, settings: Settings, workflow_name: str
) -> ResourcePreflight:
    from ..orchestrator import workflow_catalog_roles
    from ..workflow import Workflow
    from ..workflows import WORKFLOWS, get_workflow
    from .runtime import create_catalog_runtime_snapshot

    try:
        if workflow_name in WORKFLOWS:
            workflow = get_workflow(workflow_name)
        else:
            custom = await catalog.get_workflow_def(workflow_name)
            if custom is None or not custom.enabled:
                raise ValueError("工作流不存在或已停用")
            workflow = Workflow.model_validate(custom.model_dump())
        required = workflow_catalog_roles(workflow)
        snapshot = await create_catalog_runtime_snapshot(catalog, required, settings)
        return await inspect_snapshot(catalog, settings, snapshot, required, workflow=workflow_name)
    except ValueError as exc:
        return ResourcePreflight(ok=False, workflow=workflow_name, errors=[str(exc)])
