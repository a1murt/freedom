"""Executive overview (GET /api/overview) and operator scorecards (GET /api/operators/{name}).

In-memory SQLite (StaticPool) seeded with the organizer checklist. Appeals and decisions go through the real review
service, so final totals are exactly what production computes. Never touches api/data/cqa.db or the Claude API.

Fixture (checklist v1 unless noted; weights C1 10, C2 15, C3 20, C4 20, C5 15, C6 15, C7 5, C8 0):
  o-ru-001 Алина   chat RU  e1   perfect, C3 N/A                         -> 100.00; C1 appeal confirmed
                            old1 superseded (2026-10-01)                 ->  76.25 (not the latest)
  o-ru-002 Алексей chat RU  e2   C2 judge error, C3 no, C4 10, C5 no,
                                 C6 4, C8 yes (insult)                   ->  36.47, critical
  o-kz-003 Алина   chat KZ  e3   C3 N/A, C4 15, C6 7; C7 changed yes->no -> 88.13 AI, 81.88 final;
                                 C2 confirmed; C6 appeal still open
  o-en-004 —       call EN  e4   N/A-heavy (C2 C3 C4 C7), C1 no, C6 7;
                                 C1 changed no->N/A by a reviewer        -> 63.75 AI, 85.00 final
  o-ru-005 Алексей chat RU  e5   checklist v2 only (C7 weight 10)        -> 100.00
  o-ru-006 Мария   chat RU  f6   failed evaluation only
  checklist v3: no evaluations at all.
"""
from __future__ import annotations

import json
import statistics
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from cqa.analytics.coaching import OperatorNotFound, operator_scorecard, resolve_operator
from cqa.analytics.overview import load_views, overview, percentile, split_model
from cqa.analytics.violations import violations
from cqa.db import Base, get_db
from cqa.models import (
    Appeal,
    Checklist,
    ChecklistVersion,
    Conversation,
    Criterion,
    Evaluation,
    EvaluationCriterion,
    LlmTrace,
    Turn,
)
from cqa.review import service as review
from cqa.routers.insights import router as insights_router
from cqa.scoring.engine import CriterionInput, answer_points, compute_score

SEED = json.loads((Path(__file__).resolve().parents[1] / "seeds" / "checklist_freedom_v1.json").read_text(encoding="utf-8"))
CODES = [c["code"] for c in SEED["criteria"]]
NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
PERFECT = {"C1": "yes", "C2": "yes", "C3": "yes", "C4": "20", "C5": "yes", "C6": "10", "C7": "yes", "C8": "no"}
OPUS = ("anthropic", "claude-opus-5-5")
SONNET = ("anthropic", "claude-sonnet-5-5")
INSULT = "Грубые ошибки — Да. Оператор оскорбил клиента: «Вы вообще читать умеете?»."
C7_REASON = "Оператор попрощался, не уточнив, остались ли вопросы"
F2 = 100 / 85  # e2: applicable weights C1 C3 C4 C5 C6 C7 = 85 (C2 judge error excluded)


def _r(value: float, digits: int = 2) -> float:
    return round(value, digits)


# ----------------------------------------------------------------------------- fixture helpers


def _version(db: Session, checklist: Checklist, version_no: int, *, active: bool, weights: dict[str, float] | None = None):
    version = ChecklistVersion(checklist_id=checklist.id, version_no=version_no, is_active=active, change_note=f"v{version_no}",
                               scoring_rules=SEED["scoring_rules"], sections=SEED["sections"])
    db.add(version)
    db.flush()
    crits = []
    for i, c in enumerate(SEED["criteria"], start=1):
        crit = Criterion(code=c["code"], order=i, section=c["section"], title=c["title"], instruction=c["instruction"],
                         answer_type=c["answer_type"], options=c["options"], max_points=c["max_points"],
                         weight=(weights or {}).get(c["code"], c["weight"]), na_allowed=c["na_allowed"], na_condition=c["na_condition"],
                         is_critical=c["is_critical"], critical_value=c.get("critical_value"), channel_overrides=c["channel_overrides"])
        version.criteria.append(crit)
        crits.append(crit)
    db.flush()
    return version, crits


def _conversation(db: Session, cid: str, channel: str, language: str, operator: str | None, first_response: float | None) -> Conversation:
    conv = Conversation(id=cid, channel=channel, language=language, operator_name=operator, turn_count=6, first_response_seconds=first_response)
    db.add(conv)
    for idx in range(6):
        conv.turns.append(Turn(idx=idx, timestamp_raw=f"00:{idx * 10:02d}", offset_seconds=float(idx * 10),
                               speaker="client" if idx % 2 == 0 else "operator", text=f"Реплика {idx}", segment="operator"))
    db.flush()
    return conv


