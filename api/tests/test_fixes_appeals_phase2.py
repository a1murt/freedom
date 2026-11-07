"""Appeals, phase 2: a withdrawn appeal cannot be decided (APPEALS-ROLES-1); withdrawing restores a decision a partial
re-evaluation carried onto the row (APPEALS-ROLES-2); a later withdrawn appeal never hides the decision on the card
(APPEALS-ROLES-4); and the contract of the deployed web app (AppealDialog, ReviewPage): any non-blank comment or reason
is enough, and a missing new_answer (null or "") means N/A where the criterion allows N/A on the dialog's channel
(the backend half of APPEALS-ROLES-3).

Offline by construction: in-memory SQLite (api/data/cqa.db is never opened), no API key, and the partial
re-evaluations judge nothing (every row is copied from the base evaluation), so no provider is ever called.
"""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
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
from cqa.judge import runner as judge_runner
from cqa.models import Checklist, ChecklistVersion, Conversation, Criterion, Decision, Evaluation, EvaluationCriterion, Turn
from cqa.routers import appeals as appeals_router
from cqa.routers import conversations as conversations_router
from cqa.routers import export as export_router
from cqa.scoring.engine import CriterionInput, answer_points, compute_score
from cqa.security import middleware as security_mw

SEED = json.loads((Path(__file__).resolve().parents[1] / "seeds" / "checklist_freedom_v1.json").read_text(encoding="utf-8"))
ANSWERS = {"C1": "yes", "C2": "yes", "C3": "yes", "C4": 15, "C5": "yes", "C6": 7, "C7": "yes", "C8": "no"}
ALINA = "Алина"
ALINA_ENC = "%D0%90%D0%BB%D0%B8%D0%BD%D0%B0"
# organizer weights 10/15/20/20/15/15/5/0: the AI total, C7 = no, C7 = N/A (weight redistributed), C1 = N/A
AI_TOTAL, C7_NO_TOTAL, C7_NA_TOTAL, C1_NA_TOTAL = 90.5, 85.5, 90.0, 89.44
NOT_OPEN_RU = "Апелляция закрыта (отозвана): решение можно принять только по открытой апелляции"


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
        db.add(Criterion(checklist_version_id=version.id, code=c["code"], order=i, section=c["section"], title=c["title"],
                         instruction=c["instruction"], answer_type=c["answer_type"], options=c["options"], max_points=c["max_points"],
                         weight=c["weight"], na_allowed=c["na_allowed"], na_condition=c["na_condition"], is_critical=c["is_critical"],
                         critical_value=c.get("critical_value"), channel_overrides=c["channel_overrides"]))
    db.flush()
    return version


def _conversation(db: Session, cid: str, channel: str) -> None:
    turns = [("client", "00:00", "Здравствуйте, не могу войти в приложение"),
             ("operator", "00:40", f"Здравствуйте! Меня зовут {ALINA}. Какую ошибку видите?"),
             ("client", "01:10", "Пишет «неверный пароль»"),
             ("operator", "01:40", "Нажмите «Забыли пароль», код придёт по SMS. Хорошего дня!")]
    db.add(Conversation(id=cid, channel=channel, language="RU", operator_name=ALINA, turn_count=len(turns)))
    db.flush()
    for idx, (speaker, ts, text) in enumerate(turns):
        db.add(Turn(conversation_id=cid, idx=idx, timestamp_raw=ts, offset_seconds=float(idx * 20), speaker=speaker, text=text,
                    segment="operator" if idx else "pre"))
    db.flush()


