"""Round 5 (registers): the gross-violations register header (WP-3) and the appeals queue (WP-4).

GET /api/violations keeps `{total, counts, types, items}` (after the type filter) and adds, in the same scope before the
type filter: `total_all` / `counts_all` (type chips keep their numbers while a type is selected), `evaluated` (latest
evaluations in scope, violation or not: «проверено 65»), `checklist_versions` [{id, version_no}] and `generated_at`
(UTC with offset); the new `conversation_id` param narrows the register to one dialog (the card's gross strip).
Appeal items add `is_critical` / `critical_value` of the criterion, and call evidence carries the current transcript's
`offset_seconds` (as on the card), the stored evidence untouched.

Review round: the header keys are GET /api/violations' own (`violations(..., with_header=True)`), so the operator
scorecard's nested `violations` keeps its four keys for every operator; `conversation_id` selects the dialog's latest
evaluation in SQL (the card does not load every evaluation); the appeals queue reads the offsets of all its calls in one
query of (dialog, turn, offset) instead of loading each call's turns.

In-memory SQLite (StaticPool) behind the real routers and the roles middleware; no network, no Claude, never api/data/cqa.db.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from cqa.analytics.violations import violations
from cqa.config import settings
from cqa.db import Base, get_db
from cqa.models import (
    Checklist,
    ChecklistVersion,
    Conversation,
    Criterion,
    Dataset,
    Evaluation,
    EvaluationCriterion,
    Turn,
)
from cqa.routers import appeals as appeals_router
from cqa.routers import insights as insights_router
from cqa.scoring.engine import CriterionInput, answer_points, compute_score
from cqa.security import middleware as security_mw

SEED = json.loads((Path(__file__).resolve().parents[1] / "seeds" / "checklist_freedom_v1.json").read_text(encoding="utf-8"))
ANSWERS = {"C1": "yes", "C2": "yes", "C3": "yes", "C4": 15, "C5": "yes", "C6": 7, "C7": "yes", "C8": "no"}
DONE = datetime(2026, 11, 7, 6, 0, tzinfo=UTC)
PAID_QUOTE = "Можете дать личный номер? Вечером за плату объясню."
# C8 explanation and flags per violation kind (classify() types them from the text); "none" is a C8 = Нет verdict
C8 = {
    "paid": (("Грубые ошибки — Да. Оператор попросил личный номер клиента, чтобы за плату дать частную консультацию в обход "
              "компании. Основание — реплика [3]."), ["paid_service_bypass"]),
    "insult": ("Грубые ошибки — Да. Оператор оскорбил клиента. Основание — реплика [3].", ["insult"]),
    "both": (("Грубые ошибки — Да. Оператор оскорбил клиента и предложил платную консультацию в обход компании. "
              "Основание — реплика [3]."), ["insult", "paid_service_bypass"]),
    "disclosure": ("Грубые ошибки — Да. Оператор раскрыл баланс счёта без подтверждения личности. Основание — реплика [3].",
                   ["disclosure_without_identification"]),
    "none": (("Грубые ошибки — Нет. Оператор не оскорблял клиента, не предлагал платных услуг в обход компании и не раскрывал "
              "данные счёта. Основание — реплики [1], [3]."), []),
}
# id, channel, language, operator, C8 kind, dataset
DIALOGS = [
    ("p1-ru-001", "agent chat", "RU", "Алина", "paid", "A"),
    ("p1-ru-002", "agent chat", "RU", "Алина", "insult", "A"),
    ("p1-ru-003", "agent chat", "RU", "Алексей", "both", "A"),
    ("p1-ru-004", "agent chat", "RU", "Алексей", "none", "A"),
    ("p1-kz-005", "agent chat", "KZ", "Айгерим", "disclosure", "B"),
    ("p4-ru-006", "voice call", "RU", "Нина", "paid", "B"),
    ("p4-ru-007", "voice call", "RU", "Нина", "none", "B"),
]
ALL_COUNTS = {"insult": 2, "paid_bypass": 3, "disclosure": 1, "other": 0}  # p1-ru-003 is both insult and paid_bypass
STORED_OFFSET = 60.0  # the call's C8 evidence as written at evaluation time (turn 3 at 60 s)
SHIFT = 30.0  # the call clock re-derived after the evaluation (cqa.ingest.renormalize): every turn 30 s later


# ------------------------------------------------------------------------------------------------ fixture

def _seed_version(db: Session, checklist: Checklist, version_no: int) -> ChecklistVersion:
    version = ChecklistVersion(checklist_id=checklist.id, version_no=version_no, is_active=version_no == 1, change_note=f"v{version_no}",
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


def _conversation(db: Session, cid: str, channel: str, language: str, operator: str, dataset_id: int) -> None:
    turns = [("client", "00:00", "Здравствуйте, где посмотреть тариф?"),
             ("operator", "00:20", f"Здравствуйте! Меня зовут {operator}. Сейчас подскажу."),
             ("client", "00:40", "Нет, не надо, это унизительно."),
             ("operator", "01:00", PAID_QUOTE)]
    db.add(Conversation(id=cid, channel=channel, language=language, operator_name=operator, turn_count=len(turns), dataset_id=dataset_id))
    db.flush()
    for idx, (speaker, ts, text) in enumerate(turns):
        db.add(Turn(conversation_id=cid, idx=idx, timestamp_raw=ts, offset_seconds=float(idx * 20), speaker=speaker, text=text,
                    segment="operator" if idx else "pre"))
    db.flush()


def _evaluate(db: Session, cid: str, version: ChecklistVersion, kind: str) -> dict[str, int]:
    crits = sorted(version.criteria, key=lambda c: c.order)
    answers = {**ANSWERS, "C8": "no" if kind == "none" else "yes"}
    items = [CriterionInput(c.code, c.weight, c.max_points, answer_points(c.options, answers[c.code]), True, c.section, c.is_critical,
                            bool(c.is_critical and answers[c.code] == c.critical_value)) for c in crits]
    res = compute_score(version.scoring_rules, items, version.sections)
    ev = Evaluation(conversation_id=cid, checklist_version_id=version.id, status="completed", provider="anthropic", model="claude-opus-5-5",
                    total_score=res.total, final_total_score=res.total, critical_error=res.critical, completed_at=DONE)
    db.add(ev)
    db.flush()
    rows = {"evaluation": ev.id}
    for c, item in zip(crits, items):
        cr = res.criteria[c.code]
        explanation, flags = f"{c.title} — обоснование. Основание — реплика [1].", []
        evidence = [{"turn_idx": 1, "speaker": "operator", "quote": "Сейчас подскажу.", "role": "supports", "offset_seconds": 20.0}]
        if c.code == "C8":
            explanation, flags = C8[kind]
            evidence = [{"turn_idx": 3, "speaker": "operator", "quote": PAID_QUOTE, "role": "supports", "offset_seconds": STORED_OFFSET},
                        {"turn_idx": 2, "speaker": "client", "quote": "Нет, не надо, это унизительно.", "role": "context"},
                        {"turn_idx": 99, "speaker": "operator", "quote": "реплика вне расшифровки", "role": "context"}]
        row = EvaluationCriterion(evaluation_id=ev.id, criterion_id=c.id, code=c.code, applicable=True, answer=answers[c.code],
                                  score=item.points, max_points=c.max_points, weight=c.weight, weight_applied=cr.weight_applied,
                                  earned=cr.earned, confidence=0.9, explanation=explanation, flags=flags, evidence=evidence)
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

    for module in (appeals_router, insights_router):
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
        checklist = Checklist(name=SEED["name"], description=SEED["description"])
        db.add(checklist)
        db.flush()
        v1 = _seed_version(db, checklist, 1)
        datasets = {}
        for group in ("A", "B"):
            ds = Dataset(group_id=group, record_count=0)
            db.add(ds)
            db.flush()
            datasets[group] = ds.id
        rows = {}
        for cid, channel, language, operator, kind, group in DIALOGS:
            _conversation(db, cid, channel, language, operator, datasets[group])
            rows[cid] = _evaluate(db, cid, v1, kind)
        v2 = _seed_version(db, checklist, 2)
        rows["p1-ru-004@v2"] = _evaluate(db, "p1-ru-004", v2, "none")  # re-evaluated on v2: its latest evaluation overall
        db.commit()
        ids = SimpleNamespace(v1=v1.id, v2=v2.id, datasets=datasets, rows=rows)
    monkeypatch.setattr(settings, "anthropic_api_key", None)
    monkeypatch.setattr(security_mw, "_default_session_factory", lambda: (_ for _ in ()).throw(AssertionError("real DB")))
    with TestClient(_app(SessionFactory)) as client:
        yield SimpleNamespace(client=client, Session=SessionFactory, ids=ids, engine=engine)
    engine.dispose()


def H(role: str) -> dict[str, str]:
    return {"X-CQA-Role": role}


def _register(env, **params) -> dict:
    r = env.client.get("/api/violations", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _counts_of(items: list[dict]) -> dict[str, int]:
    out = {"insult": 0, "paid_bypass": 0, "disclosure": 0, "other": 0}
    for item in items:
        for kind in item["types"]:
            out[kind] += 1
    return out


def _appeal(env, row_id: int, comment: str = "Не согласна с оценкой: прошу перепроверить") -> dict:
    r = env.client.post("/api/appeals", json={"evaluation_criterion_id": row_id, "comment": comment}, headers=H("analyst"))
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------------------------------------------ violations: header and chips

def test_type_chips_keep_their_numbers_while_a_type_is_selected(env):
    everything = _register(env)
    assert everything["total"] == everything["total_all"] == 5
    assert everything["counts"] == everything["counts_all"] == ALL_COUNTS
    insult = _register(env, type="insult")
    assert sorted(i["conversation_id"] for i in insult["items"]) == ["p1-ru-002", "p1-ru-003"]
    assert insult["total_all"] == 5 and insult["counts_all"] == ALL_COUNTS  # the chips «Все 5 · Оскорбление 2 · Платная 3 …»
    assert insult["total"] == 2 and insult["counts"] == {"insult": 2, "paid_bypass": 1, "disclosure": 0, "other": 0}  # as before
    nothing = _register(env, type="other")
    assert nothing["total"] == 0 and nothing["items"] == [] and nothing["total_all"] == 5 and nothing["counts_all"] == ALL_COUNTS


@pytest.mark.parametrize("kind", [None, "insult", "paid_bypass", "disclosure", "other", "no-such-type"])
def test_counts_and_total_still_describe_the_listed_items(env, kind):
    body = _register(env, **({"type": kind} if kind else {}))
    assert {"total", "counts", "types", "items"} <= set(body)
    assert body["total"] == len(body["items"]) and body["counts"] == _counts_of(body["items"])
    assert [t["code"] for t in body["types"]] == ["insult", "paid_bypass", "disclosure", "other"]
    assert body["counts_all"] == ALL_COUNTS and body["total_all"] == 5 and body["evaluated"] == 7


@pytest.mark.parametrize("params,evaluated,total_all,counts_all", [
    ({}, 7, 5, ALL_COUNTS),
    ({"channel": "voice call"}, 2, 1, {"insult": 0, "paid_bypass": 1, "disclosure": 0, "other": 0}),
    ({"channel": "voice call", "type": "insult"}, 2, 1, {"insult": 0, "paid_bypass": 1, "disclosure": 0, "other": 0}),
    ({"language": "KZ"}, 1, 1, {"insult": 0, "paid_bypass": 0, "disclosure": 1, "other": 0}),
    ({"language": "kz", "type": "paid_bypass"}, 1, 1, {"insult": 0, "paid_bypass": 0, "disclosure": 1, "other": 0}),
    ({"operator": "Алексей"}, 2, 1, {"insult": 1, "paid_bypass": 1, "disclosure": 0, "other": 0}),
    ({"since": "2099-01-01T00:00:00+05:00"}, 0, 0, {"insult": 0, "paid_bypass": 0, "disclosure": 0, "other": 0}),
])
def test_evaluated_counts_every_latest_evaluation_in_scope(env, params, evaluated, total_all, counts_all):
    """«В звонках грубых нарушений не найдено · проверено 2 звонка»: evaluated counts dialogs without a violation too, once
    per dialog (p1-ru-004 has two evaluations), in the register's scope and before the type filter."""
    body = _register(env, **params)
    assert (body["evaluated"], body["total_all"], body["counts_all"]) == (evaluated, total_all, counts_all)


