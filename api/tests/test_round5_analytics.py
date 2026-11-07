"""Round 5, analytics: backend gaps found by the frontend audit (WP-5, WP-6, WP-7, WP-9, WP-10). In-memory SQLite, no network.

1. GET /api/analytics/summary: ``per_evaluation`` (one full evaluation: completed, not partial), ``bands`` by the UI legend
   (critical < 60, attention 60–85, norm ≥ 85) and ``basis`` = ai (default, unchanged) | final (reviewer decisions).
2. GET /api/overview ``cost``: ``per_evaluation_usd`` and ``evaluations_completed`` (the same figure as the summary).
3. GET /api/export/results/validate: ``schema_version``, ``team_name`` (as in results.json) and ``issues`` per dialog.
4. Checklist versions: ``version_id`` and ``evaluations`` (completed), counted with one grouped query.
5. CSV and PDF downloads list Content-Disposition in Access-Control-Expose-Headers.

Review round: the overview ``cost`` block only gains keys (test_overview pins the empty block as an exact dict, so its
expectation lists the two new keys; every old key keeps its value).
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from cqa.analytics.overview import overview
from cqa.analytics.service import BASIS_MESSAGE, summary
from cqa.checklist import service as checklist_svc
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
from cqa.platform import requestlog
from cqa.routers import analytics as analytics_router
from cqa.routers import checklists as checklists_router
from cqa.routers import export as export_router
from cqa.scoring.engine import answer_points

SEED = json.loads((Path(__file__).resolve().parents[1] / "seeds" / "checklist_freedom_v1.json").read_text(encoding="utf-8"))
NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
PERFECT = {"C1": "yes", "C2": "yes", "C3": "yes", "C4": 20, "C5": "yes", "C6": 10, "C7": "yes", "C8": "no"}
WEAK = {"C1": "no", "C2": "no", "C3": "no", "C4": 5, "C5": "no", "C6": 4, "C7": "no", "C8": "yes"}
# the overview cost block the deployed web app reads (before round 5)
OLD_COST_KEYS = {"total_usd", "per_dialog_usd", "evaluations", "tokens_in", "tokens_out", "cache_read_tokens", "period_usd", "by_model"}


# ============================================================================================ fixture helpers


def _engine():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return engine


def _checklist(db: Session, name: str) -> Checklist:
    cl = Checklist(name=name, description=None)
    db.add(cl)
    db.flush()
    return cl


def _version(db: Session, cl: Checklist, version_no: int = 1, *, active: bool = True) -> ChecklistVersion:
    """A version with the organizer's eight criteria (C1…C8), so results.json maps every organizer id."""
    v = ChecklistVersion(checklist_id=cl.id, version_no=version_no, is_active=active, change_note=f"v{version_no}",
                         scoring_rules=SEED["scoring_rules"], sections=SEED["sections"])
    db.add(v)
    db.flush()
    for order, c in enumerate(SEED["criteria"], start=1):
        db.add(Criterion(checklist_version_id=v.id, code=c["code"], order=order, section=c["section"], title=c["title"],
                         instruction=c["instruction"], answer_type=c["answer_type"], options=c["options"], max_points=c["max_points"],
                         weight=c["weight"], na_allowed=c["na_allowed"], na_condition=c["na_condition"], is_critical=c["is_critical"],
                         critical_value=c.get("critical_value"), channel_overrides=c["channel_overrides"]))
    db.flush()
    return v


def _conversation(db: Session, cid: str, *, channel: str = "agent chat", language: str = "RU", operator: str | None = None,
                  dataset_id: int | None = None, n_turns: int = 6) -> None:
    db.add(Conversation(id=cid, channel=channel, language=language, operator_name=operator, dataset_id=dataset_id, turn_count=n_turns))
    db.flush()
    for i in range(n_turns):
        speaker = "operator" if i % 2 else "client"
        text = "Здравствуйте! Меня зовут Алина, чем помочь?" if speaker == "operator" else "Добрый день, вопрос по счёту."
        db.add(Turn(conversation_id=cid, idx=i, timestamp_raw=f"00:{i:02d}", offset_seconds=float(i), speaker=speaker, text=text,
                    segment="operator"))
    db.flush()


