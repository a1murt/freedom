"""Roles catalog and the resolved actor: GET /api/roles, GET /api/me (docs/ARCH_CONTRACTS.md §1)."""
from __future__ import annotations

from fastapi import APIRouter, Request

from ..security import roles

router = APIRouter(prefix="/api", tags=["roles"])


@router.get("/roles")
def list_roles():
    """Roles with labels (RU/EN/KK), permissions, suggested home route and nav items; the permission matrix;
    audit action labels; the write routes with the permission each one needs."""
    return roles.catalog()


@router.get("/me")
def me(request: Request):
    """Echo of the actor resolved from X-CQA-Role / X-CQA-User / X-Request-ID, with its permissions and UI hints.
    `enforced` is true when the security middleware handled the request (permissions are checked on writes)."""
    actor = roles.current_actor(request)
    state = request.scope.get("state") or {}
    return roles.describe_actor(actor, enforced=state.get("actor_source") == "middleware")