def test_evaluated_follows_the_dataset_scope(env):
    body = _register(env, dataset_id=env.ids.datasets["B"])
    assert (body["evaluated"], body["total_all"]) == (3, 2)
    assert sorted(i["conversation_id"] for i in body["items"]) == ["p1-kz-005", "p4-ru-006"]
    assert _register(env, dataset_id=999)["evaluated"] == 0


def test_checklist_versions_of_the_evaluations_in_scope(env):
    v1, v2 = {"id": env.ids.v1, "version_no": 1}, {"id": env.ids.v2, "version_no": 2}
    assert _register(env)["checklist_versions"] == [v1, v2]  # p1-ru-004's latest evaluation is on v2
    assert _register(env, type="insult")["checklist_versions"] == [v1, v2]  # before the type filter, like evaluated
    assert _register(env, channel="voice call")["checklist_versions"] == [v1]
    only_v1 = _register(env, checklist_version_id=env.ids.v1)
    assert only_v1["checklist_versions"] == [v1] and only_v1["evaluated"] == 7  # p1-ru-004 counts with its v1 evaluation
    only_v2 = _register(env, checklist_version_id=env.ids.v2)
    assert only_v2["checklist_versions"] == [v2] and (only_v2["evaluated"], only_v2["total_all"], only_v2["total"]) == (1, 0, 0)
    assert _register(env, since="2099-01-01T00:00:00Z")["checklist_versions"] == []


