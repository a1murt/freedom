from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from ..checklist import service as svc
from ..config import settings
from ..serializers import iso_utc
from ..db import SessionLocal, get_db
from ..jobs import estimate as estimator
from ..jobs import guards
from ..jobs import runner as jobs
from ..judge.runner import KNOWN_PROVIDERS, UnknownModelError
from ..models import ChecklistVersion, Conversation, Job

router = APIRouter(prefix="/api", tags=["evaluations"])

# statuses after which the SSE stream sends job.done and closes ("interrupted": left by a restart, see /resume)
SSE_TERMINAL = ("completed", "failed", "cancelled", "interrupted", "missing")


class RunBody(BaseModel):
    conversation_ids: list[str] | None = None
    all: bool = False
    checklist_version_id: int | None = None
    provider: str | None = None  # omitted: the instance's default provider (Settings, else DEFAULT_PROVIDER)
    model: str | None = None
    mode: str = "full"  # full | partial
    base_version_id: int | None = None
    only_codes: list[str] | None = None
    dry_run: bool = False


def _job_dict(j: Job) -> dict:
    params = j.params or {}
    return {"id": j.id, "type": j.type, "status": j.status, "total": j.total, "done": j.done, "failed": j.failed, "cost_usd": round(j.cost_usd, 4),
            # UTC with an explicit offset, like every serializer timestamp (naive SQLite values are UTC)
            "params": j.params, "started_at": iso_utc(j.started_at),
            "finished_at": iso_utc(j.finished_at), "created_at": iso_utc(j.created_at),
            "log": (j.log or [])[-200:],
            # finished with unfinished conversations and not resumed yet → POST /api/jobs/{id}/resume is worth offering
            "resumable": j.status in jobs.TERMINAL_STATUSES and (j.done or 0) < (j.total or 0) and not params.get("resumed_as")}


def _error(status: int, code: str, message: str, **extra: Any) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message, **extra}})


def _guard_response(e: guards.GuardError) -> JSONResponse:
    headers = {"Retry-After": str(e.retry_after_s)} if e.retry_after_s else None
    return JSONResponse(status_code=e.status, content=e.envelope(), headers=headers)


def _provider_and_model(provider: str | None, model: str | None, db: Session | None = None) -> tuple[str, str | None] | JSONResponse:
    """`anthropic:<model>` is accepted as provider + model; any other unknown provider is a 422 (it used to run Claude).
    An omitted provider is the instance's default (the Settings row, else DEFAULT_PROVIDER), never Claude by accident."""
    from ..judge.runner import CLAUDE_DISABLED_MESSAGE
    from ..models import Setting

    name = (provider or "").strip()
    if not name:
        row = db.get(Setting, "default_provider") if db is not None else None
        name = str(row.value).strip() if row is not None and row.value else settings.default_provider
    base, _, tail = name.partition(":")
    base = base.strip().lower()
    if base not in KNOWN_PROVIDERS:
        message = f"Неизвестный провайдер «{name}»: доступны {', '.join(KNOWN_PROVIDERS)}."
        return _error(422, "unknown_provider", message, errors=[{"field": "provider", "message": message}])
    if base == "anthropic" and not settings.claude_enabled:
        return _error(422, "provider_unavailable", CLAUDE_DISABLED_MESSAGE,
                      errors=[{"field": "provider", "message": CLAUDE_DISABLED_MESSAGE}])
    return base, ((model or "").strip() or tail.strip() or None)


def _unknown_model(e: UnknownModelError) -> JSONResponse:
    return _error(422, "unknown_model", str(e), model=e.model, errors=[{"field": "model", "message": str(e)}])


def _estimate(db: Session, convs: list[Conversation], version: ChecklistVersion, provider: str, model: str | None, mode: str,
              base_version_id: int | None, only_codes: list[str] | None) -> estimator.RunEstimate:
    """Per-dialog estimate of what the worker will actually judge (cqa.jobs.estimate): dialogs without a completed base
    evaluation are priced in full even in a partial run, and a paid run is never priced at $0."""
    return estimator.estimate_run(db, convs, version, provider=provider, model=model, mode=mode, base_version_id=base_version_id,
                                  only_codes=only_codes)


def _too_long_message(est: estimator.RunEstimate) -> str:
    ids = [d["conversation_id"] for d in est.too_long]
    return (f"Диалоги слишком длинные для оценки ({', '.join(ids[:10])}{'…' if len(ids) > 10 else ''}): допускается до "
            f"{settings.max_transcript_chars} символов и {settings.max_transcript_turns} реплик.")