def _evaluation(db: Session, conv: Conversation, version: ChecklistVersion, crits: list[Criterion], answers: dict[str, object], *,
                completed_at: datetime, na: frozenset[str] | set[str] = frozenset(), errors: frozenset[str] | set[str] = frozenset(),
                model: str = "anthropic:claude-opus-5-5", traces: tuple = (), cost: float = 0.0, tokens: tuple[int, int] = (0, 0),
                explanations: dict[str, str] | None = None, evidence: dict[str, list] | None = None) -> Evaluation:
    items = []
    for c in crits:
        if c.code in na or c.code in errors:
            items.append(CriterionInput(c.code, c.weight, c.max_points, None, False, c.section, c.is_critical, False))
        else:
            ans = answers[c.code]
            hit = bool(c.is_critical and str(ans).lower() == str(c.critical_value or "yes").lower())
            items.append(CriterionInput(c.code, c.weight, c.max_points, answer_points(c.options, ans), True, c.section, c.is_critical, hit))
    res = compute_score(version.scoring_rules, items, version.sections)
    if traces:
        cost = sum(n * unit for _, n, unit, _, _, _ in traces)
        tokens = (sum(n * tin for _, n, _, tin, _, _ in traces), sum(n * tout for _, n, _, _, tout, _ in traces))
    ev = Evaluation(conversation_id=conv.id, checklist_version_id=version.id, status="completed", provider="anthropic", mode="single",
                    model=model, total_score=res.total, final_total_score=res.total, critical_error=res.critical, cost_usd=round(cost, 6),
                    input_tokens=tokens[0], output_tokens=tokens[1], latency_ms=1000, completed_at=completed_at)
    ev.conversation = conv
    db.add(ev)
    db.flush()
    for c, item in zip(crits, items):
        cr = res.criteria[c.code]
        is_error = c.code in errors
        ev.criteria.append(EvaluationCriterion(
            criterion_id=c.id, code=c.code, applicable=c.code not in na, na_reason=c.na_condition if c.code in na else None,
            answer=None if (c.code in na or is_error) else answers[c.code], score=item.points, max_points=c.max_points, weight=c.weight,
            weight_applied=cr.weight_applied, earned=cr.earned, confidence=0.9,
            explanation=(explanations or {}).get(c.code, f"{c.title}: обоснование для {conv.id}."),
            evidence=(evidence or {}).get(c.code, [{"turn_idx": 1, "speaker": "operator", "quote": f"Цитата {c.code}", "role": "supports"}]),
            flags=["error"] if is_error else []))
    for (provider, model_name), n, unit, tin, tout, cache in traces:
        for _ in range(n):
            ev.traces.append(LlmTrace(criterion_code="C1", kind="judge", provider=provider, model=model_name, input_tokens=tin,
                                      output_tokens=tout, cache_read_tokens=cache, cost_usd=unit))
    db.flush()
    return ev


def _appeal(db: Session, ev: Evaluation, code: str, *, created: datetime, decided: datetime | None = None, action: str | None = None,
            new_answer: object = None, reason: str = "Решение проверяющего", author_role: str = "analyst", author_name: str | None = None) -> Appeal:
    ec = next(r for r in ev.criteria if r.code == code)
    ap = review.create_appeal(db, ec, "Прошу перепроверить", "QA analyst", author_role, author_name)
    if action is not None:
        review.decide(db, ap, action=action, new_answer=new_answer, reason=reason, reviewer_label="Reviewer", add_to_fewshot=False,
                      reviewer_role="reviewer")
        db.refresh(ap)
        ap.decision.created_at = decided
    ap.created_at = created
    db.commit()
    return ap


@pytest.fixture(scope="module")
def seeded():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    SessionTest = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db = SessionTest()
    checklist = Checklist(name=SEED["name"], description=SEED["description"])
    db.add(checklist)
    db.flush()
    v1, c1 = _version(db, checklist, 1, active=True)
    v2, c2 = _version(db, checklist, 2, active=False, weights={"C7": 10})
    v3, _ = _version(db, checklist, 3, active=False)

    ru1 = _conversation(db, "o-ru-001", "agent chat", "RU", "Алина", 40.0)
    ru2 = _conversation(db, "o-ru-002", "agent chat", "RU", "Алексей", 200.0)
    kz3 = _conversation(db, "o-kz-003", "agent chat", "KZ", "Алина", 90.0)
    en4 = _conversation(db, "o-en-004", "voice call", "EN", None, None)
    ru5 = _conversation(db, "o-ru-005", "agent chat", "RU", "Алексей", 30.0)
    ru6 = _conversation(db, "o-ru-006", "agent chat", "RU", "Мария", 500.0)

    old1 = _evaluation(db, ru1, v1, c1, dict(PERFECT, C4="10", C6="4"), na={"C3"}, completed_at=datetime(2026, 10, 1, 9, 0, tzinfo=UTC),
                       model="anthropic:claude-sonnet-5-5", cost=0.03, tokens=(500, 100))
    e1 = _evaluation(db, ru1, v1, c1, PERFECT, na={"C3"}, completed_at=datetime(2026, 11, 7, 10, 0, tzinfo=UTC),
                     traces=((OPUS, 8, 0.02, 1000, 200, 500),))
    e2 = _evaluation(
        db, ru2, v1, c1, {"C1": "yes", "C3": "no", "C4": "10", "C5": "no", "C6": "4", "C7": "yes", "C8": "yes"}, errors={"C2"},
        completed_at=datetime(2026, 11, 6, 9, 0, tzinfo=UTC), traces=((OPUS, 6, 0.025, 1200, 300, 0), (SONNET, 2, 0.01, 900, 250, 0)),
        explanations={"C8": INSULT, "C3": "Идентификация — Нет. Оператор назвал остаток до проверки личности."},
        evidence={"C8": [{"turn_idx": 3, "speaker": "operator", "quote": "Вы вообще читать умеете?", "role": "supports"}],
                  "C3": [{"turn_idx": 0, "speaker": "client", "quote": "Какой у меня остаток?", "role": "context"},
                         {"turn_idx": 3, "speaker": "operator", "quote": "Остаток на счёте 150 000", "role": "supports", "offset_seconds": 31.5},
                         {"turn_idx": 5, "speaker": "operator", "quote": "Подтвердите ФИО", "role": "context"},
                         {"turn_idx": 4, "speaker": "client", "quote": "Спасибо", "role": "context"}]},
    )
    e3 = _evaluation(db, kz3, v1, c1, {"C1": "yes", "C2": "yes", "C4": 15, "C5": "yes", "C6": 7, "C7": "yes", "C8": "no"}, na={"C3"},
                     completed_at=datetime(2026, 11, 5, 15, 0, tzinfo=UTC), cost=0.12, tokens=(7000, 1500))
    e4 = _evaluation(db, en4, v1, c1, {"C1": "no", "C5": "yes", "C6": "7", "C8": "no"}, na={"C2", "C3", "C4", "C7"},
                     completed_at=datetime(2026, 11, 5, 16, 0, tzinfo=UTC), model="anthropic:claude-sonnet-5-5",
                     traces=((SONNET, 8, 0.0125, 1500, 400, 0),))
    e5 = _evaluation(db, ru5, v2, c2, PERFECT, completed_at=datetime(2026, 11, 7, 11, 0, tzinfo=UTC), traces=((OPUS, 2, 0.05, 2000, 500, 0),))
    f6 = Evaluation(conversation_id=ru6.id, checklist_version_id=v1.id, status="failed", provider="anthropic", mode="single",
                    model="anthropic:claude-opus-5-5", cost_usd=0.02, input_tokens=100, output_tokens=0, error="timeout",
                    completed_at=datetime(2026, 11, 6, 10, 0, tzinfo=UTC))
    db.add(f6)
    db.commit()

    a1 = _appeal(db, e1, "C1", created=NOW - timedelta(hours=5), decided=NOW - timedelta(hours=2), action="confirm")
    a2 = _appeal(db, e3, "C2", created=NOW - timedelta(hours=30), decided=NOW - timedelta(hours=20), action="confirm")
    a3 = _appeal(db, e3, "C7", created=NOW - timedelta(hours=30), decided=NOW - timedelta(hours=26), action="change", new_answer="no",
                 reason=C7_REASON)
    a4 = _appeal(db, e4, "C1", created=NOW - timedelta(hours=10), decided=NOW - timedelta(hours=9), action="change", new_answer="N/A",
                 reason="Приветствие не попало в запись")
    a5 = _appeal(db, e3, "C6", created=NOW - timedelta(hours=26), author_role="operator", author_name="Алина")
    ids = {"v1": v1.id, "v2": v2.id, "v3": v3.id, "old1": old1.id, "e1": e1.id, "e2": e2.id, "e3": e3.id, "e4": e4.id, "e5": e5.id,
           "f6": f6.id, "a1": a1.id, "a2": a2.id, "a3": a3.id, "a4": a4.id, "a5": a5.id}
    db.close()
    yield {"Session": SessionTest, "ids": ids}
    engine.dispose()


