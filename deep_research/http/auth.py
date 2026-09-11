"""Authorization at the HTTP boundary, including every run download and SSE route."""

from __future__ import annotations

from fastapi import Header, HTTPException, Request

from ..access import Principal, authenticate
from ..config_service import effective_settings


def principal_for(request: Request) -> Principal:
    return getattr(request.state, "principal", Principal("local", "admin"))


async def require_api_key(
    request: Request,
    x_api_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> Principal:
    settings = request.app.state.settings
    scheme, _, credential = (authorization or "").partition(" ")
    bearer = credential.strip() if scheme.casefold() == "bearer" else ""
    principal = authenticate(
        settings.api_key,
        settings.api_credentials,
        [value for value in (bearer, x_api_key or "") if value],
    )
    if principal is None:
        raise HTTPException(
            401, "invalid or missing API key", headers={"WWW-Authenticate": "Bearer"}
        )
    request.state.principal = principal
    path = request.url.path
    mutation = request.method not in {"GET", "HEAD", "OPTIONS"}
    research_action = path in {"/api/research", "/api/intent/assess"} or (
        mutation and (path == "/api/runs" or path.startswith("/api/runs/"))
    )
    if research_action and not principal.can_research:
        raise HTTPException(403, "当前身份为只读，无法创建或修改研究")
    if not principal.can_manage and (path == "/metrics" or (mutation and not research_action)):
        raise HTTPException(403, "此操作需要管理员权限")
    run_id = request.path_params.get("run_id")
    if run_id and not principal.can_manage:
        if await request.app.state.repo.get_run_owner(run_id) != principal.id:
            raise HTTPException(404, "run not found")
    request.app.state.settings = await effective_settings(
        settings, getattr(request.app.state, "catalog", None)
    )
    return principal
