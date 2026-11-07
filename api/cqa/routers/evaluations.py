from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..checklist import service as svc
from ..db import SessionLocal, get_db
from ..jobs import runner as jobs
from ..judge.runner import estimate_cost
from ..models import Conversation, Job

router = APIRouter(prefix="/api", tags=["evaluations"])


class RunBody(BaseModel):
    conversation_ids: list[str] | None = None
    all: bool = False
    checklist_version_id: int | None = None
    provider: str = "anthropic"
    model: str | None = None
    mode: str = "full"  # full | partial
    base_version_id: int | None = None
    only_codes: list[str] | None = None
    dry_run: bool = False


def _job_dict(j: Job) -> dict:
    return {"id": j.id, "type": j.type, "status": j.status, "total": j.total, "done": j.done, "failed": j.failed, "cost_usd": round(j.cost_usd, 4),
            "params": j.params, "started_at": j.started_at.isoformat() if j.started_at else None,
            "finished_at": j.finished_at.isoformat() if j.finished_at else None, "created_at": j.created_at.isoformat() if j.created_at else None,
            "log": (j.log or [])[-200:]}


@router.post("/evaluations/run")
def run_evaluations(body: RunBody, db: Session = Depends(get_db)):
    version = svc.get_version(db, body.checklist_version_id) if body.checklist_version_id else svc.active_version(db)
    if version is None:
        raise HTTPException(400, detail={"code": "no_checklist", "message": "no checklist version"})
    ids = jobs.conversation_ids_for(db, body.conversation_ids, body.all)
    if not ids:
        raise HTTPException(400, detail={"code": "no_conversations", "message": "nothing to evaluate"})
    convs = db.execute(select(Conversation).where(Conversation.id.in_(ids))).scalars().all()
    n_calls = sum(1 for c in convs if c.channel == "voice call")
    n_chats = len(convs) - n_calls
    only_codes = body.only_codes
    if body.mode == "partial" and body.base_version_id and not only_codes:
        base = svc.get_version(db, body.base_version_id)
        if base is not None:
            only_codes = svc.diff_versions(base, version)["rejudge_codes"]
    est = estimate_cost(n_chats, n_calls, body.provider)
    if body.mode == "partial" and only_codes is not None:
        est = round(est * max(len(only_codes), 0) / max(len(version.criteria), 1), 2)
    if body.dry_run:
        return {"queued": len(convs), "estimated_cost_usd": est, "rejudge_codes": only_codes, "checklist_version_id": version.id}
    job_id = jobs.start_evaluation_job([c.id for c in convs], version.id, provider=body.provider, model=body.model, mode=body.mode,
                                       base_version_id=body.base_version_id, only_codes=only_codes)
    return {"job_id": job_id, "queued": len(convs), "estimated_cost_usd": est, "rejudge_codes": only_codes, "checklist_version_id": version.id}


@router.get("/jobs")
def list_jobs(db: Session = Depends(get_db)):
    js = db.execute(select(Job).order_by(Job.created_at.desc()).limit(50)).scalars().all()
    return [_job_dict(j) for j in js]


@router.get("/jobs/{job_id}")
def get_job(job_id: str, db: Session = Depends(get_db)):
    j = db.get(Job, job_id)
    if j is None:
        raise HTTPException(404, detail={"code": "not_found", "message": "job not found"})
    return _job_dict(j)


@router.post("/jobs/{job_id}/cancel")
def cancel(job_id: str, db: Session = Depends(get_db)):
    if not jobs.cancel_job(job_id):
        raise HTTPException(404, detail={"code": "not_found", "message": "job not running"})
    j = db.get(Job, job_id)
    return _job_dict(j) if j else {"id": job_id, "status": "cancelling"}


@router.get("/jobs/{job_id}/events")
async def job_events(job_id: str):
    async def gen():
        seq = 0
        last_status = None
        idle = 0
        while True:
            st = jobs.get_state(job_id)
            if st:
                for s, kind, data in st.since(seq):
                    seq = s
                    yield f"id: {s}\nevent: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
            db = SessionLocal()
            try:
                j = db.get(Job, job_id)
                status = j.status if j else "missing"
                if j and status != last_status:
                    last_status = status
                    yield f"event: job.status\ndata: {json.dumps({'status': status, 'done': j.done, 'failed': j.failed, 'total': j.total, 'cost_usd': round(j.cost_usd, 4)}, ensure_ascii=False)}\n\n"
            finally:
                db.close()
            if status in ("completed", "failed", "cancelled", "missing"):
                yield f"event: job.done\ndata: {json.dumps({'status': status}, ensure_ascii=False)}\n\n"
                break
            idle += 1
            if idle % 20 == 0:
                yield ": keepalive\n\n"
            await asyncio.sleep(0.7)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
