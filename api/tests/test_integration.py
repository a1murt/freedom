"""Integration step (docs/arch/integration.md): cross-package wiring and the integration requests applied to shared files.

Offline by construction: in-memory SQLite (StaticPool + dependency_overrides), no Claude calls (fake provider or no
judge at all), never api/data/cqa.db. Tests that touch the real `cqa.main.app` never enter its lifespan and remove
their dependency override afterwards.
"""
from __future__ import annotations

import importlib.util
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import httpx
import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.exceptions import HTTPException as StarletteHTTPException

from cqa import main as cqa_main
from cqa.audit import service as audit
from cqa.checklist import service as checklist_svc
from cqa.config import settings
from cqa.db import Base, get_db
from cqa.export import results_json as rj
from cqa.ingest.normalize import normalize
from cqa.jobs import guards
from cqa.judge import runner as judge_runner
from cqa.models import (AuditEvent, Checklist, ChecklistVersion, Conversation, Criterion, Evaluation, EvaluationCriterion,
                        Setting, Turn)
from cqa.platform import requestlog
from cqa.routers import admin as admin_router
from cqa.routers import appeals as appeals_router
from cqa.routers import evaluations as evaluations_router
from cqa.security import middleware as security_mw
from cqa.security import roles

ROOT = Path(__file__).resolve().parents[2]
SEED = json.loads((ROOT / "api" / "seeds" / "checklist_freedom_v1.json").read_text(encoding="utf-8"))
CODES = [c["code"] for c in SEED["criteria"]]


# ----------------------------------------------------------------------------- helpers


@pytest.fixture()
def Sessions():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)
    engine.dispose()


def _override(Sessions):
    def get_test_db():
        db = Sessions()
        try:
            yield db
        finally:
            db.close()

    return get_test_db


def _seed_version(db: Session, *, name: str = SEED["name"], active: bool = True) -> ChecklistVersion:
    cl = Checklist(name=name, description=SEED.get("description"))
    db.add(cl)
    db.flush()
    v = ChecklistVersion(checklist_id=cl.id, version_no=1, is_active=active, change_note="v1", scoring_rules=SEED["scoring_rules"],
                         sections=SEED["sections"])
    db.add(v)
    db.flush()
    for i, c in enumerate(SEED["criteria"]):
        db.add(Criterion(checklist_version_id=v.id, code=c["code"], order=i, section=c.get("section"), title=c["title"],
                         instruction=c.get("instruction") or "", answer_type=c["answer_type"], options=c["options"],
                         max_points=c.get("max_points", 1), weight=c.get("weight", 0), na_allowed=c.get("na_allowed", False),
                         na_condition=c.get("na_condition"), is_critical=c.get("is_critical", False),
                         critical_value=c.get("critical_value"), channel_overrides=c.get("channel_overrides") or {}))
    db.flush()
    return v


def _chat(db: Session, cid: str, *, operator_name: str | None = "Алина", agent_name: str | None = None) -> Conversation:
    conv = Conversation(id=cid, channel="agent chat", language="RU", operator_name=operator_name, agent_name=agent_name,
                        turn_count=2, meta={})
    db.add(conv)
    db.add_all([Turn(conversation_id=cid, idx=0, timestamp_raw="00:00", offset_seconds=0.0, speaker="client", text="Добрый день"),
                Turn(conversation_id=cid, idx=1, timestamp_raw="00:20", offset_seconds=20.0, speaker="operator",
                     text="Здравствуйте! Меня зовут Алина.", segment="operator")])
    db.flush()
    return conv


# ----------------------------------------------------------------------------- request pipeline of the real app


def test_middleware_stack_order_of_the_real_app():
    """requestlog (outermost) → ServerErrorMiddleware → CORS → body limit → security → exception handlers → routes; all
    pure ASGI."""
    assert [m.cls.__name__ for m in cqa_main.app.user_middleware] == ["CORSMiddleware", "BodyLimitMiddleware", "SecurityMiddleware"]
    node, names = cqa_main.app.build_middleware_stack(), []
    while node is not None and len(names) < 12:
        names.append(type(node).__name__)
        node = getattr(node, "app", None)
    expected = ["RequestLogMiddleware", "ServerErrorMiddleware", "CORSMiddleware", "BodyLimitMiddleware", "SecurityMiddleware",
                "ExceptionMiddleware"]
    assert names[0] == "RequestLogMiddleware"  # outermost: tags and logs every answer, including the 403s and 500s
    assert [n for n in names if n in expected] == expected  # FastAPI may add its own layers (telemetry, exit stack) in between
    assert not any(n == "BaseHTTPMiddleware" for n in names)
    assert cqa_main.app._cqa_requestlog_installed is True


