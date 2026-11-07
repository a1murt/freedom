"""Round 3 (carry): a partial re-evaluation keeps a reviewer's decision on a copied criterion while a newer appeal on
that criterion is still open, and the open appeal follows the copied verdict into the new evaluation.

Before the fix only rows in the state "confirmed" / "changed" were carried: a criterion decided earlier and then
appealed again ("under_review") lost the decision in the new evaluation, whose card showed the AI answer and a total
without the reviewer's change. Now the decision is carried whenever the base row has one (final_answer is set); an
open appeal alone carries no answer.

Review of that fix: (1) the open appeal stayed on the base evaluation's row, so deciding it after the partial run
recomputed only the base evaluation and the card kept the decision the reviewer had just reversed; (2) the copied
state and final answer were read at different moments, so a decision committed in between gave a copy "confirmed"
with a final answer other than the AI answer. Now cqa.judge.runner.carry_reviews reads the base rows again in the
write transaction: an open appeal moves onto the copy (listed, decided and withdrawn there; the copy is
"under_review", the base row returns to the settled state of its decisions), review work done while the run was in
progress is carried as it is then, and a failed run leaves the appeal on the base row the card still shows.

Offline: in-memory SQLite (api/data/cqa.db is never opened), no API key; the partial re-evaluations re-judge one other
criterion (C6) with the offline "fake" judge.
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
from cqa.judge.contract import Verdict
from cqa.models import (Appeal, Checklist, ChecklistVersion, Conversation, Criterion, Evaluation, EvaluationCriterion, LlmTrace,
                        Turn)
from cqa.review import service as review
from cqa.routers import appeals as appeals_router
from cqa.routers import conversations as conversations_router
from cqa.scoring.engine import CriterionInput, answer_points, compute_score
from cqa.security import middleware as security_mw

SEED = json.loads((Path(__file__).resolve().parents[1] / "seeds" / "checklist_freedom_v1.json").read_text(encoding="utf-8"))
AI = {"C1": "yes", "C2": "yes", "C3": "yes", "C4": 15, "C5": "yes", "C6": 7, "C7": "yes", "C8": "no"}
CID = "chat-001"
COPIED = judge_runner.COPIED_FLAG
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
        db.add(Criterion(checklist_version_id=version.id, code=c["code"], order=i, section=c["section"], title=c["title"],
                         instruction=c["instruction"], answer_type=c["answer_type"], options=c["options"], max_points=c["max_points"],
                         weight=c["weight"], na_allowed=c["na_allowed"], na_condition=c["na_condition"], is_critical=c["is_critical"],
                         critical_value=c.get("critical_value"), channel_overrides=c["channel_overrides"]))
    db.flush()
    return version


def _conversation(db: Session) -> None:
    turns = [("client", "00:00", "Здравствуйте, не могу войти в приложение"),
             ("operator", "00:40", "Здравствуйте! Меня зовут Алина. Какую ошибку видите?"),
             ("client", "01:10", "Пишет «неверный пароль»"),
             ("operator", "01:40", "Нажмите «Забыли пароль», код придёт по SMS. Хорошего дня!")]
    db.add(Conversation(id=CID, channel="agent chat", language="RU", operator_name="Алина", turn_count=len(turns)))
    db.flush()
    for idx, (speaker, ts, text) in enumerate(turns):
        db.add(Turn(conversation_id=CID, idx=idx, timestamp_raw=ts, offset_seconds=float(idx * 20), speaker=speaker, text=text,
                    segment="operator" if idx else "pre"))
    db.flush()


def _inputs(version: ChecklistVersion, answers: dict) -> list[CriterionInput]:
    crits = sorted(version.criteria, key=lambda c: c.order)
    return [CriterionInput(c.code, c.weight, c.max_points, answer_points(c.options, answers[c.code]), True, c.section, c.is_critical,
                           bool(c.is_critical and answers[c.code] == c.critical_value)) for c in crits]


def _base_evaluation(db: Session, version: ChecklistVersion) -> tuple[int, dict[str, int]]:
    """A completed AI evaluation with the answers AI (total 90.5)."""
    items = _inputs(version, AI)
    res = compute_score(version.scoring_rules, items, version.sections)
    ev = Evaluation(conversation_id=CID, checklist_version_id=version.id, status="completed", provider="anthropic", model="claude-opus-5-5",
                    total_score=res.total, final_total_score=res.total, critical_error=res.critical, completed_at=datetime.now(UTC))
    db.add(ev)
    db.flush()
    rows = {}
    for c, item in zip(sorted(version.criteria, key=lambda c: c.order), items):
        cr = res.criteria[c.code]
        row = EvaluationCriterion(evaluation_id=ev.id, criterion_id=c.id, code=c.code, applicable=True, answer=AI[c.code], score=item.points,
                                  max_points=c.max_points, weight=c.weight, weight_applied=cr.weight_applied, earned=cr.earned, confidence=0.9,
                                  explanation=f"{c.title} — обоснование.", evidence=[{"turn_idx": 1, "speaker": "operator", "quote": "Здравствуйте!"}])
        db.add(row)
        db.flush()
        rows[c.code] = row.id
    return ev.id, rows


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
        _conversation(db)
        base_id, rows = _base_evaluation(db, version)
        db.commit()
        version_id = version.id
    monkeypatch.setattr(settings, "anthropic_api_key", None)
    monkeypatch.setattr(security_mw, "_default_session_factory", lambda: (_ for _ in ()).throw(AssertionError("real DB")))
    monkeypatch.setattr(judge_runner, "SessionLocal", SessionFactory)  # the re-evaluations run on the in-memory database
    with TestClient(_app(SessionFactory)) as client:
        yield SimpleNamespace(client=client, Session=SessionFactory, version_id=version_id, base_id=base_id, rows=rows)
    engine.dispose()


# ------------------------------------------------------------------------------------------------ helpers

def _file(env, row_id: int, comment: str) -> dict:
    r = env.client.post("/api/appeals", json={"evaluation_criterion_id": row_id, "comment": comment}, headers=ANALYST)
    assert r.status_code == 200, r.text
    return r.json()


def _decide(env, appeal_id: int, **body) -> dict:
    r = env.client.post(f"/api/appeals/{appeal_id}/decision", json=body, headers=REVIEWER)
    assert r.status_code == 200, r.text
    return r.json()


def _partial(env, base_evaluation_id: int, only: set[str]) -> int:
    """A partial re-evaluation on the same checklist version: the criteria in `only` are judged again by the fake
    judge, every other row is copied from the base evaluation (cqa.judge.runner)."""
    return asyncio.run(judge_runner.evaluate_conversation(CID, env.version_id, provider_name="fake", mode="partial",
                                                          base_evaluation_id=base_evaluation_id, only_codes=only))


def _card(env, evaluation_id: int | None = None) -> tuple[dict, dict[str, dict]]:
    url = f"/api/evaluations/{evaluation_id}" if evaluation_id else f"/api/conversations/{CID}/evaluation"
    r = env.client.get(url)
    assert r.status_code == 200, r.text
    ev = r.json()
    return ev, {c["code"]: c for c in ev["criteria"]}


def _row(env, evaluation_id: int, code: str) -> EvaluationCriterion:
    with env.Session() as db:
        return db.execute(select(EvaluationCriterion).where(EvaluationCriterion.evaluation_id == evaluation_id,
                                                            EvaluationCriterion.code == code)).scalar_one()


def _total(env, answers: dict) -> float:
    with env.Session() as db:
        version = db.get(ChecklistVersion, env.version_id)
        return compute_score(version.scoring_rules, _inputs(version, answers), version.sections).total


def _points(env, code: str, answer) -> float | None:
    with env.Session() as db:
        c = db.execute(select(Criterion).where(Criterion.checklist_version_id == env.version_id, Criterion.code == code)).scalar_one()
        return answer_points(c.options, answer)


def _assert_rejudged(env, evaluation_id: int, rows: dict[str, dict], code: str = "C6") -> None:
    """`code` was judged again by the fake judge (not copied); every other row was copied."""
    assert COPIED not in rows[code]["flags"] and "fake_judge" in rows[code]["flags"], rows[code]["flags"]
    assert all(COPIED in r["flags"] for c, r in rows.items() if c != code)
    with env.Session() as db:
        traced = {t.criterion_code for t in db.execute(select(LlmTrace).where(LlmTrace.evaluation_id == evaluation_id)).scalars()}
    assert traced == {code}


# ------------------------------------------------------------------------------------------------ the defect

@pytest.mark.parametrize("action,new_answer,state", [("change", "no", "changed"), ("confirm", None, "confirmed")])
def test_a_decision_under_a_newer_open_appeal_is_carried(env, action, new_answer, state):
    c7 = env.rows["C7"]
    first = _file(env, c7, "Оператор не попрощался с клиентом")
    _decide(env, first["id"], action=action, reason="Прощания не было", **({"new_answer": new_answer} if new_answer else {}))
    final = new_answer or AI["C7"]
    reopened = _file(env, c7, "Прошу пересмотреть: «Хорошего дня!» — это прощание")
    assert (reopened["review_state"], reopened["final_answer"]) == ("under_review", final)

    e2 = _partial(env, env.base_id, {"C6"})  # e.g. a checklist edit that touched C6 only: C7 is copied
    ev, rows = _card(env)
    assert (ev["id"], ev["mode"], ev["status"]) == (e2, "partial", "completed")
    _assert_rejudged(env, e2, rows)
    ai_total = _total(env, {**AI, "C6": rows["C6"]["answer"]})
    final_total = _total(env, {**AI, "C6": rows["C6"]["answer"], "C7": final})
    if action == "change":
        assert final_total < ai_total  # the reviewer's C7 = no costs its weight

    # the copied C7 keeps the reviewer's decision, and the open appeal moved onto it with the copied verdict
    row = _row(env, e2, "C7")
    assert (row.answer, row.final_answer, row.final_score, row.review_state) == ("yes", final, _points(env, "C7", final), "under_review")
    with env.Session() as db:
        stored = db.get(Evaluation, e2)
        assert (stored.total_score, stored.final_total_score) == (ai_total, final_total)
    c7_card = rows["C7"]
    assert (c7_card["final_answer"], c7_card["final_score"], c7_card["review_state"]) == (final, _points(env, "C7", final), "under_review")
    assert (c7_card["appeal"]["id"], c7_card["appeal"]["status"]) == (reopened["id"], "open")
    assert (ev["total_score"], ev["final_total_score"], ev["review_state"]) == (ai_total, final_total, "under_review")
    listed = env.client.get("/api/appeals", params={"status": "open"}).json()
    assert [(a["id"], a["evaluation_criterion_id"], a["evaluation_id"], a["is_latest_evaluation"]) for a in listed] == [
        (reopened["id"], row.id, e2, True)]
    under_review = env.client.get("/api/conversations", params={"status": "under_review"}).json()["items"]
    assert [c["id"] for c in under_review] == [CID]  # the dialog stays in the «На ревью» list

    # the base row keeps its decided appeal and returns to the state of that decision
    base_c7 = _row(env, env.base_id, "C7")
    assert (base_c7.review_state, base_c7.final_answer) == (state, final)
    _, base_rows = _card(env, env.base_id)
    assert [(a["id"], a["status"]) for a in base_rows["C7"]["appeals"]] == [(first["id"], "decided")]


def test_an_open_appeal_without_a_decision_carries_no_answer(env):
    reopened = _file(env, env.rows["C7"], "Не согласна с оценкой")
    assert reopened["review_state"] == "under_review"
    e2 = _partial(env, env.base_id, {"C6"})
    ev, rows = _card(env)
    assert ev["id"] == e2
    _assert_rejudged(env, e2, rows)
    row = _row(env, e2, "C7")
    assert (row.final_answer, row.final_score, row.review_state) == (None, None, "under_review")
    assert (rows["C7"]["final_answer"], rows["C7"]["appeal"]["id"]) == ("yes", reopened["id"])  # the AI answer, appealed
    assert ev["final_total_score"] == ev["total_score"] == _total(env, {**AI, "C6": rows["C6"]["answer"]})
    assert ev["review_state"] == "under_review"
    assert (_row(env, env.base_id, "C7").review_state, _row(env, env.base_id, "C7").final_answer) == ("none", None)
    # decided after the partial run: the new evaluation's total follows the decision
    out = _decide(env, reopened["id"], action="change", new_answer="no", reason="Прощания не было")
    assert out["evaluation"]["id"] == e2
    ev, rows = _card(env)
    assert (rows["C7"]["final_answer"], rows["C7"]["review_state"]) == ("no", "changed")
    assert ev["final_total_score"] == _total(env, {**AI, "C6": rows["C6"]["answer"], "C7": "no"}) < ev["total_score"]


def test_a_carried_decision_under_a_newer_open_appeal_is_carried_again(env):
    """The decided appeal stays on the first evaluation's row; the carried row of the second evaluation is appealed
    again, and the third evaluation still carries the decision and takes the open appeal, while the second
    evaluation's row returns to its carried decision (settled state from the final answer: "changed")."""
    first = _file(env, env.rows["C7"], "Оператор не попрощался с клиентом")
    _decide(env, first["id"], action="change", new_answer="no", reason="Прощания не было")
    e2 = _partial(env, env.base_id, set())  # judges nothing: the decision is carried onto e2's C7
    e2_c7 = _row(env, e2, "C7")
    assert (e2_c7.final_answer, e2_c7.review_state) == ("no", "changed")
    reopened = _file(env, e2_c7.id, "Прошу пересмотреть ещё раз")
    assert reopened["review_state"] == "under_review"

    e3 = _partial(env, e2, {"C6"})
    ev, rows = _card(env)
    assert ev["id"] == e3
    _assert_rejudged(env, e3, rows)
    row = _row(env, e3, "C7")
    assert (row.final_answer, row.final_score, row.review_state) == ("no", _points(env, "C7", "no"), "under_review")
    assert ev["final_total_score"] == _total(env, {**AI, "C6": rows["C6"]["answer"], "C7": "no"}) < ev["total_score"]
    assert (_row(env, e2, "C7").final_answer, _row(env, e2, "C7").review_state) == ("no", "changed")
    # the open appeal is withdrawn on e3's row, which returns to its carried decision
    r = env.client.post(f"/api/appeals/{reopened['id']}/withdraw", headers=ANALYST)
    assert r.status_code == 200, r.text
    assert (r.json()["evaluation_id"], r.json()["review_state"]) == (e3, "changed")


