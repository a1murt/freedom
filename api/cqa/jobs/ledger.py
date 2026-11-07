"""Append-only spend ledger: one row per billed provider call, in its own SQLite file next to the database.

Why a separate file:
- a snapshot restore replaces the main database (evaluations, audit events), which used to roll the daily budget back;
  the ledger is never part of a snapshot, so restoring "baseline" no longer resets the 24 h spend;
- spend that has no Evaluation row (judge-spec compilation, failed single-criterion trials, calls that failed after the
  provider billed them) is still counted.

Rows are booked by the Claude provider itself (cqa.judge.providers.anthropic_provider) for every response that carries
usage, successful or not. ``spend_context`` (a context variable) labels them with the work they belong to
(``kind``: judge | trial | compile, ``ref``: job / evaluation / conversation).

The budget (cqa.jobs.guards.spent_since) is max(ledger, database): the database side keeps counting what the ledger may
not hold (evaluations made before the ledger existed, tests that write rows directly). The first read of a new ledger
file copies the last 24 h of database spend into it (``backfill``), so after that the ledger alone is complete.

Location: settings.spend_ledger_path (env SPEND_LEDGER_PATH), default <DATA_DIR>/spend_ledger.db. A ledger that cannot be
opened never blocks work: booking logs a warning and the budget falls back to the database.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..config import settings

log = logging.getLogger("cqa.ledger")

WINDOW = timedelta(hours=24)
_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS spend (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, cost_usd REAL NOT NULL, "
    "kind TEXT NOT NULL, provider TEXT, model TEXT, input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER, "
    "cache_write_tokens INTEGER, ref TEXT, ok INTEGER)",
    "CREATE INDEX IF NOT EXISTS ix_spend_ts ON spend (ts)",
    "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)",
)
_lock = threading.RLock()
_ready: set[str] = set()  # ledger files whose schema exists (per process)
_backfilled: set[str] = set()

spend_context: ContextVar[dict[str, Any] | None] = ContextVar("cqa_spend_context", default=None)


def path() -> Path:
    configured = settings.spend_ledger_path
    return Path(configured) if configured else Path(settings.data_dir) / "spend_ledger.db"


def _ts(dt: datetime) -> float:
    return (dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)).timestamp()


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    p = path()
    key = str(p)
    with _lock:
        if key not in _ready:
            p.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(key, timeout=5.0)
        try:
            conn.execute("PRAGMA busy_timeout=5000")
            if key not in _ready:
                for stmt in _SCHEMA:
                    conn.execute(stmt)
                conn.commit()
                _ready.add(key)
            yield conn
        finally:
            conn.close()


@contextmanager
def context(**labels: Any) -> Iterator[None]:
    """Label the bookings made inside this block (and the asyncio tasks it starts): kind=..., ref=..."""
    merged = {**(spend_context.get() or {}), **{k: v for k, v in labels.items() if v is not None}}
    token = spend_context.set(merged)
    try:
        yield
    finally:
        spend_context.reset(token)


def book(cost_usd: float, *, provider: str | None = None, model: str | None = None, usage: Any = None, ok: bool = True,
         kind: str | None = None, ref: str | None = None, at: datetime | None = None) -> None:
    """Record one billed call. Never raises (a broken ledger must not fail an evaluation)."""
    try:
        cost = float(cost_usd or 0.0)
    except (TypeError, ValueError):
        return
    if cost <= 0:
        return
    labels = spend_context.get() or {}

    def tok(name: str) -> int:
        return int(getattr(usage, name, 0) or 0) if usage is not None else 0

    row = (_ts(at or datetime.now(timezone.utc)), cost, kind or labels.get("kind") or "call", provider, model,
           tok("input_tokens"), tok("output_tokens"), tok("cache_read_tokens"), tok("cache_write_tokens"),
           ref or labels.get("ref"), 1 if ok else 0)
    try:
        with _connect() as conn:
            conn.execute("INSERT INTO spend (ts, cost_usd, kind, provider, model, input_tokens, output_tokens, cache_read_tokens, "
                         "cache_write_tokens, ref, ok) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", row)
            conn.commit()
    except Exception as e:  # noqa: BLE001
        log.warning("spend ledger: booking $%.4f failed: %s", cost, e)


def spent_since(since: datetime) -> float | None:
    """Sum of booked spend since ``since``; None when the ledger cannot be read."""
    try:
        with _connect() as conn:
            value = conn.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM spend WHERE ts >= ?", (_ts(since),)).fetchone()[0]
        return float(value or 0.0)
    except Exception as e:  # noqa: BLE001
        log.warning("spend ledger unreadable: %s", e)
        return None


def rows_since(since: datetime) -> list[tuple[datetime, float]]:
    """(time, cost) of the bookings since ``since``, oldest first (used for the "budget frees in …" hint)."""
    try:
        with _connect() as conn:
            rows = conn.execute("SELECT ts, cost_usd FROM spend WHERE ts >= ? AND cost_usd > 0 ORDER BY ts", (_ts(since),)).fetchall()
    except Exception:  # noqa: BLE001
        return []
    return [(datetime.fromtimestamp(ts, timezone.utc), float(cost)) for ts, cost in rows]


def summary(since: datetime) -> dict[str, Any] | None:
    """{total, by_kind, calls, failed_calls} since ``since`` (GET /api/system/metrics); None when unreadable."""
    try:
        with _connect() as conn:
            rows = conn.execute("SELECT kind, COUNT(*), COALESCE(SUM(cost_usd), 0), SUM(CASE WHEN ok = 0 THEN 1 ELSE 0 END) "
                                "FROM spend WHERE ts >= ? GROUP BY kind", (_ts(since),)).fetchall()
    except Exception:  # noqa: BLE001
        return None
    by_kind = {str(k): round(float(c), 4) for k, _n, c, _f in rows}
    return {"total": round(sum(float(c) for _k, _n, c, _f in rows), 4), "by_kind": by_kind,
            "calls": int(sum(n for _k, n, _c, _f in rows)), "failed_calls": int(sum(f or 0 for _k, _n, _c, f in rows))}


def ensure_backfilled(db: Any, *, now: datetime | None = None) -> None:
    """Once per ledger file: copy the last 24 h of database spend (evaluations + single-criterion trials) into the ledger,
    so a ledger created after spend already happened (first deploy of this build) is complete from the start."""
    key = str(path())
    if key in _backfilled:
        return
    now = now or datetime.now(timezone.utc)
    try:
        with _connect() as conn:
            if conn.execute("SELECT 1 FROM meta WHERE key = 'backfilled_at'").fetchone() is not None:
                _backfilled.add(key)
                return
            rows = _database_spend(db, now - WINDOW)
            conn.executemany("INSERT INTO spend (ts, cost_usd, kind, provider, model, ref, ok) VALUES (?, ?, 'backfill', NULL, NULL, ?, 1)",
                             [(_ts(at), cost, ref) for at, cost, ref in rows])
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('backfilled_at', ?)", (now.isoformat(),))
            conn.commit()
        _backfilled.add(key)
        if rows:
            log.info("spend ledger %s: backfilled %d row(s), $%.2f, from the database", key, len(rows), sum(c for _a, c, _r in rows))
    except Exception as e:  # noqa: BLE001 - the database side of the budget still counts this spend
        log.warning("spend ledger backfill failed: %s", e)


def _database_spend(db: Any, since: datetime) -> list[tuple[datetime, float, str]]:
    from sqlalchemy import select

    from ..models import AuditEvent, Evaluation

    out: list[tuple[datetime, float, str]] = []
    for ev_id, created_at, cost in db.execute(select(Evaluation.id, Evaluation.created_at, Evaluation.cost_usd)
                                              .where(Evaluation.created_at >= since, Evaluation.cost_usd > 0)).all():
        out.append((created_at, float(cost or 0.0), f"evaluation:{ev_id}"))
    try:
        from .guards import TRIAL_ACTION

        for ev_id, ts, details in db.execute(select(AuditEvent.id, AuditEvent.ts, AuditEvent.details)
                                             .where(AuditEvent.action == TRIAL_ACTION, AuditEvent.ts >= since,
                                                    AuditEvent.status_code == 200)).all():
            try:
                cost = float(((details or {}).get("response") or {}).get("cost_usd") or 0.0)
            except (AttributeError, TypeError, ValueError):
                continue
            if cost > 0:
                out.append((ts, cost, f"audit:{ev_id}"))
    except Exception:  # noqa: BLE001 - no audit table
        db.rollback()
    return out


def reset_cache() -> None:
    """Forget the per-process schema/backfill flags (tests switch ledger files)."""
    with _lock:
        _ready.clear()
        _backfilled.clear()