def test_generated_at_is_utc_with_an_offset(env):
    before = datetime.now(UTC).replace(microsecond=0)
    stamp = _register(env)["generated_at"]
    after = datetime.now(UTC)
    parsed = datetime.fromisoformat(stamp)
    assert stamp.endswith("+00:00") and parsed.utcoffset() == timedelta(0)
    assert before <= parsed <= after


# ------------------------------------------------------------------------------------------------ violations: one dialog

def test_conversation_id_restricts_the_register_to_one_dialog(env):
    body = _register(env, conversation_id="p1-ru-003")
    assert [i["conversation_id"] for i in body["items"]] == ["p1-ru-003"] and body["items"][0]["types"] == ["insult", "paid_bypass"]
    assert (body["total"], body["total_all"], body["evaluated"]) == (1, 1, 1)
    assert body["counts"] == body["counts_all"] == {"insult": 1, "paid_bypass": 1, "disclosure": 0, "other": 0}
    filtered = _register(env, conversation_id="p1-ru-003", type="disclosure")
    assert (filtered["total"], filtered["total_all"], filtered["items"]) == (0, 1, [])


def test_conversation_id_without_a_violation_or_an_evaluation(env):
    clean = _register(env, conversation_id="p1-ru-004")  # evaluated, nothing gross: the card's strip says «проверено»
    assert (clean["total"], clean["total_all"], clean["evaluated"]) == (0, 0, 1)
    assert clean["checklist_versions"] == [{"id": env.ids.v2, "version_no": 2}]  # the evaluation the card shows
    on_v1 = _register(env, conversation_id="p1-ru-004", checklist_version_id=env.ids.v1)
    assert on_v1["evaluated"] == 1 and on_v1["checklist_versions"] == [{"id": env.ids.v1, "version_no": 1}]
    unknown = _register(env, conversation_id="no-such-dialog")
    assert (unknown["total"], unknown["total_all"], unknown["evaluated"], unknown["checklist_versions"]) == (0, 0, 0, [])
    assert _register(env, conversation_id="p4-ru-006", channel="agent chat")["evaluated"] == 0  # the other filters still apply
    assert _register(env, conversation_id="")["total_all"] == 5  # an empty value is no filter, like the other params