# ------------------------------------------------------------------------------------------------ review of the fix

FIRST = {"change": {"action": "change", "new_answer": "no"}, "confirm": {"action": "confirm"}}
SETTLED = {"change": "changed", "confirm": "confirmed"}


@pytest.mark.parametrize("first,then,expected", [
    ("change", {"action": "confirm"}, ("yes", "confirmed")),  # the re-appeal is upheld: the AI answer stands
    ("confirm", {"action": "change", "new_answer": "no"}, ("no", "changed")),
    ("change", None, ("no", "changed")),  # withdrawn: the carried decision stays
])
def test_the_open_appeal_is_decided_on_the_new_evaluation(env, first, then, expected):
    """Finding 1: a decision on the re-appeal taken after the partial run reaches the card. Before, the appeal stayed
    on the base row, only the base evaluation was recomputed, and the card kept the decision just reversed."""
    c7 = env.rows["C7"]
    decided = _file(env, c7, "Оператор не попрощался с клиентом")
    _decide(env, decided["id"], reason="Прощания не было", **FIRST[first])
    first_answer = FIRST[first].get("new_answer", AI["C7"])
    reopened = _file(env, c7, "Прошу пересмотреть: «Хорошего дня!» — это прощание")
    e2 = _partial(env, env.base_id, {"C6"})
    if then is None:
        r = env.client.post(f"/api/appeals/{reopened['id']}/withdraw", headers=ANALYST)
        assert r.status_code == 200, r.text
        acted_on = (r.json()["status"], r.json()["evaluation_id"])
    else:
        out = _decide(env, reopened["id"], reason="Пересмотрено по повторной апелляции", **then)
        acted_on = (out["appeal"]["status"], out["evaluation"]["id"])

    answer, state = expected
    ev, rows = _card(env)  # the card shows the new evaluation, with the decision just taken
    assert ev["id"] == e2
    assert (rows["C7"]["final_answer"], rows["C7"]["final_score"], rows["C7"]["review_state"]) == (answer, _points(env, "C7", answer), state)
    final_total = _total(env, {**AI, "C6": rows["C6"]["answer"], "C7": answer})
    assert (ev["final_total_score"], ev["review_state"]) == (final_total, "reviewed")
    items = env.client.get("/api/conversations").json()["items"]  # the dialog list shows the same total
    assert [(c["id"], c["latest_evaluation"]["final_total_score"], c["review_state"]) for c in items] == [(CID, final_total, "reviewed")]
    assert acted_on == ("withdrawn" if then is None else "decided", e2)
    # the base evaluation keeps the first decision
    base_c7 = _row(env, env.base_id, "C7")
    assert (base_c7.final_answer, base_c7.review_state) == (first_answer, SETTLED[first])
    assert _card(env, env.base_id)[0]["final_total_score"] == _total(env, {**AI, "C7": first_answer})