def _evaluation(db: Session, cid: str, version: ChecklistVersion, answers: dict | None = None, *, na: tuple[str, ...] = (),
                total: float = 100.0, final: float | None = None, mode: str = "single", status: str = "completed",
                provider: str = "anthropic", model: str = "anthropic:claude-opus-5-5", cost: float = 0.1, latency: int = 30000,
                decisions: dict[str, tuple[object, float | None]] | None = None, no_evidence: tuple[str, ...] = (),
                error: str | None = None) -> Evaluation:
    """``decisions``: {code: (final_answer, final_score)} as a reviewer decision leaves them on the row."""
    answers = answers or PERFECT
    done = status == "completed"
    ev = Evaluation(conversation_id=cid, checklist_version_id=version.id, status=status, mode=mode, provider=provider, model=model,
                    total_score=total if done else None, final_total_score=(total if final is None else final) if done else None,
                    critical_error=done and answers.get("C8") == "yes" and "C8" not in na, cost_usd=cost, latency_ms=latency,
                    error=error, completed_at=NOW if done else None)
    db.add(ev)
    db.flush()
    if not done:
        return ev
    crits = db.scalars(select(Criterion).where(Criterion.checklist_version_id == version.id).order_by(Criterion.order)).all()
    for c in crits:
        applicable = c.code not in na
        answer = answers[c.code] if applicable else None
        points = answer_points(c.options, answer) if applicable else None
        row = EvaluationCriterion(
            evaluation_id=ev.id, criterion_id=c.id, code=c.code, applicable=applicable,
            na_reason=None if applicable else "Условие N/A выполнено.", answer=answer, score=points, max_points=c.max_points,
            weight=c.weight, weight_applied=c.weight if applicable else 0.0,
            earned=(c.weight * points / c.max_points) if (applicable and points is not None and c.max_points) else 0.0,
            confidence=0.9, explanation=f"{c.title} — обоснование для {cid}.",
            evidence=[] if c.code in no_evidence else [{"turn_idx": 1, "speaker": "operator", "quote": "Здравствуйте!", "role": "supports"}],
        )
        if decisions and c.code in decisions:
            row.final_answer, row.final_score = decisions[c.code]
            row.review_state = "confirmed" if row.final_answer == (answer if applicable else "N/A") else "changed"
        db.add(row)
    db.flush()
    return ev


def _http_envelope(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)  # the same envelope as cqa.main
    async def http_exc(request: Request, exc: HTTPException):
        detail = exc.detail if isinstance(exc.detail, dict) else {"code": "error", "message": str(exc.detail)}
        return JSONResponse(status_code=exc.status_code, content={"error": detail})


def _client(factory, *routers, cors: bool = False) -> TestClient:
    app = FastAPI()
    _http_envelope(app)
    for module in routers:
        app.include_router(module.router)

    def override():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    if cors:  # the production stack: request log outermost, then CORS (cqa.main)
        requestlog.install(app)
        app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False, allow_methods=["*"], allow_headers=["*"])
    return TestClient(app)


# ============================================================================================ 1–2. summary and overview