@pytest.fixture()
def main_client(Sessions):
    """cqa.main.app without its lifespan, with get_db pointed at an in-memory database (removed afterwards)."""
    cqa_main.app.dependency_overrides[get_db] = _override(Sessions)
    try:
        yield TestClient(cqa_main.app)
    finally:
        cqa_main.app.dependency_overrides.pop(get_db, None)


def test_unknown_route_and_wrong_method_use_the_envelope(main_client):
    r = main_client.get("/api/does-not-exist", headers={"X-Request-ID": "rid-404"})
    assert r.status_code == 404 and r.headers["x-request-id"] == "rid-404"
    assert r.json() == {"error": {"code": "not_found", "message": "Адрес не найден."}}
    r = main_client.patch("/api/health")
    assert r.status_code == 405 and r.json()["error"]["code"] == "method_not_allowed" and "GET" in r.headers["allow"]
    assert r.headers["x-request-id"]


def test_request_validation_errors_use_the_envelope_and_keep_detail(main_client):
    r = main_client.post("/api/evaluations/run", json={"all": "maybe", "conversation_ids": "p1-ru-001"})
    assert r.status_code == 422
    body = r.json()
    err = body["error"]
    assert err["code"] == "validation_error" and err["message"].startswith("Некорректный запрос")
    fields = {e["field"] for e in err["errors"]}
    assert {"all", "conversation_ids"} <= fields
    assert next(e for e in err["errors"] if e["field"] == "all")["message"] == "Ожидается true или false."
    assert isinstance(body["detail"], list) and body["detail"]  # FastAPI's list kept for old clients
    bad = main_client.post("/api/evaluations/run", content=b"{not json", headers={"content-type": "application/json"})
    assert bad.status_code == 422 and bad.json()["error"]["errors"][0] == {"field": "body", "message": "Некорректный JSON в теле запроса."}
    nested = main_client.post("/api/appeals", json={"evaluation_criterion_id": "x", "comment": "c"})
    assert nested.json()["error"]["errors"][0]["field"] == "evaluation_criterion_id"


def test_field_path_format():
    assert cqa_main._field_path(("body", "criteria", 2, "weight")) == "criteria[2].weight"
    assert cqa_main._field_path(("query", "days")) == "days"
    assert cqa_main._field_path(("body",)) == "body"


def _handler_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    app.add_exception_handler(StarletteHTTPException, cqa_main.http_exc)
    app.add_exception_handler(RequestValidationError, cqa_main.validation_exc)
    app.add_exception_handler(Exception, cqa_main.any_exc)
    requestlog.install(app)

    @app.get("/api/boom")
    def boom():
        raise RuntimeError("database exploded, key sk-ant-abcdefghijklmnopqrstuvwxyz0123")

    return app


