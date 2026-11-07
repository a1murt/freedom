"""Roles, permissions and the audit trail (docs/ARCH_CONTRACTS.md §1–§2, docs/arch/roles.md).

The app under test mounts the real routers behind the security middleware, on an in-memory SQLite database
(dependency override of get_db; the middleware's audit session uses the same database through
app.state.audit_session_factory). Nothing reaches the network or api/data/cqa.db: the Claude API key is removed
(judge specs use the deterministic fallback compiler), audio resolution is stubbed, and the middleware's fallback to the
real SessionLocal is replaced by a function that fails the test.
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from cqa.audit import service as audit
from cqa.config import settings
from cqa.db import Base, get_db
from cqa.jobs import runner as jobs
from cqa.models import (
    Appeal,
    AuditEvent,
    Checklist,
    ChecklistVersion,
    Conversation,
    Criterion,
    Decision,
    Evaluation,
    EvaluationCriterion,
    Job,
    Turn,
)
from cqa.routers import appeals as appeals_router
from cqa.routers import audio as audio_router
from cqa.routers import audit as audit_router
from cqa.routers import checklists as checklists_router
from cqa.routers import conversations as conversations_router
from cqa.routers import evaluations as evaluations_router
from cqa.routers import roles as roles_router
from cqa.routers import settings_router
from cqa.scoring.engine import CriterionInput, answer_points, compute_score
from cqa.security import middleware as security_mw
from cqa.security import roles

SEED = json.loads((Path(__file__).resolve().parents[1] / "seeds" / "checklist_freedom_v1.json").read_text(encoding="utf-8"))
PERFECT = {"C1": "yes", "C2": "yes", "C3": "yes", "C4": 20, "C5": "yes", "C6": 10, "C7": "yes", "C8": "no"}
ALINA, ALEXEY, IVAN = "Алина", "Алексей", "Иван"
ALINA_ENCODED = "%D0%90%D0%BB%D0%B8%D0%BD%D0%B0"
FAKE_MP3 = b"ID3" + bytes(13) + b"\xff\xfb" + bytes(range(256)) * 4

# docs/ARCH_CONTRACTS.md §1, the permission table, verbatim
CONTRACT_MATRIX = {
    "read": {"demo", "visitor", "operator", "analyst", "reviewer", "lead", "director", "methodologist", "admin"},
    "conversations.import": {"demo", "analyst", "lead", "methodologist", "admin"},
    "evaluations.run": {"demo", "analyst", "reviewer", "lead", "methodologist", "admin"},
    "appeals.create": {"demo", "operator", "analyst", "reviewer", "lead", "admin"},
    "appeals.decide": {"demo", "reviewer", "lead", "admin"},
    "checklists.write": {"demo", "lead", "methodologist", "admin"},
    "settings.write": {"demo", "admin"},
    "calibration.write": {"demo", "reviewer", "lead", "methodologist", "admin"},
    "admin": {"demo", "admin"},
    "integrations.ingest": {"demo", "analyst", "lead", "admin"},
}
ROLE_CODES = ["demo", "visitor", "operator", "analyst", "reviewer", "lead", "director", "methodologist", "admin"]


def hdr(role: str | None = None, user: str | None = None, **extra: str) -> dict[str, str]:
    """Request headers as the web app sends them (the name URL-encoded with encodeURIComponent)."""
    h: dict[str, str] = {}
    if role is not None:
        h["X-CQA-Role"] = role
    if user is not None:
        h["X-CQA-User"] = quote(user, safe="")
    h.update(extra)
    return h


# ----------------------------------------------------------------------------- fixture data


def _seed_checklist(db: Session) -> tuple[ChecklistVersion, list[Criterion]]:
    checklist = Checklist(name=SEED["name"], description=SEED["description"])
    db.add(checklist)
    db.flush()
    version = ChecklistVersion(checklist_id=checklist.id, version_no=1, is_active=True, change_note="v1",
                               scoring_rules=SEED["scoring_rules"], sections=SEED["sections"])
    db.add(version)
    db.flush()
    crits = []
    for i, c in enumerate(SEED["criteria"], start=1):
        crit = Criterion(checklist_version_id=version.id, code=c["code"], order=i, section=c["section"], title=c["title"],
                         instruction=c["instruction"], answer_type=c["answer_type"], options=c["options"], max_points=c["max_points"],
                         weight=c["weight"], na_allowed=c["na_allowed"], na_condition=c["na_condition"], is_critical=c["is_critical"],
                         critical_value=c.get("critical_value"), channel_overrides=c["channel_overrides"])
        db.add(crit)
        crits.append(crit)
    db.flush()
    return version, crits


def _conversation(db: Session, cid: str, operator: str | None, channel: str = "agent chat", audio_url: str | None = None) -> None:
    turns = [("client", "00:00", "Здравствуйте, не могу войти в приложение"),
             ("operator", "00:40", f"Здравствуйте! Меня зовут {operator or 'оператор'}. Какую ошибку видите?"),
             ("client", "01:10", "Пишет «неверный пароль»"),
             ("operator", "01:40", "Нажмите «Забыли пароль», код придёт по SMS. Хорошего дня!")]
    db.add(Conversation(id=cid, channel=channel, language="RU", operator_name=operator, turn_count=len(turns), audio_url=audio_url))
    db.flush()
    for idx, (speaker, ts, text) in enumerate(turns):
        db.add(Turn(conversation_id=cid, idx=idx, timestamp_raw=ts, offset_seconds=float(idx * 20), speaker=speaker, text=text,
                    segment="operator" if idx else "pre"))
    db.flush()


def _evaluate(db: Session, cid: str, version: ChecklistVersion, crits: list[Criterion]) -> dict[str, int]:
    items = [CriterionInput(c.code, c.weight, c.max_points, answer_points(c.options, PERFECT[c.code]), True, c.section, c.is_critical,
                            bool(c.is_critical and PERFECT[c.code] == c.critical_value)) for c in crits]
    res = compute_score(version.scoring_rules, items, version.sections)
    ev = Evaluation(conversation_id=cid, checklist_version_id=version.id, status="completed", provider="anthropic",
                    model="claude-opus-5-5", total_score=res.total, final_total_score=res.total, critical_error=res.critical,
                    cost_usd=0.1, completed_at=datetime.now(UTC))
    db.add(ev)
    db.flush()
    rows = {}
    for c, item in zip(crits, items):
        cr = res.criteria[c.code]
        row = EvaluationCriterion(evaluation_id=ev.id, criterion_id=c.id, code=c.code, applicable=True, answer=PERFECT[c.code],
                                  score=item.points, max_points=c.max_points, weight=c.weight, weight_applied=cr.weight_applied,
                                  earned=cr.earned, confidence=0.9, explanation=f"{c.title}: обоснование",
                                  evidence=[{"turn_idx": 1, "speaker": "operator", "quote": "Здравствуйте!"}])
        db.add(row)
        db.flush()
        rows[c.code] = row.id
    return rows


def _seed(Session: sessionmaker) -> dict:
    with Session() as db:
        version, crits = _seed_checklist(db)
        _conversation(db, "own-001", ALINA)
        _conversation(db, "other-002", ALEXEY)
        _conversation(db, "anon-003", None)
        _conversation(db, "call-1", "Айгерим", channel="voice call", audio_url="https://flow.example/player")
        ids = {"version_id": version.id, "checklist_id": version.checklist_id,
               "own": _evaluate(db, "own-001", version, crits), "other": _evaluate(db, "other-002", version, crits),
               "anon": _evaluate(db, "anon-003", version, crits)}
        db.add(Job(id="j_done", type="evaluate", status="completed", total=2, done=2))
        db.commit()
    return ids


def _no_real_database():
    raise AssertionError("the audit trail must not fall back to cqa.db.SessionLocal (api/data/cqa.db) in tests")


def build_app(Session: sessionmaker, *, audit_factory: bool = True, install: bool = True) -> FastAPI:
    app = FastAPI()

    @app.exception_handler(HTTPException)  # same envelope as cqa.main
    async def http_exc(request, exc: HTTPException):
        detail = exc.detail if isinstance(exc.detail, dict) else {"code": "error", "message": str(exc.detail)}
        return JSONResponse(status_code=exc.status_code, content={"error": detail})

    for module in (conversations_router, checklists_router, evaluations_router, appeals_router, settings_router, audio_router,
                   audit_router, roles_router):
        app.include_router(module.router)

    def override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    if install:
        security_mw.install(app, session_factory=Session if audit_factory else None)
    return app


@pytest.fixture
def env(monkeypatch, tmp_path):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    ids = _seed(Session)
    monkeypatch.setattr(settings, "anthropic_api_key", None)  # never the Claude API
    monkeypatch.setattr(security_mw, "_default_session_factory", _no_real_database)
    monkeypatch.setattr(evaluations_router, "SessionLocal", Session)  # the SSE endpoint opens its own sessions
    mp3 = tmp_path / "call.mp3"
    mp3.write_bytes(FAKE_MP3)
    monkeypatch.setattr(audio_router, "resolve_and_cache", lambda conversation_id, audio_url, force=False: mp3)
    app = build_app(Session)
    yield SimpleNamespace(app=app, Session=Session, ids=ids, mp3=mp3, engine=engine)
    engine.dispose()


@pytest.fixture
def client(env) -> TestClient:
    with TestClient(env.app) as c:
        yield c


def events(env, **where) -> list[AuditEvent]:
    with env.Session() as db:
        stmt = select(AuditEvent).order_by(AuditEvent.id)
        for k, v in where.items():
            stmt = stmt.where(getattr(AuditEvent, k) == v)
        return list(db.execute(stmt).scalars().all())


def count(env, model) -> int:
    with env.Session() as db:
        return db.execute(select(func.count()).select_from(model)).scalar_one()


def appeal_body(env, dialog: str = "own", code: str = "C3", **extra) -> dict:
    return {"evaluation_criterion_id": env.ids[dialog][code], "comment": "Идентификация была, прошу перепроверить", **extra}


def paste_body(cid: str = "manual-roles-1") -> dict:
    return {"conversation_id": cid, "channel": "agent chat", "language": "RU",
            "text": "client: Здравствуйте, как пополнить счёт?\noperator: Добрый день! Меня зовут Алина. Через приложение."}


# ----------------------------------------------------------------------------- actor resolution


def test_no_headers_is_demo_with_every_permission(client, env):
    me = client.get("/api/me").json()
    assert me["role"] == "demo" and me["name"] is None and me["is_default"] is True and me["enforced"] is True
    assert me["permissions"] == list(roles.PERMISSIONS)
    assert me["home"] == "#/conversations" and me["request_id"]

    r = client.post("/api/conversations/paste", json=paste_body())
    assert r.status_code == 200, r.text
    [ev] = events(env)
    assert (ev.actor_role, ev.actor_name, ev.action, ev.status_code) == ("demo", None, "conversation.paste", 200)
    assert (ev.entity_type, ev.entity_id) == ("conversation", "manual-roles-1")
    assert ev.summary == "Добавлен диалог manual-roles-1"
    assert ev.details["body"]["text"] == {"_omitted": "text", "chars": len(paste_body()["text"]), "lines": 2}
    assert "Здравствуйте" not in json.dumps(ev.details, ensure_ascii=False)  # never the transcript itself
    assert ev.details["response"]["id"] == "manual-roles-1" and ev.details["method"] == "POST"
    assert ev.details["path"] == "/api/conversations/paste" and ev.details["duration_ms"] >= 0


def test_unknown_role_is_400_and_role_header_is_case_insensitive(client, env):
    for method in ("get", "post"):
        r = getattr(client, method)("/api/conversations", headers=hdr("hacker"))
        assert r.status_code == 400
        err = r.json()["error"]
        assert err["code"] == "unknown_role" and err["role"] == "hacker" and err["roles"] == ROLE_CODES
        assert "X-CQA-Role" in err["message"] and "operator" in err["message"]
    assert events(env) == []
    assert client.get("/api/me", headers=hdr(" Reviewer ")).json()["role"] == "reviewer"
    assert client.get("/api/me", headers=hdr("")).json()["role"] == "demo"  # empty header = no role chosen
    assert client.options("/api/checklists", headers=hdr("hacker")).status_code != 400  # OPTIONS is never refused
    r = client.get("/api/me", headers={"X-CQA-Role": "оператор".encode()})
    assert r.status_code == 400 and r.json()["error"]["role"] == "оператор"  # echoed readably


def test_user_header_decoding():
    assert roles.decode_user(ALINA_ENCODED) == ALINA
    assert roles.decode_user(ALINA.encode()) == ALINA  # raw UTF-8 bytes
    assert roles.decode_user(ALINA.encode().decode("latin-1")) == ALINA  # UTF-8 read as latin-1 (Starlette Headers)
    assert roles.decode_user(ALINA) == ALINA
    assert roles.decode_user("%D0%90%D0%BB%D0%B8%D0%BD%D0%B0%20%D0%9A.") == "Алина К."
    assert roles.decode_user("  John \t Smith\n") == "John Smith"
    assert roles.decode_user("100% sure") == "100% sure"  # not percent-encoding: kept as is
    assert roles.decode_user("Jos%E9") == "José"  # latin-1 percent-encoding fallback
    assert roles.decode_user("") is None and roles.decode_user(None) is None and roles.decode_user("%20") is None
    assert len(roles.decode_user("я" * 500)) == roles.NAME_MAX


def test_raw_utf8_user_header_and_request_id(client, env):
    me = client.get("/api/me", headers={"X-CQA-Role": "operator", "X-CQA-User": ALINA.encode(), "X-Request-ID": "req-42"}).json()
    assert (me["role"], me["name"], me["request_id"], me["display"]) == ("operator", ALINA, "req-42", "Алина (Оператор)")
    assert me["home"] == "#/my" and [n["key"] for n in me["nav"]] == ["my", "checklists"]
    assert me["constraints"] == {"appeals.create": "own_dialogs"}

    uuid = "123e4567-e89b-12d3-a456-426614174000"
    r = client.post("/api/conversations/paste", json=paste_body("manual-rid"), headers=hdr("analyst", IVAN, **{"X-Request-ID": uuid}))
    assert r.status_code == 200
    [ev] = events(env)
    assert ev.request_id == uuid  # audit_events.request_id is VARCHAR(64): the id equals the X-Request-ID header
    assert ev.actor_name == IVAN


def test_current_actor_without_the_middleware(env):
    app = build_app(env.Session, install=False)
    with TestClient(app) as c:
        me = c.get("/api/me", headers=hdr("lead", ALINA)).json()
        assert (me["role"], me["name"], me["enforced"]) == ("lead", ALINA, False)
        r = c.get("/api/me", headers=hdr("nobody"))
        assert r.status_code == 400 and r.json()["error"]["code"] == "unknown_role"


# ----------------------------------------------------------------------------- permissions on writes


def test_visitor_reads_but_cannot_write(client, env, monkeypatch):
    def must_not_run(*a, **k):
        raise AssertionError("the import handler must not run for a refused request")

    monkeypatch.setattr(conversations_router, "fetch_organizer", must_not_run)
    for url in ("/api/conversations", "/api/checklists", "/api/appeals", "/api/settings", "/api/roles", "/api/audit"):
        assert client.get(url, headers=hdr("visitor")).status_code == 200, url

    r = client.post("/api/datasets/import", json={"group_id": "DEV"}, headers=hdr("visitor", "Гость Иванов"))
    assert r.status_code == 403
    err = r.json()["error"]
    assert (err["code"], err["role"], err["permission"], err["action"]) == ("forbidden", "visitor", "conversations.import", "dataset.import")
    assert err["message"] == ("Недостаточно прав: роли «Гость» недоступно действие «Импорт выборки» "
                              "(нужно право «Импорт диалогов»).")
    assert client.put("/api/settings", json={"team_name": "x"}, headers=hdr("visitor")).status_code == 403

    first = events(env)[0]
    assert (first.actor_role, first.actor_name, first.action, first.status_code) == ("visitor", "Гость Иванов", "dataset.import", 403)
    assert first.details["permission"] == "conversations.import" and first.details["body"] == {"group_id": "DEV"}
    assert first.details["error"]["code"] == "forbidden"
    assert first.summary == "Отказано: Импорт выборки — роль «Гость» без права «Импорт диалогов»"
    assert audit.outcome(first.status_code) == "denied"


def test_operator_cannot_create_a_checklist(client, env):
    before = count(env, Checklist)
    r = client.post("/api/checklists", json={**SEED, "compile_specs": False}, headers=hdr("operator", ALINA))
    assert r.status_code == 403
    assert r.json()["error"] | {"message": None} == {"code": "forbidden", "message": None, "role": "operator",
                                                     "permission": "checklists.write", "action": "checklist.create"}
    assert count(env, Checklist) == before
    [ev] = events(env)
    assert (ev.action, ev.status_code, ev.actor_role, ev.actor_name) == ("checklist.create", 403, "operator", ALINA)
    assert ev.details["body"]["criteria"][0] == {"code": "C1", "title": SEED["criteria"][0]["title"][:60], "weight": 10,
                                                 "answer_type": "binary", "is_critical": False, "na_allowed": False}
    assert audit.json_size(ev.details["body"]) <= 2048

    # a trailing slash is the same route (not the admin-only default rule)
    r = client.post("/api/checklists/", json={**SEED, "compile_specs": False}, headers=hdr("operator", ALINA))
    assert r.status_code == 403 and r.json()["error"]["permission"] == "checklists.write"


def test_methodologist_edits_checklists_and_the_trail_names_the_entities(client, env):
    r = client.post("/api/checklists", json={**SEED, "name": "Чек-лист QA 2", "compile_specs": False},
                    headers=hdr("methodologist", "Мария"))
    assert r.status_code == 200, r.text
    ver = r.json()
    r = client.post(f"/api/checklists/{env.ids['checklist_id']}/versions/{env.ids['version_id']}/activate", headers=hdr("lead"))
    assert r.status_code == 200, r.text
    created, activated = events(env)
    assert (created.action, created.entity_type, created.entity_id) == ("checklist.create", "checklist", str(ver["checklist_id"]))
    assert created.summary == f"Создан чек-лист «Чек-лист QA 2» (#{ver['checklist_id']}, версия 1)"
    assert created.details["response"]["id"] == ver["id"] and "instruction" not in json.dumps(created.details)
    assert (activated.action, activated.entity_type, activated.entity_id) == ("checklist.version.activate", "checklist_version",
                                                                             str(env.ids["version_id"]))
    assert activated.details["path_params"] == {"checklist_id": str(env.ids["checklist_id"]), "version_id": str(env.ids["version_id"])}
    assert activated.summary == f"Активирована версия #{env.ids['version_id']} чек-листа #{env.ids['checklist_id']}"
    assert client.post("/api/checklists", json={**SEED, "compile_specs": False}, headers=hdr("director")).status_code == 403


def test_operator_appeals_own_dialog_only(client, env):
    r = client.post("/api/appeals", json=appeal_body(env), headers={**hdr("operator"), "X-CQA-User": ALINA_ENCODED, "X-Request-ID": "op-1"})
    assert r.status_code == 200, r.text
    ap = r.json()
    assert (ap["author_role"], ap["author_name"], ap["author_label"]) == ("operator", ALINA, "Алина (Оператор)")
    assert ap["conversation_id"] == "own-001" and ap["status"] == "open"

    [ev] = events(env)
    assert (ev.action, ev.actor_role, ev.actor_name, ev.status_code, ev.request_id) == ("appeal.create", "operator", ALINA, 200, "op-1")
    assert (ev.entity_type, ev.entity_id) == ("appeal", str(ap["id"]))
    assert ev.summary == f"Апелляция #{ap['id']}: C3, диалог own-001"
    assert ev.details["response"]["conversation_id"] == "own-001" and ev.details["response"]["code"] == "C3"
    assert ev.details["body"]["comment"] == "Идентификация была, прошу перепроверить"

    # someone else's dialog → 403 not_your_dialog from the handler, recorded as a refused attempt
    r = client.post("/api/appeals", json=appeal_body(env, "other"), headers={**hdr("operator"), "X-CQA-User": ALINA_ENCODED})
    assert r.status_code == 403
    assert r.json() == {"error": {"code": "not_your_dialog", "message": "Оператор может оспаривать только оценки своих диалогов"}}
    denied = events(env)[-1]
    assert (denied.action, denied.status_code, denied.entity_type, denied.entity_id) == ("appeal.create", 403, None, None)
    assert denied.details["body"]["evaluation_criterion_id"] == env.ids["other"]["C3"]  # the target is still on record
    assert denied.details["error"]["code"] == "not_your_dialog"
    assert denied.summary == "Отказано: Подача апелляции — Оператор может оспаривать только оценки своих диалогов"

    # a dialog without a detected operator is nobody's own dialog
    r = client.post("/api/appeals", json=appeal_body(env, "anon"), headers=hdr("operator", ALINA))
    assert r.status_code == 403 and r.json()["error"]["code"] == "not_your_dialog"
    # the body cannot lift the check: the header identity wins over author_role / author_name
    r = client.post("/api/appeals", json=appeal_body(env, "other", author_role="analyst", author_name=ALEXEY),
                    headers=hdr("operator", ALINA))
    assert r.status_code == 403 and r.json()["error"]["code"] == "not_your_dialog"
    # an operator must say who they are
    r = client.post("/api/appeals", json=appeal_body(env, "other", "C1"), headers=hdr("operator"))
    assert r.status_code == 403 and r.json()["error"]["code"] == "operator_name_required"
    # names compare case-insensitively (ё = е)
    r = client.post("/api/appeals", json=appeal_body(env, "own", "C1"), headers=hdr("operator", "  алина "))
    assert r.status_code == 200 and r.json()["author_name"] == "алина"

    with env.Session() as db:
        assert db.execute(select(func.count()).select_from(Appeal)).scalar_one() == 2
    assert [e.status_code for e in events(env)] == [200, 403, 403, 403, 403, 200]


def test_analyst_appeal_defaults_from_headers(client, env):
    r = client.post("/api/appeals", json=appeal_body(env, "other", "C5"), headers=hdr("analyst", "Дана"))
    assert r.status_code == 200
    ap = r.json()
    assert (ap["author_role"], ap["author_name"], ap["author_label"]) == ("analyst", "Дана", "Дана (QA-аналитик)")
    r = client.post("/api/appeals", json=appeal_body(env, "other", "C6", author_label="QA-аналитик"), headers=hdr("analyst"))
    assert r.json()["author_label"] == "QA-аналитик" and r.json()["author_role"] == "analyst"  # an explicit label is kept
    assert client.post("/api/appeals", json=appeal_body(env, "other", "C7"), headers=hdr("methodologist")).status_code == 403


def test_reviewer_decides_and_analyst_cannot(client, env):
    appeal_id = client.post("/api/appeals", json=appeal_body(env)).json()["id"]
    decision = {"action": "change", "new_answer": "no", "reason": "Идентификация не проведена", "add_to_fewshot": False}

    r = client.post(f"/api/appeals/{appeal_id}/decision", json=decision, headers=hdr("analyst", "Дана"))
    assert r.status_code == 403 and r.json()["error"]["permission"] == "appeals.decide"
    assert count(env, Decision) == 0

    r = client.post(f"/api/appeals/{appeal_id}/decision", json=decision, headers=hdr("reviewer", IVAN))
    assert r.status_code == 200, r.text
    body = r.json()
    dec = body["appeal"]["decision"]
    assert (dec["reviewer_role"], dec["reviewer_label"], dec["action"], dec["new_answer"]) == ("reviewer", "Иван (Ревьюер)", "change", "no")
    assert body["evaluation"]["final_total_score"] < body["evaluation"]["total_score"]

    created, refused, decided = events(env)
    assert (created.actor_role, refused.status_code, refused.actor_name) == ("demo", 403, "Дана")
    assert (decided.action, decided.entity_type, decided.entity_id, decided.actor_name) == ("appeal.decide", "appeal", str(appeal_id), IVAN)
    assert decided.summary == f"Апелляция #{appeal_id} (C3, диалог own-001): ответ изменён на «no»"
    assert decided.details["response"]["appeal.decision.action"] == "change"
    assert decided.details["response"]["evaluation.final_total_score"] == body["evaluation"]["final_total_score"]

    # an explicit reviewer_label (as the current web app sends) is kept; the role still defaults from the header
    second = client.post("/api/appeals", json=appeal_body(env, "own", "C1")).json()["id"]
    r = client.post(f"/api/appeals/{second}/decision", headers=hdr("lead", "Ержан"),
                    json={"action": "confirm", "reason": "Верно", "reviewer_label": "Ревьюер", "add_to_fewshot": False})
    dec = r.json()["appeal"]["decision"]
    assert (dec["reviewer_label"], dec["reviewer_role"]) == ("Ревьюер", "lead")
    assert events(env)[-1].summary == f"Апелляция #{second} (C1, диалог own-001): оценка подтверждена"


def test_requests_without_headers_keep_the_legacy_behaviour(client, env):
    # body-level author_role/author_name check exactly as before the roles package
    r = client.post("/api/appeals", json=appeal_body(env, author_role="operator", author_name=ALEXEY))
    assert r.status_code == 403 and r.json()["error"]["code"] == "not_your_dialog"
    r = client.post("/api/appeals", json=appeal_body(env, author_role="operator", author_name=ALINA))
    assert r.status_code == 200
    assert (r.json()["author_label"], r.json()["author_role"]) == ("QA analyst", "operator")
    r = client.post(f"/api/appeals/{r.json()['id']}/decision", json={"action": "confirm", "reason": "ok", "add_to_fewshot": False})
    dec = r.json()["appeal"]["decision"]
    assert (dec["reviewer_label"], dec["reviewer_role"]) == ("Reviewer", None)


def test_preview_is_open_to_everyone_and_not_recorded(client, env):
    r = client.post("/api/appeals/preview", json={"evaluation_criterion_id": env.ids["own"]["C3"], "new_answer": "no"},
                    headers=hdr("visitor"))
    assert r.status_code == 200 and "final_total_score" in r.json()
    assert events(env) == []


def test_evaluation_estimate_run_and_job_control(client, env, monkeypatch):
    monkeypatch.setattr(jobs, "start_evaluation_job", lambda ids, version_id, **kw: "j_test123")
    body = {"conversation_ids": ["own-001"], "provider": "anthropic", "dry_run": True}
    r = client.post("/api/evaluations/run", json=body, headers=hdr("analyst", "Дана"))
    assert r.status_code == 200, r.text
    r = client.post("/api/evaluations/run", json={**body, "dry_run": False}, headers=hdr("analyst", "Дана"))
    assert r.status_code == 200, r.text
    assert r.json()["job_id"] == "j_test123"
    assert client.post("/api/evaluations/run", json=body, headers=hdr("operator", ALINA)).status_code == 403

    state = jobs.JobState("j_done")
    monkeypatch.setitem(jobs._registry, "j_done", state)
    assert client.post("/api/jobs/j_done/cancel", headers=hdr("visitor")).status_code == 403
    r = client.post("/api/jobs/j_done/cancel", headers=hdr("reviewer", IVAN))
    assert r.status_code == 200 and state.cancel.is_set()

    estimate, run, refused_run, refused_cancel, cancel = events(env)
    assert (estimate.action, estimate.entity_type, estimate.entity_id) == ("evaluation.estimate", None, None)
    assert estimate.summary.startswith("Расчёт стоимости: 1 диалогов ≈ $")
    assert estimate.details["body"]["dry_run"] is True
    assert (run.action, run.entity_type, run.entity_id) == ("evaluation.run", "job", "j_test123")
    assert run.summary == "Запущена оценка: 1 диалогов, задача j_test123"
    assert (refused_run.action, refused_run.status_code) == ("evaluation.estimate", 403)
    assert (refused_cancel.action, refused_cancel.entity_id, refused_cancel.status_code) == ("job.cancel", "j_done", 403)
    assert (cancel.action, cancel.entity_type, cancel.entity_id, cancel.summary) == ("job.cancel", "job", "j_done", "Остановлена задача j_done")


def test_settings_details_are_compact_and_free_of_secrets(client, env):
    body = {"local_endpoint": "http://llm-user:hunter2@llm.local:8000/v1", "local_api_key": "sk-ant-api03-0123456789abcdefghijkl",
            "team_name": "Я Тебя Могну", "default_model": "claude-opus-5-5", "notes": "длинный текст " * 400,
            "nested": {"Authorization": "Bearer abc.def.ghi.jkl", "url": "https://blob.example/a.mp3?sp=r&sig=SECRETSIG&x=1"}}
    r = client.put("/api/settings", json=body, headers=hdr("admin", "Админ"))
    assert r.status_code == 200 and r.json()["team_name"] == "Я Тебя Могну"
    [ev] = events(env)
    assert (ev.action, ev.entity_type, ev.entity_id) == ("settings.update", "settings", None)
    assert ev.summary == "Изменены настройки: default_model, local_api_key, local_endpoint, nested, notes, team_name"
    dumped = json.dumps(ev.details, ensure_ascii=False)
    for secret in ("hunter2", "llm-user", "0123456789abcdefghijkl", "abc.def.ghi.jkl", "SECRETSIG"):
        assert secret not in dumped, secret
    b = ev.details["body"]
    assert b["local_api_key"] == "***" and b["local_endpoint"] == "http://***@llm.local:8000/v1"
    assert b["nested"]["Authorization"] == "***" and b["nested"]["url"] == "https://blob.example/a.mp3?sp=r&sig=***&x=1"
    assert len(b["notes"]) <= 201 and audit.json_size(b) <= 2048


def test_huge_bodies_are_summarized_within_2kb(client, env):
    criteria = [{**c, "code": f"K{i}", "instruction": "очень длинная инструкция " * 120, "title": c["title"] * 5}
                for i, c in enumerate(SEED["criteria"] * 6)]
    huge = {"criteria": criteria, "matrix": [[f"ячейка {i}-{j}" * 20 for j in range(20)] for i in range(20)],
            "keys": {f"k{i}": "значение " * 50 for i in range(200)}, "team_name": "Я Тебя Могну"}
    raw = json.dumps(huge, ensure_ascii=False).encode()
    assert 300_000 < len(raw) < security_mw.CAPTURE_LIMIT * 0.8  # big, but inside the capture window: parsed and compacted
    r = client.put("/api/settings", content=raw, headers={**hdr("admin"), "content-type": "application/json"})
    assert r.status_code == 200, r.text  # unknown keys are ignored by the endpoint
    [ev] = events(env)
    body = ev.details["body"]
    assert audit.json_size(body) <= 2048 and audit.json_size(ev.details) < 4096
    assert "очень длинная инструкция" not in json.dumps(body, ensure_ascii=False)
    assert body["team_name"] == "Я Тебя Могну"

    # a refused request is summarized from its first 64 KB without the app ever reading it
    r = client.post("/api/checklists", json={**SEED, "criteria": criteria, "compile_specs": False}, headers=hdr("operator", ALINA))
    assert r.status_code == 403
    refused = events(env)[-1]
    assert refused.details["body"]["truncated"] is True and refused.details["body"]["bytes"] >= security_mw.DENIED_READ_LIMIT


def test_large_body_reaches_the_app_intact(client, env, monkeypatch):
    turns = [{"timestamp": f"{i // 60:02d}:{i % 60:02d}", "speaker": "client" if i % 2 else "operator",
              "text": f"Реплика {i}: " + "длинный текст обращения клиента " * 12} for i in range(300)]
    payload = {"conversation_id": "long-1", "channel": "agent chat", "language": "RU", "transcript": turns}
    r = client.post("/api/conversations/paste", json=payload, headers=hdr("analyst"))
    assert r.status_code == 200 and len(r.json()["turns"]) == 300
    ev = events(env)[-1]
    assert ev.entity_id == "long-1" and ev.details["body"]["transcript"] == {"_omitted": "transcript", "items": 300}

    # beyond the capture window the summary keeps only the size, and the app still gets every byte
    monkeypatch.setattr(security_mw, "CAPTURE_LIMIT", 4096)
    r = client.post("/api/conversations/paste", json={**payload, "conversation_id": "long-2"}, headers=hdr("analyst"))
    assert r.status_code == 200 and len(r.json()["turns"]) == 300
    with env.Session() as db:
        assert db.get(Conversation, "long-2").turn_count == 300
    ev = events(env)[-1]
    assert ev.details["body"]["truncated"] is True and ev.details["body"]["bytes"] > 4096
    assert ev.entity_id is None and ev.summary == "Добавление диалога вручную"


def test_multipart_upload_is_recorded_with_its_file_name(client, env):
    files = {"file": ("dialog-77.txt", "client: Здравствуйте\noperator: Добрый день, меня зовут Алина\n".encode(), "text/plain")}
    r = client.post("/api/conversations/upload", files=files, headers=hdr("lead", "Ержан"))
    assert r.status_code == 200, r.text
    assert r.json()["id"] == "dialog-77"
    [ev] = events(env)
    assert (ev.action, ev.entity_type, ev.entity_id) == ("conversation.upload", "conversation", "dialog-77")
    assert ev.summary == "Загружен файл «dialog-77.txt»"
    assert ev.details["body"]["files"] == ["dialog-77.txt"] and ev.details["body"]["fields"] == ["file"]
    assert ev.details["body"]["content_type"] == "multipart/form-data"


def test_audit_session_falls_back_to_the_get_db_override(env):
    app = build_app(env.Session, audit_factory=False)
    with TestClient(app) as c:
        assert c.post("/api/conversations/paste", json=paste_body("via-override"), headers=hdr("analyst")).status_code == 200
    assert [e.entity_id for e in events(env)] == ["via-override"]


def test_a_failing_audit_write_never_breaks_the_request(env, caplog):
    def broken():
        raise RuntimeError("audit database is down")

    env.app.state.audit_session_factory = broken
    with TestClient(env.app) as c:
        r = c.post("/api/conversations/paste", json=paste_body("still-saved"), headers=hdr("analyst"))
    assert r.status_code == 200 and r.json()["id"] == "still-saved"
    assert events(env) == []
    assert "audit event conversation.paste not written" in caplog.text


# ----------------------------------------------------------------------------- streaming stays streaming


def test_sse_stream_is_unaffected(client, env, monkeypatch):
    state = jobs.JobState("j_done")
    state.emit("job.progress", {"done": 1, "total": 2})
    state.emit("evaluation.done", {"conversation_id": "own-001", "total_score": 100.0})
    monkeypatch.setitem(jobs._registry, "j_done", state)
    with client.stream("GET", "/api/jobs/j_done/events", headers=hdr("visitor")) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        lines = [line for line in r.iter_lines() if line.startswith("event:")]
    assert lines == ["event: job.progress", "event: evaluation.done", "event: job.status", "event: job.done"]
    assert events(env) == []


def test_audio_range_and_head_are_unaffected(client, env):
    r = client.get("/api/audio/call-1", headers={**hdr("operator", ALINA), "Range": "bytes=0-99"})
    assert r.status_code == 206 and r.content == FAKE_MP3[:100]
    assert r.headers["content-range"] == f"bytes 0-99/{len(FAKE_MP3)}" and r.headers["accept-ranges"] == "bytes"
    tail = client.get("/api/audio/call-1", headers={**hdr("visitor"), "Range": "bytes=-50"})
    assert tail.status_code == 206 and tail.content == FAKE_MP3[-50:]
    head = client.head("/api/audio/call-1", headers=hdr("director"))
    assert head.status_code == 200 and head.headers["content-length"] == str(len(FAKE_MP3))


def _http_scope(method: str, path: str, headers: list[tuple[bytes, bytes]], app=None) -> dict:
    return {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method, "path": path,
            "raw_path": path.encode(), "query_string": b"", "headers": headers, "app": app}


def test_reads_pass_receive_and_send_through_untouched():
    async def scenario():
        gate = asyncio.Event()
        seen: dict = {}
        sent: list[dict] = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)

        async def app(scope, rcv, snd):
            seen["identity"] = (rcv is receive, snd is send)
            seen["actor"] = scope["state"]["actor"]
            await snd({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/event-stream")]})
            await snd({"type": "http.response.body", "body": b"event: one\n\n", "more_body": True})
            await gate.wait()
            await snd({"type": "http.response.body", "body": b"event: two\n\n", "more_body": False})

        mw = security_mw.SecurityMiddleware(app)
        scope = _http_scope("GET", "/api/jobs/j1/events", [(b"x-cqa-role", b"visitor"), (b"x-cqa-user", ALINA_ENCODED.encode())])
        task = asyncio.create_task(mw(scope, receive, send))
        for _ in range(100):
            await asyncio.sleep(0)
            if len(sent) == 2:
                break
        assert [m.get("body") for m in sent] == [None, b"event: one\n\n"] and not task.done()  # first event already out
        gate.set()
        await task
        assert sent[-1]["body"] == b"event: two\n\n"
        assert seen["identity"] == (True, True)
        assert seen["actor"]["role"] == "visitor" and seen["actor"]["name"] == ALINA

    asyncio.run(scenario())


def test_writes_stream_chunks_and_record_before_the_last_one():
    async def scenario():
        log: list[str] = []
        received: list[bytes] = []
        sent: list[dict] = []
        body_chunks = [b'{"group_id": ', b'"DEV"', b"}"]

        class FakeSession:
            def add(self, ev):
                log.append(f"audit:{ev.action}:{ev.entity_id}:{ev.details['body']}")

            def commit(self):
                log.append("commit")

            def flush(self):
                pass

            def rollback(self):
                pass

            def close(self):
                log.append("close")

        queue = [{"type": "http.request", "body": c, "more_body": i < len(body_chunks) - 1} for i, c in enumerate(body_chunks)]

        async def receive():
            return queue.pop(0) if queue else {"type": "http.disconnect"}

        async def send(message):
            sent.append(message)
            log.append(f"sent:{message.get('body', b'start')!r}")

        async def app(scope, rcv, snd):
            while True:
                m = await rcv()
                received.append(m["body"])
                if not m.get("more_body"):
                    break
            await snd({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"application/json")]})
            await snd({"type": "http.response.body", "body": b'{"dataset_id": 7, ', "more_body": True})
            log.append("app:between")
            await snd({"type": "http.response.body", "body": b'"imported": 65}', "more_body": False})

        fake_app = SimpleNamespace(state=SimpleNamespace(audit_session_factory=FakeSession))
        scope = _http_scope("POST", "/api/datasets/import", [(b"x-cqa-role", b"analyst"), (b"content-type", b"application/json")],
                            app=fake_app)
        await security_mw.SecurityMiddleware(app)(scope, receive, send)
        assert received == body_chunks  # the app reads the body exactly as sent
        assert log == ["sent:b'start'", "sent:b'{\"dataset_id\": 7, '", "app:between",
                       "audit:dataset.import:7:{'group_id': 'DEV'}", "commit", "close", "sent:b'\"imported\": 65}'"]

    asyncio.run(scenario())


# ----------------------------------------------------------------------------- the audit log API


def test_audit_listing_filters_and_pagination(client, env):
    with env.Session() as db:
        base = datetime(2026, 11, 7, 10, 0, tzinfo=UTC)
        for i, (action, role, name, status) in enumerate([
            ("appeal.create", "operator", ALINA, 200), ("appeal.create", "operator", ALINA, 403), ("appeal.decide", "reviewer", IVAN, 200),
            ("checklist.create", "methodologist", "Мария", 200), ("job.interrupted", None, None, None),
        ]):
            ev = audit.record(db, action, entity_type="appeal" if action.startswith("appeal") else None, entity_id=10 + i,
                              summary=f"событие {i} диалог own-001" if i == 2 else f"событие {i}",
                              actor={"role": role, "name": name, "request_id": f"r{i}"} if role else None, status_code=status)
            ev.ts = base + timedelta(hours=i)
        db.commit()

    def get(**params):
        r = client.get("/api/audit", params=params, headers=hdr("visitor"))
        assert r.status_code == 200, r.text
        return r.json()

    page = get()
    assert [e["action"] for e in page["items"]] == ["job.interrupted", "checklist.create", "appeal.decide", "appeal.create", "appeal.create"]
    assert page["next_before_id"] is None
    system = page["items"][0]
    assert (system["actor_role"], system["actor_role_label"], system["outcome"]) == ("system", "Система", "ok")
    assert page["items"][1]["action_label"] == "Создание чек-листа" and page["items"][1]["ts"].endswith("+00:00")

    assert [e["entity_id"] for e in get(action="appeal.create")["items"]] == ["11", "10"]
    assert {e["action"] for e in get(action="appeal.*")["items"]} == {"appeal.create", "appeal.decide"}
    assert {e["action"] for e in get(action="appeal.decide,checklist.create")["items"]} == {"appeal.decide", "checklist.create"}
    assert [e["entity_id"] for e in get(entity_type="appeal", entity_id="12")["items"]] == ["12"]
    assert [e["actor_name"] for e in get(actor_role="reviewer")["items"]] == [IVAN]
    assert len(get(actor_name=ALINA)["items"]) == 2  # Cyrillic, URL-encoded in the query string
    assert [e["outcome"] for e in get(status_code="403")["items"]] == ["denied"]
    assert [e["entity_id"] for e in get(q="own-001")["items"]] == ["12"]
    assert [e["entity_id"] for e in get(since="2026-11-07T12:00:00Z", until="2026-11-07T13:30:00+00:00")["items"]] == ["13", "12"]

    first = get(limit="2")
    assert len(first["items"]) == 2 and first["next_before_id"] == first["items"][-1]["id"]
    second = get(limit="2", before_id=str(first["next_before_id"]))
    third = get(limit="2", before_id=str(second["next_before_id"]))
    ids = [e["id"] for e in first["items"] + second["items"] + third["items"]]
    assert ids == sorted(ids, reverse=True) and len(ids) == 5 and third["next_before_id"] is None
    assert len(get(limit="100000")["items"]) == 5  # clamped, not an error

    r = client.get("/api/audit", params={"limit": "abc", "since": "yesterday"})
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "validation_error" and {e["field"] for e in err["errors"]} == {"limit", "since"}

    facets = client.get("/api/audit/facets").json()
    assert facets["total"] == 5
    assert {"code": "appeal.create", "label": "Подача апелляции", "n": 2} in facets["actions"]
    assert {"code": "operator", "label": "Оператор", "n": 2} in facets["actor_roles"]


def test_audit_export_csv_needs_admin(client, env):
    client.post("/api/conversations/paste", json=paste_body("for-csv"), headers=hdr("analyst", "Дана"))
    r = client.get("/api/audit/export.csv", headers=hdr("lead"))
    assert r.status_code == 403 and r.json()["error"]["permission"] == "admin"
    for headers in (hdr("admin", "Админ"), {}):
        r = client.get("/api/audit/export.csv", params={"action": "conversation.paste"}, headers=headers)
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
        rows = list(csv.DictReader(io.StringIO(r.content.decode("utf-8-sig"))))
        assert [(row["action"], row["entity_id"], row["actor_name"]) for row in rows] == [("conversation.paste", "for-csv", "Дана")]
        assert json.loads(rows[0]["details"])["path"] == "/api/conversations/paste"


def test_record_service_limits_and_serialization(env):
    with env.Session() as db:
        ev = audit.record(db, "x" * 100, entity_type="t" * 50, entity_id="e" * 100, summary="s" * 2000,
                          details={"when": datetime(2026, 1, 1, tzinfo=UTC), "tags": {"a"}, "n": 1},
                          actor={"role": "reviewer", "name": "N" * 300, "request_id": "r" * 80}, status_code=200)
        assert (len(ev.action), len(ev.entity_type), len(ev.entity_id), len(ev.summary)) == (64, 32, 64, audit.SUMMARY_MAX)
        assert len(ev.actor_name) == 128 and len(ev.request_id) == 64
        assert ev.details == {"when": "2026-01-01 00:00:00+00:00", "tags": "{'a'}", "n": 1}
        quiet = audit.record(db, "job.interrupted", entity_type="job", entity_id="j_1", commit=False)
        assert quiet.id is not None and quiet.actor_role == "system"
        db.rollback()
        assert db.execute(select(func.count()).select_from(AuditEvent)).scalar_one() == 1
        d = audit.serialize_event(ev)
        assert d["outcome"] == "ok" and d["actor_role_label"] == "Ревьюер" and d["ts"].endswith("+00:00")


def test_compact_and_scrub_helpers():
    assert audit.scrub_text("key sk-ant-api03-AAAAAAAAAAAAAAAAAAAA and Bearer xyz.abc.def12") == "key sk-*** and Bearer ***"
    assert audit.scrub_text("postgresql://cqa:pw@db:5432/cqa") == "postgresql://***@db:5432/cqa"
    data = {"password": "p", "items": list(range(50)), "deep": {"a": {"b": {"c": {"d": {"e": {"f": 1}}}}}}, "author_label": "QA"}
    out = audit.compact(data)
    assert out["password"] == "***" and out["author_label"] == "QA"  # "author" is not "auth"
    assert out["items"][-1] == {"_more": 30} and len(out["items"]) == 21
    assert out["deep"]["a"]["b"]["c"]["d"] == {"_keys": 1}
    assert audit.json_size(audit.compact_to_size({"k": "я" * 10000})) <= 2048
    wide = {f"k{i}": "v" * 300 for i in range(300)}
    small = audit.compact_to_size(wide)
    assert audit.json_size(small) <= 2048 and small["_more_keys"] > 0
    tiny = audit.compact_to_size(wide, 100)  # even the tightest level does not fit: keep (some of) the key names
    assert tiny["_truncated"] is True and tiny["keys"][0] == "k0" and audit.json_size(tiny) <= 100


# ----------------------------------------------------------------------------- the catalog and the route map


def test_roles_catalog_matches_the_contract(client):
    cat = client.get("/api/roles", headers=hdr("visitor")).json()
    assert [r["code"] for r in cat["roles"]] == ROLE_CODES and cat["default_role"] == "demo"
    assert {p: {r for r, ok in row.items() if ok} for p, row in cat["matrix"].items()} == CONTRACT_MATRIX
    homes = {r["code"]: r["home"] for r in cat["roles"]}
    assert homes == {"demo": "#/conversations", "visitor": "#/", "operator": "#/my", "analyst": "#/conversations", "reviewer": "#/review",
                     "lead": "#/overview", "director": "#/overview", "methodologist": "#/checklists", "admin": "#/settings"}
    routes = {n["key"]: n["route"] for n in cat["nav_items"]}
    for role in cat["roles"]:
        assert set(role["label"]) == set(roles.LANGS) and all(role["label"].values()) and all(role["description"].values())
        assert role["nav"] and all(n["route"] == routes[n["key"]] and n["route"].startswith("#/") for n in role["nav"])
        assert role["permissions"] == [p for p in roles.PERMISSIONS if role["code"] in CONTRACT_MATRIX[p]]
    for item in cat["permissions"] + cat["nav_items"] + cat["actions"]:
        assert set(item["label"]) == set(roles.LANGS) and all(item["label"].values())
    decide = next(w for w in cat["write_routes"] if w["path"] == "/api/appeals/{appeal_id}/decision")
    assert decide == {"methods": ["POST"], "path": "/api/appeals/{appeal_id}/decision", "permission": "appeals.decide",
                      "action": "appeal.decide", "entity_type": "appeal", "audited": True}
    assert cat["headers"]["user"] == "X-CQA-User"


@pytest.mark.parametrize("method,path,permission,action", [
    ("POST", "/api/datasets/import", "conversations.import", "dataset.import"),
    ("POST", "/api/conversations/upload", "conversations.import", "conversation.upload"),
    ("POST", "/api/conversations/paste", "conversations.import", "conversation.paste"),
    ("POST", "/api/evaluations/run", "evaluations.run", "evaluation.run"),
    ("POST", "/api/jobs/j_1/cancel", "evaluations.run", "job.cancel"),
    ("POST", "/api/jobs/j_1/resume", "evaluations.run", "job.resume"),
    ("POST", "/api/checklists", "checklists.write", "checklist.create"),
    ("POST", "/api/checklists/test-criterion", "checklists.write", "checklist.criterion.test"),
    ("POST", "/api/checklists/1/versions", "checklists.write", "checklist.version.create"),
    ("POST", "/api/checklists/1/versions/2/activate", "checklists.write", "checklist.version.activate"),
    ("POST", "/api/checklists/1/versions/2/judge-spec/C3/regenerate", "checklists.write", "checklist.spec.regenerate"),
    ("POST", "/api/appeals", "appeals.create", "appeal.create"),
    ("POST", "/api/appeals/preview", "read", "appeal.preview"),
    ("POST", "/api/appeals/5/decision", "appeals.decide", "appeal.decide"),
    ("PUT", "/api/settings", "settings.write", "settings.update"),
    ("PUT", "/api/calibration/labels", "calibration.write", "calibration.labels.upsert"),
    ("POST", "/api/calibration/import", "calibration.write", "calibration.labels.import"),
    ("POST", "/api/integrations/dynamics/transcripts", "integrations.ingest", "integration.dynamics.ingest"),
    ("PUT", "/api/integrations/dynamics/config", "admin", "integration.config.update"),
    ("POST", "/api/admin/snapshots", "admin", "snapshot.create"),
    ("POST", "/api/admin/snapshots/baseline/restore", "admin", "snapshot.restore"),
    ("DELETE", "/api/admin/snapshots/baseline", "admin", "snapshot.delete"),
    ("DELETE", "/api/conversations/p1-ru-001", "admin", "api.write"),
    ("post", "/api/appeals/", "appeals.create", "appeal.create"),
])
def test_route_map(method, path, permission, action):
    rule, _params = roles.match_route(method, path)
    assert (rule.permission, rule.action) == (permission, action)


@pytest.mark.filterwarnings("ignore:Duplicate Operation ID")  # audio GET+HEAD share a function (not ours)
def test_every_write_endpoint_of_the_app_has_its_own_rule(env):
    import re

    from cqa import main

    for app in (env.app, main.app):
        missing = []
        for path, ops in app.openapi()["paths"].items():
            for method in ops:
                if method.upper() in roles.SAFE_METHODS:
                    continue
                sample = re.sub(r"\{(\w+)(:[^}]*)?\}", "x1", path)
                if roles.match_route(method, sample)[0] is roles.DEFAULT_RULE:
                    missing.append(f"{method.upper()} {path}")
        assert not missing, f"add a RouteRule in cqa/security/roles.py ROUTE_MAP for: {missing}"


def test_installed_in_the_real_app_through_the_platform_hook(env):
    from cqa import main

    assert sum(m.cls is security_mw.SecurityMiddleware for m in main.app.user_middleware) == 1
    security_mw.install(main.app)  # idempotent
    assert sum(m.cls is security_mw.SecurityMiddleware for m in main.app.user_middleware) == 1

    def override_get_db():
        db = env.Session()
        try:
            yield db
        finally:
            db.close()

    saved = dict(main.app.dependency_overrides)
    main.app.dependency_overrides[get_db] = override_get_db
    try:
        c = TestClient(main.app)  # no lifespan: the real database is never initialised or touched
        me = c.get("/api/me", headers={**hdr("reviewer", IVAN), "X-Request-ID": "main-1"})
        assert me.status_code == 200 and me.json()["enforced"] is True and me.json()["request_id"] == "main-1"
        r = c.post("/api/checklists", json={"name": "x"}, headers={**hdr("visitor"), "X-Request-ID": "main-2"})
        assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"
    finally:
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(saved)
    [ev] = events(env)  # written through the get_db override (main.app has no audit_session_factory)
    assert (ev.action, ev.status_code, ev.request_id, ev.actor_role) == ("checklist.create", 403, "main-2", "visitor")