def _too_long(est: estimator.RunEstimate) -> JSONResponse:
    return _error(422, "transcript_too_long", _too_long_message(est), skipped=est.too_long)


@router.post("/evaluations/run")
def run_evaluations(body: RunBody, db: Session = Depends(get_db)):
    version = svc.get_version(db, body.checklist_version_id) if body.checklist_version_id else svc.active_version(db)
    if version is None:
        raise HTTPException(400, detail={"code": "no_checklist", "message": "Не найдена версия чек-листа для оценки."})
    resolved = _provider_and_model(body.provider, body.model, db)
    if isinstance(resolved, JSONResponse):
        return resolved
    provider, model = resolved
    ids = jobs.conversation_ids_for(db, body.conversation_ids, body.all)
    if not ids:
        raise HTTPException(400, detail={"code": "no_conversations", "message": "Нет диалогов для оценки: список conversation_ids пуст "
                                                                                 "(для всех диалогов передайте all=true)."})
    convs = db.execute(select(Conversation).where(Conversation.id.in_(ids))).scalars().all()
    if not convs:
        raise HTTPException(400, detail={"code": "no_conversations", "message": "Ни один из выбранных диалогов не найден."})
    try:
        est = _estimate(db, convs, version, provider, model, body.mode, body.base_version_id, body.only_codes)
    except UnknownModelError as e:
        return _unknown_model(e)
    skipped = {d["conversation_id"] for d in est.too_long}
    runnable = [c for c in convs if c.id not in skipped]
    summary: dict[str, Any] = {"queued": len(runnable), "estimated_cost_usd": est.total_usd, "rejudge_codes": est.rejudge_codes,
                               "checklist_version_id": version.id, "estimate": est.detail()}
    if est.too_long:
        summary["skipped"] = est.too_long
    if not runnable:
        if body.dry_run:  # dry runs always answer: the reason a real run would be refused is the guard
            return {**summary, "budget": guards.budget_status(db),
                    "guard": {"code": "transcript_too_long", "message": _too_long_message(est), "skipped": est.too_long}}
        return _too_long(est)
    if body.dry_run:
        # dry runs always answer; `guard` tells the UI whether a real run would be refused right now, and why
        try:
            budget, guard = guards.check_run(db, len(runnable), est.total_usd, free=est.free), None
        except guards.GuardError as e:
            budget, guard = guards.budget_status(db), {"code": e.code, "message": e.message, **e.extra}
        return {**summary, "budget": budget, "guard": guard}
    with jobs.start_lock:
        try:
            guards.check_run(db, len(runnable), est.total_usd, free=est.free)
        except guards.GuardError as e:
            return _guard_response(e)
        job_id = jobs.start_evaluation_job([c.id for c in runnable], version.id, provider=provider, model=model, mode=body.mode,
                                           base_version_id=body.base_version_id, only_codes=est.rejudge_codes,
                                           extra_params={"estimated_cost_usd": est.total_usd, "paid": not est.free})
    # budget after queueing: this job's estimate is already counted as reserved
    return {"job_id": job_id, **summary, "budget": guards.budget_status(db)}


@router.get("/jobs")
def list_jobs(db: Session = Depends(get_db)):
    js = db.execute(select(Job).order_by(Job.created_at.desc()).limit(50)).scalars().all()
    return [_job_dict(j) for j in js]


@router.get("/jobs/{job_id}")
def get_job(job_id: str, db: Session = Depends(get_db)):
    j = db.get(Job, job_id)
    if j is None:
        raise HTTPException(404, detail={"code": "not_found", "message": f"Задача {job_id} не найдена."})
    return _job_dict(j)


@router.post("/jobs/{job_id}/cancel")
def cancel(job_id: str, db: Session = Depends(get_db)):
    if not jobs.cancel_job(job_id):
        raise HTTPException(404, detail={"code": "not_found", "message": f"Задача {job_id} сейчас не выполняется."})
    j = db.get(Job, job_id)
    return _job_dict(j) if j else {"id": job_id, "status": "cancelling"}