def test_reviewer_overturn_keeps_evaluated_and_follows_include_reviewed_out(env):
    ap = _appeal(env, env.ids.rows["p1-ru-002"]["C8"])
    r = env.client.post(f"/api/appeals/{ap['id']}/decision", headers=H("reviewer"),
                        json={"action": "change", "new_answer": "no", "reason": "Резкость без оскорбления: грубой ошибки нет",
                              "add_to_fewshot": False})
    assert r.status_code == 200, r.text
    after = _register(env)
    assert (after["evaluated"], after["total_all"], after["counts_all"]["insult"]) == (7, 4, 1)
    shown = _register(env, include_reviewed_out="true", type="insult")
    assert (shown["evaluated"], shown["total_all"], shown["counts_all"]["insult"], shown["total"]) == (7, 5, 2, 2)


def test_register_items_keep_their_fields(env):
    item = next(i for i in _register(env)["items"] if i["conversation_id"] == "p4-ru-006")
    assert set(item) == {"conversation_id", "channel", "language", "operator_name", "evaluation_id", "evaluation_criterion_id",
                         "total_score", "final_total_score", "types", "type_labels", "explanation", "confidence", "rules",
                         "classified_by", "criterion_code", "open_appeal_id", "decision", "related", "evidence", "review_state",
                         "ai_answer", "final_answer", "confirmed_by_reviewer", "overturned_by_reviewer", "evaluated_at"}
    assert item["explanation"].endswith("реплика [4].") and item["evidence"][0]["turn_idx"] == 3