@pytest.fixture()
def db(seeded):
    session = seeded["Session"]()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def ids(seeded) -> dict[str, int]:
    return seeded["ids"]


@pytest.fixture()
def ov(db, ids) -> dict:
    return overview(db, ids["v1"], days=30, now=NOW)


# ----------------------------------------------------------------------------- fixture sanity and final values


def test_fixture_totals_come_from_the_review_service(db, ids):
    ev = {k: db.get(Evaluation, ids[k]) for k in ("old1", "e1", "e2", "e3", "e4", "e5")}
    assert ev["old1"].final_total_score == 76.25
    assert ev["e1"].final_total_score == 100.0
    assert ev["e2"].final_total_score == 36.47 and ev["e2"].critical_error is True
    assert ev["e3"].total_score == 88.13 and ev["e3"].final_total_score == 81.88
    assert ev["e4"].total_score == 63.75 and ev["e4"].final_total_score == 85.0  # C1 no -> N/A redistributes the weights
    assert ev["e5"].final_total_score == 100.0
    rows = {r.code: r for r in ev["e3"].criteria}
    assert rows["C6"].review_state == "under_review" and rows["C7"].review_state == "changed" and rows["C2"].review_state == "confirmed"


def test_lost_points_add_up_to_the_final_total(db):
    views = load_views(db)
    assert {v.ev.conversation_id for v in views} == {"o-ru-001", "o-ru-002", "o-kz-003", "o-en-004", "o-ru-005"}
    for v in views:
        assert sum(rv.lost for rv in v.rows) == pytest.approx(100.0 - v.total, abs=0.006), v.ev.conversation_id
    e4 = next(v for v in views if v.ev.conversation_id == "o-en-004")
    rows = {rv.code: rv for rv in e4.rows}
    # stored AI earned for C6 is 26.25 of 37.5; after the reviewer's N/A on C1 the final weight is 50 and C6 loses 15
    assert rows["C1"].na and rows["C1"].final_answer == "N/A" and rows["C1"].ai_answer == "no" and rows["C1"].changed
    assert rows["C6"].weight_applied == pytest.approx(50.0) and rows["C6"].lost == pytest.approx(15.0)
    e2 = next(v for v in views if v.ev.conversation_id == "o-ru-002")
    c2 = next(rv for rv in e2.rows if rv.code == "C2")
    assert not c2.na and not c2.answered and c2.lost == 0.0  # judge error: neither N/A nor counted


# ----------------------------------------------------------------------------- overview panels


def test_overview_header_coverage_and_quality(ov, ids):
    assert ov["generated_at"] == NOW.isoformat() and ov["checklist_version_id"] == ids["v1"] and ov["days"] == 30
    assert ov["coverage"] == {"conversations": 6, "evaluated": 4, "not_evaluated": 2, "failed": 1, "pct": 66.67}
    q = ov["quality"]
    totals = [36.47, 81.88, 85.0, 100.0]
    assert q["n"] == 4
    assert q["mean"] == _r(statistics.fmean(totals))
    assert q["median"] == _r((81.88 + 85.0) / 2)
    assert q["p25"] == pytest.approx(36.47 + 0.75 * (81.88 - 36.47), abs=0.006)
    assert q["p75"] == pytest.approx(88.75, abs=0.001)
    assert q["share_ge_85"] == 0.5 and q["share_lt_60"] == 0.25  # 85.0 counts as >= 85
    assert q["good_threshold"] == 85.0 and q["low_threshold"] == 60.0


def test_overview_critical_reuses_violations(ov, db, ids):
    c = ov["critical"]
    assert c["count"] == 1 and c["rate"] == 0.25 and c["reviewed"] == 0
    by_type = {t["code"]: t for t in c["by_type"]}
    assert [t["code"] for t in c["by_type"]] == ["insult", "paid_bypass", "disclosure", "other"]
    assert by_type["insult"]["count"] == 1 and by_type["insult"]["ru"] == "Оскорбление клиента" and by_type["insult"]["en"]
    assert by_type["paid_bypass"]["count"] == by_type["disclosure"]["count"] == by_type["other"]["count"] == 0
    assert violations(db, checklist_version_id=ids["v1"])["total"] == c["count"]