def _evaluate(db: Session, cid: str, version: ChecklistVersion) -> dict[str, int]:
    crits = sorted(version.criteria, key=lambda c: c.order)
    items = [CriterionInput(c.code, c.weight, c.max_points, answer_points(c.options, ANSWERS[c.code]), True, c.section, c.is_critical,
                            bool(c.is_critical and ANSWERS[c.code] == c.critical_value)) for c in crits]
    res = compute_score(version.scoring_rules, items, version.sections)
    ev = Evaluation(conversation_id=cid, checklist_version_id=version.id, status="completed", provider="anthropic", model="claude-opus-5-5",
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
    return rows


def _app(SessionFactory: sessionmaker) -> FastAPI:
    app = FastAPI()

    @app.exception_handler(HTTPException)  # same envelope as cqa.main
    async def http_exc(request, exc: HTTPException):
        detail = exc.detail if isinstance(exc.detail, dict) else {"code": "error", "message": str(exc.detail)}
        return JSONResponse(status_code=exc.status_code, content={"error": detail})

    for module in (conversations_router, appeals_router, export_router):
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
        rows = {}
        for cid, channel in (("chat-001", "agent chat"), ("call-002", "voice call")):
            _conversation(db, cid, channel)
            rows[cid] = _evaluate(db, cid, version)
        db.commit()
        version_id = version.id
    monkeypatch.setattr(settings, "anthropic_api_key", None)
    monkeypatch.setattr(security_mw, "_default_session_factory", lambda: (_ for _ in ()).throw(AssertionError("real DB")))
    monkeypatch.setattr(judge_runner, "SessionLocal", SessionFactory)  # partial re-evaluations run on the in-memory database
    with TestClient(_app(SessionFactory)) as client:
        yield SimpleNamespace(client=client, Session=SessionFactory, rows=rows, version_id=version_id)
    engine.dispose()


def H(role: str, user_enc: str | None = None) -> dict[str, str]:
    return {"X-CQA-Role": role, **({"X-CQA-User": user_enc} if user_enc else {})}


ANALYST, REVIEWER, OPERATOR = H("analyst"), H("reviewer"), H("operator", ALINA_ENC)


def _file(env, code: str = "C7", conv: str = "chat-001", comment: str = "Не согласна с оценкой", headers=None, row_id: int | None = None) -> dict:
    r = env.client.post("/api/appeals", json={"evaluation_criterion_id": row_id or env.rows[conv][code], "comment": comment},
                        headers=headers or ANALYST)
    assert r.status_code == 200, r.text
    return r.json()


def _withdraw(env, appeal_id: int, headers=None) -> dict:
    r = env.client.post(f"/api/appeals/{appeal_id}/withdraw", headers=headers or ANALYST)
    assert r.status_code == 200, r.text
    return r.json()


def _decide(env, appeal_id: int, **body):
    return env.client.post(f"/api/appeals/{appeal_id}/decision", json=body, headers=REVIEWER)


def _card(env, conv: str = "chat-001") -> tuple[dict, dict[str, dict]]:
    ev = env.client.get(f"/api/conversations/{conv}/evaluation").json()
    return ev, {c["code"]: c for c in ev["criteria"]}


def _preview(env, row_id: int, new_answer) -> float:
    r = env.client.post("/api/appeals/preview", json={"evaluation_criterion_id": row_id, "new_answer": new_answer})
    assert r.status_code == 200, r.text
    return r.json()["final_total_score"]


def _results_criterion(env, criterion_id: str, conv: str = "chat-001") -> dict:
    r = env.client.get("/api/export/results.json", params={"conversation_ids": conv, "answers": "final", "download": "false"})
    assert r.status_code == 200, r.text
    record = next(x for x in r.json()["results"] if x["conversation_id"] == conv)
    return next(c for c in record["criteria"] if c["criterion_id"] == criterion_id)


def _partial(env, conv: str, base_evaluation_id: int) -> int:
    """A partial re-evaluation that judges nothing: every row is copied from the base evaluation and the reviewer
    decisions on it are carried (cqa.judge.runner), as for a new checklist version whose criteria did not change."""
    return asyncio.run(judge_runner.evaluate_conversation(conv, env.version_id, provider_name="fake", mode="partial",
                                                          base_evaluation_id=base_evaluation_id, only_codes=set()))


# ------------------------------------------------------------------------------------------------ APPEALS-ROLES-1

def test_withdrawn_appeal_cannot_be_decided(env):
    ap = _file(env, headers=OPERATOR)
    _withdraw(env, ap["id"], headers=OPERATOR)
    for body in ({"action": "change", "new_answer": "no", "reason": "Не попрощался"}, {"action": "confirm"}):
        r = _decide(env, ap["id"], **body)
        assert r.status_code == 400, r.text
        assert r.json()["error"]["code"] == "invalid_decision" and r.json()["error"]["message"] == NOT_OPEN_RU
    ev, rows = _card(env)
    assert (rows["C7"]["review_state"], rows["C7"]["final_answer"], ev["final_total_score"]) == ("none", "yes", AI_TOTAL)
    got = env.client.get(f"/api/appeals/{ap['id']}").json()
    assert got["status"] == "withdrawn" and got["decision"] is None
    with env.Session() as db:
        assert db.execute(select(Decision)).scalars().all() == []


def test_an_old_withdrawn_appeal_cannot_overwrite_the_newer_open_one(env):
    first = _file(env, headers=OPERATOR)
    _withdraw(env, first["id"], headers=OPERATOR)
    second = _file(env, headers=OPERATOR, comment="Подаю заново: оператор попрощался")
    r = _decide(env, first["id"], action="confirm")
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_decision"
    ev, rows = _card(env)
    assert rows["C7"]["review_state"] == "under_review" and ev["review_state"] == "under_review"
    listed = env.client.get("/api/conversations", params={"status": "under_review"}).json()
    assert [c["id"] for c in listed["items"]] == ["chat-001"]
    # the open appeal is decided as usual, and the withdrawn one still cannot overwrite that decision
    assert _decide(env, second["id"], action="change", new_answer="no", reason="Не попрощался").status_code == 200
    assert _decide(env, first["id"], action="confirm").status_code == 400
    ev, rows = _card(env)
    assert (rows["C7"]["review_state"], rows["C7"]["final_answer"], ev["final_total_score"]) == ("changed", "no", C7_NO_TOTAL)


def test_a_decided_appeal_still_answers_already_decided(env):
    ap = _file(env)
    assert _decide(env, ap["id"], action="confirm").status_code == 200
    r = _decide(env, ap["id"], action="change", new_answer="no", reason="Передумал")
    assert r.status_code == 400
    assert r.json()["error"] == {"code": "invalid_decision", "message": "По этой апелляции уже принято решение"}


# ------------------------------------------------------------------------------------------------ APPEALS-ROLES-2

def test_withdraw_restores_a_decision_carried_by_a_partial_re_evaluation(env):
    changed = _file(env)
    assert _decide(env, changed["id"], action="change", new_answer="no", reason="Не попрощался").status_code == 200
    confirmed = _file(env, code="C2", comment="Потребность выявлена")
    assert _decide(env, confirmed["id"], action="confirm").status_code == 200
    e2 = _partial(env, "chat-001", changed["evaluation_id"])
    ev, rows = _card(env)
    assert ev["id"] == e2 and ev["final_total_score"] == C7_NO_TOTAL
    assert (rows["C7"]["review_state"], rows["C7"]["appeals"], rows["C2"]["review_state"]) == ("changed", [], "confirmed")
    # appeal the carried rows of the new evaluation (the decided appeals stay on the base rows), then withdraw
    for code, state in (("C7", "changed"), ("C2", "confirmed")):
        ap = _file(env, row_id=rows[code]["id"], comment="Прошу пересмотреть")
        assert ap["review_state"] == "under_review"
        assert _withdraw(env, ap["id"])["review_state"] == state
    ev, rows = _card(env)
    assert (rows["C7"]["review_state"], rows["C7"]["final_answer"], rows["C2"]["review_state"]) == ("changed", "no", "confirmed")
    assert (ev["review_state"], ev["final_total_score"]) == ("reviewed", C7_NO_TOTAL)
    assert _results_criterion(env, "closure")["value"] is False  # results.json answers=final exports the reviewer's C7
    # the next partial re-evaluation still carries both decisions
    e3 = _partial(env, "chat-001", e2)
    ev, rows = _card(env)
    assert ev["id"] == e3 and ev["final_total_score"] == C7_NO_TOTAL
    assert (rows["C7"]["review_state"], rows["C7"]["final_answer"], rows["C2"]["review_state"]) == ("changed", "no", "confirmed")


# ------------------------------------------------------------------------------------------------ APPEALS-ROLES-4

def test_a_later_withdrawn_appeal_does_not_hide_the_decision(env):
    decided = _file(env)
    reason = "Оператор не попрощался с клиентом"
    assert _decide(env, decided["id"], action="change", new_answer="no", reason=reason).status_code == 200
    later = _file(env, comment="Прошу пересмотреть ещё раз")
    _withdraw(env, later["id"])
    _, rows = _card(env)
    c7 = rows["C7"]
    assert (c7["review_state"], c7["final_answer"]) == ("changed", "no")
    assert c7["appeal"]["id"] == decided["id"] and c7["appeal"]["decision"]["reason"] == reason
    assert [(a["id"], a["status"]) for a in c7["appeals"]] == [(decided["id"], "decided"), (later["id"], "withdrawn")]
    closure = _results_criterion(env, "closure")
    assert closure["value"] is False and reason in closure["rationale"]
    # an open appeal is the one the card shows
    reopened = _file(env, comment="Третья попытка")
    _, rows = _card(env)
    assert (rows["C7"]["appeal"]["id"], rows["C7"]["appeal"]["status"]) == (reopened["id"], "open")


# ------------------------------------------------------------------------------------------------ web app contract: texts

@pytest.mark.parametrize("comment", ["ok", "?", "  Нет  "])
def test_any_non_blank_comment_is_enough(env, comment):
    r = env.client.post("/api/appeals", json={"evaluation_criterion_id": env.rows["chat-001"]["C7"], "comment": comment, "author_label": "Аналитик"},
                        headers=ANALYST)  # the body of AppealDialog
    assert r.status_code == 200, r.text
    assert r.json()["comment"] == comment.strip()


def test_comment_length_limit_is_kept(env):
    r = env.client.post("/api/appeals", json={"evaluation_criterion_id": env.rows["chat-001"]["C7"], "comment": "я" * 4001}, headers=ANALYST)
    assert r.status_code == 422 and r.json()["error"]["code"] == "comment_too_long"
    assert _file(env, comment="я" * 4000)["status"] == "open"


def test_any_non_blank_reason_is_enough_for_a_change(env):
    ap = _file(env, code="C5")
    r = _decide(env, ap["id"], action="change", new_answer="no", reason=" \n ")
    assert r.status_code == 422 and r.json()["error"]["code"] == "reason_required"
    assert r.json()["error"]["errors"][0]["field"] == "reason"
    r = _decide(env, ap["id"], action="change", new_answer="no", reason="x")
    assert r.status_code == 200, r.text
    assert r.json()["appeal"]["decision"]["reason"] == "x"


# ------------------------------------------------------------------------------------------------ web app contract: null = N/A

@pytest.mark.parametrize("missing", [None, "", "  "])
def test_missing_new_answer_means_na_where_the_criterion_allows_it(env, missing):
    row = env.rows["chat-001"]["C7"]  # C7 allows N/A
    assert _preview(env, row, missing) == _preview(env, row, "N/A") == C7_NA_TOTAL
    ap = _file(env)
    r = env.client.post(f"/api/appeals/{ap['id']}/decision", headers=REVIEWER,  # the body of ReviewPage for the N/A chip
                        json={"action": "change", "new_answer": missing, "reason": "Клиент ушёл, завершать было некому",
                              "reviewer_label": "Ревьюер", "add_to_fewshot": True})
    assert r.status_code == 200, r.text
    assert r.json()["appeal"]["decision"]["new_answer"] == "N/A"
    c7 = next(c for c in r.json()["evaluation"]["criteria"] if c["code"] == "C7")
    assert (c7["final_answer"], c7["final_score"], c7["review_state"]) == ("N/A", None, "changed")
    assert r.json()["evaluation"]["final_total_score"] == C7_NA_TOTAL


def test_missing_new_answer_is_still_required_where_na_is_not_allowed(env):
    row = env.rows["chat-001"]["C5"]  # C5 never allows N/A
    assert _preview(env, row, None) == _preview(env, row, "") == AI_TOTAL  # the total without a decision on C5
    ap = _file(env, code="C5")
    for missing in (None, "", "  "):
        r = _decide(env, ap["id"], action="change", new_answer=missing, reason="Эмпатии не было")
        assert r.status_code == 422 and r.json()["error"]["code"] == "new_answer_required", r.text
        assert "недопустим" in r.json()["error"]["message"]
    r = _decide(env, ap["id"], action="change", new_answer="N/A", reason="Эмпатии не было")
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_decision"
    ev, rows = _card(env)
    assert (rows["C5"]["review_state"], ev["final_total_score"]) == ("under_review", AI_TOTAL)


def test_missing_new_answer_follows_the_channel_override_of_na(env):
    # C1 allows N/A only on voice calls (the greeting may have been said before the recording started)
    chat_row, call_row = env.rows["chat-001"]["C1"], env.rows["call-002"]["C1"]
    assert _preview(env, chat_row, None) == AI_TOTAL
    assert _preview(env, call_row, None) == _preview(env, call_row, "N/A") == C1_NA_TOTAL
    chat = _file(env, code="C1")
    call = _file(env, code="C1", conv="call-002")
    assert (chat["na_allowed_for_channel"], call["na_allowed_for_channel"]) == (False, True)
    r = _decide(env, chat["id"], action="change", new_answer=None, reason="Приветствие не записано")
    assert r.status_code == 422 and r.json()["error"]["code"] == "new_answer_required"
    r = _decide(env, call["id"], action="change", new_answer=None, reason="Приветствие не попало в запись")
    assert r.status_code == 200, r.text
    assert r.json()["appeal"]["decision"]["new_answer"] == "N/A"
    assert r.json()["evaluation"]["final_total_score"] == C1_NA_TOTAL