# ------------------------------------------------------------------------------------------------ appeals: critical criterion

def test_appeal_items_flag_the_gross_error_criterion(env):
    c8 = _appeal(env, env.ids.rows["p1-ru-001"]["C8"])
    c6 = _appeal(env, env.ids.rows["p1-ru-001"]["C6"])
    assert (c8["code"], c8["is_critical"], c8["critical_value"]) == ("C8", True, "yes")
    assert (c6["code"], c6["is_critical"], c6["critical_value"]) == ("C6", False, None)
    listed = {a["id"]: a for a in env.client.get("/api/appeals", params={"status": "open"}).json()}
    assert (listed[c8["id"]]["is_critical"], listed[c8["id"]]["critical_value"]) == (True, "yes")
    assert (listed[c6["id"]]["is_critical"], listed[c6["id"]]["critical_value"]) == (False, None)


def test_critical_value_defaults_like_scoring_and_unknown_criteria(env):
    with env.Session() as db:  # a legacy checklist row: gross-error criterion without a stored value
        crit = db.execute(select(Criterion).where(Criterion.checklist_version_id == env.ids.v1, Criterion.code == "C8")).scalar_one()
        crit.critical_value = None
        ev_id = env.ids.rows["p1-ru-002"]["evaluation"]
        row = EvaluationCriterion(evaluation_id=ev_id, criterion_id=crit.id, code="X9", applicable=True, answer="yes", score=1,
                                  max_points=1, weight=0, weight_applied=0, earned=0, confidence=0.5, explanation="—", evidence=[])
        db.add(row)
        db.commit()
        orphan_id = row.id
    legacy = _appeal(env, env.ids.rows["p1-ru-002"]["C8"])
    assert (legacy["is_critical"], legacy["critical_value"]) == (True, "yes")  # "yes" by default, as the scoring engine reads it
    orphan = _appeal(env, orphan_id)  # no checklist definition for the code
    assert (orphan["title"], orphan["is_critical"], orphan["critical_value"]) == (None, False, None)


# ------------------------------------------------------------------------------------------------ appeals: call evidence

def _shift_turn_offsets(env, cid: str) -> None:
    with env.Session() as db:
        for t in db.execute(select(Turn).where(Turn.conversation_id == cid)).scalars():
            t.offset_seconds = t.offset_seconds + SHIFT
        db.commit()


