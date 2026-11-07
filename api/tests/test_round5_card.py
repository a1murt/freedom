"""Card payload, round 5 (WP-1 and WP-2 of docs/BUILD_BACKLOG.md).

Every criterion row of the evaluation JSON carries what the card used to look up elsewhere: the criterion's definition
(section, gross-error flag and value, N/A allowed in general and on the dialog's channel), the AI answer and the final
answer typed like `options[].value`, and, once anything in the evaluation was decided, every row's final share of the
total (a decision to or from N/A moves weight between all rows, and the rows must still add up to final_total_score).
The evaluation carries `attention`, the conversation detail says when the dialog is being evaluated, and every
timestamp carries its UTC offset. The fields the deployed web app reads keep their values.

Offline by construction: in-memory SQLite, no provider is ever called.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from cqa.config import settings
from cqa.db import Base, get_db
from cqa.ingest.service import is_busy
from cqa.models import (
    Checklist,
    ChecklistVersion,
    Conversation,
    Criterion,
    Dataset,
    Evaluation,
    EvaluationCriterion,
    LlmTrace,
    Turn,
)
from cqa.routers import appeals as appeals_router
from cqa.routers import conversations as conversations_router
from cqa.scoring.engine import CriterionInput, answer_points, compute_score
from cqa.security import middleware as security_mw
from cqa.serializers import criterion_result, iso_utc

SEED = json.loads((Path(__file__).resolve().parents[1] / "seeds" / "checklist_freedom_v1.json").read_text(encoding="utf-8"))
NA = "N/A"
FAILED = object()  # the judge call for the criterion failed: applicable, no answer, flag "error"
# answers as older rows store them: a label («Да»), a scale value as text («15»), an AI N/A, a failed criterion
CHAT = {"C1": "yes", "C2": "Да", "C3": NA, "C4": "15", "C5": FAILED, "C6": 7, "C7": "yes", "C8": "no"}
CALL = {"C1": "yes", "C2": "yes", "C3": "yes", "C4": 15, "C5": "yes", "C6": 7, "C7": "yes", "C8": "no"}
# organizer weights 10/15/20/20/15/15/5/0; the chat scores C1, C2, C4, C6, C7 (65 of 100): 55,5 / 65 → 85,38
CHAT_AI_TOTAL = 85.38
T_IMPORT = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
T_CREATED = datetime(2026, 11, 7, 12, 5, tzinfo=UTC)
T_DONE = datetime(2026, 11, 7, 12, 6, 30, 250000, tzinfo=UTC)

OLD_EVALUATION_KEYS = {"id", "conversation_id", "checklist_version_id", "checklist_id", "checklist_name", "checklist_version_no", "status",
                       "provider", "mode", "model", "prompt_version", "total_score", "final_total_score", "critical_error", "sections",
                       "cost_usd", "input_tokens", "output_tokens", "latency_ms", "job_id", "error", "created_at", "completed_at",
                       "review_state", "criteria"}
OLD_ROW_KEYS = {"id", "code", "title", "answer_type", "options", "applicable", "na_reason", "answer", "score", "max_points", "weight",
                "weight_applied", "earned", "confidence", "explanation", "evidence", "checked_span", "flags", "claims", "votes",
                "review_state", "final_answer", "final_score", "final_earned", "appeal", "appeals"}
NEW_ROW_KEYS = {"section", "is_critical", "critical_value", "na_allowed", "na_allowed_for_channel", "answer_option", "final_option",
                "final_breakdown"}
OLD_DETAIL_KEYS = {"id", "channel", "language", "operator_name", "agent_name", "turn_count", "has_audio", "first_response_seconds",
                   "terminal_event", "imported_at", "dataset_id", "latest_evaluation", "review_state", "turns", "audio_url_available", "meta"}

ANALYST, REVIEWER = {"X-CQA-Role": "analyst"}, {"X-CQA-Role": "reviewer"}


# ------------------------------------------------------------------------------------------------ app fixture

def _seed_checklist(db: Session) -> ChecklistVersion:
    checklist = Checklist(name=SEED["name"], description=SEED["description"])
    db.add(checklist)
    db.flush()
    version = ChecklistVersion(checklist_id=checklist.id, version_no=1, is_active=True, change_note="v1",
                               scoring_rules=SEED["scoring_rules"], sections=SEED["sections"])
    db.add(version)
    db.flush()
    for i, c in enumerate(SEED["criteria"], start=1):
        overrides = dict(c["channel_overrides"])
        if c["code"] == "C5":  # N/A allowed on one channel only (the organizer checklist has no such criterion since 2026-11-08)
            overrides["voice call"] = {**overrides.get("voice call", {}), "na_allowed": True}
        db.add(Criterion(checklist_version_id=version.id, code=c["code"], order=i, section=c["section"], title=c["title"],
                         instruction=c["instruction"], answer_type=c["answer_type"], options=c["options"], max_points=c["max_points"],
                         weight=c["weight"], na_allowed=c["na_allowed"], na_condition=c["na_condition"], is_critical=c["is_critical"],
                         critical_value=c.get("critical_value"), channel_overrides=overrides))
    db.flush()
    return version


def _conversation(db: Session, cid: str, channel: str) -> None:
    turns = [("client", "Здравствуйте, не могу войти в приложение"), ("operator", "Здравствуйте! Меня зовут Алина. Какую ошибку видите?"),
             ("client", "Пишет «неверный пароль»"), ("operator", "Нажмите «Забыли пароль», код придёт по SMS. Хорошего дня!")]
    db.add(Conversation(id=cid, channel=channel, language="RU", operator_name="Алина", turn_count=len(turns), imported_at=T_IMPORT))
    db.flush()
    for idx, (speaker, text) in enumerate(turns):
        db.add(Turn(conversation_id=cid, idx=idx, timestamp_raw=f"00:{idx * 20:02d}", offset_seconds=float(idx * 20), speaker=speaker,
                    text=text, segment="operator" if idx else "pre"))
    db.flush()


def _evaluate(db: Session, cid: str, version: ChecklistVersion, answers: dict) -> Evaluation:
    """A completed evaluation scored like the runner does (cqa.judge.runner): stored shares from one compute_score."""
    crits = sorted(version.criteria, key=lambda c: c.order)
    points = {c.code: (None if answers[c.code] is FAILED or answers[c.code] == NA else answer_points(c.options, answers[c.code]))
              for c in crits}
    items = [CriterionInput(c.code, c.weight, c.max_points, points[c.code], points[c.code] is not None, c.section, c.is_critical,
                            bool(c.is_critical and points[c.code] is not None and str(answers[c.code]).lower() == c.critical_value))
             for c in crits]
    res = compute_score(version.scoring_rules, items, version.sections)
    ev = Evaluation(conversation_id=cid, checklist_version_id=version.id, status="completed", provider="anthropic", model="claude-opus-5-5",
                    total_score=res.total, final_total_score=res.total, critical_error=res.critical, created_at=T_CREATED, completed_at=T_DONE)
    db.add(ev)
    db.flush()
    for c in crits:
        a, cr = answers[c.code], res.criteria[c.code]
        common = {"evaluation_id": ev.id, "criterion_id": c.id, "code": c.code, "max_points": c.max_points, "weight": c.weight,
                  "weight_applied": cr.weight_applied, "earned": cr.earned}
        if a is FAILED:
            row = EvaluationCriterion(**common, applicable=True, answer=None, score=None, explanation="Ошибка оценки: timeout", flags=["error"])
        elif a == NA:
            row = EvaluationCriterion(**common, applicable=False, answer=None, score=None, confidence=0.9,
                                      na_reason="Клиент не запрашивал данные счёта.", explanation=f"{c.title} — N/A.")
        else:
            row = EvaluationCriterion(**common, applicable=True, answer=a, score=points[c.code], confidence=0.9,
                                      explanation=f"{c.title} — обоснование.",
                                      evidence=[{"turn_idx": 1, "speaker": "operator", "quote": "Здравствуйте!"}])
        db.add(row)
    db.flush()
    return ev


def _app(SessionFactory: sessionmaker) -> FastAPI:
    app = FastAPI()

    @app.exception_handler(HTTPException)  # same envelope as cqa.main
    async def http_exc(request, exc: HTTPException):
        detail = exc.detail if isinstance(exc.detail, dict) else {"code": "error", "message": str(exc.detail)}
        return JSONResponse(status_code=exc.status_code, content={"error": detail})

    for module in (conversations_router, appeals_router):
        app.include_router(module.router)

    def override_get_db():
        db = SessionFactory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    security_mw.install(app, session_factory=SessionFactory)
    return app


@pytest.fixture
def env(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    SessionFactory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with SessionFactory() as db:
        version = _seed_checklist(db)
        _conversation(db, "chat-001", "agent chat")
        _conversation(db, "call-002", "voice call")
        chat, call = _evaluate(db, "chat-001", version, CHAT), _evaluate(db, "call-002", version, CALL)
        db.commit()
        ids = {"version": version.id, "chat": chat.id, "call": call.id, "rows": {r.code: r.id for r in chat.criteria},
               "call_rows": {r.code: r.id for r in call.criteria}}
    monkeypatch.setattr(settings, "anthropic_api_key", None)
    monkeypatch.setattr(security_mw, "_default_session_factory", lambda: (_ for _ in ()).throw(AssertionError("real DB")))
    with TestClient(_app(SessionFactory)) as client:
        yield SimpleNamespace(client=client, Session=SessionFactory, **ids)
    engine.dispose()


def _card(env, conv: str = "chat-001") -> tuple[dict, dict[str, dict]]:
    r = env.client.get(f"/api/conversations/{conv}/evaluation")
    assert r.status_code == 200, r.text
    ev = r.json()
    return ev, {c["code"]: c for c in ev["criteria"]}


def _decide(env, row_id: int, action: str = "change", new_answer=None) -> dict:
    ap = env.client.post("/api/appeals", json={"evaluation_criterion_id": row_id, "comment": "Не согласна с оценкой"}, headers=ANALYST)
    assert ap.status_code == 200, ap.text
    r = env.client.post(f"/api/appeals/{ap.json()['id']}/decision", headers=REVIEWER,
                        json={"action": action, "new_answer": new_answer, "reason": "Проверено по расшифровке"})
    assert r.status_code == 200, r.text
    return r.json()


def _utc(stamp: str) -> datetime:
    dt = datetime.fromisoformat(stamp)
    assert dt.utcoffset() == timedelta(0), stamp
    return dt


# ------------------------------------------------------------------------------------------------ definition fields

def test_rows_carry_their_criterion_definition(env):
    _, chat = _card(env)
    _, call = _card(env, "call-002")
    assert list(chat) == [f"C{i}" for i in range(1, 9)]
    definition = ("section", "is_critical", "critical_value", "na_allowed", "na_allowed_for_channel")
    assert {k: chat["C1"][k] for k in definition} == {"section": "S1", "is_critical": False, "critical_value": None, "na_allowed": False,
                                                      "na_allowed_for_channel": False}
    assert {k: chat["C8"][k] for k in definition} == {"section": "S4", "is_critical": True, "critical_value": "yes", "na_allowed": False,
                                                      "na_allowed_for_channel": False}
    assert {code: row["section"] for code, row in chat.items()} == {c["code"]: c["section"] for c in SEED["criteria"]}
    assert (chat["C3"]["na_allowed"], chat["C3"]["na_allowed_for_channel"]) == (True, True)
    # N/A allowed by a channel override only: the base flag stays false, the channel's rule says true on calls only
    assert (chat["C5"]["na_allowed"], chat["C5"]["na_allowed_for_channel"]) == (False, False)
    assert (call["C5"]["na_allowed"], call["C5"]["na_allowed_for_channel"]) == (False, True)
    assert (call["C1"]["na_allowed"], call["C1"]["na_allowed_for_channel"]) == (False, False)  # no N/A for contact on calls
    assert call["C2"]["na_allowed_for_channel"] is True


def test_the_payload_only_grows(env):
    ev, rows = _card(env)
    assert OLD_EVALUATION_KEYS | {"attention"} <= set(ev)
    assert all(OLD_ROW_KEYS | NEW_ROW_KEYS <= set(r) for r in rows.values())
    detail = env.client.get("/api/conversations/chat-001").json()
    assert OLD_DETAIL_KEYS | {"evaluation_in_progress"} <= set(detail)
    # the stored forms and the AI shares are untouched
    c1, c2, c3, c4 = rows["C1"], rows["C2"], rows["C3"], rows["C4"]
    assert (c2["answer"], c2["final_answer"], c4["answer"], c4["final_answer"], c3["final_answer"]) == ("Да", "Да", "15", "15", None)
    assert (c1["weight_applied"], c1["earned"], c1["final_earned"], ev["total_score"]) == (15.3846, 15.3846, 15.3846, CHAT_AI_TOTAL)
    # a row serialized on its own (no definition, no channel) gets neutral defaults
    with env.Session() as db:
        alone = criterion_result(db.get(EvaluationCriterion, env.rows["C2"]))
    assert {k: alone[k] for k in NEW_ROW_KEYS} == {"section": None, "is_critical": False, "critical_value": None, "na_allowed": False,
                                                   "na_allowed_for_channel": False, "answer_option": "Да", "final_option": "Да",
                                                   "final_breakdown": None}


# ------------------------------------------------------------------------------------------------ typed answers

def test_answers_are_typed_like_the_option_values(env):
    _, rows = _card(env)
    assert (rows["C2"]["answer_option"], rows["C2"]["final_option"]) == ("yes", "yes")  # stored as the label «Да»
    assert rows["C4"]["answer_option"] == 15 and type(rows["C4"]["answer_option"]) is int and type(rows["C4"]["final_option"]) is int
    assert (rows["C1"]["answer_option"], rows["C6"]["answer_option"], rows["C8"]["answer_option"]) == ("yes", 7, "no")
    # an AI N/A reads "N/A" in both (final_answer keeps its null); a failed judge call is neither N/A nor an answer
    assert (rows["C3"]["answer_option"], rows["C3"]["final_option"]) == (NA, NA)
    assert (rows["C5"]["answer_option"], rows["C5"]["final_option"]) == (None, None)
    for r in rows.values():
        if r["answer_option"] not in (None, NA):
            assert r["answer_option"] in [o["value"] for o in r["options"]], r["code"]
    # nothing decided: no breakdown anywhere (the stored shares are the final ones)
    assert all(r["final_breakdown"] is None for r in rows.values())


# ------------------------------------------------------------------------------------------------ final breakdown

def test_a_decision_to_na_gives_every_row_its_final_share(env):
    _decide(env, env.rows["C7"], new_answer="N/A")
    ev, rows = _card(env)
    assert (ev["total_score"], ev["final_total_score"]) == (CHAT_AI_TOTAL, 84.17)  # 50,5 / 60
    c7 = rows["C7"]
    assert (c7["answer"], c7["answer_option"], c7["final_answer"], c7["final_option"]) == ("yes", "yes", NA, NA)
    assert c7["final_breakdown"] == {"applicable": False, "weight_applied": 0.0, "earned": 0.0}
    assert all(r["final_breakdown"] is not None for r in rows.values())  # every row, decided or not
    # C7's weight went to the other scored rows: 60 applicable weight → factor 100 / 60
    assert rows["C1"]["final_breakdown"] == {"applicable": True, "weight_applied": 16.6667, "earned": 16.6667}
    assert rows["C4"]["final_breakdown"] == {"applicable": True, "weight_applied": 33.3333, "earned": 25.0}
    assert (rows["C1"]["weight_applied"], rows["C1"]["final_earned"]) == (15.3846, 15.3846)  # the older fields keep the AI shares
    scored = {code for code, r in rows.items() if r["final_breakdown"]["applicable"]}
    assert scored == {"C1", "C2", "C4", "C6", "C8"}  # not N/A (C3, C7) nor failed (C5); C8 is scored with weight 0
    assert sum(rows[c]["final_breakdown"]["weight_applied"] for c in scored) == pytest.approx(100, abs=1e-3)
    assert round(sum(r["final_breakdown"]["earned"] for r in rows.values()), 2) == ev["final_total_score"]
    assert round(sum(r["final_earned"] or 0 for r in rows.values()), 2) != ev["final_total_score"]  # why the breakdown exists
    _, call = _card(env, "call-002")
    assert all(r["final_breakdown"] is None for r in call.values())  # per evaluation


def test_decisions_from_na_and_on_a_scale_keep_types_and_add_up(env):
    _decide(env, env.rows["C3"], new_answer="yes")  # the AI said N/A
    out = _decide(env, env.rows["C4"], new_answer="20")  # a scale value sent as text
    ev, rows = out["evaluation"], {c["code"]: c for c in out["evaluation"]["criteria"]}  # the decision answers with the card's payload
    assert (rows["C3"]["answer_option"], rows["C3"]["final_option"]) == (NA, "yes")
    assert rows["C3"]["final_breakdown"] == {"applicable": True, "weight_applied": 23.5294, "earned": 23.5294}
    assert (rows["C4"]["answer_option"], rows["C4"]["final_answer"], rows["C4"]["final_option"]) == (15, 20, 20)
    assert ev["final_total_score"] == 94.71  # 80,5 / 85
    assert round(sum(r["final_breakdown"]["earned"] for r in rows.values()), 2) == ev["final_total_score"]
    for code in ("C3", "C4"):  # decided rows: the stored final_earned is the breakdown's
        assert rows[code]["final_breakdown"]["earned"] == pytest.approx(rows[code]["final_earned"], abs=1e-4)
    assert _card(env)[1] == rows  # the card reads the same


def test_a_confirmation_reproduces_the_ai_shares(env):
    _decide(env, env.rows["C6"], action="confirm")
    ev, rows = _card(env)
    assert ev["final_total_score"] == ev["total_score"] == CHAT_AI_TOTAL
    for r in rows.values():
        assert r["final_breakdown"] == {"applicable": r["answer_option"] not in (None, NA), "weight_applied": r["weight_applied"],
                                        "earned": r["earned"]}, r["code"]


# ------------------------------------------------------------------------------------------------ attention

def test_the_evaluation_carries_attention(env):
    with env.Session() as db:
        db.get(EvaluationCriterion, env.call_rows["C6"]).confidence = 0.4
        db.commit()
    chat = env.client.get(f"/api/evaluations/{env.chat}").json()
    assert chat["attention"] == {"low_confidence": [], "split_votes": [], "failed": ["C5"], "contradicted_claims": 0, "needs_review": True}
    call, _ = _card(env, "call-002")
    assert call["attention"]["low_confidence"] == ["C6"] and call["attention"]["needs_review"] is True
    history = env.client.get("/api/conversations/chat-001/evaluations").json()
    assert history[0]["attention"] == chat["attention"] and "criteria" not in history[0]
    item = next(i for i in env.client.get("/api/conversations").json()["items"] if i["id"] == "chat-001")
    assert item["latest_evaluation"]["attention"] == chat["attention"]  # the list's brief and the card agree


# ------------------------------------------------------------------------------------------------ evaluation in progress

def test_the_detail_says_when_the_dialog_is_being_evaluated(env):
    assert env.client.get("/api/conversations/chat-001").json()["evaluation_in_progress"] is None
    with env.Session() as db:
        db.add(Conversation(id="chat-new", channel="agent chat", language="RU", turn_count=0, imported_at=T_IMPORT))
        db.flush()
        queued = Evaluation(conversation_id="chat-new", checklist_version_id=env.version, status="queued", job_id="j_old",
                            created_at=datetime(2026, 11, 7, 13, 0, tzinfo=UTC))
        db.add(queued)
        db.commit()
        queued_id = queued.id
    assert env.client.get("/api/conversations/chat-new/evaluation").status_code == 404  # unchanged: nothing evaluated yet
    detail = env.client.get("/api/conversations/chat-new").json()
    assert detail["evaluation_in_progress"] == {"evaluation_id": queued_id, "job_id": "j_old", "status": "queued",
                                                "started_at": "2026-11-07T13:00:00+00:00"}
    with env.Session() as db:
        running = Evaluation(conversation_id="chat-new", checklist_version_id=env.version, status="running", job_id="j_new",
                             created_at=datetime(2026, 11, 7, 13, 1, 5, tzinfo=UTC))
        db.add(running)
        db.commit()
        running_id = running.id
        assert is_busy(db, "chat-new")  # the same rows the ingest guard checks
    assert env.client.get("/api/conversations/chat-new").json()["evaluation_in_progress"] == {
        "evaluation_id": running_id, "job_id": "j_new", "status": "running", "started_at": "2026-11-07T13:01:05+00:00"}
    with env.Session() as db:
        for ev in db.execute(select(Evaluation).where(Evaluation.conversation_id == "chat-new")).scalars():
            ev.status, ev.error = "failed", "interrupted by restart"
        db.commit()
        assert not is_busy(db, "chat-new")
    assert env.client.get("/api/conversations/chat-new").json()["evaluation_in_progress"] is None


# ------------------------------------------------------------------------------------------------ timestamps

def test_iso_utc_keeps_the_instant():
    assert iso_utc(None) is None
    assert iso_utc(datetime(2026, 11, 8, 12, 0)) == "2026-11-08T12:00:00+00:00"  # naive from SQLite = UTC
    almaty = timezone(timedelta(hours=5))
    assert iso_utc(datetime(2026, 11, 8, 17, 0, tzinfo=almaty)) == "2026-11-08T12:00:00+00:00"
    assert datetime.fromisoformat(iso_utc(datetime(2026, 11, 8, 17, 0, tzinfo=almaty))) == datetime(2026, 11, 8, 17, 0, tzinfo=almaty)


def test_every_timestamp_carries_its_utc_offset(env):
    with env.Session() as db:
        db.add(Dataset(group_id="DEV", source="organizer_api", imported_at=T_IMPORT, record_count=2))
        db.add(LlmTrace(evaluation_id=env.chat, criterion_code="C1", provider="anthropic", model="claude-opus-5-5", request={},
                        response={}, created_at=T_CREATED))
        db.add(Evaluation(conversation_id="chat-001", checklist_version_id=env.version, status="failed", error="Anthropic connection error",
                          created_at=T_DONE))  # a failed retry after the completed evaluation
        db.commit()
    _decide(env, env.rows["C7"], new_answer="no")
    ev, rows = _card(env)
    assert (ev["created_at"], ev["completed_at"]) == ("2026-11-07T12:05:00+00:00", "2026-11-07T12:06:30.250000+00:00")
    assert ev["last_attempt_failed"] is True and ev["last_attempt"]["created_at"] == "2026-11-07T12:06:30.250000+00:00"
    appeal = rows["C7"]["appeal"]
    for stamp in (appeal["created_at"], appeal["decision"]["created_at"], rows["C7"]["appeals"][0]["created_at"]):
        assert abs(_utc(stamp) - datetime.now(UTC)) < timedelta(minutes=5)  # written by utcnow, read back naive from SQLite
    detail = env.client.get("/api/conversations/chat-001").json()
    assert detail["imported_at"] == "2026-11-07T12:00:00+00:00" and detail["latest_evaluation"]["completed_at"] == ev["completed_at"]
    listed = next(i for i in env.client.get("/api/conversations").json()["items"] if i["id"] == "chat-001")
    assert listed["imported_at"] == detail["imported_at"] and listed["last_attempt"]["created_at"] == ev["last_attempt"]["created_at"]
    assert env.client.get("/api/datasets").json()[0]["imported_at"] == "2026-11-07T12:00:00+00:00"
    assert env.client.get(f"/api/evaluations/{env.chat}/trace").json()[0]["created_at"] == "2026-11-07T12:05:00+00:00"
    history = env.client.get("/api/conversations/chat-001/evaluations").json()
    assert [_utc(h["created_at"]) for h in history] == [T_DONE, T_CREATED]
    appeals = env.client.get("/api/appeals").json()
    assert _utc(appeals[0]["created_at"]) == _utc(appeal["created_at"])