@pytest.fixture(scope="module")
def quality():
    """Five dialogs (latest evaluations A–E) with totals on the band edges and reviewer decisions on A and B, an older full
    evaluation of A, a partial re-evaluation of B and a failed run of F (both must stay out of the per-evaluation price)."""
    engine = _engine()
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db = factory()
    v1 = _version(db, _checklist(db, "Чек-лист качества чатов"))
    _conversation(db, "r5-a", language="RU", operator="Алина")
    _conversation(db, "r5-b", language="KZ", operator="Нұрлан")
    _conversation(db, "r5-c", channel="voice call", language="RU")
    _conversation(db, "r5-d", language="EN", operator="Dana")
    _conversation(db, "r5-e", language="RU", operator="Алина")
    _conversation(db, "r5-f", language="RU", operator="Алина")
    evs = {
        "old_a": _evaluation(db, "r5-a", v1, cost=0.15, latency=35000),
        "partial_b": _evaluation(db, "r5-b", v1, mode="partial", cost=0.03, latency=5000),
        # A: C1 confirmed, C2 changed to N/A, C7 changed to «Нет»
        "a": _evaluation(db, "r5-a", v1, total=100.0, final=85.0, cost=0.20, latency=40000,
                         decisions={"C1": ("yes", 1.0), "C2": ("N/A", None), "C7": ("no", 0.0)}),
        # B (ensemble): the judge said N/A on C3, the reviewer «Да»; C4 10 → 20 carried without stored points
        "b": _evaluation(db, "r5-b", v1, dict(PERFECT, C2="no", C4=10, C6=7), na=("C3",), total=70.0, final=84.99, mode="ensemble",
                         model="anthropic:claude-opus-5-5,anthropic:claude-sonnet-5-5", cost=0.60, latency=90000,
                         decisions={"C3": ("yes", 1.0), "C4": (20, None)}),
        "c": _evaluation(db, "r5-c", v1, WEAK, total=11.0, cost=0.10, latency=20000),
        "d": _evaluation(db, "r5-d", v1, total=60.0, cost=0.10, latency=30000),
        "e": _evaluation(db, "r5-e", v1, total=59.99, provider="fake", model="fake:fake-judge", cost=0.0, latency=1000),
        "failed_f": _evaluation(db, "r5-f", v1, status="failed", cost=0.02, latency=0, error="Таймаут модели"),
    }
    db.commit()
    yield SimpleNamespace(factory=factory, db=db, v1=v1, evs=evs)
    db.close()
    engine.dispose()


def _cells(s: dict) -> dict[tuple[str, str, str], dict]:
    return {(h["code"], h["language"], h["channel"]): h for h in s["criteria_heatmap"]}


def test_summary_default_basis_is_ai_and_unchanged(quality):
    s = summary(quality.db)
    assert s["basis"] == "ai" and s == summary(quality.db, basis="ai")
    cells = _cells(s)
    # the judge's answers: C2 A yes · B no · C no · D yes · E yes
    assert (cells[("C2", "ALL", "ALL")]["n"], cells[("C2", "ALL", "ALL")]["value"]) == (5, 0.6)
    assert (cells[("C3", "ALL", "ALL")]["n"], cells[("C3", "ALL", "ALL")]["value"]) == (4, 0.75)  # B: N/A
    assert cells[("C4", "ALL", "ALL")]["value"] == 0.75  # (1 + 0.5 + 0.25 + 1 + 1) / 5
    assert cells[("C7", "ALL", "ALL")]["value"] == 0.8
    assert cells[("C7", "RU", "ALL")]["value"] == pytest.approx(2 / 3, abs=1e-4)  # A yes · C no · E yes
    na = {r["code"]: r["na_rate"] for r in s["na_rates"]}
    assert na["C2"] == 0.0 and na["C3"] == 0.2


def test_summary_basis_final_counts_reviewer_decisions(quality):
    ai, final = summary(quality.db, basis="ai"), summary(quality.db, basis="final")
    assert final["basis"] == "final"
    cells = _cells(final)
    assert (cells[("C2", "ALL", "ALL")]["n"], cells[("C2", "ALL", "ALL")]["value"]) == (4, 0.5)  # A: final N/A drops out
    assert (cells[("C3", "ALL", "ALL")]["n"], cells[("C3", "ALL", "ALL")]["value"]) == (5, 0.8)  # B: N/A → «Да» counts
    assert cells[("C4", "ALL", "ALL")]["value"] == 0.85  # B: 20 priced from the options (no stored final points)
    assert cells[("C7", "ALL", "ALL")]["value"] == 0.6  # A: «Да» → «Нет»
    assert cells[("C7", "RU", "ALL")]["value"] == pytest.approx(1 / 3, abs=1e-4)
    assert cells[("C1", "ALL", "ALL")] == _cells(ai)[("C1", "ALL", "ALL")]  # a confirm keeps the AI answer
    na = {r["code"]: r["na_rate"] for r in final["na_rates"]}
    assert na["C2"] == 0.2 and na["C3"] == 0.0
    # totals are final on both bases; agreement always compares AI with final answers
    for key in ("count", "mean_total", "median_total", "distribution", "bands", "by_language", "by_channel", "operators", "worst",
                "critical_count", "agreement", "providers", "per_evaluation", "first_response"):
        assert final[key] == ai[key], key


