"""Analytics summary, CSV and PDF export over an in-memory SQLite fixture."""
from __future__ import annotations

import csv
import io
import json
import statistics
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from cqa.analytics.service import cohen_kappa, summary
from cqa.db import Base, get_db
from cqa.export.csv_export import evaluations_csv, header_for
from cqa.export.pdf_export import evaluation_pdf
from cqa.models import (
    Appeal,
    Checklist,
    ChecklistVersion,
    Conversation,
    Criterion,
    Decision,
    Evaluation,
    EvaluationCriterion,
    Turn,
)
from cqa.routers.analytics import router as analytics_router
from cqa.routers.export import router as export_router
from cqa.scoring.engine import CriterionInput, answer_points, compute_score

SEED = json.loads((Path(__file__).resolve().parents[1] / "seeds" / "checklist_freedom_v1.json").read_text())
CODES = [c["code"] for c in SEED["criteria"]]
NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)

PERFECT = {"C1": "yes", "C2": "yes", "C3": "yes", "C4": 20, "C5": "yes", "C6": 10, "C7": "yes", "C8": "no"}


# ----------------------------------------------------------------------------- fixture helpers


def _seed_checklist(db: Session) -> tuple[ChecklistVersion, list[Criterion]]:
    checklist = Checklist(name=SEED["name"], description=SEED["description"])
    db.add(checklist)
    db.flush()
    version = ChecklistVersion(
        checklist_id=checklist.id,
        version_no=1,
        is_active=True,
        change_note=SEED["version_note"],
        scoring_rules=SEED["scoring_rules"],
        sections=SEED["sections"],
    )
    db.add(version)
    db.flush()
    crits: list[Criterion] = []
    for i, c in enumerate(SEED["criteria"], start=1):
        crit = Criterion(
            checklist_version_id=version.id,
            code=c["code"],
            order=i,
            section=c["section"],
            title=c["title"],
            instruction=c["instruction"],
            answer_type=c["answer_type"],
            options=c["options"],
            max_points=c["max_points"],
            weight=c["weight"],
            na_allowed=c["na_allowed"],
            na_condition=c["na_condition"],
            is_critical=c["is_critical"],
            critical_value=c.get("critical_value"),
            channel_overrides=c["channel_overrides"],
        )
        db.add(crit)
        crits.append(crit)
    db.flush()
    return version, crits


def _conversation(db: Session, cid: str, channel: str, language: str, operator: str | None, first_response: float | None, turns: list[tuple[str, str, str]]) -> Conversation:
    conv = Conversation(id=cid, channel=channel, language=language, operator_name=operator, turn_count=len(turns), first_response_seconds=first_response)
    db.add(conv)
    db.flush()
    for idx, (speaker, ts, text) in enumerate(turns):
        db.add(Turn(conversation_id=cid, idx=idx, timestamp_raw=ts, offset_seconds=float(idx * 20), speaker=speaker, text=text, segment="operator" if speaker == "operator" else "pre"))
    db.flush()
    return conv


def _score(version: ChecklistVersion, crits: list[Criterion], answers: dict[str, object], na: set[str]):
    items = []
    for c in crits:
        if c.code in na:
            items.append(CriterionInput(c.code, c.weight, c.max_points, None, applicable=False, section=c.section, is_critical=c.is_critical))
        else:
            ans = answers[c.code]
            hit = bool(c.is_critical and ans == c.critical_value)
            items.append(CriterionInput(c.code, c.weight, c.max_points, answer_points(c.options, ans), True, c.section, c.is_critical, hit))
    return items, compute_score(version.scoring_rules, items, version.sections)