def test_call_appeal_evidence_seeks_to_the_current_transcript(env):
    row_id = env.ids.rows["p4-ru-006"]["C8"]
    ap = _appeal(env, row_id)
    assert [e.get("offset_seconds") for e in ap["evidence"]] == [60.0, 40.0, None]  # turn 99 is not in the transcript
    _shift_turn_offsets(env, "p4-ru-006")
    got = env.client.get(f"/api/appeals/{ap['id']}").json()
    assert [e.get("offset_seconds") for e in got["evidence"]] == [60.0 + SHIFT, 40.0 + SHIFT, None]
    assert "offset_seconds" not in got["evidence"][2]  # nothing is added where the turn is unknown
    assert [{k: v for k, v in e.items() if k != "offset_seconds"} for e in got["evidence"]] == [
        {"turn_idx": 3, "speaker": "operator", "quote": PAID_QUOTE, "role": "supports"},
        {"turn_idx": 2, "speaker": "client", "quote": "Нет, не надо, это унизительно.", "role": "context"},
        {"turn_idx": 99, "speaker": "operator", "quote": "реплика вне расшифровки", "role": "context"}]  # only offsets change
    listed = next(a for a in env.client.get("/api/appeals", params={"conversation_id": "p4-ru-006"}).json() if a["id"] == ap["id"])
    assert listed["evidence"] == got["evidence"]
    with env.Session() as db:  # the stored evidence keeps the evaluation-time offset
        stored = db.get(EvaluationCriterion, row_id).evidence
    assert stored[0]["offset_seconds"] == STORED_OFFSET and "offset_seconds" not in stored[1]


def test_chat_appeal_evidence_is_the_stored_evidence(env):
    row_id = env.ids.rows["p1-ru-001"]["C8"]
    _shift_turn_offsets(env, "p1-ru-001")  # a chat's turn offsets never rewrite its evidence
    ap = _appeal(env, row_id)
    with env.Session() as db:
        stored = db.get(EvaluationCriterion, row_id).evidence
    assert ap["evidence"] == stored and ap["evidence"][0]["offset_seconds"] == STORED_OFFSET and "offset_seconds" not in ap["evidence"][1]


def test_every_appeal_response_carries_the_new_fields(env):
    first = _appeal(env, env.ids.rows["p4-ru-006"]["C8"])
    _shift_turn_offsets(env, "p4-ru-006")
    withdrawn = env.client.post(f"/api/appeals/{first['id']}/withdraw", json={}, headers=H("analyst"))
    assert withdrawn.status_code == 200, withdrawn.text
    second = _appeal(env, env.ids.rows["p4-ru-006"]["C8"])
    decided = env.client.post(f"/api/appeals/{second['id']}/decision", headers=H("reviewer"),
                              json={"action": "confirm", "reason": "Платная консультация в обход компании подтверждена",
                                    "add_to_fewshot": False})
    assert decided.status_code == 200, decided.text
    for body in (withdrawn.json(), second, decided.json()["appeal"], env.client.get(f"/api/appeals/{second['id']}").json()):
        assert (body["is_critical"], body["critical_value"]) == (True, "yes")
        assert body["evidence"][0]["offset_seconds"] == STORED_OFFSET + SHIFT


# ------------------------------------------------------------------------------------------------ review round: shape and cost

HEADER_KEYS = {"total_all", "counts_all", "evaluated", "checklist_versions", "generated_at"}