def test_unhandled_error_envelope_has_request_id_cors_and_no_secrets(monkeypatch):
    monkeypatch.setattr(settings, "cors_origins", ["*"])
    client = TestClient(_handler_app(), raise_server_exceptions=False)
    r = client.get("/api/boom", headers={"Origin": "https://cqa.example", "X-Request-ID": "rid-500"})
    assert r.status_code == 500
    err = r.json()["error"]
    assert err["code"] == "internal" and err["request_id"] == "rid-500" and "rid-500" in err["message"]
    assert err["type"] == "RuntimeError" and "database exploded" in err["detail"] and "sk-ant-abc" not in err["detail"]
    assert r.headers["access-control-allow-origin"] == "*"  # the browser can read the 500 (built outside CORSMiddleware)
    assert r.headers["x-request-id"] == "rid-500" and "X-Request-ID" in r.headers["access-control-expose-headers"]
    monkeypatch.setattr(settings, "cors_origins", ["https://cqa.example"])
    r = client.get("/api/boom", headers={"Origin": "https://cqa.example"})
    assert r.headers["access-control-allow-origin"] == "https://cqa.example"
    r = client.get("/api/boom", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r.headers


# ----------------------------------------------------------------------------- fake provider, estimates, guards


def test_fake_provider_is_routed_natively_and_free():
    members = judge_runner._members({}, "fake", None)
    assert [(m.name, m.model) for m in members] == [("fake", "fake-judge")]
    assert judge_runner.estimate_cost(5, 2, "fake") == 0.0
    assert judge_runner.estimate_cost(1, 1, "ollama") == 0.0 and judge_runner.estimate_cost(1, 1, "local") == 0.0
    assert judge_runner.estimate_cost(1, 1, "anthropic") == 0.53 and judge_runner.estimate_cost(2, 0, "ensemble") == 0.76


def test_dry_run_with_the_fake_judge_is_never_blocked_by_the_budget(Sessions, monkeypatch):
    with Sessions() as db:
        _seed_version(db)
        _chat(db, "i-ru-001")
        db.add(Evaluation(conversation_id="i-ru-001", checklist_version_id=1, status="completed", cost_usd=0.5))
        db.commit()
    app = FastAPI()
    app.include_router(evaluations_router.router)
    app.dependency_overrides[get_db] = _override(Sessions)
    client = TestClient(app)
    monkeypatch.setattr(settings, "daily_budget_usd", 0.6)
    fake = client.post("/api/evaluations/run", json={"conversation_ids": ["i-ru-001"], "provider": "fake", "dry_run": True}).json()
    assert fake["estimated_cost_usd"] == 0.0 and fake["guard"] is None
    paid = client.post("/api/evaluations/run", json={"conversation_ids": ["i-ru-001"], "provider": "anthropic", "dry_run": True}).json()
    assert paid["guard"]["code"] == "budget_exceeded" and paid["budget"]["spent_24h"] == 0.5


def test_trial_spend_counts_towards_the_daily_budget(Sessions, monkeypatch):
    now = datetime.now(UTC)
    with Sessions() as db:
        db.add(Evaluation(conversation_id="x", checklist_version_id=1, status="completed", cost_usd=1.0))
        db.add(AuditEvent(action=guards.TRIAL_ACTION, details={"response": {"cost_usd": 0.25}}, status_code=200))
        db.add(AuditEvent(action=guards.TRIAL_ACTION, details={"response": {"cost_usd": 9.0}}, status_code=403))  # refused: no spend
        db.add(AuditEvent(action=guards.TRIAL_ACTION, details={"response": {"cost_usd": 5.0}}, status_code=200,
                          ts=now - timedelta(hours=30)))  # outside the window
        db.add(AuditEvent(action=guards.TRIAL_ACTION, details={"response": "garbage"}, status_code=200))
        db.commit()
        assert guards.spent_since(db, now - guards.WINDOW) == pytest.approx(1.25)
        monkeypatch.setattr(settings, "daily_budget_usd", 1.28)
        with pytest.raises(guards.GuardError) as e:
            guards.check_budget(db, 0.05)
        assert e.value.code == "budget_exceeded"
        assert guards.check_budget(db, 0.0)["spent_24h"] == 1.25  # a free run is never blocked
        with pytest.raises(checklist_svc.CriterionTestError) as trial:
            checklist_svc._budget_guard(db, "anthropic", 1)
        assert trial.value.status == 429 and trial.value.code == "budget_exceeded"
        checklist_svc._budget_guard(db, "fake", 1)  # free providers pass


# ----------------------------------------------------------------------------- checklists: default version


def test_active_version_prefers_the_organizer_checklist_then_the_setting(Sessions, monkeypatch):
    with Sessions() as db:
        organizer = _seed_version(db, active=False)  # checklist 1, version id 1 (inactive)
        imported = _seed_version(db, name="Импорт")  # checklist 2, version id 2 (active)
        v2 = ChecklistVersion(checklist_id=organizer.checklist_id, version_no=2, is_active=True, scoring_rules={}, sections=[])
        db.add(v2)
        db.commit()
        assert checklist_svc.active_version(db).id == v2.id  # not the import, although its row comes first
        monkeypatch.setattr(settings, "default_checklist_id", imported.checklist_id)
        assert checklist_svc.active_version(db).id == imported.id
        db.add(Setting(key="default_checklist_id", value=organizer.checklist_id))  # the DB setting wins over the env
        db.commit()
        assert checklist_svc.active_version(db).id == v2.id
        db.get(Setting, "default_checklist_id").value = 999  # unknown checklist: fall back to the ordering
        db.commit()
        assert checklist_svc.active_version(db).id == v2.id


# ----------------------------------------------------------------------------- results.json


def _row(code: str, answer, *, applicable: bool = True, review_state: str = "none", final=None, reason: str | None = None) -> dict:
    spec = next(c for c in SEED["criteria"] if c["code"] == code)
    row = {"code": code, "answer_type": spec["answer_type"], "applicable": applicable, "answer": answer if applicable else None,
           "na_reason": None if applicable else "нет данных", "weight": spec.get("weight", 0), "max_points": spec.get("max_points", 1),
           "explanation": f"{code}: обоснование модели", "evidence": [{"turn_idx": 1, "role": "supports"}], "flags": [],
           "review_state": review_state, "final_answer": final if final is not None else (answer if applicable else None),
           "appeal": {"decision": {"reason": reason}} if reason else None}
    return row


def test_results_export_mapping_follows_version_export_ids():
    assert rj.export_mapping(CODES, {}) == rj.DEFAULT_EXPORT_IDS
    replaced = [("C6b" if c == "C6" else c) for c in CODES]
    mapping = rj.export_mapping(replaced, {"export_ids": {"C6b": "dialogue"}})
    assert mapping["C6b"] == "dialogue" and list(mapping.values()) == list(rj.REQUIRED_IDS)
    with pytest.raises(rj.ExportError, match="dialogue"):
        rj.export_mapping(replaced, {})
    assert rj.export_mapping(CODES + ["C9"], {})["C1"] == "contact"  # extra criteria are simply not exported


def test_results_answers_final_is_opt_in():
    ev = {"status": "completed", "criteria": [
        _row("C1", "no", review_state="changed", final="yes", reason="Приветствие было"), _row("C2", "yes"), _row("C3", "yes"),
        _row("C4", 20), _row("C5", "yes"), _row("C6", 10), _row("C7", "yes"), _row("C8", "no")]}
    ai = rj.build_record("x", ev, n_turns=5)
    final = rj.build_record("x", ev, n_turns=5, answers="final")
    contact_ai = next(c for c in ai["criteria"] if c["criterion_id"] == "contact")
    contact_final = next(c for c in final["criteria"] if c["criterion_id"] == "contact")
    assert contact_ai["value"] is False and ai["score"]["final_score"] == 90.0
    assert contact_final["value"] is True and final["score"]["final_score"] == 100.0
    assert contact_final["rationale"].startswith("Решение ревьюера: Приветствие было. Обоснование модели: C1")
    ev["criteria"][1] = _row("C2", "yes", review_state="changed", final="N/A")
    assert next(c for c in rj.build_record("x", ev, n_turns=5, answers="final")["criteria"]
                if c["criterion_id"] == "discovery")["value"] is None
    assert rj.model_block(["fake:fake-judge"], ["fake"]) == {"mode": "local", "name": "fake-judge"}
    assert rj.model_block(["anthropic:claude-opus-5-5,fake:fake-judge"]) == {"mode": "hybrid", "name": "claude-opus-5-5, fake-judge"}


# ----------------------------------------------------------------------------- audit: privacy and route map


def test_audit_never_stores_dynamics_documentbody_or_participants():
    out = audit.compact({"annotation": {"documentbody": "W3siaWQiOiAiMTc5Mzk1NTYwMDAwMCJ9XQ==", "objecttypecode": "msdyn_transcript"},
                         "participants": {"Иван Петров": "customer"}, "conversation_id": "c-1"})
    assert out["annotation"]["documentbody"] == {"_omitted": "text", "chars": 36, "lines": 1}
    assert out["annotation"]["objecttypecode"] == "msdyn_transcript"
    assert out["participants"] == {"_omitted": "object", "keys": 1} and out["conversation_id"] == "c-1"
    assert audit.COLUMN_LIMITS["request_id"] == 64


def _event(method: str, path: str, body: dict | list, response: dict, *, query: bytes = b"", status: int = 200) -> dict:
    rule, params = roles.match_route(method, path)
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    scope = {"type": "http", "method": method, "path": path, "headers": [(b"content-type", b"application/json")],
             "query_string": query}
    return security_mw.build_event(scope, roles.make_actor("analyst", "Иван", "rid-1"), rule, params, method, body=raw,
                                   body_total=len(raw), body_truncated=False, status=status, response_json=response,
                                   started=time.perf_counter(), denied=False)


def test_dynamics_push_is_audited_per_conversation_with_evaluate_refinement():
    resp = {"conversation_id": "d-1", "dataset_id": 1, "turns": 7, "language": "RU", "channel": "agent chat", "job_id": "j_1",
            "created": True, "evaluation": {"status": "started"}}
    ev = _event("POST", "/api/integrations/dynamics/transcripts",
                {"conversation_id": "d-1", "evaluate": True, "annotation": {"documentbody": "QUJD"}}, resp)
    assert ev["action"] == "integration.dynamics.ingest_evaluate"
    assert (ev["entity_type"], ev["entity_id"]) == ("conversation", "d-1")
    assert ev["details"]["response"]["evaluation.status"] == "started" and ev["details"]["response"]["turns"] == 7
    assert ev["details"]["body"]["annotation"]["documentbody"]["_omitted"] == "text"
    assert ev["summary"] == "Транскрипт Dynamics 365: диалог d-1, реплик 7, оценка started"
    plain = _event("POST", "/api/integrations/dynamics/transcripts", {"messages": [{"content": "x"}]}, resp | {"job_id": None})
    assert plain["action"] == "integration.dynamics.ingest" and plain["details"]["body"]["messages"]["_omitted"] == "transcript"


def test_calibration_dry_runs_are_recorded_as_previews():
    counts = {"created": 2, "updated": 1, "unchanged": 0, "removed": 0, "split_propagated": 0, "labelers": ["team"], "dry_run": False}
    saved = _event("PUT", "/api/calibration/labels", {"labeler": "team", "items": []}, counts)
    assert saved["action"] == "calibration.labels.upsert" and saved["summary"] == "Эталонные метки: создано 2, обновлено 1, удалено 0"
    assert saved["details"]["response"]["labelers"] == ["team"]
    preview = _event("PUT", "/api/calibration/labels", {"labeler": "team", "items": [], "dry_run": True}, counts | {"dry_run": True})
    assert preview["action"] == "calibration.labels.preview"
    imported = _event("POST", "/api/calibration/import", {"labels": {}}, {"labeler": "team", "dialogs": 3, "created": 5, "updated": 0},
                      query=b"labeler=team&dry_run=true")
    assert imported["action"] == "calibration.labels.preview"
    real = _event("POST", "/api/calibration/import", {"labels": {}}, {"labeler": "team", "dialogs": 3, "created": 5, "updated": 0},
                  query=b"labeler=team")
    assert real["action"] == "calibration.labels.import" and real["summary"] == "Импорт эталона «team»: диалогов 3, создано 5, обновлено 0"


def test_refine_hooks_with_one_parameter_still_work():
    assert security_mw.call_refine(roles._dry_run, {"dry_run": True}, b"") == "evaluation.estimate"
    assert security_mw.call_refine(roles._dry_run, None, b"dry_run=true") is None
    assert security_mw.call_refine(roles._calibration_dry_run, None, b"dry_run=1") == "calibration.labels.preview"


def test_snapshot_restore_capture_includes_verification():
    rule, _ = roles.match_route("POST", "/api/admin/snapshots/baseline/restore")
    assert {"safety_snapshot", "verified", "counts"} <= set(rule.capture)


# ----------------------------------------------------------------------------- appeals: D365 agent identity


def test_operator_may_appeal_a_dialog_by_its_source_system_identity(Sessions):
    with Sessions() as db:
        v = _seed_version(db)
        _chat(db, "d365-1", operator_name=None, agent_name="Алина Сапарова")
        ev = Evaluation(conversation_id="d365-1", checklist_version_id=v.id, status="completed", total_score=90.0, final_total_score=90.0)
        db.add(ev)
        db.flush()
        crit = next(c for c in v.criteria if c.code == "C1") if v.criteria else db.query(Criterion).filter_by(code="C1").one()
        row = EvaluationCriterion(evaluation_id=ev.id, criterion_id=crit.id, code="C1", applicable=True, answer="no", score=0.0,
                                  max_points=1, weight=10)
        db.add(row)
        db.commit()
        row_id = row.id
    app = FastAPI()
    app.include_router(appeals_router.router)
    app.add_exception_handler(StarletteHTTPException, cqa_main.http_exc)
    app.dependency_overrides[get_db] = _override(Sessions)
    client = TestClient(app)

    def appeal(name: str):
        return client.post("/api/appeals", json={"evaluation_criterion_id": row_id, "comment": "Приветствие было"},
                           headers={"X-CQA-Role": "operator", "X-CQA-User": quote(name)})

    other = appeal("Иван")
    assert other.status_code == 403 and other.json()["error"]["code"] == "not_your_dialog"
    own = appeal("алина сапарова")  # same person: case-insensitive
    assert own.status_code == 200 and own.json()["author_name"] == "алина сапарова"
    again = appeal("Алина Сапарова")
    assert again.status_code == 409 and again.json()["error"]["message"] == "По этому критерию уже есть открытая апелляция"


# ----------------------------------------------------------------------------- ingest: ISO timestamps in chats


def test_chat_with_iso_timestamps_gets_offsets_and_first_response():
    rec = {"channel": "agent chat", "transcript": [
        {"timestamp": "2026-11-07T10:00:00Z", "speaker": "client", "text": "Здравствуйте, вопрос по счёту"},
        {"timestamp": "2026-11-07T10:00:30Z", "speaker": "system", "text": "Агент присоединился к беседе"},
        {"timestamp": "2026-11-07T10:01:00.1234567Z", "speaker": "operator", "text": "Здравствуйте! Меня зовут Алина."},
        {"timestamp": "2026-11-07T10:01:01Z", "speaker": "operator", "text": "Здравствуйте! Меня зовут Алина."}]}
    n = normalize(rec)
    assert [t.offset_seconds for t in n.turns] == [0.0, 30.0, pytest.approx(60.123456), 61.0]
    assert n.first_response_seconds == pytest.approx(30.123456) and n.operator_name == "Алина"
    assert n.turns[3].is_duplicate is True  # same speaker and text within 2 s
    clocks = normalize({"channel": "agent chat", "transcript": [{"timestamp": "00:05", "speaker": "client", "text": "a"},
                                                                {"timestamp": "01:10", "speaker": "operator", "text": "b"}]})
    assert [t.offset_seconds for t in clocks.turns] == [5.0, 70.0]  # organizer MM:SS chats are unchanged


# ----------------------------------------------------------------------------- admin settings


def test_admin_token_and_pre_restore_keep_come_from_settings_too(monkeypatch):
    monkeypatch.delenv(admin_router.ADMIN_TOKEN_ENV, raising=False)
    monkeypatch.setattr(settings, "cqa_admin_token", None)
    assert admin_router._expected_token() == ""
    monkeypatch.setattr(settings, "cqa_admin_token", " from-dotenv ")
    assert admin_router._expected_token() == "from-dotenv"
    monkeypatch.setenv(admin_router.ADMIN_TOKEN_ENV, "from-env")
    assert admin_router._expected_token() == "from-env"
    monkeypatch.setattr(settings, "snapshot_keep_pre_restore", 2)
    assert admin_router._pre_restore_keep() == 2
    monkeypatch.setattr(settings, "snapshot_keep_pre_restore", 0)
    assert admin_router._pre_restore_keep() == admin_router.PRE_RESTORE_KEEP


# ----------------------------------------------------------------------------- scripts/control_run.py


@pytest.fixture(scope="module")
def control_run():
    spec = importlib.util.spec_from_file_location("control_run_under_test", ROOT / "scripts" / "control_run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _api(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://api.test")


def test_control_run_resumes_an_interrupted_job(control_run):
    polls = {"j_2": 0}
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        path = request.url.path
        if path == "/api/evaluations/run":
            return httpx.Response(200, json={"job_id": "j_1", "queued": 2, "estimated_cost_usd": 0.36})
        if path == "/api/jobs/j_1":
            return httpx.Response(200, json={"id": "j_1", "status": "interrupted", "done": 1, "failed": 0, "total": 2, "cost_usd": 0.18})
        if path == "/api/jobs/j_1/resume":
            return httpx.Response(200, json={"job_id": "j_2", "queued": 1, "resumed_from": "j_1"})
        if path == "/api/jobs/j_2":
            polls["j_2"] += 1
            done = polls["j_2"] >= 2
            return httpx.Response(200, json={"id": "j_2", "status": "completed" if done else "running", "done": int(done), "failed": 0,
                                             "total": 1, "cost_usd": 0.18})
        return httpx.Response(404, json={"error": {"code": "not_found", "message": path}})

    job = control_run.run_job(_api(handler), "http://api.test", ["a", "b"], 1, "anthropic", poll_s=0)
    assert (job["id"], job["status"]) == ("j_2", "completed")
    assert seen[:3] == ["POST /api/evaluations/run", "GET /api/jobs/j_1", "POST /api/jobs/j_1/resume"]


def test_control_run_stops_resuming_when_nothing_is_left(control_run):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/evaluations/run":
            return httpx.Response(200, json={"job_id": "j_1", "queued": 1})
        if request.url.path == "/api/jobs/j_1":
            return httpx.Response(200, json={"id": "j_1", "status": "interrupted", "done": 1, "failed": 0, "total": 1, "cost_usd": 0})
        return httpx.Response(409, json={"error": {"code": "nothing_to_resume", "message": "Возобновлять нечего"}})

    assert control_run.run_job(_api(handler), "http://api.test", ["a"], 1, "anthropic", poll_s=0)["status"] == "interrupted"


def test_control_run_waits_while_the_api_is_busy_and_reports_guards(control_run, monkeypatch):
    monkeypatch.setattr(control_run, "BUSY_RETRY_S", 0)
    calls = {"run": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        if request.url.path == "/api/evaluations/run" and body.get("dry_run"):
            return httpx.Response(200, json={"queued": 2, "estimated_cost_usd": 0.36, "budget": {"remaining": 0.1, "unlimited": False},
                                             "guard": {"code": "budget_exceeded", "message": "Превышен суточный бюджет"}})
        if request.url.path == "/api/evaluations/run":
            calls["run"] += 1
            if calls["run"] == 1:
                return httpx.Response(429, json={"error": {"code": "too_many_jobs", "message": "активно 2 из 2"}})
            return httpx.Response(200, json={"job_id": "j_9", "queued": 2})
        return httpx.Response(200, json={"id": "j_9", "status": "completed", "done": 2, "failed": 0, "total": 2, "cost_usd": 0.3})

    http = _api(handler)
    with pytest.raises(SystemExit) as stop:
        control_run.preflight(http, "http://api.test", ["a", "b"], 1, "anthropic")
    assert "Превышен суточный бюджет" in str(stop.value) and "--skip-import --ids a,b" in str(stop.value)
    assert control_run.run_job(http, "http://api.test", ["a", "b"], 1, "anthropic", poll_s=0)["id"] == "j_9"
    assert calls["run"] == 2


def test_control_run_keeps_polling_while_the_api_restarts(control_run):
    polls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/evaluations/run":
            return httpx.Response(200, json={"job_id": "j_1", "queued": 1})
        polls["n"] += 1
        if polls["n"] == 1:
            raise httpx.ConnectError("connection refused")  # redeploy in progress
        if polls["n"] == 2:
            return httpx.Response(502, text="Bad Gateway")
        return httpx.Response(200, json={"id": "j_1", "status": "completed", "done": 1, "failed": 0, "total": 1, "cost_usd": 0.2})

    assert control_run.run_job(_api(handler), "http://api.test", ["a"], 1, "anthropic", poll_s=0)["status"] == "completed"
    assert polls["n"] == 3


def test_sse_sends_exactly_one_job_done(Sessions, monkeypatch):
    from cqa.jobs import runner as jobs_runner
    from cqa.models import Job

    with Sessions() as db:
        db.add(Job(id="j_one", status="completed", total=1, done=1, params={}))
        db.commit()
    monkeypatch.setattr(evaluations_router, "SessionLocal", Sessions)  # the SSE loop opens its own sessions
    st = jobs_runner.JobState("j_one")
    st.emit("evaluation.done", {"conversation_id": "a", "evaluation_id": 1})
    st.emit("job.done", {"status": "completed", "done": 1, "failed": 0, "cost_usd": 0.0})  # what the worker emits
    st.finished.set()
    monkeypatch.setitem(jobs_runner._registry, "j_one", st)
    app = FastAPI()
    app.include_router(evaluations_router.router)
    with TestClient(app).stream("GET", "/api/jobs/j_one/events") as r:
        kinds = [line[len("event: "):] for line in r.iter_lines() if line.startswith("event: ")]
    assert kinds.count("job.done") == 1 and kinds[:2] == ["evaluation.done", "job.done"]