def _meanwhile(env, action: str, appeal_id: int | None) -> None:
    """Review work another request commits while the partial run is in progress."""
    with env.Session() as db:
        if action == "confirm":
            review.decide(db, db.get(Appeal, appeal_id), action="confirm", new_answer=None, reason="Прощание было",
                          reviewer_label="Reviewer", add_to_fewshot=False)
        elif action == "withdraw":
            review.withdraw_appeal(db, db.get(Appeal, appeal_id))
        else:  # a new appeal on the base row
            review.create_appeal(db, db.get(EvaluationCriterion, env.rows["C7"]), "Прошу пересмотреть", "QA analyst")


@pytest.mark.parametrize("hook", ["reference_facts_for", "judge_criterion"])  # while the run is prepared / judged
@pytest.mark.parametrize("action,expected", [
    ("confirm", ("yes", "confirmed", "base")),  # the re-appeal upheld meanwhile: the new decision is carried
    ("withdraw", ("no", "changed", "base")),  # withdrawn meanwhile: the earlier decision; the appeal stays withdrawn
    ("appeal", ("no", "under_review", "copy")),  # appealed meanwhile: the new open appeal moves onto the copy
])
def test_review_work_done_while_the_partial_run_is_in_progress_is_carried(env, monkeypatch, hook, action, expected):
    """Finding 2: the copied decision and its review state come from one read, made when the results are written.
    Before, the state could come from a decision committed after the final answer was read (a copy "confirmed" with a
    final answer other than the AI answer), and a decision taken while the judges ran never reached the new card."""
    c7 = env.rows["C7"]
    decided = _file(env, c7, "Оператор не попрощался с клиентом")
    _decide(env, decided["id"], action="change", new_answer="no", reason="Прощания не было")
    reopened = _file(env, c7, "Прошу пересмотреть") if action != "appeal" else None
    real, done = getattr(judge_runner, hook), []

    def once() -> None:
        if not done:
            done.append(True)
            _meanwhile(env, action, reopened["id"] if reopened else None)

    def facts(*args, **kwargs):
        once()
        return real(*args, **kwargs)

    async def judge(*args, **kwargs):
        once()
        return await real(*args, **kwargs)

    monkeypatch.setattr(judge_runner, hook, facts if hook == "reference_facts_for" else judge)
    e2 = _partial(env, env.base_id, {"C6"})
    assert done == [True]

    final_answer, state, appeal_on = expected
    row = _row(env, e2, "C7")
    assert (row.answer, row.final_answer, row.final_score, row.review_state) == ("yes", final_answer, _points(env, "C7", final_answer), state)
    ev, rows = _card(env)
    assert ev["id"] == e2 and ev["final_total_score"] == _total(env, {**AI, "C6": rows["C6"]["answer"], "C7": final_answer})
    base = _row(env, env.base_id, "C7")
    assert (base.final_answer, base.review_state) == (final_answer, "confirmed" if action == "confirm" else "changed")
    with env.Session() as db:
        latest = db.execute(select(Appeal).order_by(Appeal.id.desc())).scalars().first()
        assert (latest.status, latest.evaluation_criterion_id) == (
            {"confirm": "decided", "withdraw": "withdrawn", "appeal": "open"}[action], row.id if appeal_on == "copy" else c7)


