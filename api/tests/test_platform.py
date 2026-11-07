"""Platform package: request id middleware, run guards, start-up recovery, resume, system endpoints, SSE through the middleware.

Every test uses its own SQLite file in tmp_path (worker threads open their own connections), never api/data/cqa.db, and
the judge is replaced by an offline fake: nothing here calls the Claude API or any other network service.
"""
from __future__ import annotations

import asyncio
import logging
import sys
import threading
import time
import types
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from cqa.config import settings
from cqa.db import Base, get_db
from cqa.jobs import guards, runner
from cqa.models import (
    Appeal,
    Checklist,
    ChecklistVersion,
    Conversation,
    Criterion,
    Evaluation,
    EvaluationCriterion,
    Job,
    Setting,
)
from cqa.platform import requestlog
from cqa.routers import evaluations as evaluations_router
from cqa.routers import system as system_router

REAL_AUDIT_RECORD = runner._audit_record  # captured before the env fixture isolates tests from the real cqa.audit


def utc(**delta: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(**delta)


def wait_for(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


# ----------------------------------------------------------------------------- fixtures


class FakeJudge:
    """Stands in for cqa.judge.runner.evaluate_conversation: no network; writes a completed evaluation (or raises)."""

    def __init__(self, factory: sessionmaker, cost: float = 0.05):
        self.factory = factory
        self.cost = cost
        self.fail: set[str] = set()
        self.gate = threading.Event()
        self.gate.set()
        self.calls: list[tuple[str, str | None, str]] = []

    async def __call__(self, conversation_id: str, checklist_version_id: int, *, provider_name: str = "anthropic", model=None,
                       mode: str = "full", base_evaluation_id=None, only_codes=None, job_id=None, on_event=None) -> int:
        self.calls.append((conversation_id, job_id, provider_name))
        await asyncio.to_thread(self.gate.wait, 10)
        if conversation_id in self.fail:
            raise RuntimeError("judge unavailable")
        db = self.factory()
        try:
            ev = Evaluation(conversation_id=conversation_id, checklist_version_id=checklist_version_id, status="completed",
                            provider=provider_name, model="fake:offline", job_id=job_id, cost_usd=self.cost, latency_ms=1200,
                            total_score=90.0, final_total_score=90.0, completed_at=datetime.now(timezone.utc))
            db.add(ev)
            db.commit()
            if on_event:
                on_event("evaluation.done", {"conversation_id": conversation_id, "evaluation_id": ev.id, "status": "completed"})
            return ev.id
        finally:
            db.close()


@dataclass
class Env:
    factory: sessionmaker
    judge: FakeJudge
    registry: dict


@pytest.fixture()
def env(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'platform.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)
    judge = FakeJudge(factory)
    registry: dict = {}
    # every module that opens its own session points at the test database; the judge is offline
    monkeypatch.setattr(runner, "SessionLocal", factory)
    monkeypatch.setattr(evaluations_router, "SessionLocal", factory)
    monkeypatch.setattr(runner, "evaluate_conversation", judge)
    monkeypatch.setattr(runner, "_registry", registry)
    monkeypatch.setattr(runner, "_audit_record", lambda: None)  # isolate from the roles package's real audit module
    monkeypatch.setattr(settings, "daily_budget_usd", 75.0)
    monkeypatch.setattr(settings, "max_dialogs_per_run", 200)
    monkeypatch.setattr(settings, "max_concurrent_jobs", 2)
    monkeypatch.setattr(settings, "local_endpoint", None)
    system_router._probe_cache.clear()
    yield Env(factory, judge, registry)
    judge.gate.set()
    for st in list(registry.values()):
        st.finished.wait(5)
    system_router._probe_cache.clear()
    engine.dispose()


def _envelope(request: Request, exc: HTTPException) -> JSONResponse:  # same as cqa.main
    detail = exc.detail if isinstance(exc.detail, dict) else {"code": "error", "message": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content={"error": detail})


def make_app(env: Env) -> FastAPI:
    app = FastAPI()
    app.add_exception_handler(HTTPException, _envelope)
    app.include_router(evaluations_router.router)
    app.include_router(system_router.router)
    requestlog.install(app)

    def override():
        db = env.factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    return app


@pytest.fixture()
def client(env):
    with TestClient(make_app(env)) as c:  # the test app has no lifespan: nothing touches the demo database
        yield c


@pytest.fixture()
def db(env):
    s = env.factory()
    yield s
    s.close()


# ----------------------------------------------------------------------------- seed helpers


def seed_version(db: Session) -> ChecklistVersion:
    cl = Checklist(name="Тестовый чек-лист")
    db.add(cl)
    db.flush()
    v = ChecklistVersion(checklist_id=cl.id, version_no=1, is_active=True, scoring_rules={}, sections=[{"code": "S1", "title": "Все"}])
    db.add(v)
    db.flush()
    for i, code in enumerate(("C1", "C2"), start=1):
        db.add(Criterion(checklist_version_id=v.id, code=code, order=i, section="S1", title=code, instruction="—", answer_type="binary",
                         options=[{"value": "yes", "label": "Да", "points": 1}, {"value": "no", "label": "Нет", "points": 0}],
                         max_points=1, weight=50))
    db.commit()
    return v


def seed_convs(db: Session, ids: list[str], channel: str = "agent chat") -> list[str]:
    for cid in ids:
        db.add(Conversation(id=cid, channel=channel, language="RU", turn_count=0))
    db.commit()
    return ids


def add_eval(db: Session, cid: str, version_id: int, *, status: str = "completed", cost: float = 0.0, created_at: datetime | None = None,
             latency: int = 0, provider: str = "anthropic", model: str = "anthropic:claude-opus-5-5", tokens: tuple[int, int] = (0, 0),
             job_id: str | None = None, completed_at: datetime | None = None) -> Evaluation:
    ev = Evaluation(conversation_id=cid, checklist_version_id=version_id, status=status, provider=provider, model=model, cost_usd=cost,
                    latency_ms=latency, input_tokens=tokens[0], output_tokens=tokens[1], job_id=job_id,
                    created_at=created_at or utc(), completed_at=completed_at or (utc() if status in ("completed", "failed") else None))
    db.add(ev)
    db.commit()
    return ev


def add_job(db: Session, job_id: str, status: str, conversation_ids: list[str], version_id: int, *, done: int = 0, failed: int = 0,
            cost: float = 0.0, **params) -> Job:
    job = Job(id=job_id, type="evaluate", status=status, total=len(conversation_ids), done=done, failed=failed, cost_usd=cost,
              params={"checklist_version_id": version_id, "provider": "anthropic", "model": None, "mode": "full", "base_version_id": None,
                      "only_codes": None, "conversation_ids": conversation_ids, **params},
              log=[{"ts": utc().isoformat(), "level": "info", "message": "queued"}])
    db.add(job)
    db.commit()
    return job


def job_row(env: Env, job_id: str) -> Job:
    s = env.factory()
    try:
        return s.get(Job, job_id)
    finally:
        s.close()


def wait_job(env: Env, job_id: str) -> Job:
    assert wait_for(lambda: job_id in env.registry and env.registry[job_id].finished.is_set()), f"job {job_id} did not finish"
    return job_row(env, job_id)


# ----------------------------------------------------------------------------- guards


def test_budget_status_counts_only_the_last_24_hours(db):
    v = seed_version(db)
    seed_convs(db, ["a"])
    add_eval(db, "a", v.id, cost=10.0, created_at=utc(hours=1))
    add_eval(db, "a", v.id, cost=5.0, created_at=utc(hours=23), status="failed")  # failed evaluations cost money too
    add_eval(db, "a", v.id, cost=100.0, created_at=utc(hours=25))  # outside the window
    b = guards.budget_status(db)
    assert b == {"spent_24h": 15.0, "limit": 75.0, "remaining": 60.0, "reserved": 0.0, "unlimited": False, "window_hours": 24}


def test_budget_zero_means_unlimited(db, monkeypatch):
    monkeypatch.setattr(settings, "daily_budget_usd", 0)
    v = seed_version(db)
    seed_convs(db, ["a"])
    add_eval(db, "a", v.id, cost=500.0)
    b = guards.budget_status(db)
    assert b["unlimited"] is True and b["limit"] is None and b["remaining"] is None and b["spent_24h"] == 500.0
    assert guards.check_run(db, 10, 1_000_000.0)["unlimited"] is True


def test_check_run_budget_exceeded_has_numbers_and_retry_hint(db, monkeypatch):
    monkeypatch.setattr(settings, "daily_budget_usd", 20.0)
    v = seed_version(db)
    seed_convs(db, ["a"])
    add_eval(db, "a", v.id, cost=10.0, created_at=utc(hours=1))
    add_eval(db, "a", v.id, cost=5.0, created_at=utc(hours=23))
    assert guards.check_run(db, 1, 5.0)["remaining"] == 5.0  # exactly at the limit is allowed
    with pytest.raises(guards.GuardError) as ei:
        guards.check_run(db, 1, 6.0)
    e = ei.value
    assert (e.code, e.status) == ("budget_exceeded", 429)
    for part in ("Превышен суточный бюджет", "$15.00", "$20.00", "$6.00", "24 ч", "освободится примерно через 1 ч"):
        assert part in e.message, part
    # the $5 evaluation from 23 h ago leaves the window in about an hour, which frees enough for this run
    assert 3500 < e.retry_after_s <= 3700
    body = e.envelope()["error"]
    assert body["code"] == "budget_exceeded" and body["estimate_usd"] == 6.0 and body["budget"]["spent_24h"] == 15.0


def test_run_costing_more_than_the_whole_budget_gets_no_retry_hint(db, monkeypatch):
    monkeypatch.setattr(settings, "daily_budget_usd", 20.0)
    with pytest.raises(guards.GuardError) as ei:
        guards.check_run(db, 150, 25.0)
    assert ei.value.retry_after_s is None and "дороже всего суточного бюджета" in ei.value.message


def test_free_runs_are_not_blocked_by_an_exhausted_budget(db, monkeypatch):
    monkeypatch.setattr(settings, "daily_budget_usd", 1.0)
    v = seed_version(db)
    seed_convs(db, ["a"])
    add_eval(db, "a", v.id, cost=3.0)
    assert guards.check_run(db, 5, 0.0)["remaining"] == 0.0  # local model: estimate $0


def test_active_jobs_reserve_their_remaining_estimate(db, monkeypatch):
    monkeypatch.setattr(settings, "daily_budget_usd", 20.0)
    v = seed_version(db)
    seed_convs(db, ["a", "b"])
    add_job(db, "j_run", "running", ["a", "b"], v.id, cost=2.0, estimated_cost_usd=12.0)
    add_eval(db, "a", v.id, cost=2.0, job_id="j_run")
    b = guards.budget_status(db)
    assert (b["spent_24h"], b["reserved"], b["remaining"]) == (2.0, 10.0, 8.0)
    assert guards.check_run(db, 1, 8.0)["remaining"] == 8.0
    with pytest.raises(guards.GuardError) as ei:
        guards.check_run(db, 1, 9.0)
    assert "зарезервировано" in ei.value.message and "$10.00" in ei.value.message


def test_too_many_jobs(db, monkeypatch):
    v = seed_version(db)
    seed_convs(db, ["a"])
    add_job(db, "j_1", "queued", ["a"], v.id)
    add_job(db, "j_2", "running", ["a"], v.id)
    add_job(db, "j_3", "completed", ["a"], v.id)
    add_job(db, "j_4", "interrupted", ["a"], v.id)
    with pytest.raises(guards.GuardError) as ei:
        guards.check_run(db, 1, 0.1)
    e = ei.value
    assert e.code == "too_many_jobs" and e.status == 429
    assert e.extra == {"running": 2, "limit": 2, "active_job_ids": ["j_1", "j_2"]}
    assert "активно 2 из 2" in e.message
    monkeypatch.setattr(settings, "max_concurrent_jobs", 3)
    guards.check_run(db, 1, 0.1)
    monkeypatch.setattr(settings, "max_concurrent_jobs", 0)  # 0 disables the limit
    guards.check_run(db, 1, 0.1)


def test_too_many_dialogs_is_checked_first(db, monkeypatch):
    monkeypatch.setattr(settings, "daily_budget_usd", 0.01)
    assert guards.check_run(db, 200, 0.0)
    with pytest.raises(guards.GuardError) as ei:
        guards.check_run(db, 201, 999.0)
    e = ei.value
    assert e.code == "too_many_dialogs" and e.extra == {"requested": 201, "limit": 200}
    assert "201" in e.message and "200" in e.message
    monkeypatch.setattr(settings, "max_dialogs_per_run", 0)
    with pytest.raises(guards.GuardError) as ei:  # now the budget is the reason
        guards.check_run(db, 201, 999.0)
    assert ei.value.code == "budget_exceeded"


def test_plural_ru():
    forms = ("задача", "задачи", "задач")
    assert [guards.plural_ru(n, forms) for n in (1, 2, 5, 11, 21, 22, 112)] == ["задача", "задачи", "задач", "задач", "задача", "задачи", "задач"]


# ----------------------------------------------------------------------------- guards in the evaluations router


def test_run_is_refused_with_429_envelope_when_the_budget_is_spent(client, env, db):
    v = seed_version(db)
    seed_convs(db, ["a", "b"])
    add_eval(db, "a", v.id, cost=74.9)
    r = client.post("/api/evaluations/run", json={"conversation_ids": ["a", "b"]})
    assert r.status_code == 429
    err = r.json()["error"]
    assert err["code"] == "budget_exceeded" and err["estimate_usd"] == 0.36
    assert "$74.90" in err["message"] and "$75.00" in err["message"]
    assert set(err["budget"]) >= {"spent_24h", "limit", "remaining"}
    assert r.headers.get("retry-after") and r.headers.get("x-request-id")
    assert db.scalar(select(func.count()).select_from(Job)) == 0 and env.judge.calls == []


def test_dry_run_always_answers_with_budget_and_guard(client, db):
    v = seed_version(db)
    seed_convs(db, ["a", "b"])
    r = client.post("/api/evaluations/run", json={"conversation_ids": ["a", "b"], "dry_run": True})
    assert r.status_code == 200
    body = r.json()
    assert body["queued"] == 2 and body["estimated_cost_usd"] == 0.36 and body["checklist_version_id"] == v.id
    assert body["budget"]["limit"] == 75.0 and body["budget"]["remaining"] == 75.0 and body["guard"] is None
    add_eval(db, "a", v.id, cost=80.0)
    body = client.post("/api/evaluations/run", json={"conversation_ids": ["a", "b"], "dry_run": True}).json()
    assert body["guard"]["code"] == "budget_exceeded" and body["budget"]["remaining"] == 0.0
    assert db.scalar(select(func.count()).select_from(Job)) == 0


def test_run_is_refused_when_too_many_jobs_are_active(client, db):
    v = seed_version(db)
    seed_convs(db, ["a"])
    add_job(db, "j_1", "running", ["a"], v.id)
    add_job(db, "j_2", "queued", ["a"], v.id)
    r = client.post("/api/evaluations/run", json={"conversation_ids": ["a"]})
    assert r.status_code == 429 and r.json()["error"]["code"] == "too_many_jobs"
    assert r.json()["error"]["active_job_ids"] == ["j_1", "j_2"]


def test_run_is_refused_when_too_many_dialogs(client, db, monkeypatch):
    monkeypatch.setattr(settings, "max_dialogs_per_run", 1)
    seed_version(db)
    seed_convs(db, ["a", "b"])
    r = client.post("/api/evaluations/run", json={"all": True})
    assert r.status_code == 429 and r.json()["error"]["code"] == "too_many_dialogs"
    assert r.json()["error"]["requested"] == 2


def test_run_starts_a_job_reserves_its_estimate_and_reports_budget(client, env, db):
    seed_version(db)
    seed_convs(db, ["a", "b"])
    env.judge.gate.clear()  # keep the job running while we look at the budget
    r = client.post("/api/evaluations/run", json={"conversation_ids": ["a", "b"]})
    assert r.status_code == 200
    body = r.json()
    assert set(body) >= {"job_id", "queued", "estimated_cost_usd", "rejudge_codes", "checklist_version_id", "budget"}
    assert body["queued"] == 2 and body["budget"]["reserved"] == 0.36 and body["budget"]["remaining"] == round(75 - 0.36, 4)
    job = job_row(env, body["job_id"])
    assert job.params["estimated_cost_usd"] == 0.36 and sorted(job.params["conversation_ids"]) == ["a", "b"]
    env.judge.gate.set()
    job = wait_job(env, body["job_id"])
    assert (job.status, job.done, job.failed) == ("completed", 2, 0)
    assert client.get(f"/api/jobs/{job.id}").json()["resumable"] is False


def test_run_with_unknown_conversation_ids_is_a_400(client, db):
    seed_version(db)
    r = client.post("/api/evaluations/run", json={"conversation_ids": ["nope"]})
    assert r.status_code == 400 and r.json()["error"]["code"] == "no_conversations"


# ----------------------------------------------------------------------------- start-up recovery


def _stale_world(db: Session) -> dict:
    v = seed_version(db)
    seed_convs(db, ["a", "b", "c"])
    add_job(db, "j_q", "queued", ["a"], v.id)
    add_job(db, "j_r", "running", ["a", "b", "c"], v.id, done=1)
    add_job(db, "j_c", "completed", ["a"], v.id, done=1)
    before_boot = runner.PROCESS_STARTED_AT - timedelta(hours=1)
    return {
        "in_job": add_eval(db, "b", v.id, status="running", job_id="j_r").id,
        "orphan_old": add_eval(db, "c", v.id, status="running", created_at=before_boot).id,
        "orphan_new": add_eval(db, "c", v.id, status="running").id,  # created by this process outside a job: still running
        "done": add_eval(db, "a", v.id, status="completed", job_id="j_r").id,
    }


def test_recover_marks_jobs_interrupted_and_running_evaluations_failed(env, db):
    ids = _stale_world(db)
    result = runner.recover_stale_jobs()
    assert result == {"jobs": ["j_q", "j_r"], "evaluations": sorted([ids["in_job"], ids["orphan_old"]])}
    s = env.factory()
    try:
        for jid in ("j_q", "j_r"):
            j = s.get(Job, jid)
            assert j.status == "interrupted" and j.finished_at is not None
            assert j.log[-1]["level"] == "warning" and "interrupted by restart" in j.log[-1]["message"]
            assert f"/api/jobs/{jid}/resume" in j.log[-1]["message"]
        assert "1 evaluation(s) in flight marked failed" in s.get(Job, "j_r").log[-1]["message"]
        assert s.get(Job, "j_c").status == "completed" and len(s.get(Job, "j_c").log) == 1
        for key in ("in_job", "orphan_old"):
            ev = s.get(Evaluation, ids[key])
            assert (ev.status, ev.error) == ("failed", "interrupted by restart") and ev.completed_at is not None
        assert s.get(Evaluation, ids["orphan_new"]).status == "running"
        assert s.get(Evaluation, ids["done"]).status == "completed"
    finally:
        s.close()


def test_recover_is_idempotent(env, db):
    _stale_world(db)
    runner.recover_stale_jobs()
    logs = {jid: len(job_row(env, jid).log) for jid in ("j_q", "j_r")}
    assert runner.recover_stale_jobs() == {"jobs": [], "evaluations": []}
    assert {jid: len(job_row(env, jid).log) for jid in ("j_q", "j_r")} == logs


def test_recover_leaves_jobs_alive_in_this_process_alone(env, db):
    ids = _stale_world(db)
    env.registry["j_r"] = runner.JobState("j_r")  # its worker is running here
    result = runner.recover_stale_jobs(env.factory)
    env.registry["j_r"].finished.set()
    assert result["jobs"] == ["j_q"] and ids["in_job"] not in result["evaluations"]
    assert job_row(env, "j_r").status == "running"


def test_recover_never_raises(env, db):
    def broken_factory():
        raise RuntimeError("database is gone")

    class BrokenSession(Session):
        def execute(self, *a, **kw):
            raise RuntimeError("disk I/O error")

    assert runner.recover_stale_jobs(broken_factory) == {"jobs": [], "evaluations": []}
    broken = sessionmaker(bind=env.factory.kw["bind"], class_=BrokenSession)
    assert runner.recover_stale_jobs(broken) == {"jobs": [], "evaluations": []}


def test_recover_writes_one_audit_event_per_job_when_cqa_audit_exists(env, db, monkeypatch):
    _stale_world(db)
    calls: list[dict] = []

    def record(db, *, action, entity_type=None, entity_id=None, summary=None, details=None, actor=None):
        calls.append({"action": action, "entity_type": entity_type, "entity_id": entity_id, "summary": summary, "details": details, "actor": actor})

    monkeypatch.setitem(sys.modules, "cqa.audit", types.SimpleNamespace(record=record))
    monkeypatch.setattr(runner, "_audit_record", REAL_AUDIT_RECORD)  # resolves cqa.audit.record like in production
    runner.recover_stale_jobs()
    assert [(c["action"], c["entity_type"], c["entity_id"]) for c in calls] == [("job.interrupted", "job", "j_q"), ("job.interrupted", "job", "j_r")]
    assert calls[1]["details"]["previous_status"] == "running" and calls[1]["details"]["evaluations_failed"] == 1
    assert calls[1]["actor"]["role"] == "system" and "прервана" in calls[1]["summary"]


def test_recover_survives_a_failing_audit_hook(env, db, monkeypatch):
    _stale_world(db)

    def record(db, **kwargs):
        raise RuntimeError("audit table missing")

    monkeypatch.setattr(runner, "_audit_record", lambda: record)
    assert runner.recover_stale_jobs()["jobs"] == ["j_q", "j_r"]
    assert job_row(env, "j_q").status == "interrupted"


def test_audit_call_drops_keywords_the_hook_does_not_accept(db):
    seen = {}

    def record(db, action, entity_id=None):
        seen.update(action=action, entity_id=entity_id)

    runner._call_audit(record, db, action="job.interrupted", entity_id="j_1", actor={"role": "system"}, summary="x")
    assert seen == {"action": "job.interrupted", "entity_id": "j_1"}


def test_main_startup_hook_resolves_to_recover_stale_jobs():
    import importlib

    mod_name, fn_name = "cqa.jobs.runner:recover_stale_jobs".split(":")  # the hook string in cqa/main.py
    assert getattr(importlib.import_module(mod_name), fn_name) is runner.recover_stale_jobs


# ----------------------------------------------------------------------------- runner robustness


def test_worker_crash_marks_the_job_failed_instead_of_running(env, db, monkeypatch):
    v = seed_version(db)
    seed_convs(db, ["a"])

    def broken_progress(*a, **kw):
        raise RuntimeError("progress table locked")

    monkeypatch.setattr(runner, "_progress", broken_progress)
    jid = runner.start_evaluation_job(["a"], v.id, provider="anthropic", model=None, mode="full", base_version_id=None, only_codes=None)
    job = wait_job(env, jid)
    assert job.status == "failed" and job.finished_at is not None and "worker crashed" in job.log[-1]["message"]
    assert env.registry[jid].since(0)[-1][1:] == ("job.done", {"status": "failed"})
    assert not env.registry[jid].alive and jid not in runner.live_job_ids()


def test_extra_params_never_override_run_parameters(env, db):
    v = seed_version(db)
    seed_convs(db, ["a"])
    jid = runner.start_evaluation_job(["a"], v.id, provider="anthropic", model=None, mode="full", base_version_id=None, only_codes=None,
                                      extra_params={"mode": "partial", "estimated_cost_usd": 0.18})
    job = wait_job(env, jid)
    assert job.params["mode"] == "full" and job.params["estimated_cost_usd"] == 0.18 and job.status == "completed"


def test_registry_keeps_only_recent_finished_states(monkeypatch):
    reg: dict = {}
    monkeypatch.setattr(runner, "_registry", reg)
    for i in range(runner.KEEP_FINISHED_STATES + 5):
        st = runner.JobState(f"j_{i}")
        st.finished_at = float(i)
        st.finished.set()
        reg[st.id] = st
    reg["j_live"] = runner.JobState("j_live")
    runner._prune_registry()
    assert "j_live" in reg and "j_0" not in reg and "j_4" not in reg and "j_5" in reg
    assert len(reg) == runner.KEEP_FINISHED_STATES + 1


# ----------------------------------------------------------------------------- resume


def test_resume_unknown_job_is_404(client):
    r = client.post("/api/jobs/j_missing/resume")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found" and "j_missing" in r.json()["error"]["message"]


def test_resume_with_nothing_left_is_409(client, db):
    v = seed_version(db)
    seed_convs(db, ["a", "b"])
    add_job(db, "j_old", "interrupted", ["a", "b"], v.id, done=2)
    add_eval(db, "a", v.id)
    add_eval(db, "b", v.id)
    r = client.post("/api/jobs/j_old/resume")
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "nothing_to_resume" and err["completed"] == 2 and "Возобновлять нечего" in err["message"]


def test_resume_of_an_active_job_is_409(client, db):
    v = seed_version(db)
    seed_convs(db, ["a"])
    add_job(db, "j_run", "running", ["a"], v.id)
    r = client.post("/api/jobs/j_run/resume")
    assert r.status_code == 409 and r.json()["error"]["code"] == "job_active"


def test_resume_requeues_only_conversations_without_a_completed_evaluation(client, env, db):
    v = seed_version(db)
    seed_convs(db, ["a", "b", "c"])
    add_job(db, "j_old", "interrupted", ["a", "b", "c", "gone"], v.id, done=1, failed=1, cost=0.2, estimated_cost_usd=0.72)
    add_eval(db, "a", v.id, job_id="j_old")  # done
    add_eval(db, "b", v.id, status="failed", job_id="j_old")  # failed → re-queued
    assert client.get("/api/jobs/j_old").json()["resumable"] is True

    r = client.post("/api/jobs/j_old/resume")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["resumed_from"] == "j_old" and body["queued"] == 2 and body["estimated_cost_usd"] == 0.36
    assert (body["skipped_completed"], body["skipped_missing"]) == (1, 1) and body["checklist_version_id"] == v.id
    assert set(body["budget"]) >= {"spent_24h", "limit", "remaining"}
    new = wait_job(env, body["job_id"])
    assert new.status == "completed" and new.params["conversation_ids"] == ["b", "c"] and new.params["resumed_from"] == "j_old"
    assert new.params["provider"] == "anthropic" and new.params["mode"] == "full" and new.params["estimated_cost_usd"] == 0.36
    assert sorted(c[0] for c in env.judge.calls) == ["b", "c"] and all(c[1] == new.id for c in env.judge.calls)
    old = job_row(env, "j_old")
    assert old.params["resumed_as"] == new.id and old.log[-1]["message"].startswith(f"resumed as {new.id}")
    assert client.get("/api/jobs/j_old").json()["resumable"] is False
    # everything is evaluated now
    assert client.post("/api/jobs/j_old/resume").json()["error"]["code"] == "nothing_to_resume"


def test_resume_ignores_completed_evaluations_older_than_the_job(client, db):
    """A re-evaluation job of dialogs evaluated before (e.g. a DEV re-run) must still resume the ones it did not finish
    (found in the integration step's live check: the interrupted dialog had an older completed evaluation)."""
    v = seed_version(db)
    seed_convs(db, ["a", "b"])
    add_eval(db, "a", v.id, created_at=utc(days=1))  # yesterday's run of the same dialog and version
    add_job(db, "j_rerun", "interrupted", ["a", "b"], v.id, done=1)
    add_eval(db, "b", v.id, job_id="j_rerun")  # finished by this job
    plan = runner.resume_plan(db, db.get(Job, "j_rerun"))
    assert (plan["pending"], plan["completed"], plan["missing"]) == (["a"], ["b"], [])
    r = client.post("/api/jobs/j_rerun/resume")
    assert r.status_code == 200, r.text
    assert (r.json()["queued"], r.json()["skipped_completed"]) == (1, 1)


def test_resume_while_the_resumed_copy_runs_is_409(client, db):
    v = seed_version(db)
    seed_convs(db, ["a"])
    add_job(db, "j_old", "interrupted", ["a"], v.id, resumed_as="j_new")
    add_job(db, "j_new", "running", ["a"], v.id, resumed_from="j_old")
    r = client.post("/api/jobs/j_old/resume")
    assert r.status_code == 409 and r.json()["error"]["code"] == "already_resumed" and r.json()["error"]["job_id"] == "j_new"


def test_resume_respects_the_guards(client, db):
    v = seed_version(db)
    seed_convs(db, ["a"])
    add_job(db, "j_old", "interrupted", ["a"], v.id)
    add_eval(db, "a", v.id, status="failed", cost=75.0)
    r = client.post("/api/jobs/j_old/resume")
    assert r.status_code == 429 and r.json()["error"]["code"] == "budget_exceeded"


def test_resume_job_function_raises_resume_errors(env, db):
    v = seed_version(db)
    seed_convs(db, ["a"])
    with pytest.raises(runner.ResumeError) as ei:
        runner.resume_job("j_nope")
    assert ei.value.status == 404
    add_job(db, "j_done", "completed", ["a"], v.id, done=1)
    add_eval(db, "a", v.id)
    with pytest.raises(runner.ResumeError) as ei:
        runner.resume_job("j_done")
    assert (ei.value.code, ei.value.status) == ("nothing_to_resume", 409)


# ----------------------------------------------------------------------------- version / health / metrics


def test_version_shape(client):
    r = client.get("/api/version")
    assert r.status_code == 200
    body = r.json()
    assert set(body) >= {"app", "version", "started_at", "python"}
    assert body["app"] == "CQA API" and body["version"] == system_router.VERSION and body["version"]
    assert body["python"] == ".".join(map(str, sys.version_info[:3]))
    assert datetime.fromisoformat(body["started_at"]).tzinfo is not None


def test_resolve_version_precedence(monkeypatch):
    monkeypatch.setattr(settings, "app_version", "1.4.0-rc1")
    assert system_router.resolve_version() == ("1.4.0-rc1", "app_version")
    monkeypatch.setattr(settings, "app_version", None)
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "0123456789abcdef0123456789abcdef01234567")
    assert system_router.resolve_version() == ("0123456", "railway")
    monkeypatch.delenv("RAILWAY_GIT_COMMIT_SHA")
    monkeypatch.setattr(system_router, "_git_head", lambda repo: "abc1234")
    assert system_router.resolve_version() == ("abc1234", "git")
    monkeypatch.setattr(system_router, "_git_head", lambda repo: None)
    assert system_router.resolve_version()[1] == "package"


def test_git_head_reads_the_checkout_without_git(tmp_path):
    sha = "89abcdef0123456789abcdef0123456789abcdef"
    loose = tmp_path / "loose"
    (loose / ".git" / "refs" / "heads").mkdir(parents=True)
    (loose / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (loose / ".git" / "refs" / "heads" / "main").write_text(sha + "\n")
    assert system_router._git_head(loose) == "89abcde"

    packed = tmp_path / "packed"
    (packed / ".git").mkdir(parents=True)
    (packed / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (packed / ".git" / "packed-refs").write_text(f"# pack-refs with: peeled fully-peeled sorted\n{sha} refs/heads/main\n")
    assert system_router._git_head(packed) == "89abcde"

    detached = tmp_path / "detached"
    (detached / ".git").mkdir(parents=True)
    (detached / ".git" / "HEAD").write_text(sha + "\n")
    assert system_router._git_head(detached) == "89abcde"

    wt_git = loose / ".git" / "worktrees" / "wt"
    wt_git.mkdir(parents=True)
    (wt_git / "HEAD").write_text("ref: refs/heads/feature\n")
    (wt_git / "commondir").write_text("../..\n")
    (loose / ".git" / "refs" / "heads" / "feature").write_text("fedcba9876543210fedcba9876543210fedcba98\n")
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / ".git").write_text(f"gitdir: {wt_git}\n")
    assert system_router._git_head(worktree) == "fedcba9"

    assert system_router._git_head(tmp_path / "no-repo") is None


def _seed_appeals(db: Session, v: ChecklistVersion) -> None:
    ev = add_eval(db, "a", v.id, cost=0.1, latency=1000)
    crit = db.execute(select(Criterion).where(Criterion.checklist_version_id == v.id)).scalars().first()
    row = EvaluationCriterion(evaluation_id=ev.id, criterion_id=crit.id, code=crit.code, answer="no", score=0, max_points=1, weight=50)
    db.add(row)
    db.flush()
    db.add(Appeal(evaluation_criterion_id=row.id, comment="Не согласен", status="open"))
    db.add(Appeal(evaluation_criterion_id=row.id, comment="Ещё раз", status="decided"))
    db.commit()


def test_system_health_shape(client, db):
    v = seed_version(db)
    seed_convs(db, ["a", "b"])
    _seed_appeals(db, v)
    add_job(db, "j_i", "interrupted", ["a"], v.id)
    r = client.get("/api/system/health")
    assert r.status_code == 200 and r.headers.get("x-request-id")
    body = r.json()
    assert set(body) >= {"ok", "version", "uptime_s", "db", "providers", "jobs", "disk_free_bytes", "budget"}
    assert body["ok"] is True and body["version"] == system_router.VERSION and body["uptime_s"] >= 0
    dbi = body["db"]
    assert (dbi["kind"], dbi["ok"], dbi["conversations"], dbi["evaluations"], dbi["appeals"]) == ("sqlite", True, 2, 1, 2)
    assert dbi["size_bytes"] > 0 and isinstance(dbi["latency_ms"], float)
    assert isinstance(body["providers"]["anthropic"], bool)
    assert body["providers"]["local"] == {"configured": False, "endpoint": None, "model": settings.local_model, "reachable": None}
    assert body["jobs"] == {"running": 0, "queued": 0, "interrupted": 1, "live_workers": 0}
    assert isinstance(body["disk_free_bytes"], int) and body["disk_free_bytes"] > 0
    assert body["budget"]["spent_24h"] == 0.1 and body["budget"]["limit"] == 75.0
    assert isinstance(body["warnings"], list)


def test_system_health_probes_the_local_model_with_a_30s_cache(client, db, monkeypatch):
    calls: list[str] = []

    def fake_get(url, timeout, headers):
        calls.append(url)
        assert timeout == 1.0
        return httpx.Response(200, json={"object": "list", "data": [{"id": "qwen3:8b"}, {"id": "llama"}]})

    monkeypatch.setattr(system_router, "_http_get", fake_get)
    monkeypatch.setattr(settings, "local_endpoint", "http://gpu.example:8000/v1/")
    first = client.get("/api/system/health").json()["providers"]["local"]
    second = client.get("/api/system/health").json()["providers"]["local"]
    assert calls == ["http://gpu.example:8000/v1/models"]
    assert first["configured"] and first["reachable"] is True and first["models"] == ["qwen3:8b", "llama"] and first["cached"] is False
    assert second["reachable"] is True and second["cached"] is True

    def refused(url, timeout, headers):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(system_router, "_http_get", refused)
    db.add(Setting(key="local_endpoint", value="http://10.0.0.5:8000/v1"))  # runtime setting wins over the env value
    db.commit()
    body = client.get("/api/system/health").json()
    local = body["providers"]["local"]
    assert local["endpoint"] == "http://10.0.0.5:8000/v1" and local["reachable"] is False and local["error"] == "ConnectError"
    assert "local_unreachable" in [w["code"] for w in body["warnings"]] and body["ok"] is True


def test_system_health_reports_a_broken_database_without_failing(env, monkeypatch, tmp_path):
    app = make_app(env)
    broken = sessionmaker(bind=create_engine(f"sqlite:///{tmp_path / 'missing-dir' / 'nested' / 'x.db'}"))

    def override():
        s = broken()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override
    with TestClient(app) as c:
        r = c.get("/api/system/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False and body["db"]["ok"] is False and body["db"]["error"] == "OperationalError"
    assert body["budget"] is None and "db_unavailable" in [w["code"] for w in body["warnings"]]


def test_system_metrics(client, db):
    v = seed_version(db)
    seed_convs(db, ["a", "b", "c"])
    t = utc(minutes=5)
    for cost, latency in ((0.1, 1000), (0.2, 2000), (0.3, 3000)):
        add_eval(db, "a", v.id, cost=cost, latency=latency, tokens=(100, 10), completed_at=t)
    add_eval(db, "b", v.id, cost=0.4, latency=4000, tokens=(100, 10), provider="local", model="local:qwen3:8b", completed_at=utc(minutes=1))
    add_eval(db, "b", v.id, status="failed", cost=0.05, provider="local", model="local:qwen3:8b", completed_at=utc(minutes=2))
    add_eval(db, "c", v.id, status="running")
    crit = db.execute(select(Criterion)).scalars().first()
    first = db.execute(select(Evaluation)).scalars().first()
    row = EvaluationCriterion(evaluation_id=first.id, criterion_id=crit.id, code=crit.code)
    db.add(row)
    db.flush()
    db.add_all([Appeal(evaluation_criterion_id=row.id, comment="x", status="open"), Appeal(evaluation_criterion_id=row.id, comment="y", status="decided")])
    db.commit()

    m = client.get("/api/system/metrics").json()
    assert m["evaluations"] == {"total": 6, "by_status": {"completed": 4, "failed": 1, "running": 1}}
    assert m["latency_ms"] == {"n": 4, "p50": 2500, "p90": 3700, "max": 4000, "mean": 2500}
    assert m["cost_usd"] == {"total": 1.05, "per_completed_evaluation": 0.25}
    assert m["tokens"] == {"input": 400, "output": 40}
    anth, local = m["by_provider"]
    assert (anth["provider"], anth["model"], anth["evaluations"], anth["completed"], anth["failed"]) == ("anthropic", "anthropic:claude-opus-5-5", 4, 3, 0)
    assert anth["cost_usd"] == 0.6 and anth["cost_per_completed_usd"] == 0.2 and anth["latency_ms"] == {"p50": 2000, "p90": 2800, "max": 3000}
    assert (local["provider"], local["completed"], local["failed"], local["cost_usd"], local["cost_per_completed_usd"]) == ("local", 1, 1, 0.45, 0.4)
    assert m["appeals"] == {"total": 2, "by_status": {"open": 1, "decided": 1}}
    assert datetime.fromisoformat(m["last_evaluation_at"]) > utc(minutes=1, seconds=30)
    assert m["http"]["requests"] >= 0 and set(m["http"]["by_status_class"]) >= {"2xx", "4xx", "5xx"}
    assert m["window"] == {"since_hours": None, "since": None}


def test_system_metrics_since_hours_window(client, db):
    v = seed_version(db)
    seed_convs(db, ["a"])
    add_eval(db, "a", v.id, cost=1.0, latency=9000, created_at=utc(hours=48))
    add_eval(db, "a", v.id, cost=0.2, latency=1000)
    m = client.get("/api/system/metrics", params={"since_hours": 24}).json()
    assert m["evaluations"]["total"] == 1 and m["cost_usd"]["total"] == 0.2 and m["latency_ms"]["max"] == 1000
    assert m["window"]["since_hours"] == 24
    assert client.get("/api/system/metrics").json()["evaluations"]["total"] == 2
    assert client.get("/api/system/metrics", params={"since_hours": 0}).status_code == 422


# ----------------------------------------------------------------------------- request id middleware


def _rid_app(env: Env) -> FastAPI:
    app = make_app(env)

    @app.get("/t/async")
    async def rid_async(request: Request):
        return {"state": request.state.request_id, "ctx": requestlog.get_request_id()}

    @app.get("/t/sync")
    def rid_sync(request: Request):
        return {"state": request.state.request_id, "ctx": requestlog.get_request_id()}

    @app.get("/t/boom")
    def boom():
        raise RuntimeError("kaboom")

    return app


def test_request_id_is_echoed_generated_or_replaced(env):
    with TestClient(_rid_app(env)) as c:
        r = c.get("/t/async", headers={"X-Request-ID": "web-42.a:b"})
        assert r.headers["x-request-id"] == "web-42.a:b" and r.json() == {"state": "web-42.a:b", "ctx": "web-42.a:b"}
        r = c.get("/t/sync")
        rid = r.headers["x-request-id"]
        assert len(rid) == 12 and int(rid, 16) >= 0 and r.json() == {"state": rid, "ctx": rid}
        for bad in ("has space", "x" * 65, "-leading-dash", "кириллица"):
            r = c.get("/t/async", headers={"X-Request-ID": bad.encode("utf-8")})
            assert len(r.headers["x-request-id"]) == 12 and r.headers["x-request-id"] != bad
        assert requestlog.get_request_id() is None  # reset after the request


def test_access_log_has_one_line_per_request(env, caplog):
    caplog.set_level(logging.INFO, logger="cqa.access")
    with TestClient(_rid_app(env)) as c:
        c.get("/api/version", headers={"X-Request-ID": "rid-one"})
        c.get("/api/jobs/j_none", headers={"X-Request-ID": "rid-two", "X-CQA-Role": "Analyst"})
    lines = [r.getMessage() for r in caplog.records if r.name == "cqa.access"]
    one = [line for line in lines if "rid=rid-one" in line]
    two = [line for line in lines if "rid=rid-two" in line]
    assert len(one) == 1 and len(two) == 1
    assert one[0].startswith("GET /api/version 200 ") and "ms role=demo" in one[0]
    assert two[0].startswith("GET /api/jobs/j_none 404 ") and "role=analyst" in two[0]
    assert all(r.levelno == logging.INFO for r in caplog.records if r.name == "cqa.access")


def test_errors_and_unhandled_exceptions_carry_the_request_id(env, caplog):
    caplog.set_level(logging.INFO, logger="cqa.access")
    with TestClient(_rid_app(env), raise_server_exceptions=False) as c:
        r = c.get("/t/boom", headers={"X-Request-ID": "rid-500"})
        assert r.status_code == 500 and r.headers["x-request-id"] == "rid-500"
        r = c.post("/api/jobs/j_none/resume", headers={"X-Request-ID": "rid-404"})
        assert r.status_code == 404 and r.headers["x-request-id"] == "rid-404"
    line = next(r.getMessage() for r in caplog.records if "rid=rid-500" in r.getMessage())
    assert " 500 " in line and line.endswith("error=RuntimeError")


def test_middleware_runs_outside_middlewares_added_later(env):
    """The security middleware and CORS are installed after the request log; their own answers still get the id."""
    app = _rid_app(env)
    seen: dict = {}

    class Deny:  # stands in for cqa.security.middleware answering 403 by itself
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] == "http" and scope["path"] == "/t/denied":
                seen["rid"] = scope["state"]["request_id"]
                await JSONResponse({"error": {"code": "forbidden", "message": "Недостаточно прав"}}, status_code=403)(scope, receive, send)
                return
            await self.app(scope, receive, send)

    app.add_middleware(Deny)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    with TestClient(app) as c:
        r = c.get("/t/denied", headers={"X-Request-ID": "rid-403"})
        assert r.status_code == 403 and r.headers["x-request-id"] == "rid-403" and seen["rid"] == "rid-403"
        pre = c.options("/api/version", headers={"Origin": "https://cqa.example", "Access-Control-Request-Method": "GET"})
        assert pre.status_code == 200 and len(pre.headers["x-request-id"]) == 12


def test_cross_origin_responses_expose_the_request_id_header(env):
    app = _rid_app(env)

    @app.get("/t/export")
    def export():
        return Response("{}", media_type="application/json", headers={"Access-Control-Expose-Headers": "X-Results-Valid, Content-Disposition"})

    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    with TestClient(app) as c:
        exposed = c.get("/t/export", headers={"Origin": "https://cqa.example"}).headers["access-control-expose-headers"]
        assert exposed == "X-Results-Valid, Content-Disposition, X-Request-ID"
        assert c.get("/api/version", headers={"Origin": "https://cqa.example"}).headers["access-control-expose-headers"] == "X-Request-ID"
        assert "access-control-expose-headers" not in c.get("/api/version").headers  # same-origin: untouched


def test_install_is_idempotent(env):
    app = make_app(env)
    requestlog.install(app)
    requestlog.install(app)
    stack = app.build_middleware_stack()
    assert isinstance(stack, requestlog.RequestLogMiddleware) and not isinstance(stack.app, requestlog.RequestLogMiddleware)


def test_install_on_an_already_started_app_wraps_the_built_stack():
    app = FastAPI()

    @app.get("/ping")
    def ping():
        return {"ok": True}

    with TestClient(app) as c:
        assert "x-request-id" not in c.get("/ping").headers  # the stack is built now
        requestlog.install(app)
        assert isinstance(app.middleware_stack, requestlog.RequestLogMiddleware)
        assert len(c.get("/ping").headers["x-request-id"]) == 12


def test_streaming_bodies_are_forwarded_without_buffering():
    async def scenario() -> list[dict]:
        sent: list[dict] = []
        first_chunk_delivered = asyncio.Event()

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/event-stream")]})
            await send({"type": "http.response.body", "body": b"event: a\n\n", "more_body": True})
            # a buffering middleware would hold the chunk back and this wait would time out
            await asyncio.wait_for(first_chunk_delivered.wait(), timeout=2)
            await send({"type": "http.response.body", "body": b"event: b\n\n", "more_body": False})

        async def send(message):
            sent.append(message)
            if message.get("body") == b"event: a\n\n":
                first_chunk_delivered.set()

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        stats = requestlog.RequestStats()
        mw = requestlog.RequestLogMiddleware(app, stats=stats)
        await mw({"type": "http", "method": "GET", "path": "/s", "raw_path": b"/s", "headers": [], "query_string": b""}, receive, send)
        assert stats.snapshot()["streams"] == 1 and stats.snapshot()["latency_ms"]["window"] == 0
        return sent

    sent = asyncio.run(scenario())
    assert [m["type"] for m in sent] == ["http.response.start", "http.response.body", "http.response.body"]
    assert any(k == b"x-request-id" and len(v) == 12 for k, v in sent[0]["headers"])


def test_sse_job_events_stream_through_the_middleware(client, env, db):
    v = seed_version(db)
    seed_convs(db, ["a"])
    add_job(db, "j_sse", "completed", ["a"], v.id, done=1)
    st = runner.JobState("j_sse")
    st.emit("evaluation.done", {"conversation_id": "a", "evaluation_id": 1})
    st.emit("job.progress", {"done": 1, "failed": 0, "total": 1, "cost_usd": 0.05})
    st.finished.set()
    env.registry["j_sse"] = st
    with client.stream("GET", "/api/jobs/j_sse/events", headers={"X-Request-ID": "rid-sse"}) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        assert r.headers["x-request-id"] == "rid-sse"
        text = "".join(r.iter_text())
    assert text.index("event: evaluation.done") < text.index("event: job.progress") < text.index("event: job.status") < text.index("event: job.done")
    assert 'data: {"status": "completed"}' in text


def test_sse_closes_for_an_interrupted_job(client, db):
    v = seed_version(db)
    seed_convs(db, ["a"])
    add_job(db, "j_int", "interrupted", ["a"], v.id)
    r = client.get("/api/jobs/j_int/events")
    assert r.status_code == 200 and r.text.rstrip().endswith('data: {"status": "interrupted"}')


def test_request_stats_snapshot_counts_by_status_class():
    stats = requestlog.RequestStats(window=3)
    stats.begin()
    assert stats.snapshot()["in_flight"] == 1
    for status, ms in ((200, 10.0), (404, 20.0), (500, 30.0), (204, 40.0)):
        stats.end(status, ms, streaming=False)
    stats.end(None, 5.0, streaming=False)
    snap = stats.snapshot()
    assert snap["requests"] == 5 and snap["in_flight"] == 0
    assert snap["by_status_class"] == {"2xx": 2, "3xx": 0, "4xx": 1, "5xx": 1, "other": 0, "aborted": 1}
    assert snap["latency_ms"] == {"window": 3, "p50": 30.0, "p90": 38.0, "max": 40.0}  # last 3 durations: 30, 40, 5