def test_summary_rejects_an_unknown_basis(quality):
    for basis in ("x", "FINAL", ""):
        with pytest.raises(ValueError, match="basis"):
            summary(quality.db, basis=basis)


def test_summary_bands_follow_the_ui_legend(quality):
    s = summary(quality.db)
    # A 85.0 → norm (edge) · B 84.99 and D 60.0 → attention · C 11.0 and E 59.99 → critical
    assert s["bands"] == {"critical": 2, "attention": 2, "norm": 1}
    assert sum(s["bands"].values()) == s["count"] == sum(d["n"] for d in s["distribution"])
    assert summary(quality.db, language="KZ")["bands"] == {"critical": 0, "attention": 1, "norm": 0}


def test_summary_per_evaluation_is_one_full_evaluation(quality):
    s = summary(quality.db)
    # old A 0.15 · A 0.20 · B 0.60 (ensemble) · C 0.10 · D 0.10 · E 0.00; the partial run of B and the failed F are left out
    assert s["per_evaluation"] == {"n": 6, "mean_cost_usd": round(1.15 / 6, 4), "mean_latency_ms": 36000.0}
    assert summary(quality.db, quality.v1.id)["per_evaluation"] == s["per_evaluation"]
    assert summary(quality.db, language="KZ")["per_evaluation"] == {"n": 1, "mean_cost_usd": 0.6, "mean_latency_ms": 90000.0}
    providers = {(p["provider"], p["model"]): p for p in s["providers"]}  # the providers block still counts every completed run
    assert sum(p["n"] for p in providers.values()) == 7


def test_summary_on_an_empty_database():
    engine = _engine()
    with Session(engine) as db:
        s = summary(db)
        assert s["bands"] == {"critical": 0, "attention": 0, "norm": 0}
        assert s["per_evaluation"] == {"n": 0, "mean_cost_usd": None, "mean_latency_ms": None}
        assert s["basis"] == "ai"
    engine.dispose()


def test_overview_cost_per_evaluation_matches_the_summary(quality):
    cost = overview(quality.db, now=NOW)["cost"]
    assert cost["total_usd"] == 1.2 and cost["evaluations"] == 8  # every run in scope, any status (unchanged)
    assert cost["per_dialog_usd"] == 0.24  # fully loaded: 1.20 / 5 evaluated dialogs (unchanged)
    assert cost["per_evaluation_usd"] == round(1.15 / 6, 4) and cost["evaluations_completed"] == 6
    for kwargs in ({}, {"checklist_version_id": quality.v1.id}, {"language": "KZ"}, {"language": "RU"}, {"operator": "Алина"}):
        version = kwargs.pop("checklist_version_id", None)
        s = summary(quality.db, version, **kwargs)["per_evaluation"]
        c = overview(quality.db, version, now=NOW, **kwargs)["cost"]
        assert (c["per_evaluation_usd"], c["evaluations_completed"]) == (s["mean_cost_usd"], s["n"]), kwargs
    kz = overview(quality.db, now=NOW, language="KZ")["cost"]
    assert kz["per_evaluation_usd"] == 0.6 and kz["evaluations_completed"] == 1 and kz["per_dialog_usd"] == 0.63
    assert set(cost) == OLD_COST_KEYS | {"per_evaluation_usd", "evaluations_completed"}  # additive: nothing renamed or dropped


