"""Background batch jobs: a worker thread with its own asyncio loop; progress in the Job table and an in-memory event log.

Platform hardening (docs/ARCH_CONTRACTS.md §3, docs/arch/platform.md):
- `recover_stale_jobs()` runs at start-up (main.py): jobs a previous process left queued/running become `interrupted`
  (log entry + audit event), its `running` evaluations become `failed` with error "interrupted by restart";
- `resume_job(job_id)` starts a new job for the conversations of a finished job that still have no completed evaluation
  for the job's checklist version (same provider, model and mode);
- `start_lock` makes "check the guards, then create the job" atomic inside the process (a snapshot restore holds it too,
  so no job starts while the database is being replaced);
- a paid job checks the daily budget before each dialog and stops starting dialogs once the 24 h spend reached the
  budget (status `cancelled`, a log line says why; the job stays resumable);
- a worker that crashes marks its job `failed` instead of leaving it `running` (which would block the concurrency guard);
  a job row that disappeared (restored database) is tolerated.

Assumption: one API process runs the jobs of its database (Railway runs a single uvicorn process). An offline script that
runs jobs against the same database while the API restarts sees its job marked interrupted until the script finishes it.
"""
from __future__ import annotations

import asyncio
import importlib
import inspect
import logging
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import SessionLocal, restore_generation
from ..judge.runner import FREE_PROVIDERS, evaluate_conversation
from ..models import Conversation, Evaluation, Job

log = logging.getLogger("cqa.jobs")

ACTIVE_STATUSES = ("queued", "running")
TERMINAL_STATUSES = ("completed", "failed", "cancelled", "interrupted")
INTERRUPTED_ERROR = "interrupted by restart"
PROCESS_STARTED_AT = datetime.now(timezone.utc)
KEEP_FINISHED_STATES = 20  # finished jobs whose in-memory event log is kept (SSE replay); older ones are dropped

# Guard checks and job creation run under this lock, so two concurrent requests cannot both pass the concurrency limit.
start_lock = threading.RLock()


class JobState:
    def __init__(self, job_id: str):
        self.id = job_id
        self.events: deque[tuple[int, str, dict[str, Any]]] = deque(maxlen=5000)
        self.seq = 0
        self.cancel = threading.Event()
        self.finished = threading.Event()  # set when the worker thread exits
        self.finished_at: float | None = None
        self.lock = threading.Lock()
        self.paid = True  # checks the daily budget before each dialog (False for local / fake judges)
        self.stop_reason: str | None = None  # "budget_exhausted" when the worker stopped itself
        self.generation = restore_generation()  # a snapshot restore after this: the job row is not ours any more

    @property
    def restored(self) -> bool:
        return restore_generation() != self.generation

    @property
    def alive(self) -> bool:
        return not self.finished.is_set()

    def emit(self, kind: str, data: dict[str, Any]) -> None:
        with self.lock:
            self.seq += 1
            self.events.append((self.seq, kind, data))

    def since(self, seq: int) -> list[tuple[int, str, dict[str, Any]]]:
        with self.lock:
            return [e for e in self.events if e[0] > seq]


_registry: dict[str, JobState] = {}


def get_state(job_id: str) -> JobState | None:
    return _registry.get(job_id)


def live_job_ids() -> set[str]:
    """Jobs whose worker runs in this process right now."""
    return {jid for jid, st in list(_registry.items()) if st.alive}


def _prune_registry() -> None:
    finished = sorted((st for st in list(_registry.values()) if not st.alive), key=lambda s: s.finished_at or 0.0)
    for st in finished[:-KEEP_FINISHED_STATES] if len(finished) > KEEP_FINISHED_STATES else []:
        _registry.pop(st.id, None)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _log(job: Job, level: str, message: str) -> None:
    job.log = (job.log or []) + [{"ts": datetime.now(timezone.utc).isoformat(), "level": level, "message": message}]