def _evaluation(
    db: Session,
    conv: Conversation,
    version: ChecklistVersion,
    crits: list[Criterion],
    answers: dict[str, object],
    *,
    na: set[str] = frozenset(),
    provider: str = "anthropic",
    model: str = "claude-opus-5-5",
    cost: float = 0.1,
    latency: int = 30000,
    status: str = "completed",
    reviews: dict[str, tuple[str, object]] | None = None,
) -> Evaluation:
    items, res = _score(version, crits, answers, set(na))
    ev = Evaluation(
        conversation_id=conv.id,
        checklist_version_id=version.id,
        status=status,
        provider=provider,
        model=model,
        total_score=res.total if status == "completed" else None,
        final_total_score=res.total if status == "completed" else None,
        critical_error=res.critical if status == "completed" else False,
        cost_usd=cost,
        latency_ms=latency,
        completed_at=NOW if status == "completed" else None,
    )
    db.add(ev)
    db.flush()
    rows: dict[str, EvaluationCriterion] = {}
    for c, item in zip(crits, items):
        cr = res.criteria[c.code]
        row = EvaluationCriterion(
            evaluation_id=ev.id,
            criterion_id=c.id,
            code=c.code,
            applicable=item.applicable,
            na_reason=c.na_condition if not item.applicable else None,
            answer=None if not item.applicable else answers[c.code],
            score=item.points,
            max_points=c.max_points,
            weight=c.weight,
            weight_applied=cr.weight_applied,
            earned=cr.earned,
            confidence=0.9,
            explanation=f"{c.title} — краткое обоснование для {conv.id}.",
            evidence=[{"turn_idx": 1, "speaker": "operator", "quote": "Здравствуйте! Меня зовут Алина.", "offset_seconds": 20.0, "role": "supports"}],
        )
        db.add(row)
        rows[c.code] = row
    db.flush()
    if reviews:
        final_answers = dict(answers)
        for code, (action, new_answer) in reviews.items():
            row = rows[code]
            appeal = Appeal(evaluation_criterion_id=row.id, comment="Прошу перепроверить", status="decided")
            db.add(appeal)
            db.flush()
            db.add(Decision(appeal_id=appeal.id, action=action, new_answer=new_answer, reason="Решение ревьюера"))
            if action == "confirm":
                row.review_state = "confirmed"
                row.final_answer = row.answer
                row.final_score = row.score
                row.final_earned = row.earned
            else:
                row.review_state = "changed"
                row.final_answer = new_answer
                final_answers[code] = new_answer
        _, res2 = _score(version, crits, final_answers, set(na))
        for code, (action, _new) in reviews.items():
            if action == "change":
                crit = next(c for c in crits if c.code == code)
                rows[code].final_score = answer_points(crit.options, final_answers[code])
                rows[code].final_earned = res2.criteria[code].earned
        ev.final_total_score = res2.total
        ev.critical_error = res2.critical
    db.commit()
    return ev


@pytest.fixture(scope="module")
def engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture(scope="module")
def seeded(engine) -> dict:
    SessionTest = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db = SessionTest()
    version, crits = _seed_checklist(db)

    ru_chat = _conversation(
        db, "t-ru-001", "agent chat", "RU", "Алина", 37.0,
        [
            ("client", "00:00", "Здравствуйте, не могу войти в приложение"),
            ("bot", "00:05", "Здравствуйте! Я бот Freedom. Переключаю на оператора."),
            ("operator", "00:42", "Здравствуйте! Меня зовут Алина. Подскажите, какую ошибку видите?"),
            ("client", "01:10", "Пишет «неверный пароль»"),
            ("operator", "01:40", "Нажмите «Забыли пароль», код придёт по SMS."),
            ("operator", "02:30", "Остались ли ещё вопросы? Хорошего дня!"),
            ("system", "02:45", "Чат закрыт"),
        ],
    )
    kz_chat = _conversation(
        db, "t-kz-002", "agent chat", "KZ", "Нұрлан", 200.0,
        [
            ("client", "00:00", "Сәлеметсіз бе, комиссия туралы сұрағым бар"),
            ("operator", "03:20", "Сәлеметсіз бе! Менің атым Нұрлан. Қандай комиссия қызықтырады?"),
            ("client", "03:50", "Акция сатып алу комиссиясы"),
            ("operator", "04:10", "Комиссия 0,05% құрайды. Сау болыңыз!"),
            ("system", "04:30", "Чат жабылды"),
        ],
    )
    ru_call = _conversation(
        db, "t-ru-003", "voice call", "RU", None, None,
        [
            ("system", "00:00", "Клиент подключён"),
            ("client", "00:03", "Алло, здравствуйте"),
            ("operator", "00:10", "Да. Остаток на вашем счёте — 150 000 тенге."),
            ("client", "00:30", "Спасибо"),
            ("system", "00:40", "Клиент завершил разговор"),
        ],
    )

    # older evaluation of the RU chat (must not count as the latest one)
    old = _evaluation(db, ru_chat, version, crits, dict(PERFECT, C4=10, C6=4), na={"C3"}, provider="local", model="qwen3:8b", cost=0.0, latency=5000)
    e1 = _evaluation(db, ru_chat, version, crits, PERFECT, na={"C3"}, cost=0.17, latency=41000, reviews={"C1": ("confirm", None), "C7": ("confirm", None)})
    e2 = _evaluation(
        db, kz_chat, version, crits, dict(PERFECT, C4=15, C6=7), na={"C2", "C3"}, cost=0.12, latency=30000,
        reviews={"C1": ("confirm", None), "C7": ("change", "no")},
    )
    e3 = _evaluation(
        db, ru_call, version, crits,
        {"C1": "no", "C2": "no", "C3": "no", "C4": 5, "C5": "no", "C6": 4, "C7": "no", "C8": "yes"},
        model="claude-sonnet-5-5", cost=0.05, latency=20000,
    )
    failed = _evaluation(db, ru_call, version, crits, PERFECT, status="failed", cost=0.0, latency=0)
    db.commit()
    return {"Session": SessionTest, "db": db, "version": version, "evals": {"old": old, "e1": e1, "e2": e2, "e3": e3, "failed": failed}}