def test_overview_cost_without_evaluations():
    engine = _engine()
    with Session(engine) as db:
        cost = overview(db, now=NOW)["cost"]
        assert cost["per_evaluation_usd"] is None and cost["evaluations_completed"] == 0 and cost["per_dialog_usd"] is None
        # the whole empty block, as test_overview's empty panels pin it: the old keys keep their values
        assert cost == {"total_usd": 0.0, "per_dialog_usd": None, "evaluations": 0, "tokens_in": 0, "tokens_out": 0,
                        "cache_read_tokens": 0, "period_usd": 0.0, "by_model": [], "per_evaluation_usd": None,
                        "evaluations_completed": 0}
    engine.dispose()


def test_http_summary_basis(quality):
    with _client(quality.factory, analytics_router) as c:
        default = c.get("/api/analytics/summary").json()
        assert default["basis"] == "ai" and default["bands"] == {"critical": 2, "attention": 2, "norm": 1}
        assert default["per_evaluation"]["n"] == 6
        final = c.get("/api/analytics/summary", params={"basis": "final"}).json()
        assert final["basis"] == "final" and final["criteria_heatmap"] == summary(quality.db, basis="final")["criteria_heatmap"]
        scoped = c.get("/api/analytics/summary", params={"basis": "final", "language": "KZ"}).json()
        assert scoped["count"] == 1 and scoped["basis"] == "final"
        r = c.get("/api/analytics/summary", params={"basis": "human"})
        assert r.status_code == 422
        assert r.json() == {"error": {"code": "validation_error", "message": BASIS_MESSAGE, "errors": [{"field": "basis", "message": BASIS_MESSAGE}]}}


# ============================================================================================ 5. download headers


def test_downloads_expose_content_disposition(quality):
    eval_id = quality.evs["a"].id
    urls = ["/api/export/evaluations.csv", f"/api/export/evaluations.csv?checklist_version_id={quality.v1.id}",
            "/api/export/violations.csv", "/api/export/decisions.csv", f"/api/export/evaluations/{eval_id}.pdf"]
    with _client(quality.factory, export_router, cors=True) as c:
        for url in urls:
            same_origin = c.get(url)
            assert same_origin.status_code == 200, url
            assert same_origin.headers["content-disposition"]  # unchanged file names
            assert same_origin.headers["access-control-expose-headers"] == "Content-Disposition", url
            cross = c.get(url, headers={"Origin": "https://cqa-freedom-pi.vercel.app"})
            assert cross.headers["access-control-allow-origin"] == "*"
            assert cross.headers["access-control-expose-headers"] == "Content-Disposition, X-Request-ID", url
        assert c.get(f"/api/export/evaluations/{eval_id}.pdf").headers["content-disposition"] == f'inline; filename="evaluation_{eval_id}.pdf"'
        assert c.get("/api/export/evaluations/999999.pdf").status_code == 404  # errors are not downloads


# ============================================================================================ 3. results/validate


@pytest.fixture(scope="module")
def results():
    """Group R5: one clean dialog, one without evidence on C5 (warning), one with a C1 N/A (error) and one failed run."""
    engine = _engine()
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db = factory()
    v1 = _version(db, _checklist(db, "Чек-лист качества чатов"))
    ds = Dataset(group_id="R5", source="test", record_count=4)
    db.add(ds)
    db.flush()
    for cid in ("r5-ok", "r5-noev", "r5-contact", "r5-failed"):
        _conversation(db, cid, dataset_id=ds.id)
    _evaluation(db, "r5-ok", v1)
    _evaluation(db, "r5-noev", v1, no_evidence=("C5",))
    _evaluation(db, "r5-contact", v1, na=("C1",))
    _evaluation(db, "r5-failed", v1, status="failed", error="Таймаут модели")
    db.commit()
    db.close()
    yield factory
    engine.dispose()


@pytest.fixture()
def team_env(monkeypatch):
    monkeypatch.delenv("TEAM_NAME", raising=False)
    monkeypatch.setattr(settings, "team_name", "Я Тебя Могну")
    return monkeypatch