def start_evaluation_job(conversation_ids: list[str], checklist_version_id: int, *, provider: str, model: str | None, mode: str,
                         base_version_id: int | None, only_codes: list[str] | None,
                         extra_params: dict[str, Any] | None = None) -> str:
    """Create the job row (queued) and start its worker thread. `extra_params` (e.g. `estimated_cost_usd`,
    `resumed_from`) are stored in `Job.params` next to the run parameters and never override them."""
    job_id = "j_" + secrets.token_hex(6)
    params: dict[str, Any] = {"checklist_version_id": checklist_version_id, "provider": provider, "model": model, "mode": mode,
                              "base_version_id": base_version_id, "only_codes": only_codes, "conversation_ids": conversation_ids}
    for key, value in (extra_params or {}).items():
        params.setdefault(key, value)
    db = SessionLocal()
    try:
        job = Job(id=job_id, type="evaluate", status="queued", total=len(conversation_ids), params=params)
        note = f", resumed from {params['resumed_from']}" if params.get("resumed_from") else ""
        _log(job, "info", f"queued {len(conversation_ids)} conversations, provider={provider}, mode={mode}{note}")
        db.add(job)
        db.commit()
    finally:
        db.close()
    _prune_registry()
    state = JobState(job_id)
    state.paid = bool(params.get("paid", provider not in FREE_PROVIDERS))
    _registry[job_id] = state
    th = threading.Thread(target=_worker, args=(state, conversation_ids, checklist_version_id, provider, model, mode, base_version_id, only_codes), daemon=True, name=job_id)
    try:
        th.start()
    except RuntimeError as e:  # cannot start a thread: do not leave a queued job nobody will run
        state.finished.set()
        state.finished_at = time.monotonic()
        _finish_crashed(state, e)
        raise
    return job_id


def _worker(state: JobState, ids: list[str], version_id: int, provider: str, model: str | None, mode: str, base_version_id: int | None, only_codes: list[str] | None) -> None:
    try:
        asyncio.run(_run(state, ids, version_id, provider, model, mode, base_version_id, only_codes))
    except BaseException as e:  # a crashed worker must not leave its job "running"
        log.exception("job %s: worker crashed", state.id)
        _finish_crashed(state, e)
    finally:
        state.finished_at = time.monotonic()
        state.finished.set()


def _finish_crashed(state: JobState, exc: BaseException) -> None:
    status = "failed"
    try:
        db = SessionLocal()
        try:
            job = db.get(Job, state.id)
            if job is None:
                status = "missing"
            elif job.status in ACTIVE_STATUSES:
                job.status = "failed"
                job.finished_at = _now()
                _log(job, "error", f"worker crashed: {type(exc).__name__}: {exc}"[:500])
                db.commit()
            else:
                status = job.status
        finally:
            db.close()
    except Exception:  # pragma: no cover - database gone as well
        log.exception("job %s: could not record the crash", state.id)
    state.emit("job.done", {"status": status})


def base_evaluation_id(db: Session, conversation_id: str, base_version_id: int | None) -> int | None:
    """The evaluation a partial run copies from: the dialog's latest completed evaluation on the base version."""
    if not base_version_id:
        return None
    return db.execute(select(Evaluation.id).where(Evaluation.conversation_id == conversation_id,
                                                  Evaluation.checklist_version_id == base_version_id,
                                                  Evaluation.status == "completed").order_by(Evaluation.id.desc())).scalars().first()


def _budget_stop(state: JobState) -> bool:
    """True when this paid job must not start another dialog: the 24 h spend reached the daily budget."""
    if not state.paid:
        return False
    from . import guards

    d = SessionLocal()
    try:
        exhausted = guards.budget_exhausted(d)
    except Exception as e:  # noqa: BLE001 - a failing check must not stop the job
        log.warning("job %s: budget check failed: %s", state.id, e)
        return False
    finally:
        d.close()
    if exhausted is None:
        return False
    if not state.cancel.is_set():
        state.stop_reason = "budget_exhausted"
        state.cancel.set()
        message = (f"stopped: daily budget exhausted (spent ${exhausted['spent_24h']:.2f} of ${exhausted['limit']:.2f} in 24 h); "
                   f"dialogs already running finish, the rest can be resumed later")
        log.warning("job %s: %s", state.id, message)
        d = SessionLocal()
        try:
            job = d.get(Job, state.id)
            if job is not None:
                _log(job, "warning", message)
                d.commit()
        finally:
            d.close()
        state.emit("job.budget_exhausted", exhausted)
    return True


async def _run(state: JobState, ids: list[str], version_id: int, provider: str, model: str | None, mode: str, base_version_id: int | None, only_codes: list[str] | None) -> None:
    try:
        await _run_dialogs(state, ids, version_id, provider, model, mode, base_version_id, only_codes)
    finally:
        try:  # the loop ends with the job: close its Claude client (pooled connections must not outlive their loop)
            from ..judge.providers.anthropic_provider import aclose_loop_client

            await aclose_loop_client()
        except Exception:  # noqa: BLE001
            pass