def test_overview_appeals_funnel(ov):
    assert ov["appeals"] == {
        "total": 5, "open": 1, "decided": 4, "changed": 2, "confirmed": 2, "overturn_rate": 0.5,
        "mean_hours_to_decision": 4.5,  # (3 + 10 + 4 + 1) / 4
        "oldest_open_hours": 26.0,
    }


def test_overview_agreement(ov):
    agreement = {a["code"]: a for a in ov["agreement"]}
    assert [a["code"] for a in ov["agreement"]] == CODES
    # C1: e1 confirmed (yes/yes), e4 changed (no -> N/A): N/A is a category of its own
    assert agreement["C1"]["title"] == "Установление контакта"
    assert agreement["C1"]["n_reviewed"] == 2 and agreement["C1"]["agreement_rate"] == 0.5
    assert agreement["C1"]["kappa"] == pytest.approx(1 / 3, abs=1e-4)
    assert (agreement["C2"]["n_reviewed"], agreement["C2"]["agreement_rate"], agreement["C2"]["kappa"]) == (1, 1.0, None)
    assert (agreement["C7"]["n_reviewed"], agreement["C7"]["agreement_rate"]) == (1, 0.0)
    assert agreement["C6"]["n_reviewed"] == 0 and agreement["C6"]["agreement_rate"] is None  # open appeal: not reviewed yet
    assert agreement["C4"] == {"code": "C4", "title": agreement["C4"]["title"], "n_reviewed": 0, "agreement_rate": None, "kappa": None}


def test_overview_by_language_and_channel(ov):
    assert ov["by_language"] == [
        {"language": "EN", "n": 1, "mean": 85.0, "critical": 0},
        {"language": "KZ", "n": 1, "mean": 81.88, "critical": 0},
        {"language": "RU", "n": 2, "mean": _r(statistics.fmean([100.0, 36.47])), "critical": 1},
    ]
    assert ov["by_channel"] == [
        {"channel": "agent chat", "n": 3, "mean": _r(statistics.fmean([100.0, 36.47, 81.88])), "critical": 1},
        {"channel": "voice call", "n": 1, "mean": 85.0, "critical": 0},
    ]


EXPECTED_LOST = {
    "C1": 0.0, "C2": 0.0, "C3": 20 * F2, "C4": 10 * F2 + 6.25, "C5": 15 * F2, "C6": 9 * F2 + 5.625 + 15.0, "C7": 6.25, "C8": 0.0,
}
TOTAL_LOST = sum(EXPECTED_LOST.values())


def test_overview_criteria(ov):
    criteria = {c["code"]: c for c in ov["criteria"]}
    assert [c["code"] for c in ov["criteria"]] == CODES
    assert TOTAL_LOST == pytest.approx(100 * 4 - (100.0 + 36.47 + 81.88 + 85.0), abs=0.01)
    c1 = criteria["C1"]
    assert c1 == {
        "code": "C1", "title": "Установление контакта", "section": "S1", "weight": 10.0, "answer_type": "binary", "is_critical": False,
        "n": 4, "n_applicable": 3, "pass_rate": 1.0, "mean_ratio": None, "na_rate": 0.25,
        "lost_points": 0.0, "lost_points_share": 0.0, "dialogs_losing_points": 0,
    }
    c2 = criteria["C2"]  # e2's judge error is neither N/A nor an answer
    assert (c2["n"], c2["n_applicable"], c2["pass_rate"], c2["na_rate"]) == (3, 2, 1.0, _r(1 / 3, 4))
    c3 = criteria["C3"]
    assert (c3["n"], c3["n_applicable"], c3["pass_rate"], c3["na_rate"]) == (4, 1, 0.0, 0.75)
    c4 = criteria["C4"]
    assert c4["answer_type"] == "scale" and c4["pass_rate"] is None and c4["mean_ratio"] == 0.75 and c4["na_rate"] == 0.25
    c6 = criteria["C6"]
    assert c6["mean_ratio"] == 0.7 and c6["n_applicable"] == 4 and c6["dialogs_losing_points"] == 3
    c7 = criteria["C7"]  # final answer (reviewer "no") is used, not the AI "yes"
    assert c7["pass_rate"] == _r(2 / 3, 4) and c7["lost_points"] == 6.25
    c8 = criteria["C8"]  # critical: pass = no gross error
    assert c8["is_critical"] and c8["weight"] == 0.0 and c8["pass_rate"] == 0.75 and c8["lost_points"] == 0.0
    for code, lost in EXPECTED_LOST.items():
        assert criteria[code]["lost_points"] == pytest.approx(lost, abs=0.006), code
        assert criteria[code]["lost_points_share"] == pytest.approx(lost / TOTAL_LOST, abs=1e-4), code
    assert sum(c["lost_points_share"] for c in ov["criteria"]) == pytest.approx(1.0, abs=1e-3)


def test_overview_top_failing(ov, ids):
    top = ov["top_failing"]
    assert [t["code"] for t in top] == ["C6", "C3", "C4"]
    c6 = top[0]
    assert c6["title"] and c6["lost_points"] == pytest.approx(EXPECTED_LOST["C6"], abs=0.006) and c6["dialogs_losing_points"] == 3
    ex = c6["example"]  # the dialog that lost the most on C6
    assert ex["conversation_id"] == "o-en-004" and ex["evaluation_id"] == ids["e4"] and ex["lost_points"] == 15.0
    assert ex["explanation"].startswith("Управление диалогом") and ex["final_answer"] == "7" and ex["operator_name"] is None
    assert ex["channel"] == "voice call" and ex["final_total_score"] == 85.0
    assert ex["evidence"] == [{"turn_idx": 1, "speaker": "operator", "quote": "Цитата C6", "role": "supports", "timestamp": "00:10",
                               "offset_seconds": 10.0}]
    c3 = top[1]["example"]
    assert c3["conversation_id"] == "o-ru-002" and c3["explanation"].startswith("Идентификация — Нет")
    assert c3["evidence"][0] == {"turn_idx": 3, "speaker": "operator", "quote": "Остаток на счёте 150 000", "role": "supports",
                                 "timestamp": "00:30", "offset_seconds": 31.5}
    assert top[2]["example"]["conversation_id"] == "o-ru-002" and top[2]["example"]["lost_points"] == _r(10 * F2)