def test_validate_reports_schema_team_and_issues(results, team_env):
    with _client(results, export_router) as c:
        body = c.get("/api/export/results/validate", params={"group_id": "R5", "expect": 5}).json()
    # the existing fields are unchanged
    assert body["valid"] is False and body["records"] == 4 and body["ok"] == 3 and body["error_records"] == ["r5-failed"]
    assert body["model"] == {"mode": "external_api", "name": "claude-opus-5-5"} and body["summary"].startswith("records=4 ok=3 error=1")
    assert body["schema_version"] == "1.0" and body["team_name"] == "Я Тебя Могну"
    issues = body["issues"]
    assert {"conversation_id": "r5-contact", "severity": "error",
            "message": "contact must be true or false (no N/A for contact in JSON 1.0); re-judge C1 for this dialog"} in issues
    assert {"conversation_id": None, "severity": "error", "message": "expected exactly 5 records, found 4"} in issues
    assert {"conversation_id": "r5-noev", "severity": "warning", "message": "empathy has no evidence_turns"} in issues
    assert {"conversation_id": "r5-failed", "severity": "warning", "message": "status error (Таймаут модели)"} in issues
    # one issue per string, errors first, and the prefix split is lossless
    rebuilt = [f"{i['conversation_id']}: {i['message']}" if i["conversation_id"] else i["message"] for i in issues]
    assert rebuilt == body["errors"] + body["warnings"]
    assert [i["severity"] for i in issues] == ["error"] * len(body["errors"]) + ["warning"] * len(body["warnings"])


def test_validate_team_name_follows_the_results_file(results, team_env):
    with _client(results, export_router) as c:
        def both(**params):
            v = c.get("/api/export/results/validate", params={"group_id": "R5", **params}).json()
            f = c.get("/api/export/results.json", params={"group_id": "R5", "download": False, **params})
            assert f.headers["x-results-valid"] == ("true" if v["valid"] else "false")
            assert f.headers["x-results-errors"] == str(len(v["errors"])) and f.headers["x-results-warnings"] == str(len(v["warnings"]))
            assert f.json()["team_name"] == v["team_name"] and f.json()["schema_version"] == v["schema_version"]
            return v

        assert both()["team_name"] == "Я Тебя Могну"  # settings
        team_env.setenv("TEAM_NAME", "Команда R5")
        assert both()["team_name"] == "Команда R5"  # env TEAM_NAME wins over the settings
        assert both(team_name="Явное имя")["team_name"] == "Явное имя"  # the query parameter wins over both
        team_env.delenv("TEAM_NAME")
        team_env.setattr(settings, "team_name", "")
        empty = both()  # an empty name would make the file invalid: the dry run says so too
        assert empty["team_name"] == "" and empty["valid"] is False
        assert {"conversation_id": None, "severity": "error", "message": "team_name must be a non-empty string"} in empty["issues"]


def test_issue_split_edge_cases():
    errors = ["missing conversation_ids: ['x']", "a: b: duplicate conversation_id", "a: criteria must be an array", "results[0] must be an object"]
    warnings = ["p1-ru-001: final_score is null (no applicable weighted criteria)", "unknown: something"]
    issues = export_router._issues(errors, warnings, ["a", "a: b", "p1-ru-001", None, ""])
    assert issues == [
        {"conversation_id": None, "severity": "error", "message": "missing conversation_ids: ['x']"},
        {"conversation_id": "a: b", "severity": "error", "message": "duplicate conversation_id"},  # an id with ": " matched whole
        {"conversation_id": "a", "severity": "error", "message": "criteria must be an array"},
        {"conversation_id": None, "severity": "error", "message": "results[0] must be an object"},
        {"conversation_id": "p1-ru-001", "severity": "warning", "message": "final_score is null (no applicable weighted criteria)"},
        {"conversation_id": None, "severity": "warning", "message": "unknown: something"},  # not a dialog of the file
    ]


# ============================================================================================ 4. checklist versions


