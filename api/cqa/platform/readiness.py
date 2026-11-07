"""Demo and control-hour readiness: one read that says whether the hosted instance is ready for the jury
(docs/USER_STORIES.md DEMO-03, DEMO-09, DEMO-04; docs/DEMO.md "Before the demo").

Every check is a read: no model call, no write. Severity:
- ``required``: the results submission or the live demo breaks without it (``ready`` is false when one fails);
- ``recommended``: the demo is weaker without it (``ready_for_demo`` also needs these);
- ``info``: shown for context, never blocks.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Appeal, Checklist, ChecklistVersion, Conversation, Dataset, Evaluation, Job

ORGANIZER_EXPORT_IDS = ("contact", "discovery", "identification", "consultation", "empathy", "dialogue", "closure", "critical")
CONTROL_DIALOGS = 10
LIVE_DEMO = ("p1-ru-029", "p1-ru-005")  # docs/DEMO.md blocks 2 and 3: never touched before the demo


def _check(code: str, ok: bool, severity: str, message: str, **value: Any) -> dict[str, Any]:
    return {"code": code, "ok": bool(ok), "severity": severity, "message": message, **({"value": value} if value else {})}


def _organizer_version(db: Session) -> ChecklistVersion | None:
    first = db.execute(select(Checklist).order_by(Checklist.id)).scalars().first()
    if first is None:
        return None
    return db.execute(select(ChecklistVersion).where(ChecklistVersion.checklist_id == first.id)
                      .order_by(ChecklistVersion.version_no)).scalars().first()


def readiness(db: Session, *, group_id: str = "DEV", expect: int | None = None, control_dialogs: int = CONTROL_DIALOGS) -> dict[str, Any]:
    from ..analytics.violations import violations
    from ..export.results_json import ExportError, from_db, validate_document
    from ..jobs import guards
    from ..judge.runner import estimate_cost
    from ..routers.settings_router import current

    checks: list[dict[str, Any]] = []
    try:
        db.execute(text("SELECT 1"))
        checks.append(_check("db", True, "required", "База данных доступна."))
    except Exception as e:  # pragma: no cover - the request would fail earlier
        checks.append(_check("db", False, "required", f"База данных недоступна: {type(e).__name__}."))
        return {"ready": False, "ready_for_demo": False, "generated_at": datetime.now(UTC).isoformat(), "checks": checks}

    cfg = current(db)
    checks.append(_check("claude_key", bool(settings.anthropic_api_key), "required",
                         "Ключ Claude API задан." if settings.anthropic_api_key else "Ключ Claude API не задан: оценка через Claude недоступна."))

    v1 = _organizer_version(db)
    if v1 is None:
        checks.append(_check("organizer_checklist", False, "required", "Чек-лист организаторов (версия 1) не найден."))
    else:
        from ..checklist.validation import export_ids_for

        codes = [c.code for c in sorted(v1.criteria, key=lambda c: c.order)]
        ids = export_ids_for(codes, v1.scoring_rules or {})
        weights = sum(float(c.weight or 0) for c in v1.criteria)
        ok = sorted(ids.values()) == sorted(ORGANIZER_EXPORT_IDS) and abs(weights - 100) < 1e-6
        checks.append(_check("organizer_checklist", ok, "required",
                             f"Чек-лист организаторов: версия #{v1.id}, {len(codes)} критериев, сумма весов {weights:g}."
                             if ok else f"Версия #{v1.id} не совпадает с форматом results.json (критерии {', '.join(codes)}, веса {weights:g}).",
                             version_id=v1.id, criteria=len(codes), weights_total=weights))

    ds_ids = [d.id for d in db.execute(select(Dataset).where(Dataset.group_id == group_id)).scalars()]
    group_convs = db.execute(select(Conversation.id).where(Conversation.dataset_id.in_(ds_ids))).scalars().all() if ds_ids else []
    if not group_convs and group_id.upper() == "DEV":  # very old databases: ids carry the DEV prefixes
        group_convs = db.execute(select(Conversation.id).where(Conversation.id.like("p_-%"))).scalars().all()
    n_group = len(group_convs)
    want = expect if expect is not None else (65 if group_id.upper() == "DEV" else None)
    checks.append(_check("dataset", n_group > 0 and (want is None or n_group == want), "required",
                         f"Выборка {group_id}: {n_group} диалогов" + (f" (ожидается {want})." if want is not None else "."),
                         conversations=n_group, expected=want))

    if v1 is not None and group_convs:
        evaluated = set(db.execute(select(Evaluation.conversation_id).where(
            Evaluation.checklist_version_id == v1.id, Evaluation.status == "completed",
            Evaluation.conversation_id.in_(group_convs))).scalars())
        missing = sorted(set(group_convs) - evaluated)
        checks.append(_check("coverage", not missing, "required",
                             f"Все {n_group} диалогов оценены по версии #{v1.id}." if not missing
                             else f"Не оценены по версии #{v1.id}: {len(missing)} ({', '.join(missing[:5])}{'…' if len(missing) > 5 else ''}).",
                             evaluated=len(evaluated), missing=missing[:20]))
        try:
            doc, turn_counts, channels = from_db(db, group_id=group_id, checklist_version_id=v1.id, team_name=settings.team_name)
            errors, warnings = validate_document(doc, expected_ids=list(turn_counts), expected_count=want, turn_counts=turn_counts, channels=channels)
            ok_records = sum(1 for r in doc["results"] if r["status"] == "ok")
            checks.append(_check("results_json", not errors, "required",
                                 f"results.json для {group_id} валиден: {ok_records} из {len(doc['results'])} записей ok."
                                 if not errors else f"results.json для {group_id}: {len(errors)} ошибок, первая: {errors[0]}",
                                 errors=errors[:5], warnings=len(warnings), ok_records=ok_records, records=len(doc["results"])))
        except ExportError as e:
            checks.append(_check("results_json", False, "required", f"results.json не собирается: {e}"))

    active = db.execute(select(Job.status, func.count()).where(Job.status.in_(("queued", "running", "interrupted"))).group_by(Job.status)).all()
    by_status = {str(s): int(n) for s, n in active}
    checks.append(_check("jobs", not by_status.get("running") and not by_status.get("queued"), "required",
                         "Нет выполняющихся задач." if not by_status else "Задачи: " + ", ".join(f"{k} {v}" for k, v in sorted(by_status.items())) + ".",
                         **by_status))

    budget = guards.budget_status(db)
    provider = str(cfg.get("default_provider") or "anthropic")
    reserve = estimate_cost(control_dialogs, 0, provider if provider in ("anthropic", "ensemble") else "anthropic") * 2
    budget_ok = budget["unlimited"] or (budget["remaining"] or 0) >= reserve
    checks.append(_check("budget_reserve", budget_ok, "required",
                         ("Бюджет без ограничения." if budget["unlimited"] else
                          f"Осталось ${budget['remaining']:.2f} из ${budget['limit']:.2f} за 24 ч; резерв на контрольный прогон ({control_dialogs} диалогов, "
                          f"{provider}, ×2) — ${reserve:.2f}."),
                         remaining=budget["remaining"], limit=budget["limit"], reserve=reserve))

    checks.append(_check("organizer_url", bool(settings.organizer_api_url), "recommended",
                         "Адрес API организаторов задан: импорт по group_id работает." if settings.organizer_api_url
                         else "ORGANIZER_API_URL не задан: контрольную выборку придётся загрузить файлом (--source-file)."))

    from ..models import EvaluationCriterion

    def appeal_rows(status: str):
        q = (select(Appeal.id, Evaluation.conversation_id, EvaluationCriterion.code, Evaluation.id)
             .join(EvaluationCriterion, Appeal.evaluation_criterion_id == EvaluationCriterion.id)
             .join(Evaluation, EvaluationCriterion.evaluation_id == Evaluation.id).where(Appeal.status == status))
        return db.execute(q).all()

    latest_ids = {cid: eid for cid, eid in db.execute(select(Evaluation.conversation_id, func.max(Evaluation.id))
                                                     .where(Evaluation.status == "completed").group_by(Evaluation.conversation_id)).all()}
    # the queue a juror sees: appeals outside the live-demo dialogs, on dialogs' current evaluations
    open_n = sum(1 for _, cid, _, eid in appeal_rows("open") if cid not in LIVE_DEMO and latest_ids.get(cid) == eid)
    decided_n = sum(1 for _, cid, _, eid in appeal_rows("decided") if cid not in LIVE_DEMO and latest_ids.get(cid) == eid)
    checks.append(_check("appeals_seeded", open_n >= 1 and decided_n >= 1, "recommended",
                         f"Апелляции вне демо-диалогов: открытых {open_n}, решённых {decided_n} (для показа нужна хотя бы одна "
                         "каждого вида; scripts/seed_demo.py).", open=open_n, decided=decided_n))
    # the live-demo dialogs must be untouched: the presenter files the appeal on p1-ru-005 C6 live and shows p1-ru-029
    dirty = []
    for status in ("open", "decided"):
        for aid, cid, code, eid in appeal_rows(status):
            if cid in LIVE_DEMO and latest_ids.get(cid) == eid:
                dirty.append(f"{cid} {code} #{aid} ({status})")
    checks.append(_check("live_demo_clean", not dirty, "recommended",
                         "Демо-диалоги p1-ru-029 и p1-ru-005 без апелляций на текущей оценке." if not dirty
                         else "На демо-диалогах есть апелляции: " + ", ".join(dirty) + " — восстановите снимок baseline.",
                         appeals=dirty))

    gv = violations(db, checklist_version_id=v1.id if v1 is not None else None)
    checks.append(_check("violations", gv["total"] > 0, "recommended",
                         f"Грубые нарушения: {gv['total']} (" + ", ".join(f"{t['ru']} {gv['counts'].get(t['code'], 0)}" for t in gv["types"]
                                                                  if gv["counts"].get(t["code"])) + ")." if gv["total"] else "Грубых нарушений не найдено.",
                         total=gv["total"], counts=gv["counts"]))

    try:
        from ..admin.snapshots import list_snapshots

        snaps = list_snapshots(Path(settings.data_dir) / "snapshots")
    except Exception:  # snapshots unsupported (Postgres) or the folder is missing
        snaps = []
    names = [s.get("name") for s in snaps]
    checks.append(_check("baseline_snapshot", "baseline" in names, "recommended",
                         "Снимок «baseline» есть: после работы жюри базу можно вернуть." if "baseline" in names
                         else "Нет снимка «baseline»: создайте его перед работой жюри (POST /api/admin/snapshots {\"name\": \"baseline\"}).",
                         snapshots=len(snaps)))

    corporate = not cfg.get("mascot_enabled") and str(cfg.get("default_theme") or "light") == "light"
    checks.append(_check("corporate_style", corporate, "recommended",
                         "Корпоративный стиль: светлая тема, без персонажа." if corporate
                         else f"Оформление: mascot_enabled={cfg.get('mascot_enabled')}, default_theme={cfg.get('default_theme')}.",
                         mascot_enabled=bool(cfg.get("mascot_enabled")), default_theme=cfg.get("default_theme")))

    checks.append(_check("fewshot_from_reviews", True, "info",
                         "Решения проверяющих " + ("пополняют" if cfg.get("fewshot_from_reviews") else "не пополняют")
                         + f" банк примеров для модели (сейчас примеров: {cfg.get('fewshot_count', 0)}). Перед контрольным прогоном конфигурацию стоит заморозить.",
                         enabled=bool(cfg.get("fewshot_from_reviews")), examples=cfg.get("fewshot_count", 0)))
    checks.append(_check("version", bool(settings.app_version), "info",
                         f"Версия сборки: {settings.app_version}." if settings.app_version else "APP_VERSION не задан (версия берётся из git или пакета).",
                         app_version=settings.app_version))

    required_ok = all(c["ok"] for c in checks if c["severity"] == "required")
    demo_ok = required_ok and all(c["ok"] for c in checks if c["severity"] == "recommended")
    failed = [c["code"] for c in checks if not c["ok"] and c["severity"] != "info"]
    return {"ready": required_ok, "ready_for_demo": demo_ok, "generated_at": datetime.now(UTC).isoformat(), "group_id": group_id,
            "summary": ("Готово к контрольному часу и показу." if demo_ok else
                        "Готово к контрольному часу; для показа: " + ", ".join(failed) + "." if required_ok else
                        "Не готово: " + ", ".join(failed) + "."),
            "failed": failed, "checks": checks}
