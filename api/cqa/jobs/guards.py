"""Platform guards for evaluation runs on a public hosted instance (docs/ARCH_CONTRACTS.md §3).

- budget: the spend of the last 24 h, plus what queued/running jobs are still expected to spend (their
  `estimated_cost_usd` minus what they already spent) and what in-flight single-criterion trials and judge-spec
  compilations reserved in this process, plus this run's estimate must not exceed `settings.daily_budget_usd`
  (0 disables the budget). Spend = max(spend ledger, database): the ledger (cqa.jobs.ledger, a file a snapshot restore
  never touches) books every billed Claude call, including compilations and calls that failed after billing; the
  database side (Evaluation.cost_usd plus trial costs from the audit trail) covers rows the ledger may not hold.
  Only runs that make no paid model call (local, fake, a weight-only partial re-evaluation) are exempt (`free=True`);
  a paid run is checked with at least MIN_PAID_ESTIMATE_USD even if its estimate rounds to 0;
- concurrency: at most `settings.max_concurrent_jobs` jobs in status queued|running;
- size: at most `settings.max_dialogs_per_run` dialogs per run.

`check_run` raises `GuardError` (HTTP 429 by default) with a precise code and a Russian message with the numbers.
"""
from __future__ import annotations

import itertools
import math
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import AuditEvent, Evaluation, Job

WINDOW = timedelta(hours=24)
ACTIVE_JOB_STATUSES = ("queued", "running")
TRIAL_ACTION = "checklist.criterion.test"  # POST /api/checklists/test-criterion (audited with details.response.cost_usd)
MIN_PAID_ESTIMATE_USD = 0.01  # a paid run is never treated as free, whatever its estimate rounds to

# In-flight work that is not a job (single-criterion trials, judge-spec compilations) reserves its estimate here while its
# calls run, under the same lock as the check (jobs.runner.start_lock), so concurrent requests cannot all pass one check.
_reservations: dict[int, float] = {}
_reservation_ids = itertools.count(1)
_reservation_lock = threading.Lock()