@pytest.fixture()
def db(seeded) -> Session:
    return seeded["db"]


# ----------------------------------------------------------------------------- analytics


def test_fixture_scores(seeded):
    e = seeded["evals"]
    assert e["e1"].total_score == 100.0 and e["e1"].final_total_score == 100.0
    assert e["e2"].total_score == 85.38 and e["e2"].final_total_score == 77.69
    assert e["e3"].total_score == 11.0 and e["e3"].critical_error is True


def test_summary_counts_and_totals(db, seeded):
    s = summary(db, seeded["version"].id)
    e = seeded["evals"]
    finals = [e["e1"].final_total_score, e["e2"].final_total_score, e["e3"].final_total_score]
    assert s["count"] == 3
    assert s["mean_total"] == pytest.approx(statistics.fmean(finals), abs=0.01)
    assert s["median_total"] == e["e2"].final_total_score
    assert s["critical_count"] == 1
    assert [d["bucket"] for d in s["distribution"]] == ["0-59", "60-69", "70-79", "80-89", "90-100"]
    assert {d["bucket"]: d["n"] for d in s["distribution"]} == {"0-59": 1, "60-69": 0, "70-79": 1, "80-89": 0, "90-100": 1}


def test_summary_unfiltered_matches_filtered(db, seeded):
    assert summary(db)["count"] == summary(db, seeded["version"].id)["count"] == 3


def test_summary_groups(db, seeded):
    s = summary(db, seeded["version"].id)
    by_lang = {g["language"]: g for g in s["by_language"]}
    assert by_lang["RU"]["n"] == 2 and by_lang["KZ"]["n"] == 1
    assert by_lang["RU"]["mean_total"] == pytest.approx((100.0 + 11.0) / 2, abs=0.01)
    by_ch = {g["channel"]: g for g in s["by_channel"]}
    assert by_ch["agent chat"]["n"] == 2 and by_ch["voice call"]["n"] == 1
    assert by_ch["agent chat"]["mean_total"] == pytest.approx((100.0 + 77.69) / 2, abs=0.01)