def test_a_failed_partial_run_leaves_the_open_appeal_on_the_base_row(env, monkeypatch):
    """Every judged criterion failed: the card keeps showing the base evaluation, so the open appeal stays there and
    is decided there; the failed run's copy carries the decision in its settled state."""
    c7 = env.rows["C7"]
    decided = _file(env, c7, "Оператор не попрощался с клиентом")
    _decide(env, decided["id"], action="change", new_answer="no", reason="Прощания не было")
    reopened = _file(env, c7, "Прошу пересмотреть")

    async def failing(members, conv, crit, **kwargs):
        verdict = Verdict(criterion_code=crit.code, applicable=True, answer="", confidence=0, explanation="")
        return judge_runner.CriterionOutcome(code=crit.code, verdict=verdict, error="провайдер недоступен")

    monkeypatch.setattr(judge_runner, "judge_criterion", failing)
    e2 = _partial(env, env.base_id, {"C6"})
    with env.Session() as db:
        assert db.get(Evaluation, e2).status == "failed"
    row = _row(env, e2, "C7")
    assert (row.final_answer, row.review_state) == ("no", "changed")
    ev, rows = _card(env)
    assert ev["id"] == env.base_id
    assert (rows["C7"]["review_state"], rows["C7"]["appeal"]["id"], rows["C7"]["appeal"]["status"]) == ("under_review", reopened["id"], "open")
    out = _decide(env, reopened["id"], action="confirm", reason="Прощание было")
    assert out["evaluation"]["id"] == env.base_id
    ev, rows = _card(env)
    assert (rows["C7"]["final_answer"], rows["C7"]["review_state"]) == ("yes", "confirmed")
    assert ev["final_total_score"] == ev["total_score"] == _total(env, AI)