class GuardError(Exception):
    """A run refused by a platform guard. `envelope()` is the API error body; `extra` fields are merged into it."""

    def __init__(self, code: str, message: str, status: int = 429, extra: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.extra: dict[str, Any] = dict(extra or {})

    @property
    def retry_after_s(self) -> int | None:
        value = self.extra.get("retry_after_s")
        return int(value) if value else None

    def envelope(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message, **self.extra}}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    """SQLite returns naive datetimes; every timestamp in this app is UTC."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def plural_ru(n: int, forms: tuple[str, str, str]) -> str:
    """Russian plural: (1 задача, 2 задачи, 5 задач)."""
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return forms[0]
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return forms[1]
    return forms[2]


def _usd(x: float) -> str:
    return f"${x:,.2f}".replace(",", " ")


def _duration_ru(seconds: int) -> str:
    minutes = max(math.ceil(seconds / 60), 1)
    hours, minutes = divmod(minutes, 60)
    if hours and minutes:
        return f"{hours} ч {minutes} мин"
    return f"{hours} ч" if hours else f"{minutes} мин"


def active_jobs(db: Session) -> list[Job]:
    return list(db.execute(select(Job).where(Job.status.in_(ACTIVE_JOB_STATUSES)).order_by(Job.created_at)).scalars())


def trial_spend_since(db: Session, since: datetime) -> float:
    """Claude spend of single-criterion trials since `since` (they persist no Evaluation; the audit event keeps the cost)."""
    try:
        rows = db.execute(select(AuditEvent.details).where(AuditEvent.action == TRIAL_ACTION, AuditEvent.ts >= since,
                                                           AuditEvent.status_code == 200)).scalars().all()
    except Exception:  # noqa: BLE001 - an audit table problem must not block evaluations
        return 0.0
    total = 0.0
    for details in rows:
        try:
            total += float(((details or {}).get("response") or {}).get("cost_usd") or 0.0)
        except (AttributeError, TypeError, ValueError):
            continue
    return total


def database_spent_since(db: Session, since: datetime) -> float:
    """Spend recorded in the database: Evaluation.cost_usd plus single-criterion trials from the audit trail."""
    total = db.execute(select(func.coalesce(func.sum(Evaluation.cost_usd), 0.0)).where(Evaluation.created_at >= since)).scalar()
    return float(total or 0.0) + trial_spend_since(db, since)


def spent_since(db: Session, since: datetime) -> float:
    """max(spend ledger, database). The ledger survives snapshot restores and also holds spend without an Evaluation row."""
    from . import ledger

    database = database_spent_since(db, since)
    ledger.ensure_backfilled(db)
    booked = ledger.spent_since(since)
    return max(database, booked) if booked is not None else database


def inflight_reserved() -> float:
    with _reservation_lock:
        return float(sum(_reservations.values()))


@contextmanager
def reservation(amount: float) -> Iterator[None]:
    """Reserve ``amount`` of the daily budget for the duration of the block (released when it ends, booked spend stays)."""
    amount = max(float(amount or 0.0), 0.0)
    rid = next(_reservation_ids)
    with _reservation_lock:
        _reservations[rid] = amount
    try:
        yield
    finally:
        with _reservation_lock:
            _reservations.pop(rid, None)


def reserved_usd(jobs: list[Job]) -> float:
    """What active jobs are still expected to spend (their estimate minus the cost already booked by their evaluations)
    plus what in-flight trials and compilations of this process reserved."""
    total = 0.0
    for j in jobs:
        try:
            estimate = float((j.params or {}).get("estimated_cost_usd") or 0.0)
        except (TypeError, ValueError):
            estimate = 0.0
        total += max(estimate - float(j.cost_usd or 0.0), 0.0)
    return total + inflight_reserved()


def budget_status(db: Session, *, now: datetime | None = None) -> dict[str, Any]:
    """`{spent_24h, limit, remaining, reserved, unlimited, window_hours}`; limit and remaining are null when unlimited."""
    now = now or _now()
    limit = float(settings.daily_budget_usd or 0.0)
    spent = spent_since(db, now - WINDOW)
    reserved = reserved_usd(active_jobs(db))
    unlimited = limit <= 0
    return {
        "spent_24h": round(spent, 4),
        "limit": None if unlimited else round(limit, 2),
        "remaining": None if unlimited else round(max(limit - spent - reserved, 0.0), 4),
        "reserved": round(reserved, 4),
        "unlimited": unlimited,
        "window_hours": int(WINDOW.total_seconds() // 3600),
    }


def _seconds_until_freed(db: Session, now: datetime, amount: float) -> int | None:
    """Seconds until spend worth at least `amount` leaves the 24 h window (None if the window never frees enough)."""
    from . import ledger

    rows = db.execute(select(Evaluation.created_at, Evaluation.cost_usd)
                      .where(Evaluation.created_at >= now - WINDOW, Evaluation.cost_usd > 0)
                      .order_by(Evaluation.created_at)).all()
    booked = ledger.rows_since(now - WINDOW)
    if sum(c for _t, c in booked) > sum(float(c or 0.0) for _t, c in rows):
        rows = booked
    freed = 0.0
    for created_at, cost in rows:
        freed += float(cost or 0.0)
        if freed >= amount - 1e-9:
            return max(int((_aware(created_at) + WINDOW - now).total_seconds()) + 1, 1)
    return None


def check_run(db: Session, n_dialogs: int, estimate_usd: float | None, *, now: datetime | None = None,
              free: bool | None = None) -> dict[str, Any]:
    """Raise GuardError if this run must not start; otherwise return the current `budget_status`.
    `free`: the run makes no paid model call (default: estimate <= 0, the behaviour of older callers)."""
    now = now or _now()
    estimate = max(float(estimate_usd or 0.0), 0.0)

    max_dialogs = int(settings.max_dialogs_per_run or 0)
    if max_dialogs > 0 and n_dialogs > max_dialogs:
        raise GuardError(
            "too_many_dialogs",
            f"Слишком много диалогов в одном запуске: выбрано {n_dialogs}, допускается не больше {max_dialogs}. "
            f"Разбейте запуск на несколько частей.",
            extra={"requested": n_dialogs, "limit": max_dialogs},
        )

    max_jobs = int(settings.max_concurrent_jobs or 0)
    active = active_jobs(db)
    if max_jobs > 0 and len(active) >= max_jobs:
        raise GuardError(
            "too_many_jobs",
            f"Достигнут лимит одновременных задач оценки: сейчас активно {len(active)} из {max_jobs}. "
            f"Дождитесь завершения или отмените одну из задач.",
            extra={"running": len(active), "limit": max_jobs, "active_job_ids": [j.id for j in active]},
        )

    return check_budget(db, estimate, now=now, free=free)


def check_budget(db: Session, estimate_usd: float | None, *, now: datetime | None = None, free: bool | None = None) -> dict[str, Any]:
    """Only the budget part of `check_run` (also used by the single-criterion trial and judge-spec compilation): raise
    GuardError `budget_exceeded` when spend + reservations + this estimate exceed the daily budget, else return
    `budget_status`. Free work (`free=True`) is never blocked; paid work counts at least MIN_PAID_ESTIMATE_USD."""
    now = now or _now()
    estimate = max(float(estimate_usd or 0.0), 0.0)
    if free is None:
        free = estimate <= 0
    budget = budget_status(db, now=now)
    if budget["unlimited"] or free:
        return budget
    estimate = max(estimate, MIN_PAID_ESTIMATE_USD)
    limit, spent, reserved = float(budget["limit"]), float(budget["spent_24h"]), float(budget["reserved"])
    if spent + reserved + estimate > limit + 1e-9:
        available = max(limit - spent - reserved, 0.0)
        message = (f"Превышен суточный бюджет на оценку: за последние 24 ч потрачено {_usd(spent)} из {_usd(limit)}, "
                   f"оценка этого запуска {_usd(estimate)}, доступно {_usd(available)}.")
        if reserved > 0:
            message += f" Ещё {_usd(reserved)} зарезервировано задачами, которые сейчас выполняются."
        retry: int | None = None
        if estimate > limit:
            message += " Этот запуск дороже всего суточного бюджета: уменьшите число диалогов."
        elif reserved + estimate > limit:
            message += " Дождитесь завершения выполняющихся задач."
        else:
            retry = _seconds_until_freed(db, now, spent + reserved + estimate - limit)
            if retry:
                message += f" Бюджет освободится примерно через {_duration_ru(retry)}."
        raise GuardError("budget_exceeded", message,
                         extra={"budget": budget, "estimate_usd": round(estimate, 2), "retry_after_s": retry})
    return budget


def budget_exhausted(db: Session, *, now: datetime | None = None) -> dict[str, Any] | None:
    """The in-run check of a paid job before each dialog: the budget status when the 24 h spend already reached the
    daily budget (the job must stop starting dialogs), else None. Reservations do not count here: they are the job's own."""
    now = now or _now()
    limit = float(settings.daily_budget_usd or 0.0)
    if limit <= 0:
        return None
    spent = spent_since(db, now - WINDOW)
    if spent + 1e-9 < limit:
        return None
    return {"spent_24h": round(spent, 4), "limit": round(limit, 2)}


def reserve(db: Session, amount: float, *, free: bool = False) -> int | None:
    """Check the daily budget for `amount` and reserve it, atomically with job starts in this process (under
    cqa.jobs.runner.start_lock). Returns a reservation id for `release` (None for free work, which is not checked).
    Raises GuardError `budget_exceeded`. Blocking: async callers run it in the thread pool."""
    if free:
        return None
    from .runner import start_lock

    amount = max(float(amount or 0.0), MIN_PAID_ESTIMATE_USD)
    rid = next(_reservation_ids)
    with start_lock:
        check_budget(db, amount, free=False)
        with _reservation_lock:
            _reservations[rid] = amount
    return rid


def release(rid: int | None) -> None:
    if rid is None:
        return
    with _reservation_lock:
        _reservations.pop(rid, None)


@contextmanager
def budget_reservation(db: Session, amount: float, *, free: bool = False) -> Iterator[None]:
    """`reserve` for the duration of the block. Used by work that is not a job (single-criterion trials, judge-spec
    compilations), so N concurrent requests cannot all pass one check."""
    rid = reserve(db, amount, free=free)
    try:
        yield
    finally:
        release(rid)
