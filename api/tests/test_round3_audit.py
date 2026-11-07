"""Round 3, audit trail of appeal decisions: the summary names the answer that was saved. Since phase 2 a "change" with
new_answer null, "" or blank means N/A (the endpoint accepts it only where the criterion allows N/A on the dialog's
channel), and N/A aliases ("н/п", "na", "not applicable"…) are stored as "N/A"; the trail said "ответ изменён",
"ответ изменён на «»" or "ответ изменён на «н/п»" instead of "ответ изменён на «N/A»".

Offline: in-memory SQLite (api/data/cqa.db is never opened), no API key, no judge call.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from cqa.config import settings
from cqa.db import Base, get_db
from cqa.models import (
    Checklist,
    ChecklistVersion,
    Conversation,
    Criterion,
    Evaluation,
    EvaluationCriterion,
    Turn,
)
from cqa.review.service import NA_ALIASES
from cqa.routers import appeals as appeals_router
from cqa.routers import audit as audit_router
from cqa.scoring.engine import CriterionInput, answer_points, compute_score
from cqa.security import middleware as security_mw
from cqa.security.middleware import decision_text

SEED = json.loads((Path(__file__).resolve().parents[1] / "seeds" / "checklist_freedom_v1.json").read_text(encoding="utf-8"))
ANSWERS = {"C1": "yes", "C2": "yes", "C3": "yes", "C4": 15, "C5": "yes", "C6": 7, "C7": "yes", "C8": "no"}
CHANGED_TO_NA = "ответ изменён на «N/A»"


# ------------------------------------------------------------------------------------------------ decision_text

@pytest.mark.parametrize("missing", [None, "", "   ", "\t\n"])
def test_a_missing_new_answer_is_shown_as_na(missing):
    assert decision_text({"action": "change", "new_answer": missing, "reason": "Не требовалось"}) == CHANGED_TO_NA


def test_a_change_without_the_new_answer_field_is_shown_as_na():
    assert decision_text({"action": "change", "reason": "Не требовалось"}) == CHANGED_TO_NA  # DecisionBody default: null


@pytest.mark.parametrize("alias", sorted(NA_ALIASES) + ["N/A", " Н/П ", "NA", "Not Applicable", "НЕПРИМЕНИМО"])
def test_every_na_alias_is_shown_as_na(alias):
    assert decision_text({"action": "change", "new_answer": alias}) == CHANGED_TO_NA


@pytest.mark.parametrize("answer,shown", [("Да", "Да"), ("no", "no"), ("yes", "yes"), (15, "15"), (0, "0")])
def test_other_answers_are_shown_as_sent(answer, shown):
    assert decision_text({"action": "change", "new_answer": answer}) == f"ответ изменён на «{shown}»"


def test_confirm_and_other_bodies_are_unchanged():
    assert decision_text({"action": "confirm"}) == "оценка подтверждена"
    assert decision_text({"action": "confirm", "new_answer": None}) == "оценка подтверждена"
    assert decision_text({"action": "reject", "new_answer": None}) is None
    assert decision_text(None) is None and decision_text(["change"]) is None


# ------------------------------------------------------------------------------------------------ through the API

def _seed(db: Session) -> dict[str, int]:
    """The organizer checklist, one chat dialog and its completed evaluation; returns {criterion code: result row id}."""
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
    turns = [("client", "00:00", "Здравствуйте, не могу войти в приложение"),
             ("operator", "00:40", "Здравствуйте! Меня зовут Алина. Нажмите «Забыли пароль», код придёт по SMS.")]
    db.add(Conversation(id="chat-001", channel="agent chat", language="RU", operator_name="Алина", turn_count=len(turns)))
    db.flush()
    for idx, (speaker, ts, text) in enumerate(turns):
        db.add(Turn(conversation_id="chat-001", idx=idx, timestamp_raw=ts, offset_seconds=float(idx * 40), speaker=speaker, text=text,
                    segment="operator" if idx else "pre"))
    items = [CriterionInput(c.code, c.weight, c.max_points, answer_points(c.options, ANSWERS[c.code]), True, c.section, c.is_critical,
                            bool(c.is_critical and ANSWERS[c.code] == c.critical_value)) for c in crits]
    res = compute_score(version.scoring_rules, items, version.sections)
    ev = Evaluation(conversation_id="chat-001", checklist_version_id=version.id, status="completed", provider="fake", model="fake",
                    total_score=res.total, final_total_score=res.total, critical_error=res.critical, completed_at=datetime.now(UTC))
    db.add(ev)
    db.flush()
    rows = {}
    for c, item in zip(crits, items):
        cr = res.criteria[c.code]
        row = EvaluationCriterion(evaluation_id=ev.id, criterion_id=c.id, code=c.code, applicable=True, answer=ANSWERS[c.code], score=item.points,
                                  max_points=c.max_points, weight=c.weight, weight_applied=cr.weight_applied, earned=cr.earned, confidence=0.9,
                                  explanation=f"{c.title} — обоснование.", evidence=[{"turn_idx": 1, "speaker": "operator", "quote": "Здравствуйте!"}])
        db.add(row)
        db.flush()
        rows[c.code] = row.id
    db.commit()
    return rows


def _app(SessionFactory: sessionmaker) -> FastAPI:
    app = FastAPI()

    @app.exception_handler(HTTPException)  # same envelope as cqa.main
    async def http_exc(request, exc: HTTPException):
        detail = exc.detail if isinstance(exc.detail, dict) else {"code": "error", "message": str(exc.detail)}
        return JSONResponse(status_code=exc.status_code, content={"error": detail})

    for module in (appeals_router, audit_router):
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
        rows = _seed(db)
    monkeypatch.setattr(settings, "anthropic_api_key", None)  # never the Claude API
    monkeypatch.setattr(security_mw, "_default_session_factory", lambda: (_ for _ in ()).throw(AssertionError("real DB")))
    with TestClient(_app(SessionFactory)) as client:
        yield SimpleNamespace(client=client, rows=rows)
    engine.dispose()


@pytest.mark.parametrize("new_answer", [None, "", "н/п"])
def test_the_audit_trail_names_na_for_a_decision_saved_as_na(env, new_answer):
    r = env.client.post("/api/appeals", headers={"X-CQA-Role": "analyst"},
                        json={"evaluation_criterion_id": env.rows["C3"], "comment": "Идентификация не требовалась"})  # C3 allows N/A
    assert r.status_code == 200, r.text
    appeal_id = r.json()["id"]
    r = env.client.post(f"/api/appeals/{appeal_id}/decision", headers={"X-CQA-Role": "reviewer"},  # ReviewPage's body for the N/A chip
                        json={"action": "change", "new_answer": new_answer, "reason": "Запрос не требовал идентификации",
                              "reviewer_label": "Ревьюер", "add_to_fewshot": True})
    assert r.status_code == 200, r.text
    assert r.json()["appeal"]["decision"]["new_answer"] == "N/A"

    r = env.client.get("/api/audit", params={"action": "appeal.decide", "entity_id": str(appeal_id)}, headers={"X-CQA-Role": "visitor"})
    assert r.status_code == 200, r.text
    (event,) = r.json()["items"]
    assert (event["actor_role"], event["status_code"]) == ("reviewer", 200)
    assert event["summary"] == f"Апелляция #{appeal_id} (C3, диалог chat-001): {CHANGED_TO_NA}"
    assert event["details"]["response"]["appeal.decision.new_answer"] == "N/A"  # the summary agrees with the saved answer
