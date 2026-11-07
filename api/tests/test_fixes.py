"""Regression tests for the reviewed security / correctness findings (docs/arch/fixes.md).

SEC-1..SEC-10 (budget and spend), F1..F5 (correctness), and the two compat findings (empty control set, restart-interrupted
rows). Every test uses its own SQLite file in tmp_path or an in-memory engine, never api/data/cqa.db; the spend ledger is
isolated per test (conftest.py). Claude is never called: a fake SDK client is installed in anthropic_provider._client
(the provider's test seam) or providers are stubbed.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from starlette.exceptions import HTTPException as StarletteHTTPException

import cqa.db as cqa_db
from cqa.admin import snapshots as snap
from cqa.checklist import compiler
from cqa.checklist import service as svc
from cqa.checklist.validation import INSTRUCTION_MAX, MAX_CRITERIA, prepare_checklist_payload
from cqa.config import settings
from cqa.db import Base, get_db
from cqa.export import results_json
from cqa.jobs import estimate as estimator
from cqa.jobs import guards, ledger, runner
from cqa.judge import local_endpoint
from cqa.judge import runner as judge_runner
from cqa.judge.contract import ProviderResult, Usage
from cqa.judge.providers import anthropic_provider
from cqa.judge.providers.anthropic_provider import AnthropicProvider
from cqa.judge.providers.base import cost_usd
from cqa.models import (AuditEvent, Checklist, ChecklistVersion, Conversation, Criterion, Evaluation, EvaluationCriterion, Job,
                        LlmTrace, Setting, Turn)
from cqa.platform import bodylimit
from cqa.routers import admin as admin_router
from cqa.routers import checklists as checklists_router
from cqa.routers import conversations as conversations_router
from cqa.routers import evaluations as evaluations_router
from cqa.routers import settings_router
from cqa.routers import system as system_router
from cqa.serializers import latest_evaluation

OPUS = "claude-opus-5-5"
BINARY = [{"value": "yes", "label": "Да", "points": 1}, {"value": "no", "label": "Нет", "points": 0}]


def utc(**delta: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(**delta)


def wait_for(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


# ============================================================================================ fixtures


class FakeJudge:
    """Stands in for cqa.judge.runner.evaluate_conversation inside job workers: no network, a completed evaluation."""

    def __init__(self, factory: sessionmaker, cost: float = 0.05):
        self.factory = factory
        self.cost = cost
        self.calls: list[str] = []

    async def __call__(self, conversation_id: str, checklist_version_id: int, *, provider_name: str = "anthropic", model=None,
                       mode: str = "full", base_evaluation_id=None, only_codes=None, job_id=None, on_event=None) -> int:
        self.calls.append(conversation_id)
        db = self.factory()
        try:
            ev = Evaluation(conversation_id=conversation_id, checklist_version_id=checklist_version_id, status="completed",
                            provider=provider_name, model="fake:offline", job_id=job_id, cost_usd=self.cost,
                            total_score=90.0, final_total_score=90.0, completed_at=datetime.now(timezone.utc))
            db.add(ev)
            db.commit()
            return ev.id
        finally:
            db.close()


@dataclass
class World:
    engine: Any
    Session: sessionmaker
    judge: FakeJudge
    registry: dict
    path: Path


@pytest.fixture()
def world(tmp_path, monkeypatch):
    path = tmp_path / "fixes.db"
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)
    judge = FakeJudge(factory)
    registry: dict = {}
    for module in (runner, evaluations_router, judge_runner, cqa_db):
        monkeypatch.setattr(module, "SessionLocal", factory)
    monkeypatch.setattr(runner, "evaluate_conversation", judge)
    monkeypatch.setattr(runner, "_registry", registry)
    monkeypatch.setattr(runner, "_audit_record", lambda: None)
    monkeypatch.setattr(settings, "daily_budget_usd", 75.0)
    monkeypatch.setattr(settings, "max_dialogs_per_run", 200)
    monkeypatch.setattr(settings, "max_concurrent_jobs", 2)
    monkeypatch.setattr(settings, "anthropic_api_key", None)
    monkeypatch.setattr(settings, "local_endpoint", None)
    monkeypatch.setattr(settings, "local_endpoint_allowlist", None)
    monkeypatch.setattr(settings, "public_instance", False)
    monkeypatch.setattr(anthropic_provider, "_client", None)
    system_router._probe_cache.clear()
    yield World(engine, factory, judge, registry, path)
    for st in list(registry.values()):
        st.finished.wait(5)
    system_router._probe_cache.clear()
    engine.dispose()


def make_app(world: World, *modules: Any) -> FastAPI:
    app = FastAPI()

    @app.exception_handler(StarletteHTTPException)
    async def _envelope(request, exc):  # same shape as cqa.main
        detail = exc.detail if isinstance(exc.detail, dict) else {"code": "error", "message": str(exc.detail)}
        return JSONResponse(status_code=exc.status_code, content={"error": detail})

    from cqa.main import _guard_error

    app.add_exception_handler(guards.GuardError, _guard_error)
    for m in modules:
        app.include_router(m.router)

    def override():
        db = world.Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    return app


def seed_version(db: Session, codes: tuple[str, ...] = ("C1", "C2", "C3"), *, checklist: Checklist | None = None,
                 version_no: int = 1, weights: dict[str, float] | None = None, active: bool = True) -> ChecklistVersion:
    if checklist is None:
        checklist = Checklist(name="Тестовый чек-лист")
        db.add(checklist)
        db.flush()
    v = ChecklistVersion(checklist_id=checklist.id, version_no=version_no, is_active=active, scoring_rules={},
                         sections=[{"code": "S1", "title": "Все"}])
    db.add(v)
    db.flush()
    for i, code in enumerate(codes, start=1):
        db.add(Criterion(checklist_version_id=v.id, code=code, order=i, section="S1", title=f"Критерий {code}",
                         instruction="Оператор поздоровался.", answer_type="binary", options=BINARY, max_points=1,
                         weight=(weights or {}).get(code, 10)))
    db.commit()
    db.refresh(v)
    return v


TURNS = [("client", "Здравствуйте, не пришли деньги на счёт."), ("operator", "Добрый день! Меня зовут Алина, сейчас проверю.")]


def seed_conv(db: Session, cid: str, *, channel: str = "agent chat", turns: list[tuple[str, str]] | None = None) -> None:
    turns = TURNS if turns is None else turns
    db.add(Conversation(id=cid, channel=channel, language="RU", turn_count=len(turns), meta={"operator_start_idx": 1}))
    for idx, (speaker, text) in enumerate(turns):
        db.add(Turn(conversation_id=cid, idx=idx, timestamp_raw=f"00:{idx:02d}", offset_seconds=float(idx), speaker=speaker,
                    text=text, segment="operator" if speaker == "operator" else "pre"))
    db.commit()


def add_eval(db: Session, cid: str, version: ChecklistVersion, *, status: str = "completed", cost: float = 0.0,
             error: str | None = None, rows: bool = False, created_at: datetime | None = None) -> Evaluation:
    ev = Evaluation(conversation_id=cid, checklist_version_id=version.id, status=status, provider="anthropic", model=OPUS,
                    cost_usd=cost, error=error, created_at=created_at or utc(), total_score=100.0, final_total_score=100.0,
                    completed_at=utc() if status in ("completed", "failed") else None)
    db.add(ev)
    db.flush()
    if rows:
        for c in version.criteria:
            db.add(EvaluationCriterion(evaluation_id=ev.id, criterion_id=c.id, code=c.code, applicable=True, answer="yes", score=1,
                                       max_points=1, weight=c.weight, explanation="ok", flags=[]))
    db.commit()
    return ev


def spend(db: Session, amount: float) -> None:
    """$amount already spent in the last 24 h (a cost-only evaluation row; enough for the database side of the budget)."""
    db.add(Conversation(id=f"spent-{amount}-{time.monotonic_ns()}", channel="agent chat", language="RU", turn_count=0))
    db.flush()
    version_id = db.execute(select(ChecklistVersion.id)).scalars().first()
    db.add(Evaluation(conversation_id=db.execute(select(Conversation.id).order_by(Conversation.imported_at.desc())).scalars().first(),
                      checklist_version_id=version_id, status="completed", cost_usd=amount))
    db.commit()


# --- a fake Claude SDK client (messages.create), installed through the provider's test seam


def verdict_json(code: str, answer: str = "yes") -> str:
    return json.dumps({"criterion_code": code, "applicable": True, "na_reason": "", "answer": answer, "confidence": 0.9,
                       "evidence": [], "checked_span": None, "explanation": "Оператор поздоровался.", "step_results": [],
                       "flags": [], "claims": []}, ensure_ascii=False)


def message(text: str = "", *, stop_reason: str = "end_turn", usage: tuple[int, int, int, int] = (1000, 500, 0, 2000)) -> SimpleNamespace:
    i, o, cr, cw = usage
    return SimpleNamespace(id="msg_test", model=OPUS, stop_reason=stop_reason,
                           usage=SimpleNamespace(input_tokens=i, output_tokens=o, cache_read_input_tokens=cr, cache_creation_input_tokens=cw),
                           content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)])


class FakeSDK:
    """client.messages.create(**kwargs): answers with `respond(kwargs)`; optional asyncio gate to hold calls in flight."""

    def __init__(self, respond):
        self.respond = respond
        self.calls: list[dict[str, Any]] = []
        self.gate: asyncio.Event | None = None
        self.messages = self

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.gate is not None:
            await self.gate.wait()
        out = self.respond(kwargs)
        if isinstance(out, BaseException):
            raise out
        return out


def criterion_code(kwargs: dict[str, Any]) -> str:
    import re

    text = "".join(b.get("text", "") for m in kwargs["messages"] for b in (m["content"] if isinstance(m["content"], list) else []))
    found = re.findall(r"#\s*Criterion\s+(\S+)", text)
    return found[0] if found else "C1"


def ok_judge(kwargs: dict[str, Any]) -> Any:
    return message(verdict_json(criterion_code(kwargs)))


def per_call(usage: tuple[int, int, int, int] = (1000, 500, 0, 2000)) -> float:
    return cost_usd(OPUS, *usage)


# ============================================================================================ SEC-1 / F3: partial-run estimate


def test_partial_run_without_a_base_is_priced_in_full_and_refused_when_the_budget_is_spent(world):
    with world.Session() as db:
        seed_version(db)
        for cid in ("a", "b", "c"):
            seed_conv(db, cid)
        spend(db, 80.0)  # $80 of $75 already spent
    client = TestClient(make_app(world, evaluations_router))
    body = {"conversation_ids": ["a", "b", "c"], "provider": "anthropic", "mode": "partial", "only_codes": []}
    dry = client.post("/api/evaluations/run", json={**body, "dry_run": True}).json()
    assert dry["estimated_cost_usd"] >= 0.54  # three dialogs without a base evaluation: judged in full (at least $0.18 each)
    assert dry["estimate"]["paid"] is True and dry["estimate"]["judged_dialogs"] == 3
    assert dry["guard"]["code"] == "budget_exceeded"
    r = client.post("/api/evaluations/run", json=body)
    assert r.status_code == 429 and r.json()["error"]["code"] == "budget_exceeded"
    with world.Session() as db:
        assert db.scalar(select(func.count()).select_from(Job)) == 0
    assert world.judge.calls == []


def test_partial_run_prices_only_dialogs_that_have_no_completed_base_evaluation(world):
    with world.Session() as db:
        v1 = seed_version(db)
        v2 = seed_version(db, checklist=db.get(Checklist, v1.checklist_id), version_no=2, weights={"C1": 50})  # weight-only change
        for cid in ("based", "fresh"):
            seed_conv(db, cid)
        add_eval(db, "based", v1, rows=True)
        v1_id, v2_id = v1.id, v2.id
    client = TestClient(make_app(world, evaluations_router))
    body = {"checklist_version_id": v2_id, "provider": "anthropic", "mode": "partial", "base_version_id": v1_id, "dry_run": True}
    both = client.post("/api/evaluations/run", json={**body, "conversation_ids": ["based", "fresh"]}).json()
    assert both["rejudge_codes"] == []  # weight-only: nothing to re-judge where a base exists
    assert both["estimated_cost_usd"] == 0.18 and both["estimate"]["judged_dialogs"] == 1  # only "fresh" is judged (in full)
    only_based = client.post("/api/evaluations/run", json={**body, "conversation_ids": ["based"]}).json()
    assert only_based["estimated_cost_usd"] == 0.0 and only_based["estimate"]["paid"] is True
    assert only_based["estimate"]["model_calls"] == 0
    with world.Session() as db:
        spend(db, 80.0)
    # the budget is spent: a run that judges something is refused, a pure copy (no model call) is not
    assert client.post("/api/evaluations/run", json={**body, "conversation_ids": ["based", "fresh"]}).json()["guard"]["code"] == "budget_exceeded"
    assert client.post("/api/evaluations/run", json={**body, "conversation_ids": ["based"]}).json()["guard"] is None


def test_resume_of_a_partial_job_uses_the_same_estimate(world):
    with world.Session() as db:
        v = seed_version(db)
        for cid in ("a", "b"):
            seed_conv(db, cid)
        db.add(Job(id="j_partial", type="evaluate", status="interrupted", total=2,
                   params={"checklist_version_id": v.id, "provider": "anthropic", "model": None, "mode": "partial",
                           "base_version_id": None, "only_codes": [], "conversation_ids": ["a", "b"]}))
        db.commit()
        spend(db, 80.0)
    r = TestClient(make_app(world, evaluations_router)).post("/api/jobs/j_partial/resume")
    assert r.status_code == 429 and r.json()["error"]["code"] == "budget_exceeded"


def test_a_paid_run_is_never_checked_as_free():
    assert guards.MIN_PAID_ESTIMATE_USD > 0


# ============================================================================================ SEC-3: cache writes are paid


def test_cost_includes_prompt_cache_writes():
    rin, rout, rcache = settings.rate_for(OPUS)
    expected = (1000 * rin + 500 * rout + 300 * rcache + 8000 * rin * 1.25) / 1_000_000
    assert cost_usd(OPUS, 1000, 500, 300, 8000) == pytest.approx(expected)
    usage = Usage(input_tokens=1000, output_tokens=500, cache_read_tokens=300, cache_write_tokens=8000)
    assert AnthropicProvider(OPUS).cost(usage) == pytest.approx(expected)


# ============================================================================================ SEC-4: unpriced models


def test_an_unknown_model_is_never_priced_at_zero():
    assert settings.rate_for("claude-opus-4-7") == settings.rate_for(OPUS) != (0.0, 0.0, 0.0)  # the highest configured rate
    assert settings.is_known_model(OPUS) and settings.is_known_model("claude-sonnet-5-5")
    assert not settings.is_known_model("claude-fable-5-1") and not settings.is_known_model("claude-opus-5")


def test_runs_with_an_unpriced_model_are_refused(world):
    with world.Session() as db:
        seed_version(db)
        seed_conv(db, "a")
    client = TestClient(make_app(world, evaluations_router))
    for dry in (True, False):
        r = client.post("/api/evaluations/run", json={"conversation_ids": ["a"], "model": "claude-opus-4-7", "dry_run": dry})
        assert r.status_code == 422 and r.json()["error"]["code"] == "unknown_model"
    with world.Session() as db:  # a value stored before validation existed
        db.add(Setting(key="default_model", value="claude-fable-5-1"))
        db.commit()
    r = client.post("/api/evaluations/run", json={"conversation_ids": ["a"]})
    assert r.status_code == 422 and r.json()["error"]["model"] == "claude-fable-5-1"
    with world.Session() as db:
        assert db.scalar(select(func.count()).select_from(Job)) == 0
    r = client.post("/api/evaluations/run", json={"conversation_ids": ["a"], "provider": "gpt-9", "dry_run": True})
    assert r.status_code == 422 and r.json()["error"]["code"] == "unknown_provider"
    r = client.post("/api/evaluations/run", json={"conversation_ids": ["a"], "provider": "anthropic:claude-sonnet-5-5", "dry_run": True})
    assert r.status_code == 200 and r.json()["estimate"]["members"] == ["anthropic:claude-sonnet-5-5"]


# ============================================================================================ SEC-5 / SEC-8: settings validation


def test_settings_reject_unpriced_models_oversized_ensembles_and_unsafe_values(world):
    client = TestClient(make_app(world, settings_router))
    bad = [
        {"default_model": "claude-fable-5-1"},
        {"secondary_model": "claude-opus-4-7"},
        {"ensemble_members": [{"provider": "anthropic", "model": OPUS}] * 30},
        {"ensemble_members": [{"provider": "anthropic", "model": "claude-opus-4-1"}]},
        {"ensemble_members": []},
        {"judge_effort": "ultra-max"},
        {"max_parallel_criteria": 0},
        {"max_parallel_criteria": "lots"},
        {"default_provider": "gpt-9"},
        {"local_endpoint": "http://169.254.169.254/latest/meta-data/iam/?x="},
        {"local_endpoint": "http://attacker.example:8080/v1"},
        {"local_endpoint": "file:///etc/passwd"},
    ]
    for body in bad:
        r = client.put("/api/settings", json=body)
        assert r.status_code == 422, body
        err = r.json()["error"]
        assert err["code"] == "invalid_settings" and err["errors"][0]["field"].split("[")[0] == next(iter(body))
    with world.Session() as db:
        assert db.scalar(select(func.count()).select_from(Setting)) == 0  # nothing was written
    ok = {"default_model": OPUS, "secondary_model": None, "judge_effort": "xhigh", "max_parallel_criteria": 4,
          "ensemble_members": [{"provider": "anthropic", "model": OPUS}, {"provider": "anthropic", "model": "claude-sonnet-5-5"},
                               {"provider": "local", "model": "qwen3:8b"}],
          "local_endpoint": "http://localhost:11434/v1", "local_model": None, "default_provider": "ensemble"}
    r = client.put("/api/settings", json=ok)
    assert r.status_code == 200, r.text
    assert r.json()["judge_effort"] == "xhigh" and len(r.json()["ensemble_members"]) == 3


def test_settings_accept_a_resent_stored_value_and_clearing(world):
    with world.Session() as db:
        db.add(Setting(key="local_endpoint", value="http://169.254.169.254/latest/"))  # stored before validation existed
        db.commit()
    client = TestClient(make_app(world, settings_router))
    assert client.put("/api/settings", json={"local_endpoint": "http://169.254.169.254/latest/", "team_name": "x"}).status_code == 200
    assert client.put("/api/settings", json={"local_endpoint": None}).status_code == 200  # clearing is always allowed
    assert client.get("/api/health").json()["providers"]["local"] is False


def test_on_the_hosted_instance_only_configured_local_endpoints_are_accepted(world, monkeypatch):
    monkeypatch.setattr(settings, "public_instance", True)
    client = TestClient(make_app(world, settings_router))
    r = client.put("/api/settings", json={"local_endpoint": "http://127.0.0.1:8000/v1"})
    assert r.status_code == 422 and "LOCAL_ENDPOINT" in r.json()["error"]["errors"][0]["message"]
    monkeypatch.setattr(settings, "local_endpoint_allowlist", "http://gpu.example.org:8000/v1, https://llm.example.org")
    assert client.put("/api/settings", json={"local_endpoint": "http://gpu.example.org:8000/v1"}).status_code == 200
    assert client.put("/api/settings", json={"local_endpoint": "https://llm.example.org/v1/chat"}).status_code == 200
    assert client.put("/api/settings", json={"local_endpoint": "http://gpu.example.org:9999/v1"}).status_code == 422


def test_local_endpoint_rules():
    check = local_endpoint.check_endpoint
    assert check("http://localhost:11434/v1")[0] and check("http://127.0.0.1:8000")[0] and check("http://[::1]:8000/v1")[0]
    assert check("http://10.1.2.3:8000/v1")[0] and check("http://ollama:11434/v1")[0] and check("http://gpu.local:8000/v1")[0]
    for url in ("http://169.254.169.254/latest/meta-data/", "http://metadata.google.internal/computeMetadata/v1/",
                "http://[fe80::1]/v1", "http://0.0.0.0:80/", "http://attacker.example/v1", "ftp://localhost/", "not a url", ""):
        assert check(url)[0] is False, url


def test_parallel_criteria_and_effort_of_bad_stored_values_are_safe():
    assert judge_runner.parallel_criteria_of({"max_parallel_criteria": 0}) == 1  # Semaphore(0) used to hang the evaluation
    assert judge_runner.parallel_criteria_of({"max_parallel_criteria": "lots"}) == settings.max_parallel_criteria
    assert judge_runner.parallel_criteria_of({"max_parallel_criteria": 500}) == judge_runner.MAX_PARALLEL_CRITERIA
    assert judge_runner.effort_of({"judge_effort": "ultra-max"}) == "high" and judge_runner.effort_of({"judge_effort": "low"}) == "low"


def test_ensemble_estimate_scales_with_the_number_of_members(world):
    with world.Session() as db:
        v = seed_version(db)
        seed_conv(db, "a")
        conv, version = db.get(Conversation, "a"), db.get(ChecklistVersion, v.id)
        three = {**judge_runner._runtime_settings(db), "ensemble_members": [{"provider": "anthropic", "model": OPUS}] * 3}
        thirty = {**three, "ensemble_members": [{"provider": "anthropic", "model": OPUS}] * 30}
        e3 = estimator.estimate_run(db, [conv], version, provider="ensemble", model=None, rs=three)
        e30 = estimator.estimate_run(db, [conv], version, provider="ensemble", model=None, rs=thirty)
    assert e3.total_usd >= 0.38  # never below the flat ensemble rate of a chat
    assert e30.total_usd >= 9 * e3.total_usd  # ten times the members: about ten times the price
    assert e30.model_calls == 10 * e3.model_calls == 3 * 30


# ============================================================================================ SEC-8: local endpoint at run time


def test_members_never_modify_global_settings_and_ignore_a_disallowed_endpoint(monkeypatch):
    monkeypatch.setattr(settings, "public_instance", False)
    monkeypatch.setattr(settings, "local_endpoint", "http://127.0.0.1:9/v1")
    monkeypatch.setattr(settings, "local_api_key", "sk-local-secret")
    monkeypatch.setattr(settings, "local_endpoint_allowlist", None)
    rs = {"local_endpoint": "http://169.254.169.254/v1", "local_model": "evil"}
    [member] = judge_runner._members(rs, "local", None)
    assert member.base_url == "http://127.0.0.1:9/v1" and member.api_key == "sk-local-secret"  # env endpoint, trusted
    assert settings.local_endpoint == "http://127.0.0.1:9/v1" and settings.local_model != "evil"  # nothing global changed
    [lan] = judge_runner._members({"local_endpoint": "http://192.168.1.50:8000/v1"}, "local", None)
    assert lan.base_url == "http://192.168.1.50:8000/v1" and lan.api_key == "none"  # accepted, but the key stays home
    ensemble = judge_runner._members({"local_endpoint": "http://169.254.169.254/v1",
                                      "ensemble_members": [{"provider": "anthropic", "model": OPUS}]}, "ensemble", None)
    assert [p.name for p in ensemble] == ["anthropic", "local"] and ensemble[1].base_url == "http://127.0.0.1:9/v1"


def test_health_probe_never_contacts_a_disallowed_endpoint(world, monkeypatch):
    seen: list[tuple[str, str]] = []

    def fake_get(url: str, timeout: float, headers: dict[str, str]) -> httpx.Response:
        seen.append((url, headers.get("Authorization", "")))
        return httpx.Response(200, json={"data": [{"id": "m1"}]})

    monkeypatch.setattr(system_router, "_http_get", fake_get)
    monkeypatch.setattr(settings, "local_api_key", "sk-local-secret")
    with world.Session() as db:
        db.add(Setting(key="local_endpoint", value="http://169.254.169.254/latest/meta-data/?x="))
        db.commit()
    client = TestClient(make_app(world, system_router))
    assert client.get("/api/system/health").json()["providers"]["local"]["configured"] is False
    assert seen == []  # the stored metadata URL is never requested
    monkeypatch.setattr(settings, "local_endpoint", "http://127.0.0.1:9/v1")
    system_router._probe_cache.clear()
    client.get("/api/system/health")
    assert seen == [("http://127.0.0.1:9/v1/models", "Bearer sk-local-secret")]  # the operator's endpoint, with its key
    with world.Session() as db:
        db.get(Setting, "local_endpoint").value = "http://localhost:11434/v1"
        db.commit()
    system_router._probe_cache.clear()
    client.get("/api/system/health")
    assert seen[-1] == ("http://localhost:11434/v1/models", "Bearer none")  # accepted locally, but not trusted with the key


# ============================================================================================ SEC-6: input size, in-run budget


def test_body_limit_answers_413_before_reading_and_while_streaming():
    app = FastAPI()

    @app.exception_handler(StarletteHTTPException)
    async def _envelope(request, exc):
        return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})

    @app.post("/api/conversations/paste")
    async def paste(payload: dict[str, Any]):
        return {"keys": sorted(payload)}

    bodylimit.install(app)
    client = TestClient(app)
    assert client.post("/api/conversations/paste", json={"text": "x" * 1000}).status_code == 200
    big = json.dumps({"text": "x" * (2 * bodylimit.MB + 10)})
    r = client.post("/api/conversations/paste", content=big, headers={"content-type": "application/json"})
    assert r.status_code == 413 and r.json()["error"]["code"] == "payload_too_large"

    # a chunked body without Content-Length is counted while the app reads it
    sent: list[dict[str, Any]] = []
    chunks = [b'{"text": "' + b"x" * bodylimit.MB, b"x" * bodylimit.MB, b"x" * 1024 + b'"}']

    async def receive():
        return {"type": "http.request", "body": chunks.pop(0), "more_body": bool(chunks)}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": "/api/conversations/paste", "headers": [(b"content-type", b"application/json")],
             "query_string": b"", "root_path": "", "scheme": "http", "server": ("test", 80), "client": ("1.2.3.4", 1), "http_version": "1.1"}
    asyncio.run(app(scope, receive, send))
    assert sent[0]["status"] == 413 and json.loads(sent[1]["body"])["error"]["code"] == "payload_too_large"
    assert bodylimit.limit_for("/api/checklists/1/versions") == 6 * bodylimit.MB and bodylimit.limit_for("/api/other") == bodylimit.DEFAULT_LIMIT


def test_a_paid_job_stops_starting_dialogs_once_the_budget_is_spent(world, monkeypatch):
    monkeypatch.setattr(settings, "daily_budget_usd", 2.0)
    monkeypatch.setattr(settings, "max_parallel_conversations", 1)
    world.judge.cost = 1.0  # each dialog costs far more than estimated
    with world.Session() as db:
        seed_version(db)
        for i in range(5):
            seed_conv(db, f"d{i}")
    client = TestClient(make_app(world, evaluations_router))
    r = client.post("/api/evaluations/run", json={"conversation_ids": [f"d{i}" for i in range(5)]})
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]
    assert wait_for(lambda: job_id in world.registry and world.registry[job_id].finished.is_set())
    job = client.get(f"/api/jobs/{job_id}").json()
    assert world.judge.calls == ["d0", "d1"]  # $2 spent after two dialogs: the third is never started
    assert job["status"] == "cancelled" and job["done"] == 2 and job["resumable"] is True
    assert any("budget exhausted" in entry["message"] for entry in job["log"])
    events = [kind for _s, kind, _d in world.registry[job_id].since(0)]
    assert "job.budget_exhausted" in events


def test_dialogs_over_the_transcript_limits_are_never_judged(world, monkeypatch):
    monkeypatch.setattr(settings, "max_transcript_chars", 80)
    with world.Session() as db:
        seed_version(db)
        seed_conv(db, "short", turns=[("operator", "Здравствуйте!")])
        seed_conv(db, "long", turns=[("operator", "очень длинная реплика " * 10)])
    client = TestClient(make_app(world, evaluations_router))
    dry = client.post("/api/evaluations/run", json={"conversation_ids": ["short", "long"], "dry_run": True}).json()
    assert dry["queued"] == 1 and [s["conversation_id"] for s in dry["skipped"]] == ["long"]
    r = client.post("/api/evaluations/run", json={"conversation_ids": ["long"]})
    assert r.status_code == 422 and r.json()["error"]["code"] == "transcript_too_long"
    dry_long = client.post("/api/evaluations/run", json={"conversation_ids": ["long"], "dry_run": True})
    assert dry_long.status_code == 200 and dry_long.json()["queued"] == 0 and dry_long.json()["guard"]["code"] == "transcript_too_long"
    with world.Session() as db:
        with pytest.raises(judge_runner.TranscriptTooLong):  # the worker refuses it too
            judge_runner.check_transcript_size("long", *judge_runner.transcript_size(db.get(Conversation, "long")))


def test_estimate_grows_with_the_transcript_and_the_number_of_criteria(world):
    with world.Session() as db:
        small = seed_version(db, codes=("C1", "C2"))
        large = seed_version(db, codes=tuple(f"K{i}" for i in range(64)))
        seed_conv(db, "tiny")
        seed_conv(db, "huge", turns=[("operator", "слово " * 2000)] * 5)  # 60 000 characters, under the limit
        convs = {c.id: c for c in db.execute(select(Conversation)).scalars()}
        tiny_small = estimator.estimate_run(db, [convs["tiny"]], small, provider="anthropic", model=None).total_usd
        tiny_large = estimator.estimate_run(db, [convs["tiny"]], large, provider="anthropic", model=None).total_usd
        huge_small = estimator.estimate_run(db, [convs["huge"]], small, provider="anthropic", model=None).total_usd
        free = estimator.estimate_run(db, [convs["huge"]], large, provider="local", model=None)
    assert tiny_small == 0.18  # the historical flat rate is the floor
    assert tiny_large > 5 * tiny_small  # 64 criteria = 64 judge calls
    assert huge_small > tiny_small * 2  # 60k characters are written to the prompt cache by every call
    assert free.total_usd == 0.0 and free.paid is False


def test_checklist_size_limits():
    crit = {"code": "C1", "title": "Приветствие", "answer_type": "binary", "weight": 10, "instruction": "Поздоровался."}
    many = prepare_checklist_payload({"criteria": [{**crit, "code": f"K{i}"} for i in range(MAX_CRITERIA + 1)]})
    assert any(e["field"] == "criteria" and str(MAX_CRITERIA) in e["message"] for e in many.errors)
    long = prepare_checklist_payload({"criteria": [{**crit, "instruction": "x" * (INSTRUCTION_MAX + 1)}]})
    assert [e["field"] for e in long.errors] == ["criteria[0].instruction"]
    assert prepare_checklist_payload({"criteria": [crit]}).ok


# ============================================================================================ SEC-7: restore and the budget


def test_restoring_a_snapshot_does_not_rewind_the_daily_budget(world, tmp_path):
    with world.Session() as db:
        seed_version(db)
        assert guards.budget_status(db)["spent_24h"] == 0.0  # the ledger exists from here on (backfilled with $0)
    sdir = tmp_path / "snapshots"
    snap.create_snapshot(world.path, sdir, "day0")
    with world.Session() as db:  # $5 is spent after the snapshot: the provider books it, the evaluation records it
        ledger.book(5.0, provider="anthropic", model=OPUS)
        spend(db, 5.0)
        assert guards.budget_status(db)["spent_24h"] == 5.0
    snap.restore_snapshot(world.path, sdir, "day0", world.engine.dispose)
    with world.Session() as db:
        assert guards.database_spent_since(db, utc(hours=24)) == 0.0  # the database is back to the snapshot
        assert guards.budget_status(db)["spent_24h"] == 5.0  # the ledger is not part of the snapshot


def test_ledger_backfills_the_database_once(world):
    with world.Session() as db:
        v = seed_version(db)
        seed_conv(db, "a")
        add_eval(db, "a", v, cost=3.0, created_at=utc(hours=1))
        db.add(AuditEvent(action=guards.TRIAL_ACTION, details={"response": {"cost_usd": 0.5}}, status_code=200))
        db.commit()
        assert guards.spent_since(db, utc(hours=24)) == pytest.approx(3.5)
        assert ledger.spent_since(utc(hours=24)) == pytest.approx(3.5)
        db.execute(Evaluation.__table__.delete())
        db.commit()
        ledger.reset_cache()  # a new process: the file says it was backfilled already
        assert guards.spent_since(db, utc(hours=24)) == pytest.approx(3.5)
        ledger.book(1.0, provider="anthropic", model=OPUS, kind="compile")
        assert guards.spent_since(db, utc(hours=24)) == pytest.approx(4.5)
    assert ledger.summary(utc(hours=24))["by_kind"] == {"backfill": 3.5, "compile": 1.0}
    assert Path(ledger.path()).parent != settings.data_dir  # tests never write the real ledger


def _admin_app(tmp_path: Path, monkeypatch) -> tuple[TestClient, Path]:
    live = tmp_path / "live.db"
    engine = create_engine(f"sqlite:///{live}")
    Base.metadata.create_all(engine)
    engine.dispose()
    sdir = tmp_path / "snapshots"
    monkeypatch.setattr(admin_router, "_live_db_path", lambda: live)
    monkeypatch.setattr(admin_router, "_snapshots_dir", lambda: sdir)
    monkeypatch.setattr(admin_router, "_active_jobs", list)
    monkeypatch.setattr(admin_router, "_audit", lambda *a, **k: None)
    monkeypatch.setattr(admin_router, "_dispose_engine", lambda: None)
    monkeypatch.setattr(admin_router, "_safe_sync_schema", lambda: "skipped")
    monkeypatch.setattr(admin_router, "_safe_recover_stale_jobs", lambda: "skipped")
    monkeypatch.delenv(admin_router.ADMIN_TOKEN_ENV, raising=False)
    monkeypatch.setattr(settings, "cqa_admin_token", None)
    app = FastAPI()
    app.include_router(admin_router.router)
    return TestClient(app), sdir


def test_snapshot_writes_fail_closed_on_the_hosted_instance_without_a_token(tmp_path, monkeypatch):
    client, _ = _admin_app(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "public_instance", True)
    monkeypatch.setattr(settings, "allow_unprotected_snapshots", False)
    r = client.post("/api/admin/snapshots", json={"name": "baseline"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "admin_token_not_configured"
    for call in (lambda: client.post("/api/admin/snapshots/baseline/restore", json={"confirm": "baseline"}),
                 lambda: client.get("/api/admin/snapshots/baseline/download"),
                 lambda: client.delete("/api/admin/snapshots/baseline?force=true")):
        assert call().status_code == 403
    listing = client.get("/api/admin/snapshots").json()
    assert listing["writes_allowed"] is False and listing["token_required"] is False
    monkeypatch.setattr(settings, "allow_unprotected_snapshots", True)  # explicit opt-out
    assert client.post("/api/admin/snapshots", json={"name": "baseline"}).status_code == 201
    monkeypatch.setattr(settings, "allow_unprotected_snapshots", False)
    monkeypatch.setenv(admin_router.ADMIN_TOKEN_ENV, "s3cret")
    assert client.post("/api/admin/snapshots", json={"name": "two"}).json()["error"]["code"] == "admin_token_required"
    assert client.post("/api/admin/snapshots", json={"name": "two"}, headers={admin_router.ADMIN_TOKEN_HEADER: "s3cret"}).status_code == 201
    monkeypatch.setattr(settings, "public_instance", False)
    monkeypatch.delenv(admin_router.ADMIN_TOKEN_ENV)
    assert client.post("/api/admin/snapshots", json={"name": "local"}).status_code == 201  # local use is unchanged


def test_manual_snapshots_are_capped(tmp_path, monkeypatch):
    client, _ = _admin_app(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "public_instance", False)
    monkeypatch.setattr(settings, "snapshot_max_manual", 2)
    assert client.post("/api/admin/snapshots", json={"name": "one"}).status_code == 201
    assert client.post("/api/admin/snapshots", json={"name": "two"}).status_code == 201
    r = client.post("/api/admin/snapshots", json={"name": "three"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "too_many_snapshots"
    assert client.post("/api/admin/snapshots", json={"name": "two", "overwrite": True}).status_code == 201  # replacing is fine


# ============================================================================================ SEC-9: trials


def _trial_world(world: World) -> None:
    with world.Session() as db:
        seed_version(db)
        seed_conv(db, "t1")


C9 = {"code": "C9", "title": "Приветствие", "answer_type": "binary", "weight": 10, "instruction": "Оператор поздоровался.",
      "judge_spec": {"definition": "Приветствие", "decision_steps": ["Найдите приветствие."]}}


async def _trial(world: World, **kw: Any) -> Any:
    db = world.Session()
    try:
        return await svc.run_criterion_test(db, criterion=dict(C9), conversation_id="t1", provider="anthropic", compile_spec=False, **kw)
    except svc.CriterionTestError as e:
        return e
    finally:
        db.close()


def test_concurrent_trials_cannot_all_pass_one_budget_check(world, monkeypatch):
    _trial_world(world)
    monkeypatch.setattr(settings, "daily_budget_usd", 0.10)  # room for two trials of $0.05
    monkeypatch.setattr(settings, "max_concurrent_trials", 10)
    sdk = FakeSDK(ok_judge)
    monkeypatch.setattr(anthropic_provider, "_client", sdk)

    async def main() -> list[Any]:
        sdk.gate = asyncio.Event()
        tasks = [asyncio.create_task(_trial(world)) for _ in range(3)]
        await asyncio.sleep(0.5)  # all three reached the budget check; two calls are in flight
        refused = [t.result() for t in tasks if t.done()]
        sdk.gate.set()
        return refused + [r for r in await asyncio.gather(*tasks) if r not in refused]

    results = asyncio.run(main())
    codes = sorted(r.code if isinstance(r, svc.CriterionTestError) else "ok" for r in results)
    assert codes == ["budget_exceeded", "ok", "ok"]
    assert len(sdk.calls) == 2 and guards.inflight_reserved() == 0.0


def test_trials_are_capped_and_failed_trials_are_booked(world, monkeypatch):
    _trial_world(world)
    monkeypatch.setattr(settings, "max_concurrent_trials", 1)
    sdk = FakeSDK(lambda kw: message("", stop_reason="refusal", usage=(9000, 800, 0, 4000)))
    monkeypatch.setattr(anthropic_provider, "_client", sdk)

    async def two() -> list[Any]:
        sdk.gate = asyncio.Event()
        first = asyncio.create_task(_trial(world))
        await asyncio.sleep(0.3)
        second = await _trial(world)
        sdk.gate.set()
        return [await first, second]

    first, second = asyncio.run(two())
    assert isinstance(second, svc.CriterionTestError) and (second.status, second.code) == (429, "too_many_trials")
    assert isinstance(first, svc.CriterionTestError) and (first.status, first.code) == (502, "judge_failed")
    billed = per_call((9000, 800, 0, 4000))
    assert first.extra["cost_usd"] == pytest.approx(billed, abs=1e-6)  # the refusal was billed and is reported
    assert ledger.spent_since(utc(hours=1)) == pytest.approx(billed)  # and counted in the budget, without an audit event
    monkeypatch.setattr(settings, "daily_budget_usd", billed + 0.01)
    monkeypatch.setattr(settings, "max_concurrent_trials", 2)
    again = asyncio.run(_trial(world))
    assert isinstance(again, svc.CriterionTestError) and again.code == "budget_exceeded"


def test_trial_with_an_unpriced_model_or_too_long_dialog_is_refused(world, monkeypatch):
    _trial_world(world)
    r = asyncio.run(_trial(world, model="claude-opus-4-7"))
    assert isinstance(r, svc.CriterionTestError) and (r.status, r.code) == (422, "unknown_model")
    monkeypatch.setattr(settings, "max_transcript_chars", 10)
    r = asyncio.run(_trial(world))
    assert isinstance(r, svc.CriterionTestError) and (r.status, r.code) == (422, "transcript_too_long")


# ============================================================================================ SEC-10: billed failures


def _packs() -> tuple[Any, Any]:
    from cqa.ingest.normalize import NormTurn
    from cqa.judge.packer import ConversationPack, CriterionPack

    turns = [NormTurn(idx=i, timestamp_raw=f"00:0{i}", offset_seconds=float(i), speaker=s, text=t,
                      segment="operator" if s == "operator" else "pre") for i, (s, t) in enumerate(TURNS)]
    conv = ConversationPack(conversation_id="x", channel="agent chat", language="RU", turns=turns, operator_start_idx=1,
                            terminal_event=None, identity_confirmed_event=False, first_response_seconds=None, operator_name=None)
    crit = CriterionPack(code="C1", title="Приветствие", instruction="Оператор поздоровался.", answer_type="binary", options=BINARY,
                         na_allowed=False, na_condition=None, channel_overrides={}, judge_spec=None)
    return conv, crit


def test_a_billed_first_call_survives_a_retry_that_fails(monkeypatch):
    answers = [message(verdict_json("C1", answer="maybe")), message("", stop_reason="refusal")]  # invalid answer → retry → refusal
    sdk = FakeSDK(lambda kw: answers.pop(0))
    monkeypatch.setattr(anthropic_provider, "_client", sdk)
    conv, crit = _packs()
    out = asyncio.run(judge_runner.judge_criterion([AnthropicProvider(OPUS)], conv, crit, rs={"judge_effort": "high"},
                                                   fewshots=[], reference_facts=[]))
    assert out.error and len(sdk.calls) == 2
    assert [t["kind"] for t in out.traces] == ["judge", "retry"] and out.traces[1]["failed"] is True
    assert out.cost_usd == pytest.approx(2 * per_call())  # both billed calls count
    assert ledger.spent_since(utc(hours=1)) == pytest.approx(2 * per_call())


def test_an_ensemble_member_that_fails_after_billing_keeps_its_cost(monkeypatch):
    calls = {"n": 0}

    def respond(kw: dict[str, Any]) -> Any:
        calls["n"] += 1
        return message(verdict_json("C1")) if kw["model"] == OPUS else message("", stop_reason="max_tokens")

    monkeypatch.setattr(anthropic_provider, "_client", FakeSDK(respond))
    conv, crit = _packs()
    members = [AnthropicProvider(OPUS), AnthropicProvider("claude-sonnet-5-5")]
    out = asyncio.run(judge_runner.judge_criterion(members, conv, crit, rs={}, fewshots=[], reference_facts=[]))
    assert out.error is None and out.verdict.answer == "yes"
    assert out.cost_usd == pytest.approx(per_call() + cost_usd("claude-sonnet-5-5", 1000, 500, 0, 2000))


def test_a_failed_evaluation_stores_its_billed_traces(world, monkeypatch):
    with world.Session() as db:
        v = seed_version(db, codes=("C1", "C2"))
        seed_conv(db, "a")
        vid = v.id
    monkeypatch.setattr(anthropic_provider, "_client", FakeSDK(lambda kw: message("", stop_reason="refusal")))
    eid = asyncio.run(judge_runner.evaluate_conversation("a", vid, provider_name="anthropic"))
    with world.Session() as db:
        ev = db.get(Evaluation, eid)
        assert ev.status == "failed" and ev.cost_usd == pytest.approx(2 * per_call(), abs=1e-6)
        assert db.scalar(select(func.count()).select_from(LlmTrace).where(LlmTrace.evaluation_id == eid)) == 2


# ============================================================================================ F1: restore under a running evaluation


class _StubProvider:
    name = "anthropic"
    model = OPUS

    def __init__(self, during=None):
        self.during = during

    async def complete(self, system, messages, schema, *, effort="high", max_tokens=4096) -> ProviderResult:
        if self.during:
            self.during()
        parsed = json.loads(verdict_json(criterion_code({"messages": messages})))
        return ProviderResult(parsed=parsed, usage=Usage(input_tokens=10, output_tokens=5), raw={}, model=OPUS, provider="anthropic",
                              latency_ms=1)

    def cost(self, usage: Usage) -> float:
        return 0.0


def test_an_evaluation_never_writes_into_a_restored_database(world, monkeypatch):
    with world.Session() as db:
        v = seed_version(db, codes=("C1",))
        seed_conv(db, "a")
        vid = v.id
    monkeypatch.setattr(judge_runner, "get_provider", lambda name, model=None, **kw: _StubProvider(during=cqa_db.bump_restore_generation))
    with pytest.raises(judge_runner.EvaluationAborted):
        asyncio.run(judge_runner.evaluate_conversation("a", vid))
    with world.Session() as db:
        assert db.scalar(select(func.count()).select_from(EvaluationCriterion)) == 0  # nothing of it was written


def test_an_evaluation_does_not_overwrite_a_row_that_now_belongs_to_another_dialog(world, monkeypatch):
    with world.Session() as db:
        v = seed_version(db, codes=("C1",))
        seed_conv(db, "a")
        seed_conv(db, "other")
        vid = v.id

    def swap_rows() -> None:  # what a restore of a bigger snapshot does: the running row's id is another dialog's evaluation
        with world.Session() as db:
            running = db.execute(select(Evaluation).where(Evaluation.status == "running")).scalars().one()
            rid = running.id
            db.delete(running)
            db.flush()
            db.add(Evaluation(id=rid, conversation_id="other", checklist_version_id=vid, status="completed", total_score=100.0))
            db.commit()

    monkeypatch.setattr(judge_runner, "get_provider", lambda name, model=None, **kw: _StubProvider(during=swap_rows))
    with pytest.raises(judge_runner.EvaluationAborted):
        asyncio.run(judge_runner.evaluate_conversation("a", vid))
    with world.Session() as db:
        [ev] = db.execute(select(Evaluation)).scalars().all()
        assert (ev.conversation_id, ev.status, ev.total_score) == ("other", "completed", 100.0)
        assert db.scalar(select(func.count()).select_from(EvaluationCriterion)) == 0


def test_restore_waits_for_cancelled_workers_under_the_start_lock(monkeypatch, tmp_path):
    state = runner.JobState("j_inflight")
    monkeypatch.setattr(runner, "_registry", {"j_inflight": state})
    monkeypatch.setattr(admin_router, "_active_jobs", lambda: ["j_inflight"] if not state.finished.is_set() else [])
    monkeypatch.setattr(admin_router, "RESTORE_JOB_WAIT_S", 0.2)
    monkeypatch.setattr(admin_router, "_safe_sync_schema", lambda: "skipped")
    monkeypatch.setattr(admin_router, "_safe_recover_stale_jobs", lambda: {"jobs": [], "evaluations": []})
    seen: list[tuple[bool, int]] = []

    def fake_restore(db_path, sdir, name, dispose, *, actor=None, keep_pre_restore=None):
        seen.append((runner.start_lock._is_owned(), cqa_db.restore_generation()))
        return {"counts": {}, "snapshot": {"name": name}, "pre_restore": {"name": "pre"}}

    monkeypatch.setattr(snap, "restore_snapshot", fake_restore)
    with pytest.raises(admin_router._JobsRunning):
        admin_router._restore_locked(tmp_path / "x.db", tmp_path, "baseline", False, None, 5)
    with pytest.raises(admin_router._JobsStillRunning) as still:
        admin_router._restore_locked(tmp_path / "x.db", tmp_path, "baseline", True, None, 5)
    assert still.value.jobs == ["j_inflight"] and state.cancel.is_set() and seen == []  # nothing copied under a live worker
    generation = cqa_db.restore_generation()
    monkeypatch.setattr(admin_router, "_active_jobs", lambda: ["j_inflight"])
    threading.Timer(0.05, state.finished.set).start()  # the worker finishes its in-flight dialog
    result = admin_router._restore_locked(tmp_path / "x.db", tmp_path, "baseline", True, None, 5)
    assert result[3] == ["j_inflight"] and seen == [(True, generation + 1)]  # under start_lock, after the generation bump


def test_a_worker_tolerates_a_job_row_that_disappeared(world):
    state = runner.JobState("j_gone")
    runner._progress(state, "a", ok=True, evaluation_id=None)  # no row: logged, no AttributeError
    asyncio.run(runner._run_dialogs(state, ["a"], 1, "fake", None, "full", None, None))
    assert [kind for _s, kind, data in state.since(0)] == ["job.done"] and state.since(0)[0][2]["status"] == "missing"


# ============================================================================================ F2: one Claude client per event loop


def test_each_event_loop_gets_its_own_claude_client(monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", "test-key-never-used")
    monkeypatch.setattr(anthropic_provider, "_client", None)

    async def use() -> tuple[Any, bool]:
        c = anthropic_provider.client()
        same = anthropic_provider.client() is c
        await anthropic_provider.aclose_loop_client()
        return c, same and asyncio.get_running_loop() not in anthropic_provider._clients

    c1, ok1 = asyncio.run(use())
    c2, ok2 = asyncio.run(use())
    assert ok1 and ok2 and c1 is not c2
    out: list[Any] = []
    threads = [threading.Thread(target=lambda: out.append(asyncio.run(use())[0])) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert len(out) == 2 and out[0] is not out[1]


# ============================================================================================ F4: no pinned connection


def test_no_pooled_connection_is_held_while_the_judges_run(world, monkeypatch):
    with world.Session() as db:
        v = seed_version(db, codes=("C1", "C2", "C3"))
        seed_conv(db, "a")
        vid = v.id
    checked_out: list[int] = []
    monkeypatch.setattr(judge_runner, "get_provider",
                        lambda name, model=None, **kw: _StubProvider(during=lambda: checked_out.append(world.engine.pool.checkedout())))
    eid = asyncio.run(judge_runner.evaluate_conversation("a", vid))
    assert checked_out == [0, 0, 0]
    with world.Session() as db:
        assert db.get(Evaluation, eid).status == "completed"


def test_engine_pool_has_room_for_the_configured_concurrency():
    if not settings.database_url.startswith("sqlite") or ":memory:" in settings.database_url:
        pytest.skip("pool sizing applies to a file database")
    assert cqa_db.engine.pool._max_overflow >= settings.max_concurrent_jobs * settings.max_parallel_conversations + 10


# ============================================================================================ F5 + empty selections: exports


ORGANIZER_CODES = tuple(f"C{i}" for i in range(1, 9))


def _export_world(world: World) -> tuple[int, int]:
    with world.Session() as db:
        v1 = seed_version(db, codes=ORGANIZER_CODES)
        v2 = seed_version(db, codes=ORGANIZER_CODES, checklist=db.get(Checklist, v1.checklist_id), version_no=2, weights={"C1": 20})
        for cid in ("c1", "c2", "c3"):
            seed_conv(db, cid)
        add_eval(db, "c1", v1, status="failed", error="timeout")    # c1: failed on v1, completed on v2
        add_eval(db, "c1", v2, rows=True)
        add_eval(db, "c2", v1, rows=True)                            # c2: completed on v1, then a failed v1 re-run
        add_eval(db, "c2", v1, status="failed", error="timeout")
        add_eval(db, "c3", v2, rows=True)                            # c3: only v2
        return v1.id, v2.id


def _records(doc: dict[str, Any]) -> dict[str, str]:
    return {r["conversation_id"]: r["status"] for r in doc["results"]}


def test_results_export_never_uses_another_versions_evaluation_and_both_sources_agree(world, monkeypatch):
    v1, _v2 = _export_world(world)
    with world.Session() as db:
        doc, _, _ = results_json.from_db(db, checklist_version_id=v1, team_name="t")
    assert _records(doc) == {"c1": "error", "c2": "ok", "c3": "error"}  # v2 results never stand in for the organizer version
    app = make_app(world, conversations_router, checklists_router)
    monkeypatch.setattr(httpx, "Client", lambda **kw: TestClient(app, base_url="http://testserver"))
    api_doc, _, _ = results_json.from_api("http://testserver", checklist_version_id=v1, team_name="t")
    assert _records(api_doc) == _records(doc)  # the --api and --db paths select the same evaluations


def test_an_explicit_empty_selection_selects_nothing(world, monkeypatch):
    v1, _v2 = _export_world(world)
    client = TestClient(make_app(world, evaluations_router))
    r = client.post("/api/evaluations/run", json={"conversation_ids": [], "all": False})
    assert r.status_code == 400 and r.json()["error"]["code"] == "no_conversations"
    assert client.post("/api/evaluations/run", json={"conversation_ids": None, "dry_run": True}).json()["queued"] == 3  # null = all
    assert client.post("/api/evaluations/run", json={"all": True, "dry_run": True}).json()["queued"] == 3
    with world.Session() as db:
        assert results_json.from_db(db, conversation_ids=[], checklist_version_id=v1, team_name="t")[0]["results"] == []
    app = make_app(world, conversations_router, checklists_router)
    monkeypatch.setattr(httpx, "Client", lambda **kw: TestClient(app, base_url="http://testserver"))
    assert results_json.from_api("http://testserver", conversation_ids=[], checklist_version_id=v1, team_name="t")[0]["results"] == []


# ============================================================================================ restart-interrupted rows


def test_a_row_interrupted_by_a_restart_does_not_hide_the_last_result(world):
    with world.Session() as db:
        v = seed_version(db)
        seed_conv(db, "a")
        seed_conv(db, "b")
        done = add_eval(db, "a", v, rows=True)
        add_eval(db, "a", v, status="running", created_at=runner.PROCESS_STARTED_AT - timedelta(hours=1))
        only = add_eval(db, "b", v, status="running", created_at=runner.PROCESS_STARTED_AT - timedelta(hours=1))
        done_id, only_id = done.id, only.id
    recovered = runner.recover_stale_jobs(session_factory=world.Session)
    assert len(recovered["evaluations"]) == 2
    with world.Session() as db:
        assert latest_evaluation(db, "a").id == done_id  # the completed result stays visible
        assert latest_evaluation(db, "b").id == only_id  # with nothing else, the interrupted row is shown
        failed = add_eval(db, "a", db.get(ChecklistVersion, v.id), status="failed", error="Anthropic connection error")
        assert latest_evaluation(db, "a").id == failed.id  # a real failure is still the latest attempt
    assert runner.INTERRUPTED_ERROR == "interrupted by restart"


# ============================================================================================ SEC-2: judge-spec compilation


def _spec_payload() -> dict[str, Any]:
    return {"name": "Свой", "criteria": [{"code": f"K{i}", "title": f"Критерий {i}", "answer_type": "binary", "weight": 10,
                                          "instruction": "Оператор поздоровался."} for i in range(3)]}


def test_compilation_is_budgeted_and_falls_back_when_the_budget_is_spent(world, monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", "test-key-never-used")
    compiled: list[str] = []
    reserved: list[float] = []

    async def fake_compile(crit: dict[str, Any]) -> dict[str, Any]:
        compiled.append(crit["code"])
        reserved.append(guards.inflight_reserved())
        return compiler.fallback_spec(crit) | {"_compiled_by": "test"}

    monkeypatch.setattr(compiler, "compile_spec_async", fake_compile)
    with world.Session() as db:
        seed_version(db)
        spend(db, 80.0)
        warnings: list[dict[str, str]] = []
        v = svc.create_checklist(db, _spec_payload(), warnings_out=warnings)
        assert compiled == [] and all(c.judge_spec and "_compiled_by" not in c.judge_spec for c in v.criteria)
        assert any("бюджет" in w["message"] for w in warnings)
        monkeypatch.setattr(settings, "daily_budget_usd", 1000.0)  # spend stays booked; a bigger budget makes room
        svc.create_checklist(db, _spec_payload())
    assert sorted(compiled) == ["K0", "K1", "K2"]
    assert reserved and all(r == pytest.approx(3 * compiler.COMPILE_ESTIMATE_USD) for r in reserved)  # reserved while compiling
    assert guards.inflight_reserved() == 0.0


def test_compilation_runs_with_bounded_concurrency(monkeypatch):
    running = {"now": 0, "max": 0}

    async def slow_compile(crit: dict[str, Any]) -> dict[str, Any]:
        running["now"] += 1
        running["max"] = max(running["max"], running["now"])
        await asyncio.sleep(0.01)
        running["now"] -= 1
        return compiler.fallback_spec(crit)

    monkeypatch.setattr(compiler, "compile_spec_async", slow_compile)
    crits = [{"code": f"K{i}", "instruction": "x", "answer_type": "binary"} for i in range(20)]
    assert len(compiler.run_in_fresh_loop(lambda: compiler.compile_many_async(crits))) == 20
    assert running["max"] == compiler.COMPILE_CONCURRENCY


def test_regenerating_a_spec_is_refused_when_the_budget_is_spent(world, monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", "test-key-never-used")
    called: list[str] = []

    async def fake_compile(crit: dict[str, Any]) -> dict[str, Any]:
        called.append(crit["code"])
        return compiler.fallback_spec(crit)

    monkeypatch.setattr(compiler, "compile_spec_async", fake_compile)
    with world.Session() as db:
        v = seed_version(db)
        spend(db, 80.0)
        cl_id, vid = v.checklist_id, v.id
    client = TestClient(make_app(world, checklists_router))
    # force=true: the spend fixture is a completed evaluation of this version, which alone refuses an in-place rebuild (CHK-15)
    r = client.post(f"/api/checklists/{cl_id}/versions/{vid}/judge-spec/C1/regenerate", params={"force": "true"})
    assert r.status_code == 429 and r.json()["error"]["code"] == "budget_exceeded" and called == []
    monkeypatch.setattr(settings, "daily_budget_usd", 1000.0)
    assert client.post(f"/api/checklists/{cl_id}/versions/{vid}/judge-spec/C1/regenerate",
                       params={"force": "true"}).status_code == 200 and called == ["C1"]


def test_compilation_cost_is_booked(monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", "test-key-never-used")
    spec = {"definition": "Приветствие", "decision_steps": ["Найдите приветствие."], "na_conditions": [], "evidence_requirement": "",
            "pass_examples": [], "fail_examples": [], "channel_notes": {}, "pitfalls": []}
    sdk = FakeSDK(lambda kw: message(json.dumps(spec, ensure_ascii=False), usage=(1500, 2500, 0, 0)))
    monkeypatch.setattr(anthropic_provider, "_client", sdk)
    out = asyncio.run(compiler.compile_spec_async({"code": "K1", "title": "Приветствие", "instruction": "Поздоровался.",
                                                   "answer_type": "binary"}))
    assert out["_compiled_by"] == OPUS and sdk.calls[0]["model"] == settings.secondary_model
    booked = ledger.summary(utc(hours=1))
    assert booked["by_kind"] == {"compile": round(cost_usd(settings.secondary_model, 1500, 2500), 4)}


# ============================================================================================ the real SDK, offline


def test_real_sdk_request_shape_and_billed_refusal_through_a_mock_transport(monkeypatch):
    """The provider through the real AsyncAnthropic (its own HTTP library, a mock transport: no network): the request is
    the structured-output request messages.parse would send, real Message objects parse, a refusal keeps its usage."""
    import anthropic
    from anthropic import _exceptions as sdk_exceptions

    from cqa.judge.contract import Verdict
    from cqa.judge.providers import ProviderRefusal

    http = getattr(sdk_exceptions, "httpx2", None) or sdk_exceptions.httpx
    bodies: list[dict[str, Any]] = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        refusal = len(bodies) == 2
        return http.Response(200, json={
            "id": f"msg_{len(bodies)}", "type": "message", "role": "assistant", "model": OPUS, "stop_sequence": None,
            "stop_reason": "refusal" if refusal else "end_turn",
            "content": [{"type": "thinking", "thinking": "", "signature": "sig"}, {"type": "text", "text": "" if refusal else verdict_json("C1")}],
            "usage": {"input_tokens": 100, "output_tokens": 50, "cache_read_input_tokens": 10, "cache_creation_input_tokens": 200}})

    client = anthropic.AsyncAnthropic(api_key="test-key-never-used", max_retries=0,
                                      http_client=http.AsyncClient(transport=http.MockTransport(handler)))
    monkeypatch.setattr(anthropic_provider, "_client", client)
    provider = AnthropicProvider(OPUS)
    msgs = [{"role": "user", "content": [{"type": "text", "text": "## Transcript"}]}]

    async def calls() -> tuple[Any, Any]:
        ok = await provider.complete("SYS", msgs, Verdict, effort="high")
        with pytest.raises(ProviderRefusal) as refused:
            await provider.complete("SYS", msgs, Verdict, effort="high")
        return ok, refused.value

    ok, refused = asyncio.run(calls())
    assert bodies[0]["output_config"] == {"effort": "high", "format": anthropic_provider.json_schema_format(Verdict)}
    assert bodies[0]["thinking"] == {"type": "adaptive"} and bodies[0]["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert ok.parsed["answer"] == "yes" and ok.usage == Usage(input_tokens=100, output_tokens=50, cache_read_tokens=10, cache_write_tokens=200)
    assert refused.billed and refused.cost_usd == pytest.approx(cost_usd(OPUS, 100, 50, 10, 200))
    assert ledger.summary(utc(hours=1))["calls"] == 2 and ledger.summary(utc(hours=1))["failed_calls"] == 1
