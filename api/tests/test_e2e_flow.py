"""End-to-end process test (package "testing").

import two organizer chats → evaluate with the offline ``fake`` judge (job + SSE) → conversation card → operator
appeal → reviewer decision (totals recomputed) → gross-violations list → new checklist version with an added
criterion → partial re-evaluation → results.json export (builder, CLI and API) → audit trail.

The whole flow runs in a SUBPROCESS whose environment points DATABASE_URL and DATA_DIR into pytest's tmp dir, so the
engine that cqa.db creates at import time never touches api/data/cqa.db, and the in-process job worker threads die
with the subprocess. The subprocess prints one JSON summary line (prefix ``E2E_SUMMARY=``); the tests below assert on
it. Checks that depend on packages that may not be installed yet (roles middleware, audit router) are skipped then.

Run the flow by hand (prints the summary):
  cd api && DATABASE_URL=sqlite:////tmp/cqa-e2e/e2e.db DATA_DIR=/tmp/cqa-e2e ANTHROPIC_API_KEY= .venv/bin/python tests/test_e2e_flow.py
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import subprocess
import sys
import time
import warnings
from collections import Counter
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest

API_DIR = Path(__file__).resolve().parents[1]
DEMO_DB = API_DIR / "data" / "cqa.db"
MARKER = "E2E_SUMMARY="

GOOD_ID = "e2e-ru-good"
BAD_ID = "e2e-ru-bad"
GOOD_OPERATOR = "Алина"
BAD_OPERATOR = "Алексей"
REVIEWER = "Марат"
ANALYST = "Дана"
METHODOLOGIST = "Сауле"
APPEAL_REQUEST_ID = "e2e-appeal-1"


def _turns(*rows: tuple[str, str, str]) -> list[dict[str, str]]:
    return [{"timestamp": ts, "speaker": speaker, "text": text} for ts, speaker, text in rows]


# Organizer API shape ({"isSuccess", "data": {"records": [...]}}), uploaded as a .json file.
ORGANIZER_PAYLOAD: dict[str, Any] = {
    "isSuccess": True,
    "error": None,
    "data": {
        "records": [
            {
                "conversation_id": GOOD_ID,
                "channel": "agent chat",
                "language": "RU",
                "audio_url": None,
                "transcript": _turns(
                    ("00:00", "client", "Здравствуйте, пополнил счёт вчера, а деньги так и не зачислены."),
                    ("00:04", "bot", "Здравствуйте! Я виртуальный помощник AIM. Подключаю оператора, ожидайте, пожалуйста."),
                    ("00:10", "system", "Чат передан оператору"),
                    ("00:40", "operator", "Здравствуйте! Меня зовут Алина, компания Freedom Broker. Понимаю ваше беспокойство, сейчас разберёмся."),
                    ("00:55", "operator", "Уточните, пожалуйста, ФИО и номер счёта для проверки?"),
                    ("01:30", "client", "Иванов Иван, счёт 40817"),
                    ("01:35", "system", "Личность клиента подтверждена"),
                    ("02:05", "operator", "Спасибо! Ваш платёж в обработке: зачисление занимает до одного рабочего дня, средства поступят сегодня до 18:00."),
                    ("02:30", "client", "Понял, спасибо."),
                    ("02:40", "operator", "Пожалуйста! Хорошего дня, до свидания!"),
                ),
            },
            {
                "conversation_id": BAD_ID,
                "channel": "agent chat",
                "language": "RU",
                "audio_url": None,
                "transcript": _turns(
                    ("00:00", "client", "Добрый день, где скачать отчёт брокера за год?"),
                    ("00:04", "bot", "Здравствуйте! Я виртуальный помощник AIM. Подключаю оператора."),
                    ("00:10", "system", "Чат передан оператору"),
                    ("00:50", "operator", "Меня зовут Алексей. Что у вас?"),
                    ("01:10", "client", "Я же написал: отчёт брокера за год."),
                    ("01:30", "operator", "Вы вообще читать умеете? Всё есть в приложении: «Меню → Отчёты и документы → Отчёт брокера»."),
                    ("01:50", "client", "Можно повежливее?"),
                    ("02:00", "operator", "Пока."),
                    ("02:05", "system", "Оператор закрыл чат"),
                ),
            },
        ]
    },
}

# Criterion added in checklist version 2 (the organizers' live ask: "add a new criterion and re-apply").
NEW_CRITERION: dict[str, Any] = {
    "code": "C9",
    "section": "S3",
    "title": "Предложение дополнительной помощи",
    "instruction": "Да — перед завершением оператор спросил, остались ли у клиента вопросы, или предложил дополнительную помощь.",
    "answer_type": "binary",
    "options": [{"value": "yes", "label": "Да", "points": 1}, {"value": "no", "label": "Нет", "points": 0}],
    "max_points": 1,
    "weight": 5,
    "na_allowed": False,
    "na_condition": None,
    "is_critical": False,
    "critical_value": None,
    "channel_overrides": {},
    "order": 8,
}


# ============================================================================================= subprocess side


def _actor(role: str | None, name: str | None = None, request_id: str | None = None) -> dict[str, str]:
    """Actor headers per docs/ARCH_CONTRACTS.md §1 (X-CQA-User is URL-encoded UTF-8)."""
    headers: dict[str, str] = {}
    if role:
        headers["X-CQA-Role"] = role
    if name:
        headers["X-CQA-User"] = quote(name)
    if request_id:
        headers["X-Request-ID"] = request_id
    return headers


def _req(client: Any, method: str, url: str, *, expect: int = 200, **kwargs: Any) -> Any:
    r = client.request(method, url, **kwargs)
    if r.status_code != expect:
        raise AssertionError(f"{method} {url} -> HTTP {r.status_code}, expected {expect}: {r.text[:1200]}")
    return r


def _error(r: Any) -> dict[str, Any]:
    try:
        body = r.json()
    except ValueError:
        return {"status": r.status_code, "code": None, "body": r.text[:300]}
    err = body.get("error") if isinstance(body, dict) else None
    return {"status": r.status_code, "code": (err or {}).get("code"), "message": (err or {}).get("message")}


def _wait_job(client: Any, job_id: str, timeout: float = 90.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        job = _req(client, "GET", f"/api/jobs/{job_id}").json()
        if job["status"] not in ("queued", "running"):
            return job
        if time.monotonic() > deadline:
            raise AssertionError(f"job {job_id} still {job['status']} after {timeout}s: {job.get('log')}")
        time.sleep(0.05)


def _sse_counts(text: str) -> dict[str, int]:
    return dict(Counter(line.split(":", 1)[1].strip() for line in text.splitlines() if line.startswith("event:")))


def _round2(x: float) -> float:
    return float(Decimal(repr(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _expected_total(rows: list[dict[str, Any]], *, final: bool) -> float | None:
    """Independent re-implementation of the organizer rule: N/A excluded, weights redistributed, 0..100, half-up."""
    num = den = 0.0
    for r in rows:
        weight = float(r.get("weight") or 0)
        if weight <= 0:
            continue
        if final:
            answer = r.get("final_answer")
            if answer is None or answer == "N/A":
                continue
        else:
            if not r.get("applicable"):
                continue
            answer = r.get("answer")
        points = next((float(o["points"]) for o in r.get("options") or [] if str(o["value"]).lower() == str(answer).lower()), None)
        if points is None:
            continue
        max_points = float(r.get("max_points") or 0)
        num += weight * (max(0.0, min(1.0, points / max_points)) if max_points > 0 else 0.0)
        den += weight
    return _round2(num * 100.0 / den) if den > 0 else None


def _card_facts(card: dict[str, Any], turns: list[dict[str, Any]]) -> dict[str, Any]:
    by_idx = {t["idx"]: t for t in turns}
    rows = card["criteria"]
    return {
        "evaluation_id": card["id"],
        "status": card["status"],
        "provider": card["provider"],
        "model": card["model"],
        "mode": card["mode"],
        "total_score": card["total_score"],
        "final_total_score": card["final_total_score"],
        "critical_error": card["critical_error"],
        "cost_usd": card["cost_usd"],
        "input_tokens": card["input_tokens"],
        "output_tokens": card["output_tokens"],
        "codes": [r["code"] for r in rows],
        "answers": {r["code"]: (r["answer"] if r["applicable"] else "N/A") for r in rows},
        "final_answers": {r["code"]: r["final_answer"] for r in rows},
        "review_states": {r["code"]: r["review_state"] for r in rows},
        "evidence_exists": all(e.get("turn_idx") in by_idx and by_idx[e["turn_idx"]]["speaker"] == e.get("speaker")
                               for r in rows for e in r["evidence"]),
        "cites_operator_turn": all(any(by_idx.get(e.get("turn_idx"), {}).get("speaker") == "operator" for e in r["evidence"]) for r in rows),
        "explanations_ru": all(re.search(r"[А-Яа-яЁё]", r.get("explanation") or "") for r in rows),
        "fake_flagged": all("fake_judge" in (r.get("flags") or []) for r in rows),
        "validation_flags": sorted({f for r in rows for f in r.get("flags") or [] if f.startswith("issues:") or f == "validation_failed"}),
        "copied": sorted(r["code"] for r in rows if "copied_from_previous_version" in (r.get("flags") or [])),
        "expected_total": _expected_total(rows, final=False),
        "expected_final_total": _expected_total(rows, final=True),
        "weight_applied_sum": round(sum(float(r["weight_applied"]) for r in rows), 4),
        "earned_sum": round(sum(float(r["earned"]) for r in rows), 4),
        "sections_sum": round(sum(float(s["score"]) for s in card.get("sections") or []), 2),
    }


def _guard() -> dict[str, Any]:
    """Refuse to run against the demo database or with a real API key: the flow must stay offline and isolated."""
    db_url = os.environ.get("DATABASE_URL", "")
    data_dir = os.environ.get("DATA_DIR", "")
    if not db_url.startswith("sqlite:///") or not data_dir:
        raise SystemExit("refusing to run: set DATABASE_URL=sqlite:///<tmp>/e2e.db and DATA_DIR=<tmp> (see the module docstring)")
    db_path = Path(db_url[len("sqlite:///"):])
    if db_path.resolve() == DEMO_DB.resolve():
        raise SystemExit("refusing to run against the demo database api/data/cqa.db")
    from cqa.config import settings

    if settings.database_url != db_url or Path(settings.data_dir).resolve() != Path(data_dir).resolve():
        raise SystemExit(f"settings did not pick up the isolated paths: {settings.database_url} / {settings.data_dir}")
    if settings.anthropic_api_key:
        raise SystemExit("refusing to run with ANTHROPIC_API_KEY set: the e2e flow must never call the Claude API")
    return {"database_url": settings.database_url, "data_dir": str(settings.data_dir), "demo_db": str(DEMO_DB),
            "anthropic_key_set": bool(settings.anthropic_api_key), "organizer_url_set": bool(settings.organizer_api_url)}


def _route_fake_provider() -> bool | None:
    """True when the judge runner routes provider 'fake' itself. Otherwise install a thin test shim (and say so)."""
    from cqa.judge import runner as judge_runner
    from cqa.judge.providers import get_provider

    members_fn = getattr(judge_runner, "_members", None)
    if members_fn is None:  # the runner was refactored; assume it routes providers through get_provider
        return None
    try:
        native = all(getattr(p, "name", None) == "fake" for p in members_fn({}, "fake", None))
    except Exception:
        native = False
    if not native:
        def _members_with_fake(rs: dict[str, Any], provider_name: str, model: str | None):
            if provider_name == "fake":
                return [get_provider("fake", model)]
            return members_fn(rs, provider_name, model)

        judge_runner._members = _members_with_fake
    return native


def run_flow() -> dict[str, Any]:
    guard = _guard()
    summary: dict[str, Any] = {"guard": guard, "fake_routed_natively": _route_fake_provider()}

    from fastapi.testclient import TestClient

    from cqa.config import settings
    from cqa.db import SessionLocal
    from cqa.export import results_json as rj
    from cqa.main import app

    ids = [GOOD_ID, BAD_ID]
    with TestClient(app) as c:
        # ---------------------------------------------------------------- roles middleware present?
        probe = c.get("/api/conversations", headers={"X-CQA-Role": "e2e-no-such-role"})
        roles_active = probe.status_code == 400 and _error(probe)["code"] == "unknown_role"
        summary["roles"] = {"active": roles_active, "probe": _error(probe) if probe.status_code != 200 else {"status": 200}}
        if roles_active:
            me = c.get("/api/me", headers=_actor("operator", BAD_OPERATOR))
            summary["roles"]["me"] = {"status": me.status_code, "body": me.json() if me.status_code == 200 else None}

        # ---------------------------------------------------------------- organizer checklist (seeded at start-up)
        checklists = _req(c, "GET", "/api/checklists").json()
        organizer = next(cl for cl in checklists if any(v["version_no"] == 1 for v in cl["versions"]))
        v1_id = next(v["id"] for v in organizer["versions"] if v["version_no"] == 1)
        v1 = _req(c, "GET", f"/api/checklists/{organizer['id']}/versions/{v1_id}").json()
        summary["checklist"] = {"checklist_id": organizer["id"], "v1_id": v1_id, "v1_codes": [cr["code"] for cr in v1["criteria"]]}

        # ---------------------------------------------------------------- 1. import two organizer-format chats
        upload = c.post("/api/conversations/upload", headers=_actor("analyst", ANALYST),
                        files={"file": ("e2e_chats.json", json.dumps(ORGANIZER_PAYLOAD, ensure_ascii=False).encode("utf-8"), "application/json")})
        if upload.status_code != 200:
            raise AssertionError(f"upload failed: HTTP {upload.status_code} {upload.text[:800]}")
        up = upload.json()
        convs = {cid: _req(c, "GET", f"/api/conversations/{cid}").json() for cid in ids}
        summary["import"] = {"imported": up.get("imported"), "conversation_ids": up.get("conversation_ids"), "by_channel": up.get("by_channel"),
                             "operators": {cid: convs[cid]["operator_name"] for cid in ids},
                             "turn_counts": {cid: convs[cid]["turn_count"] for cid in ids}}

        # ---------------------------------------------------------------- 2. evaluate with the fake judge
        run_body = {"conversation_ids": ids, "checklist_version_id": v1_id, "provider": "fake", "mode": "full"}
        dry = _req(c, "POST", "/api/evaluations/run", json={**run_body, "dry_run": True}, headers=_actor("analyst", ANALYST)).json()
        jobs_before = _req(c, "GET", "/api/jobs").json()
        run = _req(c, "POST", "/api/evaluations/run", json=run_body, headers=_actor("analyst", ANALYST)).json()
        job = _wait_job(c, run["job_id"])
        sse = c.get(f"/api/jobs/{run['job_id']}/events")
        summary["run"] = {"dry_run": {k: dry.get(k) for k in ("queued", "estimated_cost_usd")}, "jobs_before": len(jobs_before),
                          "queued": run["queued"], "job_status": job["status"], "done": job["done"], "failed": job["failed"],
                          "cost_usd": job["cost_usd"], "log_tail": [entry["message"] for entry in job.get("log", [])[-4:]]}
        summary["sse"] = {"status": sse.status_code, "content_type": sse.headers.get("content-type", ""), "events": _sse_counts(sse.text)}

        # ---------------------------------------------------------------- 3. conversation cards
        raw_cards = {cid: _req(c, "GET", f"/api/conversations/{cid}/evaluation", params={"checklist_version_id": v1_id}).json() for cid in ids}
        summary["cards"] = {cid: _card_facts(raw_cards[cid], convs[cid]["turns"]) for cid in ids}
        trace = _req(c, "GET", f"/api/evaluations/{raw_cards[BAD_ID]['id']}/trace").json()
        summary["trace"] = {"n": len(trace), "providers": sorted({t["provider"] for t in trace}), "models": sorted({t["model"] for t in trace}),
                            "codes": sorted(t["criterion_code"] for t in trace), "cost_usd": round(sum(t["cost_usd"] for t in trace), 6),
                            "tokens": sum(t["input_tokens"] + t["output_tokens"] + t["cache_read_tokens"] for t in trace)}

        # ---------------------------------------------------------------- 4. appeals: operator files, reviewer decides
        rows = {r["code"]: r for r in raw_cards[BAD_ID]["criteria"]}
        appeal: dict[str, Any] = {}
        # 4a. an operator may appeal only own dialogs (with the roles middleware the author comes from the headers)
        foreign_body: dict[str, Any] = {"evaluation_criterion_id": rows["C5"]["id"], "comment": "Прошу пересмотреть", "author_label": "Оператор"}
        if not roles_active:
            foreign_body |= {"author_role": "operator", "author_name": GOOD_OPERATOR}
        foreign = c.post("/api/appeals", json=foreign_body, headers=_actor("operator", GOOD_OPERATOR))
        appeal["foreign_operator"] = {**_error(foreign), "body_had_author": not roles_active}
        # 4b. the dialog's own operator appeals C1
        created = _req(c, "POST", "/api/appeals", headers=_actor("operator", BAD_OPERATOR, APPEAL_REQUEST_ID), json={
            "evaluation_criterion_id": rows["C1"]["id"], "comment": "Я представился и сразу перешёл к вопросу клиента, прошу пересмотреть.",
            "author_label": "Оператор", "author_role": "operator", "author_name": BAD_OPERATOR}).json()
        after_create = _req(c, "GET", f"/api/conversations/{BAD_ID}/evaluation", params={"checklist_version_id": v1_id}).json()
        appeal["created"] = {"id": created["id"], "status": created["status"], "author_role": created.get("author_role"),
                             "author_name": created.get("author_name"), "operator_name": created.get("operator_name"),
                             "review_state": next(r["review_state"] for r in after_create["criteria"] if r["code"] == "C1"),
                             "card_review_state": after_create.get("review_state")}
        duplicate = c.post("/api/appeals", json={"evaluation_criterion_id": rows["C1"]["id"], "comment": "ещё раз"}, headers=_actor("analyst", ANALYST))
        appeal["duplicate_open"] = _error(duplicate)
        if roles_active:
            appeal["operator_decides"] = _error(c.post(f"/api/appeals/{created['id']}/decision", headers=_actor("operator", BAD_OPERATOR),
                                                       json={"action": "change", "new_answer": "yes", "reason": "сам себе"}))
            appeal["visitor_runs"] = _error(c.post("/api/evaluations/run", json={**run_body, "dry_run": True}, headers=_actor("visitor")))
            appeal["operator_imports"] = _error(c.post("/api/conversations/paste", headers=_actor("operator", BAD_OPERATOR),
                                                       json={"conversation_id": "e2e-forbidden", "text": "client: тест"}))
        preview = _req(c, "POST", "/api/appeals/preview", json={"evaluation_criterion_id": rows["C1"]["id"], "new_answer": "yes"},
                       headers=_actor("reviewer", REVIEWER)).json()
        # 4c. the reviewer changes the answer; final totals are recomputed, the AI total is kept
        decided = _req(c, "POST", f"/api/appeals/{created['id']}/decision", headers=_actor("reviewer", REVIEWER, "e2e-decide-1"), json={
            "action": "change", "new_answer": "yes", "reason": "Оператор представился; формальное приветствие не обязательно для этого диалога.",
            "reviewer_label": "Проверяющий", "reviewer_role": "reviewer", "add_to_fewshot": True}).json()
        ev_after = decided["evaluation"]
        c1_after = next(r for r in ev_after["criteria"] if r["code"] == "C1")
        persisted = _req(c, "GET", f"/api/conversations/{BAD_ID}/evaluation", params={"checklist_version_id": v1_id}).json()
        appeal["decision"] = {"appeal_status": decided["appeal"]["status"], "action": (decided["appeal"].get("decision") or {}).get("action"),
                              "reviewer_role": (decided["appeal"].get("decision") or {}).get("reviewer_role"),
                              "preview_final_total": preview["final_total_score"], "total_score": ev_after["total_score"],
                              "final_total_score": ev_after["final_total_score"], "persisted_final_total": persisted["final_total_score"],
                              "expected_final_total": _expected_total(ev_after["criteria"], final=True),
                              "c1_final_answer": c1_after["final_answer"], "c1_review_state": c1_after["review_state"],
                              "c1_final_earned": c1_after["final_earned"], "sections_sum": round(sum(s["score"] for s in ev_after["sections"]), 2)}
        appeal["decide_twice"] = _error(c.post(f"/api/appeals/{created['id']}/decision", headers=_actor("reviewer", REVIEWER),
                                               json={"action": "confirm", "reason": "повтор"}))
        # 4d. an analyst disputes the gross error, the reviewer confirms it: totals stay
        disputed = _req(c, "POST", "/api/appeals", headers=_actor("analyst", ANALYST), json={
            "evaluation_criterion_id": rows["C8"]["id"], "comment": "Проверьте, действительно ли это оскорбление.", "author_label": "QA analyst",
            "author_role": "analyst", "author_name": ANALYST}).json()
        confirmed = _req(c, "POST", f"/api/appeals/{disputed['id']}/decision", headers=_actor("reviewer", REVIEWER), json={
            "action": "confirm", "reason": "Фраза «Вы вообще читать умеете?» — оскорбление клиента.", "reviewer_label": "Проверяющий",
            "reviewer_role": "reviewer", "add_to_fewshot": False}).json()
        appeal["confirm"] = {"final_total_score": confirmed["evaluation"]["final_total_score"], "critical_error": confirmed["evaluation"]["critical_error"],
                             "c8_review_state": next(r["review_state"] for r in confirmed["evaluation"]["criteria"] if r["code"] == "C8")}
        listed = _req(c, "GET", "/api/appeals", params={"operator": BAD_OPERATOR}).json()
        appeal["listed_for_operator"] = sorted((a["code"], a["status"]) for a in listed)
        summary["appeal"] = appeal

        # ---------------------------------------------------------------- 5. gross violations, operators, analytics, CSV/PDF
        vio = _req(c, "GET", "/api/violations", params={"checklist_version_id": v1_id}).json()
        summary["violations"] = {"total": vio["total"], "counts": vio["counts"],
                                 "items": [{"conversation_id": i["conversation_id"], "types": i["types"], "review_state": i["review_state"],
                                            "confirmed_by_reviewer": i["confirmed_by_reviewer"], "evidence_turns": [e["turn_idx"] for e in i["evidence"]]}
                                           for i in vio["items"]]}
        ops = _req(c, "GET", "/api/operators", params={"checklist_version_id": v1_id}).json()
        summary["operators"] = {o["operator_name"]: {"mean_total": o["mean_total"], "critical_count": o["critical_count"], "evaluated": o["evaluated"]} for o in ops}
        analytics = _req(c, "GET", "/api/analytics/summary", params={"checklist_version_id": v1_id}).json()
        csv_text = _req(c, "GET", "/api/export/evaluations.csv", params={"checklist_version_id": v1_id}).text
        pdf = _req(c, "GET", f"/api/export/evaluations/{raw_cards[BAD_ID]['id']}.pdf")
        summary["reports"] = {"analytics_count": analytics.get("count"), "csv_has_both": all(cid in csv_text for cid in ids),
                              "pdf_ok": pdf.content[:4] == b"%PDF" and pdf.headers.get("content-type", "").startswith("application/pdf")}

        # ---------------------------------------------------------------- 6. checklist v2 adds a criterion → partial re-evaluation
        criteria_v2 = [{k: v for k, v in cr.items() if k not in ("id", "judge_spec")} for cr in v1["criteria"]] + [NEW_CRITERION]
        v2 = _req(c, "POST", f"/api/checklists/{organizer['id']}/versions", headers=_actor("methodologist", METHODOLOGIST),
                  json={"change_note": "e2e: добавлен критерий C9", "compile_specs": False, "criteria": criteria_v2}).json()
        diff = _req(c, "GET", f"/api/checklists/{organizer['id']}/versions/{v1_id}/diff/{v2['id']}").json()
        partial_run = _req(c, "POST", "/api/evaluations/run", headers=_actor("analyst", ANALYST), json={
            "conversation_ids": ids, "checklist_version_id": v2["id"], "provider": "fake", "mode": "partial", "base_version_id": v1_id}).json()
        partial_job = _wait_job(c, partial_run["job_id"])
        partial_cards = {cid: _req(c, "GET", f"/api/conversations/{cid}/evaluation", params={"checklist_version_id": v2["id"]}).json() for cid in ids}
        partial_trace = _req(c, "GET", f"/api/evaluations/{partial_cards[BAD_ID]['id']}/trace").json()
        summary["checklist"].update({"v2_id": v2["id"], "v2_version_no": v2["version_no"], "v2_codes": [cr["code"] for cr in v2["criteria"]],
                                     "diff_added": diff["added"], "diff_removed": diff["removed"], "diff_changed": [d["code"] for d in diff["changed"]],
                                     "rejudge_codes": diff["rejudge_codes"]})
        summary["partial"] = {"rejudge_codes": partial_run.get("rejudge_codes"), "job_status": partial_job["status"], "done": partial_job["done"],
                              "failed": partial_job["failed"], "trace_codes": sorted(t["criterion_code"] for t in partial_trace),
                              "cards": {cid: _card_facts(partial_cards[cid], convs[cid]["turns"]) for cid in ids}}
        bad_partial = {r["code"]: r for r in partial_cards[BAD_ID]["criteria"]}
        summary["partial"]["bad_c1_carried_review"] = {"answer": bad_partial["C1"]["answer"], "final_answer": bad_partial["C1"]["final_answer"],
                                                       "review_state": bad_partial["C1"]["review_state"]}

        # ---------------------------------------------------------------- 7. results.json for the organizer version (v1)
        db = SessionLocal()
        try:
            doc, turn_counts, channels = rj.from_db(db, conversation_ids=ids, team_name=settings.team_name)
        finally:
            db.close()
        errors, warns = rj.validate_document(doc, expected_ids=ids, expected_count=2, turn_counts=turn_counts, channels=channels)
        out_path = Path(os.environ["DATA_DIR"]) / "results" / "results.json"
        cli_out = io.StringIO()
        with contextlib.redirect_stdout(cli_out):
            cli_rc = rj.main(["--db", "--ids", ",".join(ids), "--expect", "2", "--team", settings.team_name, "--out", str(out_path)])
            cli_validate_rc = rj.main(["--validate", str(out_path), "--expect", "2"])
        api_file = _req(c, "GET", "/api/export/results.json", headers=_actor("director", "Ерлан"),
                        params={"conversation_ids": ",".join(ids), "expect": 2, "team_name": settings.team_name})
        api_check = _req(c, "GET", "/api/export/results/validate", params={"conversation_ids": ",".join(ids), "expect": 2}).json()
        records = {r["conversation_id"]: r for r in doc["results"]}
        summary["export"] = {
            "errors": errors, "warnings": warns, "schema_version": doc["schema_version"], "team_name": doc["team_name"], "model": doc["model"],
            "statuses": {cid: records[cid]["status"] for cid in ids},
            "scores": {cid: records[cid]["score"] for cid in ids},
            "values": {cid: {cr["criterion_id"]: cr["value"] for cr in records[cid]["criteria"]} for cid in ids},
            "evidence_turns": {cid: {cr["criterion_id"]: cr["evidence_turns"] for cr in records[cid]["criteria"]} for cid in ids},
            "cli_rc": cli_rc, "cli_validate_rc": cli_validate_rc, "cli_tail": cli_out.getvalue().strip().splitlines()[-1:],
            "file_written": out_path.is_file() and json.loads(out_path.read_text(encoding="utf-8"))["results"] == doc["results"],
            "api_header_valid": api_file.headers.get("x-results-valid"), "api_same_results": api_file.json()["results"] == doc["results"],
            "api_validate": {"valid": api_check["valid"], "records": api_check["records"], "ok": api_check["ok"]},
        }

        # ---------------------------------------------------------------- 8. audit trail (roles package)
        audit = c.get("/api/audit", params={"limit": 500})
        if audit.status_code == 200:
            data = audit.json()
            events = data if isinstance(data, list) else next((data[k] for k in ("items", "events") if isinstance(data.get(k), list)), [])

            def actor_of(e: dict[str, Any]) -> tuple[Any, Any]:
                nested = e.get("actor") if isinstance(e.get("actor"), dict) else {}
                return e.get("actor_role", nested.get("role")), e.get("actor_name", nested.get("name"))

            def find(action: str, role: str, name: str) -> dict[str, Any] | None:
                """The successful event of this action by this actor (any list order; denied attempts are skipped)."""
                for e in events:
                    if e.get("action") == action and actor_of(e) == (role, name) and e.get("status_code") in (None, 200, 201):
                        return {"actor_role": role, "actor_name": name, "request_id": e.get("request_id"), "status_code": e.get("status_code"),
                                "entity_type": e.get("entity_type"), "entity_id": e.get("entity_id")}
                return None

            summary["audit"] = {
                "available": True, "count": len(events), "actions": sorted({str(e.get("action")) for e in events}),
                "upload": find("conversation.upload", "analyst", ANALYST), "run": find("evaluation.run", "analyst", ANALYST),
                "appeal_create_operator": find("appeal.create", "operator", BAD_OPERATOR),
                "appeal_decide_reviewer": find("appeal.decide", "reviewer", REVIEWER),
                "version_create": find("checklist.version.create", "methodologist", METHODOLOGIST),
                # the roles package also records refused writes (403); kept for information, not asserted
                "denied": sorted({f"{e.get('action')}:{actor_of(e)[0]}" for e in events if e.get("status_code") == 403}),
            }
        else:
            summary["audit"] = {"available": False, "status": audit.status_code}

        # ---------------------------------------------------------------- 9. error envelope
        missing = c.get("/api/evaluations/999999")
        summary["error_envelope"] = {"status": missing.status_code, "body": missing.json()}
    return summary


# ============================================================================================= pytest side


@pytest.fixture(scope="module")
def e2e(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    tmp = tmp_path_factory.mktemp("e2e")
    env = {
        **os.environ,
        "DATABASE_URL": f"sqlite:///{(tmp / 'e2e.db').as_posix()}",
        "DATA_DIR": str(tmp / "data"),
        "ANTHROPIC_API_KEY": "",  # never call the Claude API (overrides the repo .env)
        "ORGANIZER_API_URL": "",
        "PYTHONPATH": os.pathsep.join([str(API_DIR), os.environ.get("PYTHONPATH", "")]).rstrip(os.pathsep),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONIOENCODING": "utf-8",
        # pin the settings the flow depends on, whatever the developer's shell exports
        "TEAM_NAME": "Я Тебя Могну",
        "DAILY_BUDGET_USD": "75",
        "MAX_DIALOGS_PER_RUN": "200",
        "MAX_CONCURRENT_JOBS": "2",
        "MAX_PARALLEL_CRITERIA": "8",
        "MAX_PARALLEL_CONVERSATIONS": "3",
    }
    proc = subprocess.run([sys.executable, str(Path(__file__).resolve())], cwd=API_DIR, env=env, capture_output=True, check=False,
                          encoding="utf-8", errors="replace", timeout=300)
    line = next((ln for ln in reversed(proc.stdout.splitlines()) if ln.startswith(MARKER)), None)
    if proc.returncode != 0 or line is None:
        pytest.fail(f"e2e subprocess failed (rc={proc.returncode})\n--- stdout ---\n{proc.stdout[-3000:]}\n--- stderr ---\n{proc.stderr[-8000:]}",
                    pytrace=False)
    return json.loads(line[len(MARKER):])


def test_flow_is_isolated_and_offline(e2e):
    g = e2e["guard"]
    assert g["database_url"].endswith("/e2e.db") and g["database_url"] != f"sqlite:///{DEMO_DB.as_posix()}"
    assert g["anthropic_key_set"] is False and g["organizer_url_set"] is False
    if e2e["fake_routed_natively"] is False:
        warnings.warn("cqa.judge.runner._members does not route provider 'fake' yet; the e2e flow used a test shim "
                      "(integration request in docs/arch/testing.md)", stacklevel=1)


def test_import_two_organizer_chats(e2e):
    imp = e2e["import"]
    assert imp["imported"] == 2 and imp["conversation_ids"] == [GOOD_ID, BAD_ID]
    assert imp["by_channel"] == {"agent chat": 2}
    assert imp["operators"] == {GOOD_ID: GOOD_OPERATOR, BAD_ID: BAD_OPERATOR}
    assert imp["turn_counts"] == {GOOD_ID: 10, BAD_ID: 9}


def test_fake_evaluation_job(e2e):
    run = e2e["run"]
    assert run["dry_run"]["queued"] == 2 and run["jobs_before"] == 0  # a dry run never creates a job
    assert run["queued"] == 2
    assert (run["job_status"], run["done"], run["failed"], run["cost_usd"]) == ("completed", 2, 0, 0.0), run["log_tail"]


def test_job_event_stream(e2e):
    sse = e2e["sse"]
    assert sse["status"] == 200 and sse["content_type"].startswith("text/event-stream")
    assert sse["events"].get("criterion.done") == 16  # 8 criteria x 2 dialogs
    assert sse["events"].get("evaluation.done") == 2
    assert sse["events"].get("job.done", 0) >= 1


def test_conversation_cards(e2e):
    good, bad = e2e["cards"][GOOD_ID], e2e["cards"][BAD_ID]
    for card in (good, bad):
        assert card["status"] == "completed" and card["provider"] == "fake" and card["model"] == "fake:fake-judge" and card["mode"] == "single"
        assert card["codes"] == e2e["checklist"]["v1_codes"] == ["C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8"]
        assert (card["cost_usd"], card["input_tokens"], card["output_tokens"]) == (0.0, 0, 0)
        assert card["evidence_exists"] and card["cites_operator_turn"] and card["explanations_ru"] and card["fake_flagged"]
        assert card["validation_flags"] == []  # fake verdicts pass cqa.judge.validate without a single issue
        assert card["total_score"] == card["final_total_score"] == card["expected_total"]
        assert card["weight_applied_sum"] == pytest.approx(100.0, abs=0.01) and card["earned_sum"] == pytest.approx(card["total_score"], abs=0.01)
        assert card["sections_sum"] == pytest.approx(card["total_score"], abs=0.03)
    # scale answers are stored as the option value string ("20"), as cqa.judge.validate normalises them
    assert good["answers"] == {"C1": "yes", "C2": "yes", "C3": "yes", "C4": "20", "C5": "yes", "C6": "10", "C7": "yes", "C8": "no"}
    assert good["total_score"] == 100.0 and good["critical_error"] is False
    # no greeting, no account talk (C2/C3 N/A), rude, no farewell, insult: weights 10+20+15+15+5 = 65, earned 20+15 = 35
    assert bad["answers"] == {"C1": "no", "C2": "N/A", "C3": "N/A", "C4": "20", "C5": "no", "C6": "10", "C7": "no", "C8": "yes"}
    assert bad["total_score"] == 53.85 and bad["critical_error"] is True
    tr = e2e["trace"]
    assert tr["n"] == 8 and tr["providers"] == ["fake"] and tr["models"] == ["fake-judge"] and tr["cost_usd"] == 0 and tr["tokens"] == 0
    assert tr["codes"] == ["C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8"]


def test_operator_appeal_and_reviewer_decision(e2e):
    ap = e2e["appeal"]
    assert (ap["foreign_operator"]["status"], ap["foreign_operator"]["code"]) == (403, "not_your_dialog")
    cr = ap["created"]
    assert cr["status"] == "open" and cr["author_role"] == "operator" and cr["author_name"] == BAD_OPERATOR and cr["operator_name"] == BAD_OPERATOR
    assert cr["review_state"] == "under_review" and cr["card_review_state"] == "under_review"
    assert (ap["duplicate_open"]["status"], ap["duplicate_open"]["code"]) == (409, "already_open")
    d = ap["decision"]
    assert d["appeal_status"] == "decided" and d["action"] == "change" and d["reviewer_role"] == "reviewer"
    assert d["c1_final_answer"] == "yes" and d["c1_review_state"] == "changed" and d["c1_final_earned"] > 0
    # C1 "no" → "yes": earned 35 → 45 of 65 → 69.23; the AI total is kept
    assert d["total_score"] == 53.85
    assert d["final_total_score"] == d["expected_final_total"] == d["preview_final_total"] == d["persisted_final_total"] == 69.23
    assert d["sections_sum"] == pytest.approx(69.23, abs=0.03)
    assert (ap["decide_twice"]["status"], ap["decide_twice"]["code"]) == (400, "invalid_decision")
    assert ap["confirm"] == {"final_total_score": 69.23, "critical_error": True, "c8_review_state": "confirmed"}
    assert ap["listed_for_operator"] == [["C1", "decided"], ["C8", "decided"]]


def test_roles_permissions_when_installed(e2e):
    if not e2e["roles"]["active"]:
        pytest.skip("roles middleware (cqa.security.middleware) not installed yet")
    ap = e2e["appeal"]
    assert ap["foreign_operator"]["body_had_author"] is False  # author filled from X-CQA-Role / X-CQA-User
    for key in ("operator_decides", "visitor_runs", "operator_imports"):
        assert (ap[key]["status"], ap[key]["code"]) == (403, "forbidden"), key
    me = e2e["roles"]["me"]
    assert me["status"] == 200 and BAD_OPERATOR in json.dumps(me["body"], ensure_ascii=False) and "operator" in json.dumps(me["body"])


def test_gross_violations_and_operator_stats(e2e):
    vio = e2e["violations"]
    assert vio["total"] == 1 and vio["counts"]["insult"] == 1
    item = vio["items"][0]
    assert item["conversation_id"] == BAD_ID and item["types"] == ["insult"] and item["evidence_turns"] == [5]
    assert item["review_state"] == "confirmed" and item["confirmed_by_reviewer"] is True
    ops = e2e["operators"]
    assert ops[GOOD_OPERATOR] == {"mean_total": 100.0, "critical_count": 0, "evaluated": 1}
    assert ops[BAD_OPERATOR] == {"mean_total": 69.23, "critical_count": 1, "evaluated": 1}
    assert e2e["reports"] == {"analytics_count": 2, "csv_has_both": True, "pdf_ok": True}


def test_new_criterion_version_and_partial_reevaluation(e2e):
    ck = e2e["checklist"]
    assert ck["v2_version_no"] == 2 and ck["v2_codes"] == ck["v1_codes"] + ["C9"]
    assert ck["diff_added"] == ["C9"] and ck["diff_removed"] == [] and ck["diff_changed"] == [] and ck["rejudge_codes"] == ["C9"]
    p = e2e["partial"]
    assert p["rejudge_codes"] == ["C9"]
    assert (p["job_status"], p["done"], p["failed"]) == ("completed", 2, 0)
    assert p["trace_codes"] == ["C9"]  # only the new criterion went to the judge
    for cid, card in p["cards"].items():
        assert card["mode"] == "partial" and card["status"] == "completed", cid
        assert card["codes"] == ck["v2_codes"] and card["answers"]["C9"] == "yes"
        assert card["copied"] == ck["v1_codes"]  # C1..C8 copied from the v1 evaluation
        assert card["evidence_exists"] and card["validation_flags"] == []
        assert card["total_score"] == card["expected_total"] and card["weight_applied_sum"] == pytest.approx(100.0, abs=0.01)
    assert p["cards"][GOOD_ID]["total_score"] == 100.0
    # copied AI answers + C9 "yes": weights 65 + 5 = 70, earned 35 + 5 = 40 → AI total 57.14; the reviewer's C1 change
    # (no → yes, decided on the v1 evaluation) is carried into the copied row, so the final total is 71.43
    bad = p["cards"][BAD_ID]
    assert bad["total_score"] == 57.14 and bad["critical_error"] is True
    assert p["bad_c1_carried_review"] == {"answer": "no", "final_answer": "yes", "review_state": "changed"}
    assert bad["final_total_score"] == 71.43 == bad["expected_final_total"]


def test_results_json_export_for_organizer_version(e2e):
    ex = e2e["export"]
    assert ex["errors"] == [], ex["errors"]
    assert ex["schema_version"] == "1.0" and ex["team_name"] == "Я Тебя Могну"
    assert ex["model"] == {"mode": ex["model"]["mode"], "name": "fake-judge"} and ex["model"]["mode"] in ("local", "external_api")
    assert ex["statuses"] == {GOOD_ID: "ok", BAD_ID: "ok"}
    assert ex["values"][GOOD_ID] == {"contact": True, "discovery": True, "identification": True, "consultation": 20, "empathy": True,
                                     "dialogue": 10, "closure": True, "critical": False}
    assert ex["values"][BAD_ID]["discovery"] is None and ex["values"][BAD_ID]["identification"] is None and ex["values"][BAD_ID]["critical"] is True
    assert ex["scores"][GOOD_ID] == {"final_score": 100.0, "critical_flag": False}
    assert ex["scores"][BAD_ID]["critical_flag"] is True and ex["scores"][BAD_ID]["final_score"] in (53.85, 69.23)
    assert ex["evidence_turns"][BAD_ID]["critical"] == [6]  # 1-based index of the original transcript array
    assert all(turns and all(t >= 1 for t in turns) for rec in ex["evidence_turns"].values() for turns in rec.values())
    assert ex["cli_rc"] == 0 and ex["cli_validate_rc"] == 0 and ex["cli_tail"] == ["VALID"] and ex["file_written"]
    assert ex["api_header_valid"] == "true" and ex["api_same_results"] and ex["api_validate"] == {"valid": True, "records": 2, "ok": 2}


def test_audit_trail_when_available(e2e):
    au = e2e["audit"]
    if not au["available"]:
        pytest.skip(f"GET /api/audit not available yet (HTTP {au['status']})")
    if not e2e["roles"]["active"]:
        pytest.skip("audit router present but the security middleware that records requests is not active")
    required = {"conversation.upload", "evaluation.run", "appeal.create", "appeal.decide", "checklist.version.create"}
    assert required <= set(au["actions"]), sorted(required - set(au["actions"]))
    # one successful event per step, attributed to the URL-decoded X-CQA-User of that step
    for key in ("upload", "run", "appeal_create_operator", "appeal_decide_reviewer", "version_create"):
        assert au[key] is not None, (key, au["actions"])
    op = au["appeal_create_operator"]
    assert op["entity_type"] == "appeal" and op["entity_id"] == str(e2e["appeal"]["created"]["id"])
    if op.get("request_id") is not None:  # X-Request-ID is the client correlation id (docs/ARCH_CONTRACTS.md §1)
        assert op["request_id"] == APPEAL_REQUEST_ID
    assert au["version_create"]["entity_id"] == str(e2e["checklist"]["v2_id"])


def test_error_envelope(e2e):
    env = e2e["error_envelope"]
    assert env["status"] == 404
    assert set(env["body"]) == {"error"} and env["body"]["error"]["code"] == "not_found" and env["body"]["error"]["message"]


if __name__ == "__main__":
    print(MARKER + json.dumps(run_flow(), default=str))