def test_overview_trend_and_period(db, ids, ov):
    trend = ov["trend"]
    assert len(trend) == 30 and trend[0]["date"] == "2026-10-09" and trend[-1]["date"] == "2026-11-07"
    by_day = {t["date"]: t for t in trend}
    assert by_day["2026-11-07"] == {"date": "2026-11-07", "evaluated": 1, "mean_total": 100.0, "critical": 0}
    assert by_day["2026-11-06"] == {"date": "2026-11-06", "evaluated": 1, "mean_total": 36.47, "critical": 1}
    assert by_day["2026-11-05"] == {"date": "2026-11-05", "evaluated": 2, "mean_total": _r((81.88 + 85.0) / 2), "critical": 0}
    assert by_day["2026-11-01"] == {"date": "2026-11-01", "evaluated": 0, "mean_total": None, "critical": 0}
    assert sum(t["evaluated"] for t in trend) == 4  # the superseded 2026-10-01 evaluation is not a latest one
    assert ov["period"] == {"days": 30, "from": "2026-10-09", "to": "2026-11-07", "evaluated": 4, "mean_total": ov["quality"]["mean"],
                            "critical": 1}
    week = overview(db, ids["v1"], days=7, now=NOW)
    assert [t["date"] for t in week["trend"]] == [f"2026-11-0{d}" for d in range(1, 8)]
    day = overview(db, ids["v1"], days=1, now=NOW)
    assert day["trend"] == [{"date": "2026-11-07", "evaluated": 1, "mean_total": 100.0, "critical": 0}]
    assert day["period"]["evaluated"] == 1 and day["quality"] == ov["quality"]  # KPIs do not depend on days
    assert len(overview(db, ids["v1"], days=0, now=NOW)["trend"]) == 1  # direct callers are clamped to 1..365
    assert len(overview(db, ids["v1"], days=10_000, now=NOW)["trend"]) == 365


def test_overview_cost(ov, db, ids):
    cost = ov["cost"]
    assert cost["total_usd"] == 0.6  # old1 0.03 + e1 0.16 + e2 0.17 + e3 0.12 + e4 0.10 + failed 0.02
    assert cost["per_dialog_usd"] == 0.15  # fully loaded: 0.60 / 4 evaluated dialogs
    assert cost["evaluations"] == 6
    assert cost["tokens_in"] == 500 + 8000 + 9000 + 7000 + 12000 + 100
    assert cost["tokens_out"] == 100 + 1600 + 2300 + 1500 + 3200
    assert cost["cache_read_tokens"] == 4000
    assert cost["period_usd"] == 0.57  # the 2026-10-01 run is outside the 30-day window
    assert cost["by_model"] == [
        # traces of e1/e2 plus e3 and the failed run attributed through Evaluation.model
        {"provider": "anthropic", "model": "claude-opus-5-5", "requests": 14, "tokens_in": 22300, "tokens_out": 4900, "cost_usd": 0.45,
         "share": 0.75},
        {"provider": "anthropic", "model": "claude-sonnet-5-5", "requests": 10, "tokens_in": 14300, "tokens_out": 3800, "cost_usd": 0.15,
         "share": 0.25},
    ]
    everything = overview(db, None, now=NOW)["cost"]
    assert everything["total_usd"] == 0.7 and everything["per_dialog_usd"] == 0.14 and everything["evaluations"] == 7


def test_overview_first_response_and_operators(ov):
    assert ov["first_response"] == {"mean_seconds": 172.0, "over_threshold_share": 0.4, "threshold_seconds": 180.0, "n": 5}
    ops = ov["operators"]
    assert ops["count"] == 3 and ops["evaluated"] == 2 and ops["at_risk_threshold"] == 60.0
    assert ops["at_risk"] == [{"operator_name": "Алексей", "n": 1, "mean_total": 36.47, "critical_count": 1, "reasons": ["critical", "low_mean"]}]


def test_overview_scopes(db, ids):
    everything = overview(db, None, now=NOW)
    assert everything["checklist_version_id"] is None
    assert everything["coverage"]["evaluated"] == 5 and everything["coverage"]["failed"] == 1
    assert everything["quality"]["mean"] == _r(statistics.fmean([100.0, 36.47, 81.88, 85.0, 100.0]))
    assert {c["code"]: c["weight"] for c in everything["criteria"]}["C7"] == 5.0  # labels from the version most dialogs use
    assert everything["operators"]["at_risk"] == [
        {"operator_name": "Алексей", "n": 2, "mean_total": _r(statistics.fmean([36.47, 100.0])), "critical_count": 1, "reasons": ["critical"]}
    ]
    v2 = overview(db, ids["v2"], now=NOW)
    assert v2["coverage"] == {"conversations": 6, "evaluated": 1, "not_evaluated": 5, "failed": 0, "pct": 16.67}
    assert v2["quality"]["mean"] == 100.0 and v2["critical"]["count"] == 0 and v2["appeals"]["total"] == 0
    assert {c["code"]: c["weight"] for c in v2["criteria"]}["C7"] == 10.0
    assert v2["cost"]["total_usd"] == 0.1 and v2["cost"]["by_model"][0]["requests"] == 2
    assert v2["top_failing"] == []