async def _run_dialogs(state: JobState, ids: list[str], version_id: int, provider: str, model: str | None, mode: str,
                       base_version_id: int | None, only_codes: list[str] | None) -> None:
    db = SessionLocal()
    try:
        job = db.get(Job, state.id)
        if job is None:  # the database was replaced (snapshot restore) before the worker started
            log.warning("job %s: job row not found, nothing to run", state.id)
            state.emit("job.done", {"status": "missing"})
            return
        job.status = "running"
        job.started_at = datetime.now(timezone.utc)
        db.commit()
    finally:
        db.close()

    sem = asyncio.Semaphore(settings.max_parallel_conversations)
    # [] is meaningful: a weight-only partial re-evaluation re-judges nothing (rows are copied, totals recomputed)
    codes = set(only_codes) if only_codes is not None else None

    def base_eval_for(cid: str) -> int | None:
        if mode != "partial" or not base_version_id:
            return None
        d = SessionLocal()
        try:
            return base_evaluation_id(d, cid, base_version_id)
        finally:
            d.close()

    async def one(cid: str) -> None:
        if state.cancel.is_set():
            return
        async with sem:
            if state.cancel.is_set() or _budget_stop(state):
                return
            t0 = time.perf_counter()
            try:
                base_id = base_eval_for(cid)
                eid = await evaluate_conversation(cid, version_id, provider_name=provider, model=model,
                                                  mode=("partial" if base_id else "full"), base_evaluation_id=base_id,
                                                  only_codes=(codes if base_id else None), job_id=state.id, on_event=state.emit)
                _progress(state, cid, ok=True, evaluation_id=eid, seconds=time.perf_counter() - t0)
            except Exception as e:
                log.exception("job %s: %s failed", state.id, cid)
                _progress(state, cid, ok=False, error=str(e), seconds=time.perf_counter() - t0)

    await asyncio.gather(*[one(cid) for cid in ids])

    if state.restored:  # the database was replaced while the job ran: never write into the restored one
        log.warning("job %s: database restored while the job ran; its final state is not written", state.id)
        state.emit("job.done", {"status": "missing"})
        return
    db = SessionLocal()
    try:
        job = db.get(Job, state.id)
        if job is None:  # the database was replaced while the job ran
            log.warning("job %s: job row disappeared before the job finished", state.id)
            state.emit("job.done", {"status": "missing"})
            return
        job.status = "cancelled" if state.cancel.is_set() else ("completed" if job.failed == 0 else "completed")
        job.finished_at = datetime.now(timezone.utc)
        reason = " (stopped: daily budget exhausted)" if state.stop_reason == "budget_exhausted" else ""
        _log(job, "info", f"finished: done={job.done} failed={job.failed} cost=${job.cost_usd:.2f}{reason}")
        db.commit()
        done = {"status": job.status, "done": job.done, "failed": job.failed, "cost_usd": job.cost_usd}
        if state.stop_reason:
            done["reason"] = state.stop_reason
        state.emit("job.done", done)
    finally:
        db.close()


def _progress(state: JobState, cid: str, *, ok: bool, evaluation_id: int | None = None, error: str | None = None, seconds: float = 0.0) -> None:
    if state.restored:
        log.warning("job %s: database restored, progress of %s not recorded", state.id, cid)
        return
    db = SessionLocal()
    try:
        job = db.get(Job, state.id)
        if job is None:  # restored database without this job: nothing to update
            log.warning("job %s: job row not found while recording %s", state.id, cid)
            return
        if ok:
            job.done += 1
            ev = db.get(Evaluation, evaluation_id) if evaluation_id else None
            if ev:
                job.cost_usd += ev.cost_usd or 0.0
                if ev.status == "failed":
                    job.failed += 1
                    job.done -= 1
                    _log(job, "error", f"{cid}: {ev.error}")
                else:
                    _log(job, "info", f"{cid}: total={ev.total_score} critical={ev.critical_error} cost=${ev.cost_usd:.3f} in {seconds:.0f}s")
        else:
            job.failed += 1
            _log(job, "error", f"{cid}: {error}")
        db.commit()
        state.emit("job.progress", {"done": job.done, "failed": job.failed, "total": job.total, "cost_usd": round(job.cost_usd, 4)})
    finally:
        db.close()


def cancel_job(job_id: str) -> bool:
    st = _registry.get(job_id)
    if not st:
        return False
    st.cancel.set()
    return True