def test_summary_heatmap_and_na(db, seeded):
    s = summary(db, seeded["version"].id)
    cells = {(h["code"], h["language"], h["channel"]): h for h in s["criteria_heatmap"]}
    c1 = cells[("C1", "ALL", "ALL")]
    assert c1["title"] == "Установление контакта"
    assert c1["n"] == 3 and c1["value"] == pytest.approx(2 / 3, abs=1e-4)
    assert c1["yes_rate_or_mean"] == c1["value"]
    c4 = cells[("C4", "ALL", "ALL")]
    assert c4["n"] == 3 and c4["value"] == pytest.approx((20 + 15 + 5) / 20 / 3, abs=1e-4)
    assert cells[("C3", "ALL", "ALL")]["n"] == 1 and cells[("C3", "ALL", "ALL")]["value"] == 0.0
    assert cells[("C1", "RU", "voice call")]["value"] == 0.0
    assert cells[("C1", "KZ", "ALL")]["value"] == 1.0
    na = {r["code"]: r["na_rate"] for r in s["na_rates"]}
    assert [r["code"] for r in s["na_rates"]] == CODES
    assert na["C3"] == pytest.approx(2 / 3, abs=1e-4) and na["C2"] == pytest.approx(1 / 3, abs=1e-4) and na["C1"] == 0.0


def test_summary_operators_and_worst(db, seeded):
    s = summary(db, seeded["version"].id)
    ops = s["operators"]
    assert [o["operator_name"] for o in ops] == ["—", "Нұрлан", "Алина"]
    assert ops[0]["critical_count"] == 1 and ops[0]["n"] == 1 and ops[0]["mean_total"] == 11.0
    assert ops[2]["mean_total"] == 100.0 and ops[2]["critical_count"] == 0
    worst = s["worst"]
    assert len(worst) == 3
    assert worst[0]["conversation_id"] == "t-ru-003" and worst[0]["critical_error"] is True
    assert set(worst[0]) >= {"conversation_id", "channel", "language", "operator_name", "total_score", "final_total_score", "critical_error"}
    assert worst[0]["operator_name"] is None and worst[0]["channel"] == "voice call"
    assert [w["conversation_id"] for w in worst] == ["t-ru-003", "t-kz-002", "t-ru-001"]


def test_summary_agreement(db, seeded):
    s = summary(db, seeded["version"].id)
    agreement = {a["code"]: a for a in s["agreement"]}
    assert agreement["C1"] == {"code": "C1", "n_reviewed": 2, "agreement_rate": 1.0, "kappa": None}
    assert agreement["C7"] == {"code": "C7", "n_reviewed": 2, "agreement_rate": 0.5, "kappa": 0.0}
    assert agreement["C4"] == {"code": "C4", "n_reviewed": 0, "agreement_rate": None, "kappa": None}


def test_cohen_kappa():
    assert cohen_kappa([]) is None and cohen_kappa([("yes", "yes")]) is None
    assert cohen_kappa([("yes", "yes"), ("yes", "yes")]) is None  # undefined: pe == 1
    assert cohen_kappa([("yes", "yes"), ("no", "no")]) == 1.0
    assert cohen_kappa([("yes", "no"), ("no", "yes")]) == -1.0
    assert cohen_kappa([("yes", "yes"), ("yes", "no"), ("no", "no"), ("no", "no")]) == pytest.approx(0.5, abs=1e-4)


def test_summary_providers_and_first_response(db, seeded):
    s = summary(db, seeded["version"].id)
    providers = {(p["provider"], p["model"]): p for p in s["providers"]}
    assert providers[("anthropic", "claude-opus-5-5")]["n"] == 2
    assert providers[("anthropic", "claude-opus-5-5")]["total_cost_usd"] == pytest.approx(0.29, abs=1e-6)
    assert providers[("anthropic", "claude-opus-5-5")]["mean_cost_usd"] == pytest.approx(0.145, abs=1e-6)
    assert providers[("anthropic", "claude-opus-5-5")]["mean_latency_ms"] == 35500
    assert providers[("anthropic", "claude-sonnet-5-5")]["n"] == 1
    assert providers[("local", "qwen3:8b")]["n"] == 1  # older evaluation still counts for spend
    fr = s["first_response"]
    assert fr["mean_seconds"] == pytest.approx((37 + 200) / 2, abs=0.1)
    assert fr["over_threshold_share"] == 0.5
    assert fr["threshold_seconds"] == 180


def test_summary_empty_version(db):
    s = summary(db, 999)
    assert s["count"] == 0 and s["mean_total"] is None and s["median_total"] is None
    assert s["criteria_heatmap"] == [] and s["worst"] == [] and s["operators"] == []
    assert sum(d["n"] for d in s["distribution"]) == 0