def _assert_empty_panels(ov: dict) -> None:
    assert ov["coverage"]["evaluated"] == 0
    assert ov["quality"] == {"n": 0, "mean": None, "median": None, "p25": None, "p75": None, "share_ge_85": None, "share_lt_60": None,
                             "good_threshold": 85.0, "low_threshold": 60.0}
    assert ov["critical"]["count"] == 0 and ov["critical"]["rate"] is None and sum(t["count"] for t in ov["critical"]["by_type"]) == 0
    assert ov["by_language"] == [] and ov["by_channel"] == [] and ov["top_failing"] == []
    assert len(ov["trend"]) == 30 and all(t == {"date": t["date"], "evaluated": 0, "mean_total": None, "critical": 0} for t in ov["trend"])
    assert ov["period"]["evaluated"] == 0 and ov["period"]["mean_total"] is None
    assert ov["cost"] == {"total_usd": 0.0, "per_dialog_usd": None, "evaluations": 0, "tokens_in": 0, "tokens_out": 0, "cache_read_tokens": 0,
                          "period_usd": 0.0, "by_model": []}
    assert ov["operators"]["at_risk"] == [] and ov["operators"]["evaluated"] == 0
    assert all(a["n_reviewed"] == 0 and a["agreement_rate"] is None and a["kappa"] is None for a in ov["agreement"])
    for c in ov["criteria"]:
        assert c["n"] == 0 and c["pass_rate"] is None and c["mean_ratio"] is None and c["na_rate"] is None
        assert c["lost_points"] == 0.0 and c["lost_points_share"] is None


def test_overview_version_without_evaluations(db, ids):
    ov = overview(db, ids["v3"], now=NOW)
    _assert_empty_panels(ov)
    assert ov["coverage"] == {"conversations": 6, "evaluated": 0, "not_evaluated": 6, "failed": 0, "pct": 0.0}
    assert [c["code"] for c in ov["criteria"]] == CODES  # the checklist still labels the (empty) table
    assert ov["appeals"]["total"] == 0 and ov["appeals"]["overturn_rate"] is None and ov["appeals"]["mean_hours_to_decision"] is None


def test_overview_empty_database():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        ov = overview(session, now=NOW)
        _assert_empty_panels(ov)
        assert ov["coverage"] == {"conversations": 0, "evaluated": 0, "not_evaluated": 0, "failed": 0, "pct": None}
        assert ov["criteria"] == [] and ov["agreement"] == []
        assert ov["appeals"] == {"total": 0, "open": 0, "decided": 0, "changed": 0, "confirmed": 0, "overturn_rate": None,
                                 "mean_hours_to_decision": None, "oldest_open_hours": None}
        assert ov["first_response"] == {"mean_seconds": None, "over_threshold_share": None, "threshold_seconds": 180.0, "n": 0}
        assert ov["operators"] == {"count": 0, "evaluated": 0, "at_risk_threshold": 60.0, "at_risk": []}
        with pytest.raises(OperatorNotFound):
            operator_scorecard(session, "Алина")
    engine.dispose()


def test_overview_and_scorecard_are_read_only(db, ids):
    overview(db, ids["v1"], now=NOW)
    operator_scorecard(db, "Алина", ids["v1"], now=NOW)
    operator_scorecard(db, "—", now=NOW)
    assert not db.new and not db.dirty and not db.deleted


def test_helpers():
    assert percentile([], 0.5) is None and percentile([7.0], 0.25) == 7.0
    assert percentile([1.0, 2.0, 3.0, 4.0], 0.25) == pytest.approx(1.75) and percentile([1.0, 2.0, 3.0, 4.0], 0.75) == pytest.approx(3.25)
    assert split_model("anthropic", "anthropic:claude-opus-5-5") == ("anthropic", "claude-opus-5-5")
    assert split_model("local", "local:qwen3:8b") == ("local", "qwen3:8b")
    assert split_model("ensemble", "anthropic:a,anthropic:b") == ("ensemble", "anthropic:a,anthropic:b")
    assert split_model("local", "qwen3:8b") == ("local", "qwen3:8b") and split_model(None, None) == ("—", "—")


# ----------------------------------------------------------------------------- operator scorecards


