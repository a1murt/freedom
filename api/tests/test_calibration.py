"""Calibration: gold labels, gold.json import/export, AI-vs-human agreement and run-to-run stability.

Everything runs on in-memory SQLite (never api/data/cqa.db) and never calls an LLM. Expected numbers are
worked out by hand in the comments next to the assertions.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from cqa.calibration import service as svc
from cqa.db import Base, get_db
from cqa.eval import harness
from cqa.models import (
    Checklist,
    ChecklistVersion,
    Conversation,
    Criterion,
    Evaluation,
    EvaluationCriterion,
    GoldLabel,
)
from cqa.routers.calibration import router as calibration_router
from cqa.scoring.engine import CriterionInput, answer_points, compute_score

API_DIR = Path(__file__).resolve().parents[1]
SEED = json.loads((API_DIR / "seeds" / "checklist_freedom_v1.json").read_text(encoding="utf-8"))
TEMPLATE = json.loads((API_DIR / "eval" / "gold.template.json").read_text(encoding="utf-8"))
CODES = [c["code"] for c in SEED["criteria"]]
NA = "N/A"
NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
C9 = {"code": "C9", "section": "S3", "title": "Предложение продукта", "instruction": "Оператор предложил подходящий продукт.",
      "answer_type": "binary", "options": [{"value": "yes", "label": "Да", "points": 1}, {"value": "no", "label": "Нет", "points": 0}],
      "max_points": 1, "weight": 0, "na_allowed": False, "na_condition": None, "is_critical": False, "channel_overrides": {}}

D1, D2, D3, D4, D5, D6, D7 = "t-ru-001", "t-ru-002", "t-kz-001", "t-en-001", "t-ru-call", "t-ru-noeval", "t-en-err"

# Latest AI verdicts on version 1, stored as the judge stores them (scale answers as strings, N/A = not applicable).
AI = {
    D1: {"C1": "yes", "C2": NA, "C3": NA, "C4": 20, "C5": "yes", "C6": 10, "C7": "yes", "C8": "no"},       # total 100.0
    D2: {"C1": "no", "C2": "yes", "C3": "yes", "C4": 20, "C5": "yes", "C6": 7, "C7": "yes", "C8": "no"},   # 85.5
    D3: {"C1": "no", "C2": "no", "C3": NA, "C4": 10, "C5": "yes", "C6": 10, "C7": "no", "C8": "no"},       # 50.0
    D4: {"C1": "yes", "C2": "no", "C3": NA, "C4": NA, "C5": "no", "C6": 10, "C7": "yes", "C8": "no"},      # 50.0
    D5: {"C1": "yes", "C2": "yes", "C3": "yes", "C4": 0, "C5": "yes", "C6": 4, "C7": "yes", "C8": "yes"},  # 71.0
}
# Reconciled human gold ("team").
HUMAN = {
    D1: dict(AI[D1]),                                                                                       # 100.0
    D2: {"C1": "yes", "C2": "yes", "C3": "yes", "C4": 15, "C5": "yes", "C6": 7, "C7": "yes", "C8": "no"},   # 90.5
    D3: {"C1": "no", "C2": NA, "C3": NA, "C4": 10, "C5": "yes", "C6": 7, "C7": "no", "C8": "no"},           # 3550/65 = 54.62
    D4: dict(AI[D4]),                                                                                       # 50.0
    D5: {"C1": "yes", "C2": NA, "C3": "yes", "C4": 5, "C5": "yes", "C6": 4, "C7": "yes", "C8": "yes"},      # 61/85 = 71.76
}
SPLIT = {D1: "train", D2: "train", D3: "train", D4: "validation", D5: "validation"}


# ----------------------------------------------------------------------------------------------- helpers


def _sessionmaker() -> sessionmaker:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _version(db: Session, *, checklist: Checklist | None = None, version_no: int = 1, active: bool = True,
             extra: tuple[dict, ...] = ()) -> ChecklistVersion:
    if checklist is None:
        checklist = Checklist(name=SEED["name"], description=SEED["description"])
        db.add(checklist)
        db.flush()
    version = ChecklistVersion(checklist_id=checklist.id, version_no=version_no, is_active=active, change_note=f"v{version_no}",
                               scoring_rules=SEED["scoring_rules"], sections=SEED["sections"])
    db.add(version)
    db.flush()
    for i, c in enumerate([*SEED["criteria"], *extra], start=1):
        db.add(Criterion(checklist_version_id=version.id, code=c["code"], order=i, section=c["section"], title=c["title"],
                         instruction=c["instruction"], answer_type=c["answer_type"], options=c["options"], max_points=c["max_points"],
                         weight=c["weight"], na_allowed=c["na_allowed"], na_condition=c.get("na_condition"), is_critical=c["is_critical"],
                         critical_value=c.get("critical_value"), channel_overrides=c.get("channel_overrides") or {}))
    db.flush()
    db.expire(version, ["criteria"])
    return version


def _conversation(db: Session, cid: str, channel: str = "agent chat", language: str = "RU", operator: str | None = None) -> Conversation:
    conv = Conversation(id=cid, channel=channel, language=language, operator_name=operator, turn_count=0)
    db.add(conv)
    db.flush()
    return conv


def _evaluate(db: Session, conv: Conversation, version: ChecklistVersion, answers: dict, *, provider: str = "anthropic",
              model: str = "claude-opus-5-5", mode: str = "single", status: str = "completed", copied: frozenset = frozenset(),
              errors: frozenset = frozenset()) -> Evaluation:
    """Persist an evaluation like cqa.judge.runner: string answers, N/A as applicable=False, failed rows flagged "error"."""
    crits = sorted(version.criteria, key=lambda c: c.order)
    inputs = []
    for c in crits:
        if c.code not in answers and c.code not in errors:
            continue
        value = None if c.code in errors else answers[c.code]
        if value is None or value == NA:
            inputs.append(CriterionInput(c.code, c.weight, c.max_points, None, False, c.section, c.is_critical))
        else:
            hit = bool(c.is_critical and value == c.critical_value)
            inputs.append(CriterionInput(c.code, c.weight, c.max_points, answer_points(c.options, value), True, c.section, c.is_critical, hit))
    res = compute_score(version.scoring_rules, inputs, version.sections)
    done = status == "completed"
    ev = Evaluation(conversation_id=conv.id, checklist_version_id=version.id, status=status, provider=provider, model=model, mode=mode,
                    total_score=res.total if done else None, final_total_score=res.total if done else None,
                    critical_error=res.critical if done else False, completed_at=NOW if done else None)
    db.add(ev)
    db.flush()
    for c in crits:
        if c.code in errors:
            row = EvaluationCriterion(evaluation_id=ev.id, criterion_id=c.id, code=c.code, applicable=True, answer=None, score=None,
                                      max_points=c.max_points, weight=c.weight, explanation="Ошибка оценки: timeout", flags=["error"])
        elif c.code in answers:
            value = answers[c.code]
            applicable = value != NA
            cr = res.criteria[c.code]
            row = EvaluationCriterion(evaluation_id=ev.id, criterion_id=c.id, code=c.code, applicable=applicable,
                                      na_reason=None if applicable else "Условие N/A выполнено",
                                      answer=str(value) if applicable else None,
                                      score=answer_points(c.options, value) if applicable else None, max_points=c.max_points,
                                      weight=c.weight, weight_applied=cr.weight_applied, earned=cr.earned, confidence=0.9,
                                      explanation=f"{c.code} для {conv.id}: обоснование модели.",
                                      flags=["copied_from_previous_version"] if c.code in copied else [])
        else:
            continue
        db.add(row)
    db.flush()
    return ev


def _rows(result: dict) -> dict[str, dict]:
    return {r["code"]: r for r in result["criteria"]}


def _count(db: Session, labeler: str | None = None) -> int:
    stmt = select(func.count(GoldLabel.id))
    if labeler is not None:
        stmt = stmt.where(GoldLabel.labeler == labeler)
    return db.scalar(stmt)


def _client(session_factory: sessionmaker) -> TestClient:
    app = FastAPI()
    app.include_router(calibration_router)

    def _override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _override
    return TestClient(app)


# ----------------------------------------------------------------------------------------------- fixtures


@pytest.fixture(scope="module")
def world() -> dict:
    factory = _sessionmaker()
    db = factory()
    v1 = _version(db)
    v2 = _version(db, checklist=db.get(Checklist, v1.checklist_id), version_no=2, active=True, extra=(C9,))
    v1.is_active = False  # the default must still be the organizer version 1, not the active one
    convs = {
        D1: _conversation(db, D1, "agent chat", "RU", "Алина"),
        D2: _conversation(db, D2, "agent chat", "RU", "Дана"),
        D3: _conversation(db, D3, "agent chat", "KZ", "Нұрлан"),
        D4: _conversation(db, D4, "agent chat", "EN", "John"),
        D5: _conversation(db, D5, "voice call", "RU", None),
        D6: _conversation(db, D6, "agent chat", "RU", "Алина"),
        D7: _conversation(db, D7, "agent chat", "EN", "John"),
    }
    ev: dict[str, Evaluation] = {}
    # older runs first: ids grow with time and "latest" means the greatest id
    ev["d2_old"] = _evaluate(db, convs[D2], v1, dict(AI[D2], C1="yes", C4=15))                                   # 90.5
    ev["d3_partial"] = _evaluate(db, convs[D3], v1, dict(AI[D3], C1="yes", C4=5), mode="partial",
                                 copied=frozenset(CODES) - {"C4"})  # only C4 was judged; copied rows are not judgments
    ev["d4_local"] = _evaluate(db, convs[D4], v1, AI[D4], provider="local", model="qwen3:8b", errors=frozenset({"C5"}))
    ev["d5_run1"] = _evaluate(db, convs[D5], v1, dict(AI[D5], C6=7))                                             # 75.5
    ev["d5_run2"] = _evaluate(db, convs[D5], v1, dict(AI[D5], C4=5))                                             # 76.0
    for cid in (D1, D2, D3, D4, D5):
        ev[cid] = _evaluate(db, convs[cid], v1, AI[cid])
    ev[D7] = _evaluate(db, convs[D7], v1, {"C2": NA, "C3": NA, "C4": 20, "C5": "yes", "C6": 10, "C7": "yes", "C8": "no"},
                       errors=frozenset({"C1"}))
    ev["d1_failed"] = _evaluate(db, convs[D1], v1, dict(AI[D1], C1="no"), status="failed")
    ev["d1_v2"] = _evaluate(db, convs[D1], v2, dict(AI[D1], C1="no", C9="yes"))                                   # 55/65 = 84.62
    # a reviewer changed D2 C4 from 20 to 15: final total 0 + 15 + 20 + 15 + 15 + 10.5 + 5 = 80.5
    c4 = next(r for r in ev[D2].criteria if r.code == "C4")
    c4.review_state, c4.final_answer, c4.final_score = "changed", 15, 15.0
    ev[D2].final_total_score = 80.5
    db.commit()

    items = []
    for cid, labels in HUMAN.items():
        for code, value in labels.items():
            item = {"conversation_id": cid, "criterion_code": code, "value": value, "split": SPLIT[cid]}
            if (cid, code) == (D2, "C4"):
                item["note"] = "не назван срок зачисления"
            items.append(item)
    items += [{"conversation_id": D6, "criterion_code": "C1", "value": "yes", "split": "train"},
              {"conversation_id": D7, "criterion_code": "C1", "value": "yes"}]
    assert svc.upsert_labels(db, items, labeler="team")["created"] == 42
    svc.upsert_labels(db, [{"conversation_id": D1, "criterion_code": "C9", "value": "yes"}], labeler="team", checklist_version_id=v2.id)
    for labeler, values in (("alina", ("yes", 20, "yes", 15)), ("kassym", ("yes", 15, "no", 15))):
        svc.upsert_labels(db, [{"conversation_id": D1, "criterion_code": "C1", "value": values[0]},
                               {"conversation_id": D1, "criterion_code": "C4", "value": values[1]},
                               {"conversation_id": D2, "criterion_code": "C1", "value": values[2]},
                               {"conversation_id": D2, "criterion_code": "C4", "value": values[3]}], labeler=labeler)
    yield {"Session": factory, "db": db, "v1": v1, "v2": v2, "ev": ev}
    db.close()


@pytest.fixture()
def db(world) -> Session:
    return world["db"]


@pytest.fixture()
def fresh() -> dict:
    factory = _sessionmaker()
    db = factory()
    version = _version(db)
    for cid, channel, language in (("g-ru-001", "agent chat", "RU"), ("g-ru-002", "agent chat", "RU"), ("g-kz-001", "agent chat", "KZ"),
                                   ("g-call-001", "voice call", "RU"), ("p1-ru-029", "agent chat", "RU")):
        _conversation(db, cid, channel, language)
    db.commit()
    yield {"Session": factory, "db": db, "version": version}
    db.close()


# ----------------------------------------------------------------------------------------------- values


def test_fixture_totals(world):
    ev = world["ev"]
    assert [ev[c].total_score for c in (D1, D2, D3, D4, D5)] == [100.0, 85.5, 50.0, 50.0, 71.0]
    assert (ev["d2_old"].total_score, ev["d5_run1"].total_score, ev["d5_run2"].total_score) == (90.5, 75.5, 76.0)
    assert ev["d1_v2"].total_score == 84.62 and ev[D2].final_total_score == 80.5


def test_canonical_values():
    cv = svc.canonical_value
    raw = ["Да", "нет", "Иә", "жоқ", "YES", True, False, None, "n/a", "NA", "", " 15 ", "7,0", 20.0, 62.31, "maybe"]
    assert [cv(x) for x in raw] == ["yes", "no", "yes", "no", "yes", "yes", "no", NA, NA, NA, NA, 15, 7, 20, 62.31, "maybe"]
    for bad in ({"a": 1}, [1], float("nan"), float("inf")):
        with pytest.raises(ValueError):
            cv(bad)


# ----------------------------------------------------------------------------------------------- labels


def test_upsert_create_update_unchanged_remove(fresh):
    db = fresh["db"]
    items = [
        {"conversation_id": "g-ru-001", "criterion_code": "C1", "value": "Да", "split": "Train"},
        {"conversation_id": "g-ru-001", "criterion_code": "c4", "value": "15"},
        {"conversation_id": "g-ru-001", "criterion_code": "total", "value": 85},
        {"conversation_id": "g-ru-001", "criterion_code": "_note", "note": "сверено с PDF"},
        {"conversation_id": "g-call-001", "criterion_code": "C7", "value": None},  # N/A: C7 allows it on every channel
        {"conversation_id": "g-ru-002", "criterion_code": "C5", "value": 1},       # binary 1/0 → yes/no
        {"conversation_id": "g-ru-002", "criterion_code": "C7", "value": 0},
    ]
    res = svc.upsert_labels(db, items)
    assert (res["created"], res["updated"], res["unchanged"], res["removed"]) == (7, 0, 0, 0)
    assert res["labelers"] == ["team"] and res["checklist_version_id"] == fresh["version"].id and res["dry_run"] is False
    got = {(r["conversation_id"], r["criterion_code"]): r for r in svc.list_labels(db)}
    assert (got[("g-ru-001", "C1")]["value"], got[("g-ru-001", "C1")]["split"]) == ("yes", "train")
    assert (got[("g-ru-001", "C4")]["value"], got[("g-ru-001", "C4")]["kind"], got[("g-ru-001", "C4")]["split"]) == (15, "criterion", "train")
    assert (got[("g-ru-001", "total")]["kind"], got[("g-ru-001", "total")]["value"]) == ("total", 85)
    note = got[("g-ru-001", "_note")]
    assert (note["kind"], note["value"], note["note"], note["split"]) == ("note", None, "сверено с PDF", "train")
    assert got[("g-call-001", "C7")]["value"] == NA and got[("g-call-001", "C7")]["split"] is None
    assert (got[("g-ru-002", "C5")]["value"], got[("g-ru-002", "C7")]["value"]) == ("yes", "no")
    assert [r["criterion_code"] for r in svc.list_labels(db, conversation_id="g-ru-001")] == ["C1", "C4", "total", "_note"]

    assert svc.upsert_labels(db, items)["unchanged"] == 7  # the same batch again changes nothing
    res = svc.upsert_labels(db, [{"conversation_id": "g-ru-001", "criterion_code": "C4", "value": 20}])
    assert (res["updated"], res["items"][0]["value"], res["items"][0]["split"]) == (1, 20, "train")  # split kept
    res = svc.upsert_labels(db, [{"conversation_id": "g-ru-001", "criterion_code": "total", "remove": True}])
    assert res["removed"] == 1 and res["items"] == [] and _count(db) == 6
    assert svc.upsert_labels(db, [{"conversation_id": "g-ru-001", "criterion_code": "total", "remove": True}])["unchanged"] == 1
    preview = svc.upsert_labels(db, [{"conversation_id": "g-kz-001", "criterion_code": "C1", "value": "no"}], dry_run=True)
    assert preview["created"] == 1 and preview["dry_run"] is True and _count(db) == 6


def test_split_is_per_dialog(fresh):
    db = fresh["db"]
    svc.upsert_labels(db, [{"conversation_id": "g-ru-001", "criterion_code": "C1", "value": "yes"},
                           {"conversation_id": "g-ru-001", "criterion_code": "C5", "value": "no"},
                           {"conversation_id": "g-ru-001", "criterion_code": "C1", "value": "no", "labeler": "alina", "split": "train"}])
    res = svc.upsert_labels(db, [{"conversation_id": "g-ru-001", "criterion_code": "C2", "value": "N/A", "split": "validation"}])
    assert (res["created"], res["split_propagated"]) == (1, 2)
    assert {r["split"] for r in svc.list_labels(db, conversation_id="g-ru-001", labeler="team")} == {"validation"}
    assert svc.list_labels(db, labeler="alina")[0]["split"] == "train"  # another labeler keeps its own split
    assert svc.upsert_labels(db, [{"conversation_id": "g-ru-001", "criterion_code": "C7", "value": "yes"}])["items"][0]["split"] == "validation"
    res = svc.upsert_labels(db, [{"conversation_id": "g-ru-001", "criterion_code": "C7", "value": "yes", "split": None}])
    assert (res["updated"], res["split_propagated"]) == (1, 3)
    assert {r["split"] for r in svc.list_labels(db, conversation_id="g-ru-001", labeler="team")} == {None}
    with pytest.raises(svc.ValidationFailed) as exc:
        svc.upsert_labels(db, [{"conversation_id": "g-ru-002", "criterion_code": "C1", "value": "yes", "split": "train"},
                               {"conversation_id": "g-ru-002", "criterion_code": "C5", "value": "yes", "split": "validation"}])
    assert exc.value.errors[0]["field"] == "items[1]"


def test_upsert_validation_is_atomic(fresh):
    db = fresh["db"]
    bad = [
        {"conversation_id": "g-ru-001", "criterion_code": "C1", "value": "N/A"},    # 0: C1 has no N/A in chats
        {"conversation_id": "g-ru-001", "criterion_code": "C4", "value": 12},       # 1: not a C4 step
        {"conversation_id": "g-ru-001", "criterion_code": "C6", "value": "N/A"},    # 2: C6 never N/A
        {"conversation_id": "nope", "criterion_code": "C1", "value": "yes"},        # 3: unknown dialog
        {"conversation_id": "g-ru-001", "criterion_code": "C99", "value": "yes"},   # 4: unknown criterion
        {"conversation_id": "g-ru-001", "criterion_code": "C2"},                    # 5: value missing
        {"conversation_id": "g-ru-001", "criterion_code": "total", "value": 150},   # 6: total out of 0..100
        {"conversation_id": "g-ru-001", "criterion_code": "C8", "value": "maybe"},  # 7: not yes/no
        {"conversation_id": "g-ru-002", "criterion_code": "C1", "value": "yes"},    # 8: fine
        {"conversation_id": "g-ru-002", "criterion_code": "c1", "value": "no"},     # 9: duplicate of 8
        "C1=yes",                                                                    # 10: not an object
        {"conversation_id": "g-ru-002", "criterion_code": "C3", "value": {"x": 1}},  # 11: wrong type
        {"conversation_id": "g-ru-002", "criterion_code": "_note"},                  # 12: note without text
    ]
    with pytest.raises(svc.ValidationFailed) as exc:
        svc.upsert_labels(db, bad)
    assert exc.value.status == 422 and exc.value.code == "validation_error"
    assert [e["field"] for e in exc.value.errors] == [
        "items[10]", "items[0].value", "items[1].value", "items[2].value", "items[3].conversation_id", "items[4].criterion_code",
        "items[5].value", "items[6].value", "items[7].value", "items[9]", "items[11].value", "items[12].value"]
    msgs = {e["field"]: e["message"] for e in exc.value.errors}
    assert "N/A недопустимо для C1" in msgs["items[0].value"] and "agent chat" in msgs["items[0].value"]
    assert "20, 15, 10, 5, 0" in msgs["items[1].value"] and "nope" in msgs["items[3].conversation_id"]
    assert "C99" in msgs["items[4].criterion_code"] and "уже указана" in msgs["items[9]"]
    assert _count(db) == 0  # nothing written

    for items, field in (({"items": []}, "items"), ([], "items")):
        with pytest.raises(svc.ValidationFailed) as exc:
            svc.upsert_labels(db, items)
        assert exc.value.errors[0]["field"] == field
    one = [{"conversation_id": "g-ru-001", "criterion_code": "C1", "value": "yes"}]
    with pytest.raises(svc.ValidationFailed) as exc:
        svc.upsert_labels(db, one, labeler="x" * 65)
    assert exc.value.errors[0]["field"] == "labeler"
    with pytest.raises(svc.VersionNotFound):
        svc.upsert_labels(db, one, checklist_version_id=999)


def test_list_labels_and_labelers(db):
    assert len(svc.list_labels(db, labeler="team")) == 43  # 5 dialogs × 8 + D6 C1 + D7 C1 + D1 C9
    assert [r["criterion_code"] for r in svc.list_labels(db, conversation_id=D1, labeler="team")] == CODES + ["C9"]
    assert {r["split"] for r in svc.list_labels(db, conversation_id=D1, labeler="team")} == {"train"}  # C9 inherited it
    assert [r["conversation_id"] for r in svc.list_labels(db, labeler="team", split="validation")] == [D4] * 8 + [D5] * 8
    assert len(svc.list_labels(db, criterion_code="C4")) == 9  # team D1..D5, alina D1 D2, kassym D1 D2
    labs = {x["labeler"]: x for x in svc.labelers(db)}
    assert list(labs) == ["alina", "kassym", "team"]
    assert (labs["team"]["labels"], labs["team"]["dialogs"], labs["team"]["totals"]) == (43, 7, 0)
    assert (labs["alina"]["labels"], labs["alina"]["dialogs"]) == (4, 2) and labs["alina"]["updated_at"]
    assert svc.default_labeler(db) == "team"


# ----------------------------------------------------------------------------------------------- import / export


def test_import_export_round_trip(fresh):
    db = fresh["db"]
    doc = {
        "_instructions": "top-level keys other than split/labels are ignored",
        "split": {"train": ["g-ru-001", "g-ru-002"], "validation": ["g-kz-001", "g-missing"]},
        "labels": {
            "g-ru-001": {"C1": "Да", "C2": "N/A", "C3": None, "C4": "20", "C5": True, "C6": 10, "C7": "yes", "C8": "Нет",
                         "total": 100, "_note": "эталонный пример"},
            "g-ru-002": {"C1": "no", "C2": "yes", "C3": "yes", "C4": 15, "C5": "no", "C6": 7, "C7": "N/A", "C8": False,
                         "_notes": {"C4": "не назван срок"}},
            "g-kz-001": {"c1": "иә", "C4": 5, "_comment": "черновик"},
            "g-missing": {"C1": "yes"},
        },
    }
    res = svc.import_gold(db, doc, "ml")
    assert (res["dialogs"], res["labels"], res["totals"], res["notes"]) == (3, 18, 1, 1)
    assert (res["created"], res["updated"], res["removed"]) == (20, 0, 0)
    assert res["skipped_conversations"] == ["g-missing"] and res["ignored_keys"] == ["_comment"]
    assert res["split"] == {"train": 2, "validation": 1} and res["split_unlabeled"] == [] and res["warnings"] == []

    out = svc.export_gold(db, "ml")
    assert (out["_meta"]["labeler"], out["_meta"]["dialogs"], out["_meta"]["labels"]) == ("ml", 3, 18)
    assert out["split"] == {"train": ["g-ru-001", "g-ru-002"], "validation": ["g-kz-001"]}
    assert out["labels"] == {
        "g-kz-001": {"C1": "yes", "C4": 5},
        "g-ru-001": {"C1": "yes", "C2": NA, "C3": NA, "C4": 20, "C5": "yes", "C6": 10, "C7": "yes", "C8": "no", "total": 100},
        "g-ru-002": {"C1": "no", "C2": "yes", "C3": "yes", "C4": 15, "C5": "no", "C6": 7, "C7": NA, "C8": "no"},
    }
    assert list(out["labels"]["g-ru-001"]) == CODES + ["total"]
    full = svc.export_gold(db, "ml", include_notes=True)
    assert full["labels"]["g-ru-001"]["_note"] == "эталонный пример"
    assert full["labels"]["g-ru-002"]["_notes"] == {"C4": "не назван срок"}

    again = svc.import_gold(db, json.loads(json.dumps(full, ensure_ascii=False)), "copy")
    assert again["created"] == 20 and again["skipped_conversations"] == []
    copy = svc.export_gold(db, "copy", include_notes=True)
    assert copy["labels"] == full["labels"] and copy["split"] == full["split"]
    same = svc.import_gold(db, doc, "ml")
    assert (same["created"], same["updated"], same["unchanged"]) == (0, 0, 20)


def test_import_teammate_template(fresh):
    db = fresh["db"]
    res = svc.import_gold(db, TEMPLATE, "ml")
    assert (res["dialogs"], res["labels"], res["notes"]) == (1, 8, 1)
    assert res["split"] == {"train": 1} and len(res["split_unlabeled"]) == 22 and res["skipped_conversations"] == []
    expected = {k: v for k, v in TEMPLATE["labels"]["p1-ru-029"].items() if not k.startswith("_")}
    out = svc.export_gold(db, "ml")
    assert out["labels"] == {"p1-ru-029": expected} and out["split"] == {"train": ["p1-ru-029"], "validation": []}
    assert svc.export_gold(db, "ml", include_notes=True)["labels"]["p1-ru-029"]["_note"] == TEMPLATE["labels"]["p1-ru-029"]["_note"]


def test_import_replace_and_dry_run(fresh):
    db = fresh["db"]
    svc.import_gold(db, {"labels": {"g-ru-001": {"C1": "yes", "C5": "no"}, "g-ru-002": {"C1": "yes"}}}, "team")
    svc.import_gold(db, {"labels": {"g-kz-001": {"C1": "no"}}}, "other")
    before = _count(db)
    preview = svc.import_gold(db, {"labels": {"g-ru-001": {"C1": "no"}}}, "team", replace=True, dry_run=True)
    assert preview["dry_run"] is True and (preview["updated"], preview["removed"]) == (1, 2)
    assert _count(db) == before == 4
    assert svc.list_labels(db, conversation_id="g-ru-001", criterion_code="C1")[0]["value"] == "yes"
    res = svc.import_gold(db, {"labels": {"g-ru-001": {"C1": "no"}}}, "team", replace=True)
    assert (res["updated"], res["removed"]) == (1, 2)
    assert [(r["conversation_id"], r["criterion_code"], r["value"]) for r in svc.list_labels(db, labeler="team")] == [("g-ru-001", "C1", "no")]
    assert _count(db, "other") == 1  # other labelers are untouched
    # a file that matches no dialog of this database must not wipe the labeler's set
    with pytest.raises(svc.ValidationFailed) as exc:
        svc.import_gold(db, {"labels": {"p9-unknown": {"C1": "yes"}}}, "team", replace=True)
    assert exc.value.errors[0]["field"] == "doc.labels" and _count(db, "team") == 1
    assert svc.import_gold(db, {"labels": {"p9-unknown": {"C1": "yes"}}}, "team")["skipped_conversations"] == ["p9-unknown"]


def test_import_errors_and_split_conflicts(fresh):
    db = fresh["db"]
    for doc, field in (("not json", "doc"), ([], "doc"), ({"split": {}}, "doc.labels"), ({"labels": []}, "doc.labels")):
        with pytest.raises(svc.ValidationFailed) as exc:
            svc.import_gold(db, doc, "team")
        assert exc.value.errors[0]["field"] == field
    bad = {"split": {"train": "g-ru-001"}, "labels": {"g-ru-001": {"C4": 12, "C1": "yes", "C9": "yes"}, "g-ru-002": "yes"}}
    with pytest.raises(svc.ValidationFailed) as exc:
        svc.import_gold(db, bad, "team")
    assert sorted(e["field"] for e in exc.value.errors) == sorted(
        ["doc.split.train", "doc.labels.g-ru-001.C4", "doc.labels.g-ru-001.C9", "doc.labels.g-ru-002"])
    assert _count(db) == 0
    assert svc.import_gold(db, json.dumps({"labels": {"g-ru-001": {"C1": "yes"}}}), "team")["created"] == 1  # JSON text works too
    res = svc.import_gold(db, {"split": {"train": ["g-ru-002"], "validation": ["g-ru-002"]}, "labels": {"g-ru-002": {"C1": "yes"}}}, "team")
    assert res["split"] == {"validation": 1} and "использована validation" in res["warnings"][0]


def test_export_world(db):
    doc = svc.export_gold(db, "team")
    assert doc["split"] == {"train": sorted([D1, D2, D3, D6]), "validation": sorted([D4, D5])}
    assert doc["labels"][D1] == {**HUMAN[D1], "C9": "yes"} and list(doc["labels"][D1]) == CODES + ["C9"]
    assert doc["labels"][D7] == {"C1": "yes"} and "_notes" not in doc["labels"][D2]
    assert svc.export_gold(db, "team", include_notes=True)["labels"][D2]["_notes"] == {"C4": "не назван срок зачисления"}
    assert svc.export_gold(db, "nobody")["labels"] == {}


# ----------------------------------------------------------------------------------------------- agreement


def test_agreement_per_criterion(db, world):
    res = svc.agreement(db)
    assert res["checklist_version_id"] == world["v1"].id  # organizer version 1 even though version 2 is active
    assert (res["labeler"], res["labelers"], res["source"], res["split"]) == ("team", ["alina", "kassym", "team"], "ai", None)
    rows = _rows(res)
    assert list(rows) == CODES
    # C1: (h, ai) = (y,y) (y,n) (n,n) (y,y) (y,y); D7 is skipped because its AI row failed.
    # exact 4/5; po .8, pe .8*.6 + .2*.4 = .56 → kappa .24/.44 = .545
    c1 = rows["C1"]
    assert (c1["n"], c1["exact"], c1["kappa"], c1["na_agree"], c1["qwk"], c1["mae"]) == (5, 0.8, 0.545, 1.0, None, None)
    # C1 has no N/A on any channel (JSON 1.0: contact is true/false only), so its matrix has no N/A row or column
    assert c1["confusion"] == {"labels": ["yes", "no"], "rows": "human", "columns": "ai", "matrix": [[3, 1], [0, 1]]}
    # C2 with N/A as a class: (NA,NA) (y,y) (NA,n) (n,n) (NA,y) → exact 3/5, N/A agreement 3/5;
    # human NA .6 yes .2 no .2, AI NA .2 yes .4 no .4 → pe .28 → kappa .32/.72 = .444
    c2 = rows["C2"]
    assert (c2["n"], c2["exact"], c2["na_agree"], c2["kappa"], c2["na_human"], c2["na_ai"]) == (5, 0.6, 0.6, 0.444, 3, 1)
    assert c2["confusion"]["matrix"] == [[1, 0, 0], [0, 1, 0], [1, 1, 1]]
    # C4 scale: (20,20) (15,20) (10,10) (NA,NA) (5,0) → exact 3/5; MAE over numeric pairs (0+5+0+5)/4 = 2.5;
    # QWK with levels 0..20 (k=5): observed Σw·o = 2/16 = .125, expected Σw·E = 1.0 → .875
    c4 = rows["C4"]
    assert (c4["n"], c4["exact"], c4["na_agree"], c4["mae"], c4["qwk"], c4["kappa"]) == (5, 0.6, 1.0, 2.5, 0.875, None)
    assert c4["confusion"]["labels"] == ["20", "15", "10", "5", "0", NA]
    assert c4["confusion"]["matrix"] == [[1, 0, 0, 0, 0, 0], [1, 0, 0, 0, 0, 0], [0, 0, 1, 0, 0, 0], [0, 0, 0, 0, 1, 0], [0] * 6,
                                         [0, 0, 0, 0, 0, 1]]
    # C6 scale without N/A: (10,10) (7,7) (7,10) (10,10) (4,4) → exact .8, MAE 3/5 = .6; QWK 1 - (1/9)/(31/45) = 26/31 = .839
    c6 = rows["C6"]
    assert (c6["exact"], c6["mae"], c6["qwk"]) == (0.8, 0.6, 0.839) and c6["confusion"]["labels"] == ["10", "7", "4", "0"]
    for code in ("C3", "C5", "C7", "C8"):
        assert (rows[code]["exact"], rows[code]["kappa"]) == (1.0, 1.0)
    assert rows["C8"]["confusion"]["labels"] == ["yes", "no"]  # C8 never allows N/A


def test_agreement_overall_groups_and_disagreements(db, world):
    res = svc.agreement(db)
    o = res["overall"]
    # criteria exact (.8 + .6 + 1 + .6 + 1 + .8 + 1 + 1) / 8 = .85; 34 of 40 pairs agree
    assert (o["n_dialogs"], o["n_pairs"], o["exact_mean"], o["exact_micro"]) == (5, 40, 0.85, 0.85)
    assert o["kappa_mean"] == pytest.approx((0.545 + 0.444 + 4) / 6, abs=0.001) and o["qwk_mean"] == pytest.approx(0.857, abs=0.001)
    # human totals recomputed from the labels: D2 90.5, D3 54.62, D5 71.76 against AI 85.5, 50, 71
    # MAE (0 + 5 + 4.62 + 0 + .76) / 5 = 2.076 → 2.08; the model is stricter on average: bias -2.08
    assert (o["n_totals"], o["total_mae"], o["total_bias"]) == (5, 2.08, -2.08)
    assert [t["conversation_id"] for t in res["totals"]] == [D2, D3, D5, D4, D1]
    assert res["totals"][1] == {"conversation_id": D3, "ai": 50.0, "human": 54.62, "delta": -4.62, "source": "computed",
                                "evaluation_id": world["ev"][D3].id}

    langs = {g["language"]: g for g in res["by_language"]}
    assert {k: (g["n_dialogs"], g["n_pairs"], g["exact"]) for k, g in langs.items()} == {"EN": (1, 8, 1.0), "KZ": (1, 8, 0.75), "RU": (3, 24, 0.833)}
    assert langs["RU"]["total_mae"] == pytest.approx(1.92, abs=0.005)  # (0 + 5 + .76) / 3
    assert {c["code"]: c["exact"] for c in langs["KZ"]["criteria"]}["C2"] == 0.0
    chans = {g["channel"]: g for g in res["by_channel"]}
    assert {k: (g["n_dialogs"], g["n_pairs"], g["exact"]) for k, g in chans.items()} == {"agent chat": (4, 32, 0.875), "voice call": (1, 8, 0.75)}

    dis = [(d["conversation_id"], d["code"], d["ai"], d["human"]) for d in res["disagreements"]]
    assert dis == [(D2, "C1", "no", "yes"), (D3, "C2", "no", NA), (D5, "C2", "yes", NA), (D2, "C4", 20, 15), (D5, "C4", 0, 5), (D3, "C6", 10, 7)]
    d = res["disagreements"][3]
    assert d["explanation"] == f"C4 для {D2}: обоснование модели." and (d["review_state"], d["final_answer"]) == ("changed", 15)
    assert (d["note"], d["split"], d["language"], d["channel"], d["operator_name"]) == ("не назван срок зачисления", "train", "RU", "agent chat", "Дана")
    assert d["evaluation_id"] == world["ev"][D2].id and d["evaluation_criterion_id"] and d["confidence"] == 0.9 and d["na_reason"] is None

    cov = res["coverage"]
    assert (cov["labeled_dialogs"], cov["missing_evaluation"], cov["unknown_codes"]) == (7, [D6], ["C9"])
    assert cov["skipped"] == [{"conversation_id": D7, "code": "C1", "reason": "ai_error"}]


def test_agreement_human_ceiling(db):
    h = svc.agreement(db)["human_agreement"]
    assert (h["labelers"], h["excluded"]) == (["alina", "kassym"], ["team"])
    assert h["pairs"] == [{"labelers": ["alina", "kassym"], "n": 4, "exact": 0.5}]
    rows = {r["code"]: r for r in h["criteria"]}
    # C1 (y,y) (y,n): po .5, pe 1*.5 → kappa 0; C4 (20,15) (15,15): QWK 1 - (1/16)/(1/16) = 0, MAE 2.5
    assert (rows["C1"]["n"], rows["C1"]["exact"], rows["C1"]["kappa"]) == (2, 0.5, 0.0)
    assert (rows["C4"]["n"], rows["C4"]["exact"], rows["C4"]["qwk"], rows["C4"]["mae"]) == (2, 0.5, 0.0, 2.5)
    assert h["exact_mean"] == 0.5 and set(rows) == {"C1", "C4"}


def test_agreement_split(db):
    res = svc.agreement(db, split="validation")  # D4 and D5 only
    rows = _rows(res)
    assert (res["split"], res["overall"]["n_dialogs"]) == ("validation", 2)
    assert (rows["C1"]["n"], rows["C1"]["exact"], rows["C1"]["kappa"]) == (2, 1.0, 1.0)  # one class: the harness returns 1.0
    # C2 (no,no) (NA,yes): po .5; human no .5 NA .5, AI no .5 yes .5 → pe .25 → kappa .25/.75 = .333
    assert (rows["C2"]["exact"], rows["C2"]["kappa"], rows["C2"]["na_agree"]) == (0.5, 0.333, 0.5)
    assert (rows["C4"]["exact"], rows["C4"]["mae"], rows["C4"]["qwk"]) == (0.5, 5.0, None)  # a single numeric pair
    assert res["overall"]["total_mae"] == 0.38  # (0 + .76) / 2
    assert res["coverage"]["missing_evaluation"] == [] and res["human_agreement"] is None
    assert svc.agreement(db, split="train")["coverage"]["missing_evaluation"] == [D6]
    assert svc.agreement(db, split="all")["overall"]["n_pairs"] == 40


def test_agreement_final_answers(db):
    res = svc.agreement(db, source="final")
    c4 = _rows(res)["C4"]
    # the reviewer changed D2 C4 to 15, so it now agrees: exact 4/5, MAE (0+0+0+5)/4 = 1.25,
    # QWK observed 1/16 = .0625, expected 56/64 = .875 → 1 - .0714 = .929
    assert (c4["exact"], c4["mae"], c4["qwk"]) == (0.8, 1.25, 0.929)
    # totals use final_total_score: D2 80.5 vs 90.5 → MAE (0 + 10 + 4.62 + 0 + .76) / 5 = 3.076 → 3.08
    assert res["overall"]["total_mae"] == 3.08 and len(res["disagreements"]) == 5


def test_agreement_other_version_labeler_and_provider(db, world):
    v2 = svc.agreement(db, checklist_version_id=world["v2"].id)
    rows = _rows(v2)
    assert list(rows) == CODES + ["C9"] and v2["overall"]["n_dialogs"] == 1  # only D1 was evaluated on version 2
    assert (rows["C1"]["n"], rows["C1"]["exact"], rows["C1"]["kappa"]) == (1, 0.0, None) and (rows["C9"]["n"], rows["C9"]["exact"]) == (1, 1.0)
    assert v2["coverage"]["unknown_codes"] == [] and v2["coverage"]["missing_evaluation"] == sorted([D2, D3, D4, D5, D6, D7])
    # human 100 vs AI 84.62 (C1 Нет: 55 of 65 applicable weight)
    assert v2["totals"] == [{"conversation_id": D1, "ai": 84.62, "human": 100.0, "delta": -15.38, "source": "computed",
                             "evaluation_id": world["ev"]["d1_v2"].id}]

    alina = svc.agreement(db, labeler="alina")
    rows = _rows(alina)
    assert alina["labeler"] == "alina" and alina["overall"]["n_dialogs"] == 2
    assert (rows["C1"]["n"], rows["C1"]["exact"], rows["C4"]["n"], rows["C4"]["exact"]) == (2, 0.5, 2, 0.5)
    assert (rows["C2"]["n"], rows["C2"]["exact"], alina["overall"]["total_mae"]) == (0, None, None)  # partial labels: no human total

    local = svc.agreement(db, provider="local")  # only D4 has a local run, and its C5 failed
    assert local["overall"]["n_dialogs"] == 1 and _rows(local)["C5"]["n"] == 0 and local["overall"]["n_totals"] == 0
    assert {"conversation_id": D4, "code": "C5", "reason": "ai_error"} in local["coverage"]["skipped"]
    nobody = svc.agreement(db, labeler="nobody")
    assert nobody["labeler"] == "nobody" and nobody["overall"]["n_pairs"] == 0 and nobody["coverage"]["labeled_dialogs"] == 0

    with pytest.raises(svc.VersionNotFound):
        svc.agreement(db, checklist_version_id=999)
    with pytest.raises(svc.ValidationFailed):
        svc.agreement(db, source="human")


def test_agreement_matches_harness_cli(db, world):
    """Same per-criterion numbers as `python -m cqa.eval.harness` on the exported gold file (D7 left out: the CLI
    counts a failed AI row as N/A, the API skips it)."""
    v1 = world["v1"]
    exported = svc.export_gold(db, "team")["labels"]
    gold = {cid: {c: v for c, v in labels.items() if c in CODES} for cid, labels in exported.items() if cid != D7}
    preds = harness.load_predictions(db, list(gold), v1.id)
    levels = {c.code: [float(o["value"]) for o in c.options] for c in v1.criteria if c.answer_type == "scale"}
    ref = harness.score(gold, preds, levels)
    rows = _rows(svc.agreement(db))
    assert ref["n_dialogs"] == 5 and set(ref["criteria"]) == set(CODES)
    for code, r in ref["criteria"].items():
        mine = rows[code]
        assert (mine["n"], mine["exact"], mine["na_agree"]) == (r["n"], r["exact"], r["na_agree"]), code
        for key in ("kappa", "qwk", "mae"):
            assert mine[key] == r.get(key), (code, key)


def test_totals_fall_back_to_the_file_total(fresh):
    db, version = fresh["db"], fresh["version"]
    ev = _evaluate(db, db.get(Conversation, "g-ru-001"), version,
                   {"C1": "yes", "C2": NA, "C3": NA, "C4": 20, "C5": "yes", "C6": 10, "C7": "yes", "C8": "no"})  # 100
    db.commit()
    svc.import_gold(db, {"labels": {"g-ru-001": {"C1": "yes", "total": 92.5}}}, "alina")
    assert svc.default_labeler(db) == "alina"  # no "team" labels: the most complete labeler is the reference
    res = svc.agreement(db)
    assert res["labeler"] == "alina" and res["warnings"] == []
    assert res["totals"] == [{"conversation_id": "g-ru-001", "ai": 100.0, "human": 92.5, "delta": 7.5, "source": "label",
                              "evaluation_id": ev.id}]
    full = {"C1": "yes", "C2": "N/A", "C3": "N/A", "C4": 15, "C5": "yes", "C6": 10, "C7": "yes", "C8": "no", "total": 92.5}
    svc.import_gold(db, {"labels": {"g-ru-001": full}}, "alina")
    res = svc.agreement(db)
    # complete labels win: (10 + 15 + 15 + 15 + 5) / 65 * 100 = 92.31, and the mismatch with the file total is reported
    assert (res["totals"][0]["human"], res["totals"][0]["source"]) == (92.31, "computed")
    assert len(res["warnings"]) == 1 and "92.5" in res["warnings"][0] and "92.31" in res["warnings"][0]


def test_empty_database():
    db = _sessionmaker()()
    try:
        res = svc.agreement(db)
        assert res["checklist_version_id"] is None and res["criteria"] == [] and res["overall"]["n_pairs"] == 0 and res["warnings"]
        assert svc.stability(db)["n_conversations"] == 0
        assert svc.export_gold(db)["labels"] == {} and svc.list_labels(db) == [] and svc.labelers(db) == []
        assert svc.default_labeler(db) is None
    finally:
        db.close()


# ----------------------------------------------------------------------------------------------- stability


def test_stability(db, world):
    res = svc.stability(db)
    assert res["checklist_version_id"] == world["v1"].id
    # D2 (2 runs), D3 (partial + full), D4 (local + anthropic), D5 (3 runs); D1 and D7 have one completed run each
    assert (res["n_conversations"], res["n_evaluations"], res["single_run_conversations"]) == (4, 9, 2)
    rows = _rows(res)
    # C1: D2 yes→no, D4 yes,yes, D5 yes×3; D3's partial run only copied C1 (not a second judgment)
    # kappa of (previous, latest): po 2/3, pe 1*2/3 → 0
    assert (rows["C1"]["n"], rows["C1"]["agree"], rows["C1"]["agreement"], rows["C1"]["kappa"]) == (3, 2, 0.667, 0.0)
    assert (rows["C1"]["majority_share"], rows["C1"]["unanimous"], rows["C1"]["flips"]) == (0.833, 0.667, 1)
    # C4: D2 15→20, D3 5→10 (judged in the partial run), D4 N/A,N/A, D5 0→5→0 → majority (.5 + .5 + 1 + 2/3) / 4
    assert (rows["C4"]["n"], rows["C4"]["agree"], rows["C4"]["agreement"], rows["C4"]["majority_share"], rows["C4"]["flips"]) == (4, 1, 0.25, 0.667, 3)
    assert (rows["C5"]["n"], rows["C5"]["agreement"]) == (2, 1.0)  # D4's local run failed on C5
    # C6: D5 7→4→4: the two latest agree, majority 2/3 → (1 + 1 + 2/3) / 3
    assert (rows["C6"]["n"], rows["C6"]["agreement"], rows["C6"]["majority_share"], rows["C6"]["flips"]) == (3, 1.0, 0.889, 1)
    assert (rows["C2"]["n"], rows["C2"]["agreement"]) == (3, 1.0)
    o = res["overall"]
    # totals compare the two latest full runs without failed criteria: D2 |85.5 - 90.5| and D5 |71 - 76|
    assert (o["n_total_pairs"], o["mean_abs_delta_total"], o["max_abs_delta_total"], o["max_delta_conversation_id"]) == (2, 5.0, 5.0, D2)
    assert (o["flipped_conversations"], o["n_flips"]) == (3, 5)
    assert [d["conversation_id"] for d in res["total_deltas"]] == [D2, D5]
    assert res["total_deltas"][1] == {"conversation_id": D5, "evaluation_ids": [world["ev"]["d5_run2"].id, world["ev"][D5].id],
                                      "totals": [76.0, 71.0], "delta": 5.0}
    flips = [(f["conversation_id"], f["code"], f["values"], f["latest_pair_differs"]) for f in res["flips"]]
    assert flips == [(D2, "C1", ["yes", "no"], True), (D3, "C4", [5, 10], True), (D2, "C4", [15, 20], True),
                     (D5, "C4", [0, 5, 0], True), (D5, "C6", [7, 4, 4], False)]
    assert res["flips"][0]["evaluation_ids"] == [world["ev"]["d2_old"].id, world["ev"][D2].id]
    assert (res["flips"][3]["language"], res["flips"][3]["channel"]) == ("RU", "voice call")


def test_stability_filters(db, world):
    res = svc.stability(db, provider="anthropic")
    rows = _rows(res)
    assert (res["n_conversations"], res["single_run_conversations"]) == (3, 3)  # D4's local run is filtered out
    assert (rows["C1"]["n"], rows["C1"]["agreement"], rows["C4"]["n"], rows["C4"]["agreement"]) == (2, 0.5, 3, 0.0)
    assert {(r["provider"], r["mode"]) for r in res["runs"]} == {("anthropic", "single"), ("anthropic", "partial")}
    empty = svc.stability(db, checklist_version_id=world["v2"].id)
    assert empty["n_conversations"] == 0 and empty["overall"]["mean_abs_delta_total"] is None
    assert all(r["n"] == 0 and r["agreement"] is None for r in empty["criteria"])
    with pytest.raises(svc.VersionNotFound):
        svc.stability(db, checklist_version_id=999)


# ----------------------------------------------------------------------------------------------- HTTP


def test_router_routes():
    assert calibration_router.prefix == "/api/calibration"
    routes = {(m, r.path) for r in calibration_router.routes for m in r.methods}
    assert routes == {("GET", "/api/calibration/labels"), ("PUT", "/api/calibration/labels"), ("POST", "/api/calibration/import"),
                      ("GET", "/api/calibration/export"), ("GET", "/api/calibration/agreement"), ("GET", "/api/calibration/stability")}


def test_http_write_endpoints(fresh):
    with _client(fresh["Session"]) as client:
        ok = client.put("/api/calibration/labels", json={"labeler": "team", "items": [
            {"conversation_id": "g-ru-001", "criterion_code": "C1", "value": "Да", "split": "train"},
            {"conversation_id": "g-call-001", "criterion_code": "C7", "value": "N/A"}]})
        assert ok.status_code == 200 and ok.json()["created"] == 2
        bad = client.put("/api/calibration/labels", json={"items": [{"conversation_id": "g-ru-001", "criterion_code": "C4", "value": 12}]})
        err = bad.json()["error"]
        assert bad.status_code == 422 and err["code"] == "validation_error" and err["message"]
        assert err["errors"][0]["field"] == "items[0].value" and "допустимые шаги" in err["errors"][0]["message"]
        assert client.put("/api/calibration/labels", json=[1, 2]).json()["error"]["errors"][0]["field"] == "body"
        assert client.put("/api/calibration/labels", json={"items": [], "checklist_version_id": "x"}).json()["error"]["errors"][0]["field"] == "checklist_version_id"
        missing = client.put("/api/calibration/labels", json={"checklist_version_id": 999, "items": [
            {"conversation_id": "g-ru-001", "criterion_code": "C1", "value": "yes"}]})
        assert missing.status_code == 404 and missing.json()["error"]["code"] == "version_not_found"

        listed = client.get("/api/calibration/labels", params={"labeler": "team"}).json()
        assert listed["total"] == 2 and listed["labelers"][0]["labeler"] == "team"
        assert client.get("/api/calibration/labels", params={"conversation_id": "g-call-001"}).json()["items"][0]["value"] == NA

        imp = client.post("/api/calibration/import", json={"labeler": "Алина", "doc": {
            "split": {"validation": ["g-kz-001"]}, "labels": {"g-kz-001": {"C1": "yes", "C4": 10}}}})
        assert imp.status_code == 200 and (imp.json()["created"], imp.json()["labeler"]) == (2, "Алина")
        raw = client.post("/api/calibration/import", params={"labeler": "raw", "dry_run": "true"}, json={"labels": {"g-ru-002": {"C1": "no"}}})
        assert raw.status_code == 200 and raw.json()["dry_run"] is True and raw.json()["created"] == 1
        assert client.get("/api/calibration/labels", params={"labeler": "raw"}).json()["total"] == 0
        bad_doc = client.post("/api/calibration/import", json={"labeler": "team", "doc": {"labels": {"g-ru-001": {"C6": 5}}}})
        assert bad_doc.status_code == 422 and bad_doc.json()["error"]["errors"][0]["field"] == "doc.labels.g-ru-001.C6"
        assert client.post("/api/calibration/import", json={"labeler": "team"}).status_code == 422

        exp = client.get("/api/calibration/export", params={"labeler": "Алина", "download": "true"})
        assert exp.status_code == 200 and exp.headers["content-type"].startswith("application/json")
        disposition = exp.headers["content-disposition"]
        assert disposition.startswith("attachment;") and "filename*=UTF-8''gold_%D0%90%D0%BB%D0%B8%D0%BD%D0%B0.json" in disposition
        assert exp.json()["labels"] == {"g-kz-001": {"C1": "yes", "C4": 10}} and exp.json()["split"]["validation"] == ["g-kz-001"]
        assert "content-disposition" not in client.get("/api/calibration/export").headers


def test_http_read_endpoints(world):
    with _client(world["Session"]) as client:
        r = client.get("/api/calibration/agreement")
        assert r.status_code == 200
        body = r.json()
        rows = {c["code"]: c for c in body["criteria"]}
        assert (body["overall"]["exact_mean"], body["overall"]["total_mae"], rows["C4"]["qwk"], rows["C1"]["kappa"]) == (0.85, 2.08, 0.875, 0.545)
        params = {"split": "validation", "labeler": "team", "checklist_version_id": world["v1"].id}
        assert client.get("/api/calibration/agreement", params=params).json()["overall"]["n_dialogs"] == 2
        assert client.get("/api/calibration/agreement", params={"source": "final"}).json()["overall"]["total_mae"] == 3.08
        nf = client.get("/api/calibration/agreement", params={"checklist_version_id": 999})
        assert nf.status_code == 404 and nf.json()["error"]["code"] == "version_not_found"
        bs = client.get("/api/calibration/agreement", params={"source": "human"})
        assert bs.status_code == 422 and bs.json()["error"]["errors"][0]["field"] == "source"

        s = client.get("/api/calibration/stability").json()
        assert s["overall"]["mean_abs_delta_total"] == 5.0 and len(s["flips"]) == 5
        assert client.get("/api/calibration/stability", params={"provider": "anthropic"}).json()["n_conversations"] == 3
        assert client.get("/api/calibration/stability", params={"checklist_version_id": 999}).status_code == 404

        assert set(client.get("/api/calibration/export").json()["labels"]) == {D1, D2, D3, D4, D5, D6, D7}
        labels = client.get("/api/calibration/labels", params={"labeler": "alina"}).json()
        assert labels["total"] == 4 and [x["labeler"] for x in labels["labelers"]] == ["alina", "kassym", "team"]


def test_contact_has_no_na_even_on_calls(fresh):
    # JSON 1.0 appendix + organizer answer 2026-11-08: contact is true/false only, calls included (an unrecorded
    # opening is credited by the judge rule, cqa.judge.validate.opening_outside_recording)
    with pytest.raises(svc.ValidationFailed):
        svc.upsert_labels(fresh["db"], [{"conversation_id": "g-call-001", "criterion_code": "C1", "value": None}])
    assert svc.upsert_labels(fresh["db"], [{"conversation_id": "g-call-001", "criterion_code": "C1", "value": "Да"}])["created"] == 1