def conversation_ids_for(db, ids: list[str] | None, all_: bool) -> list[str]:
    """`all=true` or `conversation_ids=null` = every conversation; an explicit empty list selects nothing (the caller
    answers 400 no_conversations) instead of silently meaning "everything"."""
    if all_ or ids is None:
        return list(db.execute(select(Conversation.id).order_by(Conversation.imported_at, Conversation.id)).scalars())
    return list(dict.fromkeys(str(i) for i in ids if str(i).strip()))


# --------------------------------------------------------------------------- start-up recovery


def _audit_record() -> Callable[..., Any] | None:
    """`cqa.audit.record` when the roles/audit package is installed, else None."""
    try:
        mod = importlib.import_module("cqa.audit")
    except Exception:
        return None
    fn = getattr(mod, "record", None)
    return fn if callable(fn) else None


def _call_audit(fn: Callable[..., Any], db: Session, **kwargs: Any) -> Any:
    """Call record(db, ...) passing only the keyword arguments it accepts (tolerates small signature differences)."""
    try:
        params = inspect.signature(fn).parameters
        if not any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
            kwargs = {k: v for k, v in kwargs.items() if k in params}
    except (TypeError, ValueError):  # pragma: no cover - builtins without a signature
        pass
    return fn(db, **kwargs)


def recover_stale_jobs(session_factory: Callable[[], Session] | None = None) -> dict[str, list]:
    """Mark work a previous process left in flight. Idempotent and never raises.

    - jobs in queued/running whose worker is not alive in this process → `interrupted` (finished_at, log entry, audit event);
    - evaluations in `running` not owned by a live job of this process → `failed`, error "interrupted by restart"
      (an evaluation without a job is only touched if it was created before this process started).
    Returns `{"jobs": [job ids], "evaluations": [evaluation ids]}`.
    """
    result: dict[str, list] = {"jobs": [], "evaluations": []}
    try:
        db = (session_factory or SessionLocal)()
    except Exception as e:
        log.warning("recover_stale_jobs: no database session: %s", e)
        return result
    try:
        live = live_job_ids()
        now = _now()
        failed_per_job: dict[str | None, int] = {}
        for ev in db.execute(select(Evaluation).where(Evaluation.status == "running").order_by(Evaluation.id)).scalars().all():
            if ev.job_id is not None and ev.job_id in live:
                continue
            if ev.job_id is None and ev.created_at is not None and _aware(ev.created_at) >= PROCESS_STARTED_AT:
                continue  # started by this process outside a job (still running)
            ev.status = "failed"
            ev.error = INTERRUPTED_ERROR
            ev.completed_at = now
            result["evaluations"].append(ev.id)
            failed_per_job[ev.job_id] = failed_per_job.get(ev.job_id, 0) + 1

        recovered: list[tuple[Job, str]] = []
        for job in db.execute(select(Job).where(Job.status.in_(ACTIVE_STATUSES)).order_by(Job.created_at)).scalars().all():
            if job.id in live:
                continue
            previous = job.status
            job.status = "interrupted"
            job.finished_at = now
            in_flight = failed_per_job.get(job.id, 0)
            _log(job, "warning", f"interrupted by restart (was {previous}): done={job.done} failed={job.failed} of {job.total}"
                                 + (f", {in_flight} evaluation(s) in flight marked failed" if in_flight else "")
                                 + f"; POST /api/jobs/{job.id}/resume re-queues the rest")
            recovered.append((job, previous))
        db.commit()
        result["jobs"] = [j.id for j, _ in recovered]
        if recovered or result["evaluations"]:
            log.warning("recovered %d interrupted job(s) %s and %d running evaluation(s)", len(recovered),
                        ", ".join(result["jobs"]), len(result["evaluations"]))

        record = _audit_record() if recovered else None
        for job, previous in recovered if record else []:
            try:
                _call_audit(record, db, action="job.interrupted", entity_type="job", entity_id=job.id,
                            summary=f"Задача {job.id} прервана перезапуском сервера: выполнено {job.done} из {job.total}",
                            details={"previous_status": previous, "done": job.done, "failed": job.failed, "total": job.total,
                                     "cost_usd": round(job.cost_usd or 0.0, 4), "evaluations_failed": failed_per_job.get(job.id, 0)},
                            actor={"role": "system", "name": None, "request_id": None})
                db.commit()
            except Exception as e:
                db.rollback()
                log.warning("recover_stale_jobs: audit event for %s not written: %s", job.id, e)
    except Exception as e:
        log.warning("recover_stale_jobs failed: %s", e)
        try:
            db.rollback()
        except Exception:  # pragma: no cover
            pass
    finally:
        try:
            db.close()
        except Exception:  # pragma: no cover
            pass
    return result