def test_scorecard_alina(db, ids):
    sc = operator_scorecard(db, "Алина", ids["v1"], now=NOW)
    assert sc["operator_name"] == "Алина" and sc["checklist_version_id"] == ids["v1"] and sc["generated_at"] == NOW.isoformat()
    assert sc["conversations_total"] == 2 and sc["evaluated"] == 2 and sc["critical_count"] == 0
    assert sc["mean_total"] == _r((100.0 + 81.88) / 2) and sc["median_total"] == sc["mean_total"]
    assert sc["team_mean_total"] == _r(statistics.fmean([36.47, 81.88, 85.0, 100.0]))
    assert sc["languages"] == ["KZ", "RU"] and sc["channels"] == ["agent chat"]
    assert sc["conversations"] == [  # newest first
        {"id": "o-ru-001", "channel": "agent chat", "language": "RU", "status": "evaluated", "evaluation_id": ids["e1"], "total_score": 100.0,
         "final_total_score": 100.0, "critical_error": False, "evaluated_at": "2026-11-07T10:00:00+00:00", "review_state": "reviewed"},
        {"id": "o-kz-003", "channel": "agent chat", "language": "KZ", "status": "evaluated", "evaluation_id": ids["e3"], "total_score": 88.13,
         "final_total_score": 81.88, "critical_error": False, "evaluated_at": "2026-11-05T15:00:00+00:00", "review_state": "under_review"},
    ]
    criteria = {c["code"]: c for c in sc["criteria"]}
    assert [c["code"] for c in sc["criteria"]] == CODES
    assert criteria["C4"]["mean_ratio"] == 0.875 and criteria["C4"]["lost_points"] == 6.25 and criteria["C4"]["team_mean_ratio"] == 0.75
    assert criteria["C6"]["mean_ratio"] == 0.85 and criteria["C6"]["lost_points"] == _r(5.625) and criteria["C6"]["team_mean_ratio"] == 0.7
    assert criteria["C7"]["pass_rate"] == 0.5 and criteria["C7"]["team_pass_rate"] == _r(2 / 3, 4)
    assert criteria["C3"]["n_applicable"] == 0 and criteria["C3"]["pass_rate"] is None and criteria["C3"]["na_rate"] == 1.0
    assert criteria["C1"]["pass_rate"] == 1.0 and criteria["C1"]["lost_points"] == 0.0

    # coaching: C7 and C4 both lost 6.25; C7 first (lower pass rate); C6 (5.63) is third and left out
    points = sc["coaching_points"]
    assert [p["code"] for p in points] == ["C7", "C4"]
    c7 = points[0]
    assert c7["lost_points"] == 6.25 and c7["dialogs_losing_points"] == 1 and c7["pass_rate"] == 0.5 and c7["team_pass_rate"] == _r(2 / 3, 4)
    assert c7["expected"] == SEED["criteria"][6]["instruction"]
    assert len(c7["examples"]) == 1
    ex = c7["examples"][0]
    assert ex["conversation_id"] == "o-kz-003" and ex["evaluation_id"] == ids["e3"] and ex["ai_answer"] == "yes" and ex["final_answer"] == "no"
    assert ex["review_state"] == "changed" and ex["reviewer_reason"] == C7_REASON and ex["lost_points"] == 6.25
    assert ex["explanation"] and ex["evidence"][0]["quote"] == "Цитата C7"
    c4 = points[1]
    assert c4["mean_ratio"] == 0.875 and c4["examples"][0]["conversation_id"] == "o-kz-003" and c4["examples"][0]["final_answer"] == 15
    assert c4["examples"][0]["reviewer_reason"] is None

    assert [s["code"] for s in sc["strengths"]] == ["C2", "C5"]  # 100 % and weight 15; C8 is never a strength
    assert sc["strengths"][0] == {"code": "C2", "title": "Выявление потребности", "answer_type": "binary", "weight": 15.0, "pass_rate": 1.0,
                                  "mean_ratio": None, "team_pass_rate": 1.0, "team_mean_ratio": None, "n_applicable": 2}
    assert sc["violations"]["total"] == 0 and sc["violations"]["items"] == []

    appeals = sc["appeals"]
    assert (appeals["total"], appeals["open"], appeals["decided"], appeals["changed"], appeals["confirmed"]) == (4, 1, 3, 1, 2)
    assert appeals["oldest_open_hours"] == 26.0 and appeals["mean_hours_to_decision"] == _r((3 + 10 + 4) / 3)
    first = appeals["items"][0]  # open first
    assert first["id"] == ids["a5"] and first["status"] == "open" and first["decision"] is None
    assert first["code"] == "C6" and first["conversation_id"] == "o-kz-003" and first["author_role"] == "operator"
    assert first["author_name"] == "Алина" and first["review_state"] == "under_review" and first["current_evaluation"] is True
    changed = next(a for a in appeals["items"] if a["id"] == ids["a3"])
    assert changed["decision"]["action"] == "change" and changed["ai_answer"] == "yes" and changed["final_answer"] == "no"
    assert changed["title"] == "Завершение" and changed["evaluation_id"] == ids["e3"]
    assert [a["id"] for a in appeals["items"][1:]] == sorted([ids["a1"], ids["a2"], ids["a3"]], reverse=True)


def test_scorecard_alexey(db, ids):
    sc = operator_scorecard(db, "Алексей", ids["v1"], now=NOW)
    assert sc["evaluated"] == 1 and sc["conversations_total"] == 2 and sc["mean_total"] == 36.47 and sc["critical_count"] == 1
    statuses = {c["id"]: c["status"] for c in sc["conversations"]}
    assert statuses == {"o-ru-002": "evaluated", "o-ru-005": "not_evaluated"}  # o-ru-005 only has a v2 evaluation
    assert [c["id"] for c in sc["conversations"]] == ["o-ru-002", "o-ru-005"]
    assert sc["conversations"][1] == {"id": "o-ru-005", "channel": "agent chat", "language": "RU", "status": "not_evaluated", "evaluation_id": None,
                                      "total_score": None, "final_total_score": None, "critical_error": False, "evaluated_at": None,
                                      "review_state": "none"}
    assert [p["code"] for p in sc["coaching_points"]] == ["C3", "C5"]
    c3 = sc["coaching_points"][0]
    assert c3["lost_points"] == _r(20 * F2) and c3["pass_rate"] == 0.0
    ex = c3["examples"][0]
    assert ex["conversation_id"] == "o-ru-002" and ex["explanation"].startswith("Идентификация — Нет") and ex["final_answer"] == "no"
    # supporting quotes first, then context in transcript order, at most three
    assert [e["quote"] for e in ex["evidence"]] == ["Остаток на счёте 150 000", "Какой у меня остаток?", "Подтвердите ФИО"]
    assert [e["role"] for e in ex["evidence"]] == ["supports", "context", "context"]
    assert [s["code"] for s in sc["strengths"]] == ["C1", "C7"]  # C2 had a judge error only: not rankable
    v = sc["violations"]
    assert v["total"] == 1 and v["counts"]["insult"] == 1 and v["items"][0]["conversation_id"] == "o-ru-002"
    assert v["items"][0]["types"] == ["insult"] and v["items"][0]["evidence"][0]["quote"] == "Вы вообще читать умеете?"
    assert sc["appeals"]["total"] == 0 and sc["appeals"]["items"] == []

    everything = operator_scorecard(db, "Алексей", now=NOW)
    assert everything["evaluated"] == 2 and everything["mean_total"] == _r(statistics.fmean([36.47, 100.0]))
    assert everything["conversations"][0]["id"] == "o-ru-005"  # evaluated 2026-11-07 (v2), newest first


def test_scorecard_unknown_operator_group(db, ids):
    sc = operator_scorecard(db, "—", ids["v1"], now=NOW)
    assert sc["operator_name"] == "—" and [c["id"] for c in sc["conversations"]] == ["o-en-004"] and sc["mean_total"] == 85.0
    assert sc["channels"] == ["voice call"]
    assert [p["code"] for p in sc["coaching_points"]] == ["C6"]
    assert sc["coaching_points"][0]["lost_points"] == 15.0 and sc["coaching_points"][0]["examples"][0]["conversation_id"] == "o-en-004"
    assert [s["code"] for s in sc["strengths"]] == ["C5"]  # C1 became N/A after review
    assert sc["violations"]["total"] == 0 and set(sc["violations"]["counts"]) == {"insult", "paid_bypass", "disclosure", "other"}
    assert sc["appeals"]["total"] == 1 and sc["appeals"]["changed"] == 1 and sc["appeals"]["items"][0]["final_answer"] == "N/A"


