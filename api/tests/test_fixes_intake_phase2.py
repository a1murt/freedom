"""Regression tests for the intake findings of review phase 2 (INTAKE-1..7) and the card / upload compatibility fixes.

- INTAKE-1: GET /api/conversations?status=under_review lists only dialogs with an open appeal.
- INTAKE-2: answer=lost follows the final (post-review) answer, also after a decision to or from N/A.
- INTAKE-3: a lead's manual operator attribution never reaches the judge as "operator_name_detected".
- INTAKE-4: the Dynamics 365 push follows the re-import rules (manual attribution kept, transcript_changed_at, 409 busy).
- INTAKE-5: with checklist_version_id the list never shows another version's evaluation.
- INTAKE-6: a criterion whose judge call failed matches no answer (in particular not N/A).
- INTAKE-7: a conversation_id repeated in one upload is a re-import of the earlier copy, never HTTP 500.
- Card: the completed result is shown when the newest attempt failed (last_attempt_failed / last_attempt).
- Upload: a .json with exactly one record also returns its conversation "id" (web UploadDialog navigates to it).

In-memory SQLite (StaticPool) behind the real routers; no network, no Claude, never api/data/cqa.db. The evaluation job
starter is replaced, so no worker thread starts.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.exceptions import HTTPException as StarletteHTTPException

import cqa.routers.integrations as integ
from cqa.config import settings
from cqa.db import Base, get_db
from cqa.ingest import service as ingest
from cqa.jobs import runner as jobs_runner
from cqa.judge import packer
from cqa.judge import runner as judge_runner
from cqa.models import (
    Appeal,
    Checklist,
    ChecklistVersion,
    Conversation,
    Criterion,
    Evaluation,
    EvaluationCriterion,
    Turn,
)
from cqa.routers import conversations as conversations_router
from cqa.scoring.engine import CriterionInput, answer_points, compute_score

SEED = json.loads((Path(__file__).resolve().parents[1] / "seeds" / "checklist_freedom_v1.json").read_text(encoding="utf-8"))
ALL_YES = {"C1": "yes", "C2": "yes", "C3": "yes", "C4": 20, "C5": "yes", "C6": 10, "C7": "yes", "C8": "no"}
URL = "/api/integrations/dynamics/transcripts"


# ------------------------------------------------------------------------------------------------ data helpers

def _seed_versions(db: Session) -> tuple[ChecklistVersion, ChecklistVersion]:
    """The organizer checklist as v1 (active) and an identical draft v2."""
    checklist = Checklist(name=SEED["name"], description=SEED["description"])
    db.add(checklist)
    db.flush()
    versions = []
    for no in (1, 2):
        version = ChecklistVersion(checklist_id=checklist.id, version_no=no, is_active=no == 1, change_note=f"v{no}",
                                   scoring_rules=SEED["scoring_rules"], sections=SEED["sections"])
        db.add(version)
        db.flush()
        for i, c in enumerate(SEED["criteria"], start=1):
            db.add(Criterion(checklist_version_id=version.id, code=c["code"], order=i, section=c["section"], title=c["title"],
                             instruction=c["instruction"], answer_type=c["answer_type"], options=c["options"], max_points=c["max_points"],
                             weight=c["weight"], na_allowed=c["na_allowed"], na_condition=c["na_condition"], is_critical=c["is_critical"],
                             critical_value=c.get("critical_value"), channel_overrides=c["channel_overrides"]))
        db.flush()
        versions.append(version)
    return versions[0], versions[1]


def _conversation(db: Session, cid: str, operator: str | None = "Алина") -> None:
    turns = [("client", "00:00", "Здравствуйте, не могу войти в приложение"),
             ("operator", "00:40", f"Здравствуйте! Меня зовут {operator or 'оператор'}. Какую ошибку видите?"),
             ("client", "01:10", "Пишет «неверный пароль»"),
             ("operator", "01:40", "Нажмите «Забыли пароль», код придёт по SMS. Хорошего дня!")]
    db.add(Conversation(id=cid, channel="agent chat", language="RU", operator_name=operator, turn_count=len(turns), meta={}))
    db.flush()
    for idx, (speaker, ts, text) in enumerate(turns):
        db.add(Turn(conversation_id=cid, idx=idx, timestamp_raw=ts, offset_seconds=float(idx * 20), speaker=speaker, text=text,
                    segment="operator" if idx else "pre"))
    db.flush()


def _evaluate(db: Session, cid: str, version: ChecklistVersion, answers: dict[str, Any] | None = None, *, status: str = "completed",
              error: str | None = None, total: float | None = None) -> Evaluation:
    """An evaluation scored by the real engine. An answer "N/A" is a not-applicable row, None a failed judge call
    (applicable, no answer, flag "error"), as cqa.judge.runner stores them."""
    answers = {**ALL_YES, **(answers or {})}
    crits = sorted(db.execute(select(Criterion).where(Criterion.checklist_version_id == version.id)).scalars(), key=lambda c: c.order)
    items = []
    for c in crits:
        a = answers[c.code]
        pts = None if a in ("N/A", None) else answer_points(c.options, a)
        items.append(CriterionInput(c.code, c.weight, c.max_points, pts, a != "N/A" and a is not None, c.section, c.is_critical,
                                    bool(c.is_critical and a == c.critical_value)))
    res = compute_score(version.scoring_rules, items, version.sections)
    score = res.total if total is None else total
    ev = Evaluation(conversation_id=cid, checklist_version_id=version.id, status=status, provider="fake", model="fake:offline",
                    total_score=score, final_total_score=score, critical_error=res.critical, error=error, cost_usd=0.0,
                    completed_at=datetime.now(UTC) if status == "completed" else None)
    db.add(ev)
    db.flush()
    for c, item in zip(crits, items):
        a = answers[c.code]
        cr = res.criteria[c.code]
        db.add(EvaluationCriterion(evaluation_id=ev.id, criterion_id=c.id, code=c.code, applicable=a != "N/A",
                                   answer=None if a in ("N/A", None) else a, score=item.points, max_points=c.max_points, weight=c.weight,
                                   weight_applied=cr.weight_applied, earned=cr.earned, confidence=0.9,
                                   explanation="Ошибка оценки: timeout" if a is None else f"{c.title} — обоснование.",
                                   flags=["error"] if a is None else [], evidence=[]))
    db.flush()
    return ev


def _row(db: Session, ev_id: int, code: str) -> EvaluationCriterion:
    return db.execute(select(EvaluationCriterion).where(EvaluationCriterion.evaluation_id == ev_id,
                                                        EvaluationCriterion.code == code)).scalar_one()


def _record(cid: str, last: str = "Спасибо, до свидания!", operator_line: str = "Здравствуйте! Меня зовут Дана. В приложении: Меню → Отчёты.",
            language: str = "RU") -> dict[str, Any]:
    return {"conversation_id": cid, "channel": "agent chat", "language": language, "audio_url": None, "transcript": [
        {"timestamp": "00:00", "speaker": "client", "text": "Здравствуйте, где отчёт брокера?"},
        {"timestamp": "00:30", "speaker": "operator", "text": operator_line},
        {"timestamp": "01:00", "speaker": "client", "text": last}]}


# ------------------------------------------------------------------------------------------------ D365 messages

T0 = datetime(2026, 11, 7, 9, 0, tzinfo=UTC)
AGENT = {"displayName": "Алина Сапарова", "id": "5f1c9b7e-0000-4000-8000-00000000a001"}
CUSTOMER = {"displayName": "Customer", "id": "0c2d4e6f-0000-4000-8000-00000000c001"}


def _msg(sec: float, content: str, *, user: dict, tags: str) -> dict[str, Any]:
    at = T0 + timedelta(seconds=sec)
    return {"content": content, "contentType": "text", "createdDateTime": at.isoformat(timespec="milliseconds"), "from": {"user": user},
            "attachments": [], "id": str(int(at.timestamp() * 1000)), "deleted": None, "tags": tags}


def _chat(greeting: str, *, extra: bool = False) -> list[dict[str, Any]]:
    messages = [_msg(0, "Здравствуйте, не пришли деньги", user=CUSTOMER, tags="FromCustomer"),
                _msg(30, greeting, user=AGENT, tags="public"),
                _msg(90, "Деньги придут сегодня. Хорошего дня!", user=AGENT, tags="public")]
    if extra:
        messages.append(_msg(120, "А комиссия какая?", user=CUSTOMER, tags="FromCustomer"))
    return messages


NAMELESS = "Здравствуйте! Проверю, минуту."
NAMED = "Здравствуйте! Меня зовут Алина. Проверю, минуту."


# ------------------------------------------------------------------------------------------------ app fixture

def _build_app(factory: sessionmaker) -> FastAPI:
    app = FastAPI()

    @app.exception_handler(StarletteHTTPException)  # same envelope and headers as cqa.main
    async def envelope(request, exc: StarletteHTTPException):
        detail = exc.detail if isinstance(exc.detail, dict) else {"code": "error", "message": str(exc.detail)}
        return JSONResponse(status_code=exc.status_code, content={"error": detail}, headers=getattr(exc, "headers", None))

    app.include_router(conversations_router.router)
    app.include_router(integ.router)

    def override_get_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    return app


@pytest.fixture
def env(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as db:
        v1, v2 = _seed_versions(db)
        ids = {"v1": v1.id, "v2": v2.id}
        db.commit()
    started: list[list[str]] = []

    def fake_start(conversation_ids, checklist_version_id, **kwargs):  # never a real worker
        started.append(list(conversation_ids))
        return f"j_fake{len(started):02d}"

    monkeypatch.setattr(jobs_runner, "start_evaluation_job", fake_start)
    monkeypatch.setattr(settings, "anthropic_api_key", None)
    for var in ("WEB_BASE_URL", "PUBLIC_API_URL", integ.INTEGRATION_KEY_ENV):
        monkeypatch.delenv(var, raising=False)
    for name in ("web_base_url", "public_api_url", "integrations_api_key"):
        if name in type(settings).model_fields:
            monkeypatch.setattr(settings, name, None)
    with TestClient(_build_app(factory)) as client:
        yield SimpleNamespace(client=client, Session=factory, ids=ids, started=started)
    engine.dispose()


def _version(db: Session, env, key: str) -> ChecklistVersion:
    return db.get(ChecklistVersion, env.ids[key])


def _ids(env, **params) -> list[str]:
    return [i["id"] for i in env.client.get("/api/conversations", params=params).json()["items"]]


# ------------------------------------------------------------------------------------------------ INTAKE-1

def test_intake1_status_under_review_lists_only_dialogs_with_an_open_appeal(env):
    with env.Session() as db:
        v1 = _version(db, env, "v1")
        evs = {}
        for cid in ("a", "b", "c"):
            _conversation(db, cid)
            evs[cid] = _evaluate(db, cid, v1)
        row = _row(db, evs["b"].id, "C7")
        row.review_state = "under_review"  # what cqa.review.service.create_appeal does
        db.add(Appeal(evaluation_criterion_id=row.id, comment="Итог был", author_label="QA analyst"))
        db.commit()
    body = env.client.get("/api/conversations", params={"status": "under_review"}).json()
    assert body["total"] == 1 and [i["id"] for i in body["items"]] == ["b"]
    assert body["items"][0]["review_state"] == "under_review"
    assert env.client.get("/api/conversations", params={"status": "under_review", "page_size": 1}).json()["total"] == 1
    assert env.client.get("/api/conversations").json()["total"] == 3  # without the filter: every dialog


# ------------------------------------------------------------------------------------------------ INTAKE-2

def test_intake2_answer_lost_follows_the_final_answer(env):
    with env.Session() as db:
        v1 = _version(db, env, "v1")
        for cid in ("to-na", "from-na", "scale-15", "all-yes", "confirmed"):
            _conversation(db, cid)
        to_na = _evaluate(db, "to-na", v1)  # AI C2 = yes, the reviewer changed it to N/A
        row = _row(db, to_na.id, "C2")
        row.final_answer, row.final_score, row.final_earned, row.review_state = "N/A", None, 0.0, "changed"
        assert row.weight_applied > 0  # the AI-time weight stays on the row
        from_na = _evaluate(db, "from-na", v1, {"C2": "N/A"})  # AI C2 = N/A, the reviewer changed it to "no"
        row = _row(db, from_na.id, "C2")
        assert row.weight_applied == 0
        row.final_answer, row.final_score, row.final_earned, row.review_state = "no", 0.0, 0.0, "changed"
        _evaluate(db, "scale-15", v1, {"C4": 15})
        _evaluate(db, "all-yes", v1)
        confirmed = _evaluate(db, "confirmed", v1)
        row = _row(db, confirmed.id, "C2")
        row.final_answer, row.final_score, row.final_earned, row.review_state = "yes", 1.0, row.earned, "confirmed"
        db.commit()
    assert _ids(env, criterion="C2", answer="lost") == ["from-na"]  # an N/A criterion loses nothing; "no" loses its weight
    assert _ids(env, criterion="C4", answer="lost") == ["scale-15"]  # 15 of 20 points
    assert _ids(env, criterion="C8", answer="lost") == []  # the gross-error flag has no weight
    assert sorted(_ids(env, criterion="C2", answer="N/A")) == ["to-na"]
    assert sorted(_ids(env, criterion="C2", answer="no")) == ["from-na"]


# ------------------------------------------------------------------------------------------------ INTAKE-3

def _judge_names(env, cid: str) -> tuple[str | None, str | None, str | None]:
    """(stored operator_name, name in the judge's pack, operator_name_detected in the judge's structural facts)."""
    with env.Session() as db:
        conv = db.get(Conversation, cid)
        pack = judge_runner.conversation_pack(db, conv, "fake", {"pii_masking": False})
        facts = json.loads(packer._structural_facts(pack))
        return conv.operator_name, pack.operator_name, facts["operator_name_detected"]


def _paste(env, cid: str, operator_line: str, on_conflict: str = "error"):
    r = env.client.post("/api/conversations/paste", json={"conversation_id": cid, "language": "RU", "on_conflict": on_conflict,
                                                          "transcript": _record(cid, operator_line=operator_line)["transcript"]})
    assert r.status_code == 200, r.text
    return r.json()


def test_intake3_manual_attribution_never_reaches_the_judge_as_a_detected_name(env):
    _paste(env, "nameless-1", "Здравствуйте! В приложении: Меню → Отчёты.")
    assert _judge_names(env, "nameless-1") == (None, None, None)
    assert env.client.put("/api/conversations/nameless-1/operator", json={"operator_name": "Нина"}).status_code == 200
    assert _judge_names(env, "nameless-1") == ("Нина", None, None)  # C1: the operator still did not introduce themselves
    assert _ids(env, operator="Нина") == ["nameless-1"]  # analytics and filters keep the attribution (OPR-06)

    _paste(env, "named-1", "Здравствуйте! Меня зовут Алина. В приложении: Меню → Отчёты.")
    assert _judge_names(env, "named-1") == ("Алина", "Алина", "Алина")
    env.client.put("/api/conversations/named-1/operator", json={"operator_name": "Нина"})
    assert _judge_names(env, "named-1") == ("Нина", "Алина", "Алина")
    env.client.put("/api/conversations/named-1/operator", json={"operator_name": "Дина"})  # a second manual attribution
    assert _judge_names(env, "named-1") == ("Дина", "Алина", "Алина")
    replaced = _paste(env, "named-1", "Здравствуйте! Меня зовут Дана. В приложении: Меню → Отчёты.", on_conflict="replace")
    assert replaced["import_status"] == "updated"
    assert _judge_names(env, "named-1") == ("Дина", "Дана", "Дана")  # the new greeting is detected, the attribution kept
    env.client.put("/api/conversations/named-1/operator", json={"operator_name": None})  # cleared: the detected name again
    assert _judge_names(env, "named-1") == ("Дана", "Дана", "Дана")


# ------------------------------------------------------------------------------------------------ INTAKE-4

def test_intake4_d365_repost_keeps_a_manual_attribution_and_the_integration_dedupe(env):
    first = env.client.post(URL, json={"conversation_id": "d365-x", "messages": _chat(NAMELESS)})
    assert first.status_code == 200, first.text
    assert first.json()["operator_name"] is None and first.json()["agent_name"] == "Алина Сапарова"
    assert env.client.put("/api/conversations/d365-x/operator", json={"operator_name": "Нина"}).status_code == 200
    with env.Session() as db:
        conv = db.get(Conversation, "d365-x")
        turn_ids = [t.id for t in conv.turns]
        d365_hash = conv.meta["d365"]["content_hash"]
    again = env.client.post(URL, json={"conversation_id": "d365-x", "messages": _chat(NAMELESS)})
    assert again.status_code == 200, again.text
    body = again.json()
    assert body["created"] is False and body["turns"] == 3 and body["operator_name"] == "Нина"
    assert body["agent_name"] == "Алина Сапарова" and body["evaluation"] == {"status": "not_requested"}
    with env.Session() as db:
        conv = db.get(Conversation, "d365-x")
        assert conv.operator_name == "Нина" and conv.meta["operator_name_source"] == "manual"
        assert [t.id for t in conv.turns] == turn_ids  # unchanged: the turns (and evidence on them) stay
        assert conv.meta["source"] == "dynamics365" and conv.meta["d365"]["content_hash"] == d365_hash
        assert "transcript_changed_at" not in conv.meta
        db.add(Evaluation(conversation_id="d365-x", checklist_version_id=env.ids["v1"], status="completed", provider="fake",
                          completed_at=datetime.now(UTC)))
        db.commit()
    dedupe = env.client.post(URL, json={"conversation_id": "d365-x", "messages": _chat(NAMELESS), "evaluate": True, "provider": "fake"})
    assert dedupe.status_code == 200, dedupe.text
    assert dedupe.json()["evaluation"]["status"] == "skipped_unchanged" and env.started == []


def test_intake4_d365_push_refuses_to_rewrite_a_dialog_under_evaluation(env):
    assert env.client.post(URL, json={"conversation_id": "d365-busy", "messages": _chat(NAMED)}).status_code == 200
    env.client.put("/api/conversations/d365-busy/operator", json={"operator_name": "Нина"})
    with env.Session() as db:
        running = Evaluation(conversation_id="d365-busy", checklist_version_id=env.ids["v1"], status="running", provider="fake")
        db.add(running)
        db.commit()
        running_id = running.id
        old_hash = db.get(Conversation, "d365-busy").meta["d365"]["content_hash"]
    r = env.client.post(URL, json={"conversation_id": "d365-busy", "messages": _chat(NAMED, extra=True)})
    assert r.status_code == 409, r.text
    err = r.json()["error"]
    assert err["code"] == "conversation_busy" and err["conversation_id"] == "d365-busy" and "оценивается" in err["message"]
    assert r.headers["Retry-After"] == "60"
    with env.Session() as db:
        conv = db.get(Conversation, "d365-busy")
        assert conv.turn_count == 3 and len(conv.turns) == 3 and "transcript_changed_at" not in conv.meta
        assert conv.operator_name == "Нина" and conv.meta["d365"]["content_hash"] == old_hash
    same = env.client.post(URL, json={"conversation_id": "d365-busy", "messages": _chat(NAMED)})  # identical: nothing to rewrite
    assert same.status_code == 200 and same.json()["turns"] == 3
    with env.Session() as db:
        db.get(Evaluation, running_id).status = "completed"
        db.commit()
    changed = env.client.post(URL, json={"conversation_id": "d365-busy", "messages": _chat(NAMED, extra=True)})
    assert changed.status_code == 200, changed.text
    assert changed.json()["turns"] == 4 and changed.json()["operator_name"] == "Нина"
    with env.Session() as db:
        conv = db.get(Conversation, "d365-busy")
        assert conv.turn_count == 4 and conv.meta["transcript_changed_at"] and conv.meta["previous_turn_count"] == 3
        assert conv.meta["operator_name_source"] == "manual" and conv.meta["operator_name_detected"] == "Алина"
        assert conv.meta["source"] == "dynamics365" and conv.meta["d365"]["content_hash"] != old_hash
        assert conv.agent_name == "Алина Сапарова"


# ------------------------------------------------------------------------------------------------ INTAKE-5

def test_intake5_version_filter_never_shows_another_versions_evaluation(env):
    with env.Session() as db:
        _conversation(db, "a")
        _conversation(db, "c")
        _evaluate(db, "a", _version(db, env, "v1"), {"C4": 15})
        _evaluate(db, "c", _version(db, env, "v2"), total=55.0)  # evaluated with the draft only
        db.commit()
    v1 = env.ids["v1"]
    body = env.client.get("/api/conversations", params={"checklist_version_id": v1, "status": "not_evaluated"}).json()
    assert [i["id"] for i in body["items"]] == ["c"]
    assert body["items"][0]["latest_evaluation"] is None and body["items"][0]["review_state"] == "none"
    items = env.client.get("/api/conversations", params={"checklist_version_id": v1, "sort": "total_asc"}).json()["items"]
    assert [i["id"] for i in items] == ["a", "c"]  # "c" has no v1 total: sorted last, not by its v2 total
    assert items[1]["latest_evaluation"] is None
    unfiltered = {i["id"]: i for i in env.client.get("/api/conversations").json()["items"]}
    assert unfiltered["c"]["latest_evaluation"]["checklist_version_id"] == env.ids["v2"]  # no filter: as before


# ------------------------------------------------------------------------------------------------ INTAKE-6

def test_intake6_failed_judge_call_matches_no_answer(env):
    with env.Session() as db:
        v1 = _version(db, env, "v1")
        for cid, c7 in (("err", None), ("na", "N/A"), ("yes", "yes")):
            _conversation(db, cid)
            _evaluate(db, cid, v1, {"C7": c7})
        db.commit()
    assert _ids(env, criterion="C7", answer="N/A") == ["na"]
    assert _ids(env, criterion="C7", answer="yes") == ["yes"]
    assert _ids(env, criterion="C7", answer="no") == []
    assert _ids(env, criterion="C7", answer="lost") == []


# ------------------------------------------------------------------------------------------------ INTAKE-7

def _upload(env, records: list[dict[str, Any]], name: str = "set.json", **params):
    files = {"file": (name, json.dumps(records, ensure_ascii=False).encode("utf-8"), "application/json")}
    return env.client.post("/api/conversations/upload", params=params, files=files)


def _last_text(env, cid: str) -> str:
    return env.client.get(f"/api/conversations/{cid}").json()["turns"][-1]["text"]


def test_intake7_repeated_id_in_one_upload_is_a_reimport_not_a_500(env):
    same = _upload(env, [_record("dup-1"), _record("dup-1")])
    assert same.status_code == 200, same.text
    body = same.json()
    assert (body["created"], body["unchanged"]) == (1, 1)
    assert body["conversation_ids"] == ["dup-1"] and body["by_language"] == {"RU": 1}
    assert [i["status"] for i in body["items"]] == ["created", "unchanged"]

    conflict = _upload(env, [_record("dup-2"), _record("dup-2", last="изменено")])
    assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "conversation_exists"
    assert conflict.json()["error"]["suggested_id"] == "dup-2-v2"
    assert env.client.get("/api/conversations/dup-2").status_code == 404  # nothing written

    replaced = _upload(env, [_record("dup-3"), _record("dup-3", last="последняя копия")], on_conflict="replace").json()
    assert (replaced["created"], replaced["updated"]) == (1, 1) and replaced["conversation_ids"] == ["dup-3"]
    assert _last_text(env, "dup-3") == "последняя копия"

    renamed = _upload(env, [_record("dup-4"), _record("dup-4", last="копия")], on_conflict="rename").json()
    assert (renamed["created"], renamed["renamed"]) == (1, 1) and renamed["conversation_ids"] == ["dup-4", "dup-4-v2"]
    assert _last_text(env, "dup-4-v2") == "копия"

    skipped = _upload(env, [_record("dup-5"), _record("dup-5", last="другая")], on_conflict="skip").json()
    assert (skipped["created"], skipped["skipped"]) == (1, 1) and _last_text(env, "dup-5") == "Спасибо, до свидания!"

    # the id of a renamed copy repeated later in the same set
    chain = _upload(env, [_record("dup-6"), _record("dup-6", last="B"), _record("dup-6-v2", last="B"), _record("dup-6-v2", last="C")],
                    on_conflict="rename")
    assert chain.status_code == 200, chain.text
    assert [i["status"] for i in chain.json()["items"]] == ["created", "renamed", "unchanged", "renamed"]
    assert chain.json()["conversation_ids"] == ["dup-6", "dup-6-v2", "dup-6-v2-v2"]


def test_intake7_organizer_import_with_a_repeated_id(env):
    with env.Session() as db:
        res = ingest.import_records(db, [_record("org-1"), _record("org-1", last="обновлено", language="KZ")], "DEV")
        assert (res["created"], res["updated"]) == (1, 1)
        assert res["conversation_ids"] == ["org-1"] and res["by_language"] == {"KZ": 1}  # counted once, final language
        assert db.get(Conversation, "org-1").turns[-1].text == "обновлено"


# ------------------------------------------------------------------------------------------------ card: last attempt failed

def test_card_shows_the_completed_result_when_the_newest_attempt_failed(env):
    with env.Session() as db:
        _conversation(db, "p1")
        _conversation(db, "solo")
        done = _evaluate(db, "p1", _version(db, env, "v1"))
        failed = _evaluate(db, "p1", _version(db, env, "v2"), status="failed", error="Anthropic connection error", total=34.67)
        _evaluate(db, "solo", _version(db, env, "v1"))
        db.commit()
        done_id, failed_id = done.id, failed.id
    card = env.client.get("/api/conversations/p1/evaluation").json()
    assert card["id"] == done_id and card["status"] == "completed" and card["final_total_score"] == 100.0
    assert card["last_attempt_failed"] is True
    assert card["last_attempt"]["id"] == failed_id and card["last_attempt"]["status"] == "failed"
    assert card["last_attempt"]["error"] == "Anthropic connection error" and card["last_attempt"]["created_at"]
    item = next(i for i in env.client.get("/api/conversations").json()["items"] if i["id"] == "p1")
    assert item["latest_evaluation"]["id"] == done_id and item["last_attempt"] == card["last_attempt"]  # same rule as the list
    v1_card = env.client.get("/api/conversations/p1/evaluation", params={"checklist_version_id": env.ids["v1"]}).json()
    assert v1_card["id"] == done_id and v1_card["last_attempt_failed"] is False and "last_attempt" not in v1_card
    v2_card = env.client.get("/api/conversations/p1/evaluation", params={"checklist_version_id": env.ids["v2"]}).json()
    assert v2_card["id"] == failed_id and v2_card["status"] == "failed" and v2_card["last_attempt_failed"] is False  # nothing completed
    solo = env.client.get("/api/conversations/solo/evaluation").json()
    assert solo["status"] == "completed" and solo["last_attempt_failed"] is False
    with env.Session() as db:
        _conversation(db, "none")
        db.commit()
    missing = env.client.get("/api/conversations/none/evaluation")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "not_found"


def test_card_after_a_restart_interrupted_attempt(env):
    with env.Session() as db:
        _conversation(db, "r1")
        done = _evaluate(db, "r1", _version(db, env, "v1"))
        interrupted = _evaluate(db, "r1", _version(db, env, "v1"), status="failed", error="interrupted by restart")
        db.commit()
        done_id, interrupted_id = done.id, interrupted.id
    card = env.client.get("/api/conversations/r1/evaluation").json()
    assert card["id"] == done_id and card["last_attempt_failed"] is True and card["last_attempt"]["id"] == interrupted_id
    item = next(i for i in env.client.get("/api/conversations").json()["items"] if i["id"] == "r1")
    assert item["last_attempt_failed"] is True and item["last_attempt"]["id"] == interrupted_id


# ------------------------------------------------------------------------------------------------ upload: one-record JSON

def test_one_record_json_upload_returns_the_conversation_id(env):
    one = _upload(env, [_record("one-1")], name="one.json")
    assert one.status_code == 200, one.text
    body = one.json()
    assert body["id"] == "one-1" and body["created"] == 1 and body["conversation_ids"] == ["one-1"]  # the summary stays
    wrapped = {"isSuccess": True, "data": {"records": [_record("one-2")]}}
    files = {"file": ("org.json", json.dumps(wrapped, ensure_ascii=False).encode("utf-8"), "application/json")}
    assert env.client.post("/api/conversations/upload", files=files).json()["id"] == "one-2"
    renamed = _upload(env, [_record("one-1", last="другой текст")], name="one.json", on_conflict="rename").json()
    assert renamed["id"] == "one-1-v2" and renamed["renamed"] == 1
    unchanged = _upload(env, [_record("one-1")], name="one.json").json()
    assert unchanged["id"] == "one-1" and unchanged["unchanged"] == 1
    many = _upload(env, [_record("many-1"), _record("many-2")]).json()
    assert "id" not in many and many["created"] == 2