def _nothing_to_resume(job_id: str, plan: dict[str, Any], version: ChecklistVersion) -> JSONResponse:
    if plan["completed"]:
        message = (f"Возобновлять нечего: у всех диалогов задачи {job_id} ({len(plan['completed'])}) уже есть завершённая "
                   f"оценка по версии чек-листа {version.version_no}.")
        if plan["missing"]:
            message += f" Удалено диалогов: {len(plan['missing'])}."
    else:
        message = f"Возобновлять нечего: диалогов задачи {job_id} больше нет в базе."
    return _error(409, "nothing_to_resume", message, completed=len(plan["completed"]), missing=len(plan["missing"]))


@router.post("/jobs/{job_id}/resume")
def resume_job(job_id: str, db: Session = Depends(get_db)):
    """Re-queue the job's conversations that still have no completed evaluation for its checklist version
    (same provider, model and mode). Answers like /api/evaluations/run with the new job."""
    job = db.get(Job, job_id)
    if job is None:
        return _error(404, "not_found", f"Задача {job_id} не найдена.")
    with jobs.start_lock:
        db.refresh(job)
        conflict = jobs.resume_conflict(db, job)
        if conflict is not None:
            return JSONResponse(status_code=conflict.status, content=conflict.envelope())
        plan = jobs.resume_plan(db, job)
        version = svc.get_version(db, plan["checklist_version_id"]) if plan["checklist_version_id"] else None
        if version is None:
            return _error(409, "no_checklist", f"Версия чек-листа задачи {job_id} не найдена: возобновить задачу нельзя.")
        if not plan["pending"]:
            return _nothing_to_resume(job_id, plan, version)
        params = job.params or {}
        convs = db.execute(select(Conversation).where(Conversation.id.in_(plan["pending"]))).scalars().all()
        try:
            est = _estimate(db, convs, version, params.get("provider") or "anthropic", params.get("model"), params.get("mode") or "full",
                            params.get("base_version_id"), params.get("only_codes"))
        except UnknownModelError as e:
            return _unknown_model(e)
        skipped = {d["conversation_id"] for d in est.too_long}
        pending = [c for c in plan["pending"] if c not in skipped]
        if not pending:
            return _too_long(est)
        try:
            guards.check_run(db, len(pending), est.total_usd, free=est.free)
        except guards.GuardError as e:
            return _guard_response(e)
        try:
            new_id = jobs.resume_job(job_id, pending, extra_params={"estimated_cost_usd": est.total_usd, "paid": not est.free})
        except jobs.ResumeError as e:
            return JSONResponse(status_code=e.status, content=e.envelope())
    out = {"job_id": new_id, "queued": len(pending), "estimated_cost_usd": est.total_usd, "rejudge_codes": est.rejudge_codes,
           "checklist_version_id": version.id, "budget": guards.budget_status(db), "resumed_from": job_id,
           "skipped_completed": len(plan["completed"]), "skipped_missing": len(plan["missing"]), "estimate": est.detail()}
    if est.too_long:
        out["skipped"] = est.too_long
    return out


def _job_status_row(job_id: str) -> tuple[str, dict[str, Any] | None]:
    """(status, progress) of a job from the database; runs in the thread pool, never on the event loop."""
    db = SessionLocal()
    try:
        j = db.get(Job, job_id)
        if j is None:
            return "missing", None
        return j.status, {"status": j.status, "done": j.done, "failed": j.failed, "total": j.total, "cost_usd": round(j.cost_usd, 4)}
    finally:
        db.close()


@router.get("/jobs/{job_id}/events")
async def job_events(job_id: str):
    async def gen():
        seq = 0
        last_status = None
        idle = 0
        done_sent = False  # the worker's own job.done (with done/failed/cost) makes the closing one redundant
        while True:
            st = jobs.get_state(job_id)
            if st:
                for s, kind, data in st.since(seq):
                    seq = s
                    done_sent = done_sent or kind == "job.done"
                    yield f"id: {s}\nevent: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
            status, row = await run_in_threadpool(_job_status_row, job_id)
            if row is not None and status != last_status:
                last_status = status
                yield f"event: job.status\ndata: {json.dumps(row, ensure_ascii=False)}\n\n"
            if status in SSE_TERMINAL:
                if not done_sent:  # exactly one job.done per stream
                    yield f"event: job.done\ndata: {json.dumps({'status': status}, ensure_ascii=False)}\n\n"
                break
            idle += 1
            if idle % 20 == 0:
                yield ": keepalive\n\n"
            await asyncio.sleep(0.7)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