@pytest.fixture()
def studio():
    """Organizer checklist v1 (3 completed + 1 failed evaluation) and a draft v2 (1 partial), an imported checklist v1 (1)."""
    engine = _engine()
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as db:
        org = _checklist(db, "Freedom Broker · Чек-лист качества чатов")
        org_v1 = _version(db, org, 1)
        org_v2 = _version(db, org, 2, active=False)
        imported = _checklist(db, "Импорт")
        imp_v1 = _version(db, imported, 1)
        for cid in ("s-1", "s-2", "s-3"):
            _conversation(db, cid)
            _evaluation(db, cid, org_v1)
        _evaluation(db, "s-1", org_v1, status="failed")
        _evaluation(db, "s-2", org_v2, mode="partial")
        _evaluation(db, "s-3", imp_v1)
        db.commit()
        ids = SimpleNamespace(org=org.id, org_v1=org_v1.id, org_v2=org_v2.id, imported=imported.id, imp_v1=imp_v1.id)
    yield SimpleNamespace(engine=engine, factory=factory, ids=ids)
    engine.dispose()


def test_evaluation_counts_is_one_grouped_query(studio):
    i = studio.ids
    with studio.factory() as db:
        assert checklist_svc.evaluation_counts(db) == {i.org_v1: 3, i.org_v2: 1, i.imp_v1: 1}  # completed only, any mode
        assert checklist_svc.evaluation_counts(db, [i.org_v1, None]) == {i.org_v1: 3}
        assert checklist_svc.evaluation_counts(db, []) == {}


def test_checklist_list_has_version_ids_and_evaluation_counts(studio):
    i = studio.ids
    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.lower())

    with _client(studio.factory, checklists_router) as c:
        event.listen(studio.engine, "before_cursor_execute", record)
        try:
            listed = c.get("/api/checklists").json()
        finally:
            event.remove(studio.engine, "before_cursor_execute", record)
        by_id = {cl["id"]: cl for cl in listed}
        assert [(v["id"], v["version_id"], v["evaluations"]) for v in by_id[i.org]["versions"]] == [(i.org_v1, i.org_v1, 3), (i.org_v2, i.org_v2, 1)]
        assert [(v["version_id"], v["evaluations"]) for v in by_id[i.imported]["versions"]] == [(i.imp_v1, 1)]
        assert by_id[i.org]["active_version"]["version_id"] == i.org_v1 and by_id[i.org]["active_version"]["evaluations"] == 3
        assert set(by_id[i.org]["versions"][0]) >= {"id", "version_no", "created_at", "change_note", "is_active"}  # unchanged fields
        counts = [s for s in statements if "count(" in s and "evaluations" in s]
        assert len(counts) == 1 and "group by" in counts[0]  # three checklists' versions, one query

        one = c.get(f"/api/checklists/{i.org}").json()
        assert [(v["version_id"], v["evaluations"]) for v in one["versions"]] == [(i.org_v1, 3), (i.org_v2, 1)]
        v1 = c.get(f"/api/checklists/{i.org}/versions/{i.org_v1}").json()
        assert v1["id"] == v1["version_id"] == i.org_v1 and v1["evaluations"] == 3
        assert c.get("/api/checklists/active").json()["evaluations"] == 3
        activated = c.post(f"/api/checklists/{i.org}/versions/{i.org_v2}/activate").json()
        assert activated["version_id"] == i.org_v2 and activated["evaluations"] == 1 and activated["is_active"] is True


def test_serializers_count_a_new_version_as_zero(studio):
    with studio.factory() as db:
        cl = db.get(Checklist, studio.ids.imported)
        v2 = _version(db, cl, 2, active=False)
        db.commit()
        out = checklist_svc.serialize_version(v2)
        assert out["version_id"] == v2.id and out["evaluations"] == 0
        listed = checklist_svc.serialize_checklist(db.get(Checklist, studio.ids.imported))
        assert [(v["version_no"], v["evaluations"]) for v in listed["versions"]] == [(1, 1), (2, 0)]