def _statements(env, request) -> tuple[list[str], Any]:
    """The SQL statements run while ``request()`` runs (the roles middleware's included), and its result."""
    seen: list[str] = []

    def listener(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(env.engine, "before_cursor_execute", listener)
    try:
        result = request()
    finally:
        event.remove(env.engine, "before_cursor_execute", listener)
    return seen, result


def _add_evaluation(env, cid: str, operator: str | None, kind: str, version_id: int | None = None) -> dict[str, int]:
    """A new completed evaluation of ``cid`` (a new RU chat in dataset A unless the dialog exists)."""
    with env.Session() as db:
        if db.get(Conversation, cid) is None:
            _conversation(db, cid, "agent chat", "RU", operator, env.ids.datasets["A"])
        rows = _evaluate(db, cid, db.get(ChecklistVersion, version_id or env.ids.v1), kind)
        db.commit()
    return rows


def _overturn(env, cid: str) -> None:
    ap = _appeal(env, env.ids.rows[cid]["C8"])
    r = env.client.post(f"/api/appeals/{ap['id']}/decision", headers=H("reviewer"),
                        json={"action": "change", "new_answer": "no", "reason": "Резкость без оскорбления: грубой ошибки нет",
                              "add_to_fewshot": False})
    assert r.status_code == 200, r.text


def test_operator_scorecard_keeps_the_four_violation_keys(env):
    """The register header belongs to GET /api/violations: the scorecard nests `violations` with the four keys it always had,
    for a named operator and for the unnamed «—» group alike (coaching builds the latter itself)."""
    _add_evaluation(env, "p1-ru-008", None, "insult")
    for name, total in (("Алина", 2), ("—", 1)):
        r = env.client.get(f"/api/operators/{quote(name)}")
        assert r.status_code == 200, r.text
        nested = r.json()["violations"]
        assert set(nested) == {"total", "counts", "types", "items"} and nested["total"] == total, name
    assert HEADER_KEYS <= set(_register(env))  # GET /api/violations itself carries the header
    with env.Session() as db:  # in-process callers (scorecard, overview, readiness, CSV export) get the four keys
        assert set(violations(db)) == {"total", "counts", "types", "items"}
        assert set(violations(db, with_header=True)) == {"total", "counts", "types", "items"} | HEADER_KEYS


@pytest.mark.parametrize("params", [{}, {"type": "insult"}, {"channel": "voice call"}, {"checklist_version_id": "v1"},
                                    {"checklist_version_id": "v2"}, {"include_reviewed_out": "true"},
                                    {"include_reviewed_out": "true", "type": "insult"}, {"operator": "Алексей"},
                                    {"since": "2099-01-01T00:00:00Z"}])
def test_one_dialog_registers_partition_the_whole_register(env, params):
    """conversation_id selects the dialog's latest evaluation in SQL by the register's own rule: dialog by dialog, the
    registers hold exactly the whole register's items, and their header figures add up to its own, in every scope."""
    _overturn(env, "p1-ru-002")  # include_reviewed_out makes a difference
    params = {k: getattr(env.ids, v) if k == "checklist_version_id" else v for k, v in params.items()}
    whole = _register(env, **params)
    parts = {cid: _register(env, conversation_id=cid, **params) for cid, *_ in DIALOGS}
    for cid, part in parts.items():
        assert part["items"] == [i for i in whole["items"] if i["conversation_id"] == cid], cid
        assert part["total"] == len(part["items"]) and 0 <= part["total_all"] <= part["evaluated"] <= 1, cid
    assert sum(p["evaluated"] for p in parts.values()) == whole["evaluated"]
    assert sum(p["total_all"] for p in parts.values()) == whole["total_all"]
    assert {k: sum(p["counts_all"][k] for p in parts.values()) for k in ALL_COUNTS} == whole["counts_all"]
    assert {k: sum(p["counts"][k] for p in parts.values()) for k in ALL_COUNTS} == whole["counts"]
    versions = {v["id"]: v for p in parts.values() for v in p["checklist_versions"]}
    assert [versions[k] for k in sorted(versions)] == whole["checklist_versions"]


def test_one_dialog_register_reads_only_that_dialog(env):
    """The card's strip costs the same however many dialogs are evaluated: only the dialog's latest evaluation and its
    conversation are loaded (p1-ru-004 has two evaluations), and the statement count does not grow with the data."""
    with env.Session() as db:
        rows: list[Any] = []  # every ORM object read from a row (the identity map holds them only weakly)
        event.listen(db, "loaded_as_persistent", lambda session, obj: rows.append(obj))
        violations(db, conversation_id="p1-ru-004", with_header=True)
        loaded = {(type(o).__name__, o.id) for o in rows if isinstance(o, (Conversation, Evaluation))}
    assert loaded == {("Conversation", "p1-ru-004"), ("Evaluation", env.ids.rows["p1-ru-004@v2"]["evaluation"])}
    _register(env, conversation_id="p1-ru-003")  # warm-up: the first request may read settings once
    before, _ = _statements(env, lambda: _register(env, conversation_id="p1-ru-003"))
    for n in range(12):
        _add_evaluation(env, f"p2-ru-{n:03d}", "Алина", "insult" if n % 2 else "none")
    after, body = _statements(env, lambda: _register(env, conversation_id="p1-ru-003"))
    assert len(after) == len(before) and (body["total"], body["evaluated"]) == (1, 1)
    assert _register(env)["evaluated"] == 7 + 12  # the whole register does see the new dialogs


def test_one_dialog_register_follows_the_latest_completed_evaluation(env):
    v1, v2 = {"id": env.ids.v1, "version_no": 1}, {"id": env.ids.v2, "version_no": 2}
    with env.Session() as db:  # newer runs that did not complete do not replace the latest completed evaluation
        db.add_all([Evaluation(conversation_id="p1-ru-003", checklist_version_id=env.ids.v2, status=status)
                    for status in ("failed", "running", "queued")])
        db.commit()
    body = _register(env, conversation_id="p1-ru-003")
    assert [i["evaluation_id"] for i in body["items"]] == [env.ids.rows["p1-ru-003"]["evaluation"]]
    assert body["checklist_versions"] == [v1]
    _add_evaluation(env, "p1-ru-003", "Алексей", "none", env.ids.v2)  # re-evaluated on v2: no gross error any more
    cleared = _register(env, conversation_id="p1-ru-003")
    assert (cleared["total"], cleared["total_all"], cleared["evaluated"], cleared["checklist_versions"]) == (0, 0, 1, [v2])
    on_v1 = _register(env, conversation_id="p1-ru-003", checklist_version_id=env.ids.v1)  # that version's own latest
    assert (on_v1["total"], on_v1["checklist_versions"]) == (1, [v1])
    assert "p1-ru-003" not in {i["conversation_id"] for i in _register(env)["items"]}  # as in the whole register


def test_appeals_queue_reads_every_calls_offsets_in_one_query(env):
    """GET /api/appeals reads the seek times of all calls in the queue with one query of (dialog, turn, offset): not a
    turns load per call, never the turns' text. Each call keeps its own current offsets; chats keep the stored evidence."""
    ids = {(cid, code): _appeal(env, env.ids.rows[cid][code])["id"]
           for cid in ("p4-ru-006", "p4-ru-007", "p1-ru-001") for code in ("C8", "C6")}
    _shift_turn_offsets(env, "p4-ru-007")
    seen, r = _statements(env, lambda: env.client.get("/api/appeals"))
    assert r.status_code == 200, r.text
    turns = [s for s in seen if "FROM turns" in s]
    assert len(turns) == 1 and "turns.text" not in turns[0], turns
    by_id = {a["id"]: a for a in r.json()}
    seek = {key: [e.get("offset_seconds") for e in by_id[aid]["evidence"]] for key, aid in ids.items()}
    assert seek[("p4-ru-006", "C8")] == [60.0, 40.0, None] and seek[("p4-ru-006", "C6")] == [20.0]
    assert seek[("p4-ru-007", "C8")] == [60.0 + SHIFT, 40.0 + SHIFT, None] and seek[("p4-ru-007", "C6")] == [20.0 + SHIFT]
    with env.Session() as db:
        for code in ("C8", "C6"):
            assert by_id[ids[("p1-ru-001", code)]]["evidence"] == db.get(EvaluationCriterion, env.ids.rows["p1-ru-001"][code]).evidence
    for key, aid in ids.items():  # the queue and the single appeal agree
        assert env.client.get(f"/api/appeals/{aid}").json()["evidence"] == by_id[aid]["evidence"], key


def test_single_appeal_reads_only_its_calls_offsets(env):
    chat = _appeal(env, env.ids.rows["p1-ru-001"]["C8"])
    for path in ("/api/appeals", f"/api/appeals/{chat['id']}"):  # chats only: their stored evidence is shown, nothing to read
        seen, r = _statements(env, lambda path=path: env.client.get(path))
        assert r.status_code == 200 and not [s for s in seen if "FROM turns" in s], path
    call = _appeal(env, env.ids.rows["p4-ru-006"]["C8"])
    seen, r = _statements(env, lambda: env.client.get(f"/api/appeals/{call['id']}"))
    turns = [s for s in seen if "FROM turns" in s]
    assert r.status_code == 200 and len(turns) == 1 and "turns.text" not in turns[0], turns
    assert r.json()["evidence"][0]["offset_seconds"] == 60.0