# --------------------------------------------------------------------------- resume


class ResumeError(Exception):
    """Why a job cannot be resumed: `code`, Russian `message`, HTTP `status` (404/409) and `extra` envelope fields."""

    def __init__(self, code: str, message: str, status: int = 409, extra: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.extra: dict[str, Any] = dict(extra or {})

    def envelope(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message, **self.extra}}


def resume_plan(db: Session, job: Job) -> dict[str, Any]:
    """Split the job's conversations into `pending` (no completed evaluation for the job's checklist version made by
    this job or after it), `completed` and `missing` (deleted since). Evaluations older than the job do not count:
    a re-evaluation job of already evaluated dialogs must still resume the ones it did not finish."""
    params = job.params or {}
    version_id = params.get("checklist_version_id")
    ids = list(dict.fromkeys(str(c) for c in (params.get("conversation_ids") or [])))
    existing: set[str] = set()
    completed: set[str] = set()
    if ids:
        existing = set(db.execute(select(Conversation.id).where(Conversation.id.in_(ids))).scalars())
        if version_id is not None:
            since = [Evaluation.created_at >= job.created_at] if job.created_at is not None else []
            completed = set(db.execute(select(Evaluation.conversation_id).where(
                Evaluation.conversation_id.in_(ids), Evaluation.checklist_version_id == version_id,
                Evaluation.status == "completed", *since)).scalars())
    return {"checklist_version_id": version_id,
            "pending": [c for c in ids if c in existing and c not in completed],
            "completed": [c for c in ids if c in completed],
            "missing": [c for c in ids if c not in existing]}


def resume_conflict(db: Session, job: Job) -> ResumeError | None:
    """A job that is still active, or whose resumed copy is still active, must not be resumed (double evaluation)."""
    if job.status in ACTIVE_STATUSES:
        return ResumeError("job_active", f"Задача {job.id} ещё выполняется (статус «{job.status}»). "
                                         f"Дождитесь её завершения или отмените её.", extra={"job_id": job.id, "status": job.status})
    child_id = (job.params or {}).get("resumed_as")
    if child_id:
        child = db.get(Job, child_id)
        if child is not None and child.status in ACTIVE_STATUSES:
            return ResumeError("already_resumed", f"Задача {job.id} уже возобновлена как {child.id}, и та ещё выполняется.",
                               extra={"job_id": child.id, "status": child.status})
    return None


def resume_job(job_id: str, conversation_ids: list[str] | None = None, *, extra_params: dict[str, Any] | None = None) -> str:
    """Start a new job with the same checklist version, provider, model and mode for the conversations of `job_id`
    that still lack a completed evaluation (or exactly `conversation_ids`). Returns the new job id.
    Raises ResumeError: not_found (404), job_active / already_resumed / no_checklist / nothing_to_resume (409)."""
    with start_lock:
        db = SessionLocal()
        try:
            job = db.get(Job, job_id)
            if job is None:
                raise ResumeError("not_found", f"Задача {job_id} не найдена.", status=404)
            conflict = resume_conflict(db, job)
            if conflict is not None:
                raise conflict
            params = dict(job.params or {})
            if not params.get("checklist_version_id"):
                raise ResumeError("no_checklist", f"В задаче {job_id} не записана версия чек-листа, возобновить её нельзя.")
            if conversation_ids is None:
                conversation_ids = resume_plan(db, job)["pending"]
            if not conversation_ids:
                raise ResumeError("nothing_to_resume", f"Все диалоги задачи {job_id} уже оценены по её версии чек-листа: возобновлять нечего.")
        finally:
            db.close()
        new_id = start_evaluation_job(list(conversation_ids), int(params["checklist_version_id"]),
                                      provider=params.get("provider") or "anthropic", model=params.get("model"),
                                      mode=params.get("mode") or "full", base_version_id=params.get("base_version_id"),
                                      only_codes=params.get("only_codes"), extra_params={**(extra_params or {}), "resumed_from": job_id})
        db = SessionLocal()
        try:
            job = db.get(Job, job_id)
            if job is not None:
                job.params = {**(job.params or {}), "resumed_as": new_id}
                _log(job, "info", f"resumed as {new_id}: {len(conversation_ids)} conversation(s) re-queued")
                db.commit()
        except Exception as e:  # the new job runs anyway; only the back-reference is missing
            db.rollback()
            log.warning("job %s: could not record the resume as %s: %s", job_id, new_id, e)
        finally:
            db.close()
    return new_id
