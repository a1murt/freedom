"""Audit trail: who did what, when (docs/ARCH_CONTRACTS.md §2). Rows live in `audit_events` (models.AuditEvent).

- The security middleware (cqa.security.middleware) records every write request under /api/ that ends with status < 400
  and every 403 attempt, with a compact `details` (path params, query, body summary ≤ 2 KB, a few response fields).
- Services add semantic events with `record(db, action=..., ...)` (e.g. `job.interrupted` at start-up); with no actor the
  row is attributed to the role `system`.
- `compact()` / `scrub_text()` keep details small and free of secrets: values of secret-looking keys become "***",
  API keys, bearer tokens, URL passwords and signed-URL signatures are masked, transcripts are reduced to their size,
  long strings are cut to 200 characters.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..models import AuditEvent
from ..security import roles

COLUMN_LIMITS = {"actor_role": 32, "actor_name": 128, "action": 64, "entity_type": 32, "entity_id": 64, "request_id": 64}
SUMMARY_MAX = 500
DETAILS_MAX_BYTES = 16 * 1024  # whole details JSON (the request body summary inside is ≤ 2 KB)
BODY_SUMMARY_MAX_BYTES = 2048
DEFAULT_LIMIT, MAX_LIMIT = 100, 500
FILTER_FIELDS = ("action", "entity_type", "entity_id", "actor_role", "actor_name", "status_code", "q", "since", "until")

# ----------------------------------------------------------------------------- secrets and compaction

_SECRET_EXACT = {"key", "sig", "pwd", "pass", "auth"}  # only when the whole key is exactly this (e.g. a query param)
_SECRET_SUFFIXES = ("password", "passwd", "secret", "token", "apikey", "accesskey", "privatekey", "authorization",
                    "cookie", "signature", "credential", "credentials", "sessionid")
# Values under these keys are reduced to their size, never stored: transcripts, Dynamics 365 `documentbody` (base64 of
# the whole chat) and `participants` (its keys can be customer names).
TRANSCRIPT_KEYS = frozenset({"transcript", "turns", "text", "messages", "utterances", "dialog", "documentbody", "participants"})
CRITERION_BRIEF_KEYS = ("code", "title", "weight", "answer_type", "is_critical", "na_allowed")

_API_KEY_RE = re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9][A-Za-z0-9_\-]{12,}")
_BEARER_RE = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=\-]{8,}")
_URL_USERINFO_RE = re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://)[^/\s:@]+:[^/\s@]+@")
_URL_SECRET_PARAM_RE = re.compile(
    r"(?i)([?&](?:sig|signature|token|access_token|refresh_token|api[_-]?key|apikey|key|secret|password|pwd|code)=)[^&\s#\"']+")

LEVELS = (  # progressively smaller until the JSON fits
    {"str_limit": 200, "list_limit": 20, "max_depth": 5, "max_keys": 40},
    {"str_limit": 120, "list_limit": 10, "max_depth": 4, "max_keys": 25},
    {"str_limit": 60, "list_limit": 5, "max_depth": 3, "max_keys": 15},
    {"str_limit": 40, "list_limit": 3, "max_depth": 2, "max_keys": 10},
)


def _norm_key(key: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def is_secret_key(key: Any) -> bool:
    nk = _norm_key(key)
    return nk in _SECRET_EXACT or any(nk.endswith(s) for s in _SECRET_SUFFIXES)


def scrub_text(text: str) -> str:
    """Mask API keys, bearer tokens, URL passwords and secret query parameters inside a string."""
    text = _API_KEY_RE.sub("sk-***", text)
    text = _BEARER_RE.sub(lambda m: f"{m.group(1)} ***", text)
    text = _URL_USERINFO_RE.sub(lambda m: f"{m.group(1)}***@", text)
    return _URL_SECRET_PARAM_RE.sub(lambda m: f"{m.group(1)}***", text)


def _omitted(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        return {"_omitted": "text", "chars": len(value), "lines": value.count("\n") + 1}
    if isinstance(value, Mapping):
        return {"_omitted": "object", "keys": len(value)}
    return {"_omitted": "transcript", "items": len(value)}


def compact(value: Any, *, str_limit: int = 200, list_limit: int = 20, max_depth: int = 5, max_keys: int = 40,
            _key: str | None = None, _depth: int = 0) -> Any:
    """A JSON-safe, size-bounded copy of `value` for audit details (see the module docstring)."""
    lim = {"str_limit": str_limit, "list_limit": list_limit, "max_depth": max_depth, "max_keys": max_keys}
    if _key is not None:
        if is_secret_key(_key):
            return "***" if value not in (None, "") else value
        nk = _norm_key(_key)
        if nk in TRANSCRIPT_KEYS and isinstance(value, (str, list, tuple, Mapping)) and value:
            return _omitted(value)
        if nk == "criteria" and isinstance(value, (list, tuple)):
            out = [compact({k: c[k] for k in CRITERION_BRIEF_KEYS if k in c} if isinstance(c, Mapping) else c,
                           **{**lim, "str_limit": min(str_limit, 60)}, _depth=_depth + 1) for c in value[:list_limit]]
            if len(value) > list_limit:
                out.append({"_more": len(value) - list_limit})
            return out
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        text = scrub_text(value)
        return text if len(text) <= str_limit else text[:str_limit] + "…"
    if isinstance(value, Mapping):
        if _depth >= max_depth:
            return {"_keys": len(value)}
        out_d: dict[str, Any] = {}
        for i, (k, v) in enumerate(value.items()):
            if i >= max_keys:
                out_d["_more_keys"] = len(value) - max_keys
                break
            out_d[str(k)[:64]] = compact(v, **lim, _key=str(k), _depth=_depth + 1)
        return out_d
    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
        if _depth >= max_depth:
            return {"_items": len(items)}
        out_l = [compact(v, **lim, _depth=_depth + 1) for v in items[:list_limit]]
        if len(items) > list_limit:
            out_l.append({"_more": len(items) - list_limit})
        return out_l
    if isinstance(value, datetime):
        return value.isoformat()
    return compact(str(value), **lim)


def json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))


def compact_to_size(value: Any, max_bytes: int = BODY_SUMMARY_MAX_BYTES) -> Any:
    """compact() with progressively tighter limits until the JSON is at most `max_bytes` (UTF-8)."""
    out: Any = None
    for level in LEVELS:
        out = compact(value, **level)
        if json_size(out) <= max_bytes:
            return out
    if isinstance(value, Mapping):
        keys = [str(k)[:40] for k in list(value)[:30]]
        out = {"_truncated": True, "keys": keys}
        while json_size(out) > max_bytes and keys:
            keys.pop()
        return out
    if isinstance(value, (list, tuple)):
        return {"_truncated": True, "items": len(value)}
    return {"_truncated": True, "bytes": json_size(value)}


def _jsonable(details: Any) -> dict[str, Any]:
    if not details:
        return {}
    try:
        data = json.loads(json.dumps(details, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        data = {"value": str(details)}
    if not isinstance(data, dict):
        data = {"value": data}
    if json_size(data) > DETAILS_MAX_BYTES:
        data = compact_to_size(data, DETAILS_MAX_BYTES)
    return data


# ----------------------------------------------------------------------------- write


def _cut(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if len(text) <= limit else text[:limit]


def _fit_request_id(value: Any) -> str | None:
    text = _cut(value, 256)
    if text and len(text) > COLUMN_LIMITS["request_id"] and len(text.replace("-", "")) <= COLUMN_LIMITS["request_id"]:
        text = text.replace("-", "")  # a dashed UUID still fits
    return _cut(text, COLUMN_LIMITS["request_id"]) or None


def _actor_dict(actor: Any) -> dict[str, Any]:
    if actor is None:
        return {}
    if isinstance(actor, Mapping):
        return dict(actor)
    if hasattr(actor, "scope") and hasattr(actor, "headers"):  # a Request
        return dict(roles.current_actor(actor))
    return {}


def record(db: Session, action: str, entity_type: str | None = None, entity_id: Any = None, summary: str | None = None,
           details: Mapping[str, Any] | None = None, actor: Mapping[str, Any] | Any = None, status_code: int | None = None,
           request_id: str | None = None, *, commit: bool = True) -> AuditEvent:
    """Write one audit event. `actor` is `{"role", "name", "request_id"}` (as from current_actor), a Request, or None for
    a background service (role `system`). Commits by default; pass commit=False inside a larger unit of work."""
    a = _actor_dict(actor)
    ev = AuditEvent(
        actor_role=_cut(a.get("role") or roles.SYSTEM_ROLE, COLUMN_LIMITS["actor_role"]),
        actor_name=_cut(a.get("name"), COLUMN_LIMITS["actor_name"]),
        action=_cut(action, COLUMN_LIMITS["action"]),
        entity_type=_cut(entity_type, COLUMN_LIMITS["entity_type"]),
        entity_id=_cut(entity_id, COLUMN_LIMITS["entity_id"]),
        summary=_cut(summary, SUMMARY_MAX),
        details=_jsonable(details),
        request_id=_fit_request_id(request_id or a.get("request_id")),
        status_code=status_code,
    )
    db.add(ev)
    if commit:
        db.commit()
    else:
        db.flush()
    return ev


# ----------------------------------------------------------------------------- read


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def outcome(status_code: int | None) -> str:
    if status_code == 403:
        return "denied"
    if status_code is not None and status_code >= 400:
        return "error"
    return "ok"


def serialize_event(ev: AuditEvent) -> dict[str, Any]:
    ts = _aware(ev.ts)
    return {"id": ev.id, "ts": ts.isoformat() if ts else None, "actor_role": ev.actor_role,
            "actor_role_label": roles.role_label(ev.actor_role), "actor_name": ev.actor_name, "action": ev.action,
            "action_label": roles.action_label(ev.action), "entity_type": ev.entity_type, "entity_id": ev.entity_id,
            "summary": ev.summary, "details": ev.details or {}, "request_id": ev.request_id, "status_code": ev.status_code,
            "outcome": outcome(ev.status_code)}


def _values(value: Any) -> list[str]:
    if value is None:
        return []
    items: Iterable[Any] = value if isinstance(value, (list, tuple, set)) else str(value).split(",")
    return [str(v).strip() for v in items if str(v).strip()]


def _filtered(stmt: Any, filters: Mapping[str, Any] | None) -> Any:
    f = dict(filters or {})
    actions = _values(f.get("action"))
    if actions:
        exact = [a for a in actions if not a.endswith(("*", "."))]
        prefixes = [a.rstrip("*") for a in actions if a.endswith(("*", "."))]
        conds = ([AuditEvent.action.in_(exact)] if exact else []) + [AuditEvent.action.startswith(p, autoescape=True)
                                                                    for p in prefixes]
        stmt = stmt.where(or_(*conds))
    for name in ("entity_type", "actor_role"):
        vals = _values(f.get(name))
        if vals:
            stmt = stmt.where(getattr(AuditEvent, name).in_(vals))
    if f.get("entity_id") not in (None, ""):
        stmt = stmt.where(AuditEvent.entity_id == str(f["entity_id"]))
    if f.get("actor_name") not in (None, ""):
        stmt = stmt.where(AuditEvent.actor_name == roles.decode_user(str(f["actor_name"])))
    if f.get("status_code") not in (None, ""):
        stmt = stmt.where(AuditEvent.status_code == int(f["status_code"]))
    if f.get("q"):
        q = str(f["q"]).strip()
        stmt = stmt.where(or_(AuditEvent.summary.contains(q, autoescape=True), AuditEvent.entity_id.contains(q, autoescape=True),
                              AuditEvent.actor_name.contains(q, autoescape=True)))
    if isinstance(f.get("since"), datetime):
        stmt = stmt.where(AuditEvent.ts >= _aware(f["since"]))
    if isinstance(f.get("until"), datetime):
        stmt = stmt.where(AuditEvent.ts <= _aware(f["until"]))
    return stmt


def list_events(db: Session, filters: Mapping[str, Any] | None = None, limit: int | None = DEFAULT_LIMIT,
                before_id: int | None = None) -> dict[str, Any]:
    """Newest first. Filters: action (exact, comma list, or prefix with a trailing `*`/`.`), entity_type, entity_id,
    actor_role, actor_name, status_code, q (substring of summary/entity id/actor name), since/until (datetimes).
    Returns `{"items": [...], "next_before_id": id | None}`; pass next_before_id as before_id for the next page."""
    n = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    stmt = _filtered(select(AuditEvent), filters)
    if before_id:
        stmt = stmt.where(AuditEvent.id < int(before_id))
    rows = db.execute(stmt.order_by(AuditEvent.id.desc()).limit(n + 1)).scalars().all()
    more = len(rows) > n
    rows = rows[:n]
    return {"items": [serialize_event(r) for r in rows], "next_before_id": rows[-1].id if more and rows else None}


def iter_events(db: Session, filters: Mapping[str, Any] | None = None, limit: int = 5000) -> list[AuditEvent]:
    stmt = _filtered(select(AuditEvent), filters).order_by(AuditEvent.id.desc()).limit(max(1, int(limit)))
    return list(db.execute(stmt).scalars().all())


def facets(db: Session) -> dict[str, Any]:
    """Distinct values with counts, for the filters of the audit log view."""
    def grouped(col: Any, limit: int = 100) -> list[tuple[Any, int]]:
        n = func.count(AuditEvent.id)
        return [(v, c) for v, c in db.execute(select(col, n).group_by(col).order_by(n.desc()).limit(limit)).all() if v]

    return {
        "total": db.execute(select(func.count(AuditEvent.id))).scalar_one(),
        "actions": [{"code": v, "label": roles.action_label(v), "n": c} for v, c in grouped(AuditEvent.action)],
        "entity_types": [{"code": v, "n": c} for v, c in grouped(AuditEvent.entity_type)],
        "actor_roles": [{"code": v, "label": roles.role_label(v), "n": c} for v, c in grouped(AuditEvent.actor_role)],
        "actor_names": [{"name": v, "n": c} for v, c in grouped(AuditEvent.actor_name, 50)],
    }