def test_scorecard_operator_without_evaluations(db, ids):
    sc = operator_scorecard(db, "Мария", ids["v1"], now=NOW)
    assert sc["evaluated"] == 0 and sc["mean_total"] is None and sc["median_total"] is None and sc["critical_count"] == 0
    assert sc["conversations"] == [{"id": "o-ru-006", "channel": "agent chat", "language": "RU", "status": "failed", "evaluation_id": None,
                                    "total_score": None, "final_total_score": None, "critical_error": False, "evaluated_at": None,
                                    "review_state": "none"}]
    assert all(c["n"] == 0 and c["pass_rate"] is None and c["mean_ratio"] is None and c["lost_points"] == 0.0 for c in sc["criteria"])
    assert sc["strengths"] == [] and sc["coaching_points"] == []
    assert sc["violations"]["total"] == 0 and sc["appeals"]["total"] == 0 and sc["appeals"]["items"] == []


def test_resolve_operator(db):
    assert resolve_operator(db, "Алина")[0] == "Алина"
    assert resolve_operator(db, "  алина ")[0] == "Алина"  # case and surrounding spaces
    name, convs = resolve_operator(db, "—")
    assert name == "—" and [c.id for c in convs] == ["o-en-004"]
    for missing in ("Nobody", "", "   "):
        with pytest.raises(OperatorNotFound):
            resolve_operator(db, missing)


# ----------------------------------------------------------------------------- HTTP


@pytest.fixture(scope="module")
def client(seeded):
    app = FastAPI()
    app.include_router(insights_router)

    @app.exception_handler(HTTPException)  # the same envelope as cqa.main
    async def http_exc(request: Request, exc: HTTPException):
        detail = exc.detail if isinstance(exc.detail, dict) else {"code": "error", "message": str(exc.detail)}
        return JSONResponse(status_code=exc.status_code, content={"error": detail})

    def _override():
        session = seeded["Session"]()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _override
    with TestClient(app) as c:
        yield c


def test_http_overview(client, ids):
    r = client.get("/api/overview", params={"checklist_version_id": ids["v1"]})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"generated_at", "checklist_version_id", "days", "coverage", "quality", "critical", "appeals", "agreement",
                         "by_language", "by_channel", "criteria", "top_failing", "trend", "period", "cost", "first_response", "operators",
                         "scope"}
    assert body["coverage"]["evaluated"] == 4 and len(body["trend"]) == 30 and body["days"] == 30
    today = datetime.now(UTC).date()
    assert body["trend"][-1]["date"] in {today.isoformat(), (today - timedelta(days=1)).isoformat()}  # midnight-safe
    assert client.get("/api/overview").json()["coverage"]["evaluated"] == 5
    assert len(client.get("/api/overview", params={"days": 7}).json()["trend"]) == 7
    assert len(client.get("/api/overview", params={"days": 365}).json()["trend"]) == 365


@pytest.mark.parametrize(("params", "field"), [
    ({"days": 0}, "days"), ({"days": 366}, "days"), ({"days": "abc"}, "days"), ({"days": "7.5"}, "days"),
    ({"checklist_version_id": "abc"}, "checklist_version_id"),
])
def test_http_overview_validation_envelope(client, params, field):
    r = client.get("/api/overview", params=params)
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "validation_error" and err["message"].startswith("Некорректные параметры запроса")
    assert [e["field"] for e in err["errors"]] == [field] and err["errors"][0]["message"]


def test_http_unknown_checklist_version(client):
    for url in ("/api/overview", "/api/operators/%D0%90%D0%BB%D0%B8%D0%BD%D0%B0"):
        r = client.get(url, params={"checklist_version_id": 999})
        assert r.status_code == 404
        assert r.json() == {"error": {"code": "checklist_version_not_found", "message": "Версия чек-листа 999 не найдена"}}


def test_http_operator_scorecard(client, ids):
    encoded = quote("Алина")
    r = client.get(f"/api/operators/{encoded}", params={"checklist_version_id": ids["v1"]})
    assert r.status_code == 200
    body = r.json()
    assert body["operator_name"] == "Алина" and body["evaluated"] == 2
    assert [p["code"] for p in body["coaching_points"]] == ["C7", "C4"]
    assert client.get("/api/operators/Алина").json()["operator_name"] == "Алина"  # raw UTF-8 path
    assert client.get(f"/api/operators/{quote(encoded)}").json()["operator_name"] == "Алина"  # double-encoded
    assert client.get(f"/api/operators/{quote('—')}").json()["operator_name"] == "—"
    assert client.get("/api/operators/%D0%B0%D0%BB%D0%B8%D0%BD%D0%B0").json()["operator_name"] == "Алина"  # lower case


def test_http_operator_not_found(client):
    r = client.get("/api/operators/Nobody")
    assert r.status_code == 404
    assert r.json() == {"error": {"code": "operator_not_found", "message": "Оператор «Nobody» не найден"}}
    r = client.get("/api/operators/%20")
    assert r.status_code == 404 and r.json()["error"] == {"code": "operator_not_found", "message": "Не указано имя оператора"}


def test_http_existing_insights_endpoints_unchanged(client, ids):
    ops = client.get("/api/operators", params={"checklist_version_id": ids["v1"]})
    assert ops.status_code == 200 and isinstance(ops.json(), list)
    alina = next(o for o in ops.json() if o["operator_name"] == "Алина")
    assert set(alina) == {"operator_name", "conversations", "evaluated", "mean_total", "critical_count", "languages", "channels",
                          "open_appeals", "last_evaluated_at"}
    assert alina["conversations"] == 2 and alina["open_appeals"] == 1
    v = client.get("/api/violations", params={"checklist_version_id": ids["v1"]})
    assert v.status_code == 200 and v.json()["total"] == 1 and set(v.json()) == {"total", "counts", "types", "items"}


def test_http_openapi_lists_new_routes(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert {"/api/overview", "/api/operators/{name}", "/api/operators", "/api/violations"} <= set(paths)
