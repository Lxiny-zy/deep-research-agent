"""Workspace identities for revocable, server-managed API credentials."""

from __future__ import annotations

import json
import os
import re
import secrets
from dataclasses import dataclass, field
from typing import Literal

Role = Literal["admin", "researcher", "reader"]


@dataclass(frozen=True)
class Principal:
    id: str
    role: Role

    @property
    def can_manage(self) -> bool:
        return self.role == "admin"

    @property
    def can_research(self) -> bool:
        return self.role in {"admin", "researcher"}


@dataclass(frozen=True)
class ApiCredential:
    principal: Principal
    key: str = field(repr=False)


def load_api_credentials() -> tuple[ApiCredential, ...]:
    """DR_API_KEYS is a JSON list of {id, role, key}; never echo its contents."""
    try:
        raw = json.loads(os.getenv("DR_API_KEYS", "").strip() or "[]")
        if not isinstance(raw, list) or len(raw) > 1000:
            raise ValueError
        credentials = []
        identities: set[str] = set()
        keys: set[str] = set()
        for item in raw:
            identity, role, key = item["id"], item["role"], item["key"]
            if (
                not isinstance(identity, str)
                or not re.fullmatch(r"[A-Za-z0-9_.@-]{1,64}", identity)
                or identity in {"local", "admin"}
                or identity in identities
                or role not in {"admin", "researcher", "reader"}
                or not isinstance(key, str)
                or len(key.strip()) < 16
                or key != key.strip()
                or key in keys
                or key == os.getenv("API_KEY")
            ):
                raise ValueError
            identities.add(identity)
            keys.add(key)
            credentials.append(ApiCredential(Principal(identity, role), key))
        return tuple(credentials)
    except (ValueError, TypeError, KeyError) as exc:
        raise ValueError(
            "DR_API_KEYS must contain unique ids, roles and keys (16+ characters)"
        ) from exc


def authenticate(
    api_key: str, credentials: tuple[ApiCredential, ...], candidates: list[str]
) -> Principal | None:
    if not api_key and not credentials:
        return Principal("local", "admin")
    configured = list(credentials)
    if api_key:
        configured.append(ApiCredential(Principal("admin", "admin"), api_key))
    matches = {
        credential.principal
        for credential in configured
        for candidate in candidates
        if secrets.compare_digest(candidate.encode(), credential.key.encode())
    }
    # Conflicting credentials must never silently select the more privileged identity.
    return next(iter(matches)) if len(matches) == 1 else None