# ----------------------------------------------------------------------------- CSV


def test_csv_header_and_rows(db, seeded):
    text = evaluations_csv(db, seeded["version"].id)
    assert text.startswith("﻿")
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    expected_header = (
        ["conversation_id", "channel", "language", "operator_name"]
        + [f"{c}_answer" for c in CODES]
        + [f"{c}_score" for c in CODES]
        + [f"{c}_applicable" for c in CODES]
        + [f"{c}_final" for c in CODES]
        + ["total_score", "final_total_score", "critical_error", "provider", "model", "cost_usd", "evaluated_at"]
    )
    assert rows[0] == expected_header == header_for(CODES)
    assert len(rows) == 4  # header + 3 latest evaluations
    by_id = {r[0]: dict(zip(rows[0], r)) for r in rows[1:]}
    assert set(by_id) == {"t-ru-001", "t-kz-002", "t-ru-003"}
    ru = by_id["t-ru-001"]
    assert ru["operator_name"] == "Алина" and ru["channel"] == "agent chat" and ru["language"] == "RU"
    assert ru["C3_answer"] == "N/A" and ru["C3_score"] == "N/A" and ru["C3_applicable"] == "false" and ru["C3_final"] == "N/A"
    assert ru["C1_answer"] == "yes" and ru["C4_answer"] == "20" and ru["C4_score"] == "20"
    assert ru["total_score"] == "100" and ru["provider"] == "anthropic" and ru["model"] == "claude-opus-5-5"
    assert ru["evaluated_at"].startswith("2026-11-07T12:00:00")
    kz = by_id["t-kz-002"]
    assert kz["C7_answer"] == "yes" and kz["C7_final"] == "no"
    assert kz["total_score"] == "85.38" and kz["final_total_score"] == "77.69"
    call = by_id["t-ru-003"]
    assert call["critical_error"] == "true" and call["C8_answer"] == "yes" and call["operator_name"] == ""
    assert call["C6_answer"] == "4" and call["C6_score"] == "4" and call["C6_applicable"] == "true"


def test_csv_without_version_filter(db):
    text = evaluations_csv(db, None)
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    assert len(rows) == 4 and rows[0][4] == "C1_answer"


# ----------------------------------------------------------------------------- PDF


def test_pdf_bytes(db, seeded):
    for key in ("e2", "e3"):
        pdf = evaluation_pdf(db, seeded["evals"][key].id)
        assert pdf.startswith(b"%PDF")
        assert len(pdf) > 2048
    assert b"DejaVuSans" in evaluation_pdf(db, seeded["evals"]["e1"].id)


def test_pdf_missing_raises(db):
    with pytest.raises(LookupError):
        evaluation_pdf(db, 999_999)


# ----------------------------------------------------------------------------- routers


@pytest.fixture(scope="module")
def client(seeded):
    app = FastAPI()
    app.include_router(analytics_router)
    app.include_router(export_router)

    def _override():
        session = seeded["Session"]()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _override
    with TestClient(app) as c:
        yield c


def test_router_prefixes():
    assert analytics_router.prefix == "/api/analytics"
    assert export_router.prefix == "/api/export"


def test_router_summary(client, seeded):
    r = client.get("/api/analytics/summary", params={"checklist_version_id": seeded["version"].id})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 3 and body["critical_count"] == 1
    assert client.get("/api/analytics/summary").json()["count"] == 3


def test_router_csv(client, seeded):
    r = client.get("/api/export/evaluations.csv", params={"checklist_version_id": seeded["version"].id})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv") and "charset=utf-8" in r.headers["content-type"]
    assert r.headers["content-disposition"].startswith("attachment")
    assert r.content.startswith(b"\xef\xbb\xbf")
    assert "Алина" in r.content.decode("utf-8-sig")


def test_router_pdf(client, seeded):
    r = client.get(f"/api/export/evaluations/{seeded['evals']['e2'].id}.pdf")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF") and len(r.content) > 2048
    missing = client.get("/api/export/evaluations/999999.pdf")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"
