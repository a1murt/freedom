"""Actor context (no login), soft permissions and the write-route map (docs/ARCH_CONTRACTS.md §1–§2).

- `cqa.security.roles`: ROLES, PERMISSIONS, ROUTE_MAP, `current_actor(request)`, `has_permission(role, permission)`,
  `require_permission(permission)` (FastAPI dependency).
- `cqa.security.middleware`: `install(app)` adds the pure ASGI middleware that resolves the actor, enforces permissions
  on write requests and records the audit trail (installed by `cqa.platform.install`).
"""
from .roles import (
    DEFAULT_ROLE,
    PERMISSIONS,
    ROLES,
    ROUTE_MAP,
    current_actor,
    has_permission,
    match_route,
    require_permission,
)

__all__ = ["DEFAULT_ROLE", "PERMISSIONS", "ROLES", "ROUTE_MAP", "current_actor", "has_permission", "match_route",
           "require_permission"]
