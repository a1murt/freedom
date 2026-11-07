"""Background batch jobs: a worker thread with its own asyncio loop; progress in the Job table and an in-memory event log."""
from __future__ import annotations

import asyncio
import logging
import secrets
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from ..config import settings
from ..db import SessionLocal
from ..models import Conversation, Evaluation, Job
from ..judge.runner import evaluate_conversation

log = logging.getLogger("cqa.jobs")


class JobState:
    def __init__(self, job_id: str):
        self.id = job_id
        self.events: deque[tuple[int, str, dict[str, Any]]] = deque(maxlen=5000)
        self.seq = 0
        self.cancel = threading.Event()
        self.lock = threading.Lock()

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


def _log(job: Job, level: str, message: str) -> None:
    job.log = (job.log or []) + [{"ts": datetime.now(timezone.utc).isoformat(), "level": level, "message": message}]


def start_evaluation_job(conversation_ids: list[str], checklist_version_id: int, *, provider: str, model: str | None, mode: str,
                         base_version_id: int | None, only_codes: list[str] | None) -> str:
    job_id = "j_" + secrets.token_hex(6)
    db = SessionLocal()
    try:
        job = Job(id=job_id, type="evaluate", status="queued", total=len(conversation_ids),
                  params={"checklist_version_id": checklist_version_id, "provider": provider, "model": model, "mode": mode,
                          "base_version_id": base_version_id, "only_codes": only_codes, "conversation_ids": conversation_ids})
        _log(job, "info", f"queued {len(conversation_ids)} conversations, provider={provider}, mode={mode}")
        db.add(job)
        db.commit()
    finally:
        db.close()
    state = JobState(job_id)
    _registry[job_id] = state
    th = threading.Thread(target=_worker, args=(state, conversation_ids, checklist_version_id, provider, model, mode, base_version_id, only_codes), daemon=True, name=job_id)
    th.start()
    return job_id


def _worker(state: JobState, ids: list[str], version_id: int, provider: str, model: str | None, mode: str, base_version_id: int | None, only_codes: list[str] | None) -> None:
    asyncio.run(_run(state, ids, version_id, provider, model, mode, base_version_id, only_codes))


async def _run(state: JobState, ids: list[str], version_id: int, provider: str, model: str | None, mode: str, base_version_id: int | None, only_codes: list[str] | None) -> None:
    db = SessionLocal()
    try:
        job = db.get(Job, state.id)
        job.status = "running"
        job.started_at = datetime.now(timezone.utc)
        db.commit()
    finally:
        db.close()

    sem = asyncio.Semaphore(settings.max_parallel_conversations)
    codes = set(only_codes) if only_codes else None

    def base_eval_for(cid: str) -> int | None:
        if mode != "partial" or not base_version_id:
            return None
        d = SessionLocal()
        try:
            ev = d.execute(select(Evaluation).where(Evaluation.conversation_id == cid, Evaluation.checklist_version_id == base_version_id,
                                                    Evaluation.status == "completed").order_by(Evaluation.id.desc())).scalars().first()
            return ev.id if ev else None
        finally:
            d.close()

    async def one(cid: str) -> None:
        if state.cancel.is_set():
            return
        async with sem:
            if state.cancel.is_set():
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

    db = SessionLocal()
    try:
        job = db.get(Job, state.id)
        job.status = "cancelled" if state.cancel.is_set() else ("completed" if job.failed == 0 else "completed")
        job.finished_at = datetime.now(timezone.utc)
        _log(job, "info", f"finished: done={job.done} failed={job.failed} cost=${job.cost_usd:.2f}")
        db.commit()
        state.emit("job.done", {"status": job.status, "done": job.done, "failed": job.failed, "cost_usd": job.cost_usd})
    finally:
        db.close()


def _progress(state: JobState, cid: str, *, ok: bool, evaluation_id: int | None = None, error: str | None = None, seconds: float = 0.0) -> None:
    db = SessionLocal()
    try:
        job = db.get(Job, state.id)
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
    if all_ or not ids:
        return list(db.execute(select(Conversation.id).order_by(Conversation.imported_at, Conversation.id)).scalars())
    return ids
