"""results.json export in the organizers' "JSON 1.0" format (docs/ORGANIZER_UPDATES.md §1).

Two data sources, one builder:
- the local database (used by GET /api/export/results.json and by the CLI with --db), and
- any running CQA API over REST (CLI with --api), so the file can be produced from the hosted instance.

The builder works on plain dicts shaped like the API's evaluation JSON (see cqa.serializers.evaluation_dict),
so both sources share exactly the same mapping and validation.

CLI:
  uv run python -m cqa.export.results_json --api https://api.example.com --group DEV --out ../results/dev/results.json
  uv run python -m cqa.export.results_json --db --group DEV --out ../results/dev/results.json
  uv run python -m cqa.export.results_json --validate ../results/prod/results.json --expect 10
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

SCHEMA_VERSION = "1.0"

# Organizer criterion ids (appendix, page 3). Keys are our checklist codes.
DEFAULT_EXPORT_IDS: dict[str, str] = {
    "C1": "contact",
    "C2": "discovery",
    "C3": "identification",
    "C4": "consultation",
    "C5": "empathy",
    "C6": "dialogue",
    "C7": "closure",
    "C8": "critical",
}
REQUIRED_IDS: tuple[str, ...] = ("contact", "discovery", "identification", "consultation", "empathy", "dialogue", "closure", "critical")

# Allowed value domains per organizer table. None means N/A (null).
ALLOWED_VALUES: dict[str, set[Any]] = {
    "contact": {True, False},
    "discovery": {True, False, None},
    "identification": {True, False, None},
    "consultation": {0, 5, 10, 15, 20, None},
    "empathy": {True, False},
    "dialogue": {0, 4, 7, 10},
    "closure": {True, False, None},
    "critical": {True, False},
}
WEIGHTS: dict[str, float] = {"contact": 10, "discovery": 15, "identification": 20, "consultation": 20, "empathy": 15, "dialogue": 15, "closure": 5, "critical": 0}
MAX_POINTS: dict[str, float] = {"consultation": 20, "dialogue": 10}

RATIONALE_MAX_CHARS = 1200
ANSWER_SOURCES = ("ai", "final")  # ai = the judge's answers (default, what the system produced); final = after reviewer decisions
LOCAL_PROVIDERS = ("local", "ollama", "vllm", "openai_compatible", "fake")  # offline judges count as model.mode "local"


class ExportError(Exception):
    pass


# --------------------------------------------------------------------------------------------- building


def _sentence(text: str) -> str:
    """One sentence ending in a single period (the N/A reason is often already a full sentence)."""
    t = (text or "").strip()
    return t if t.endswith(("…", "!", "?")) else t.rstrip(".").rstrip() + "."


def _round2(x: float) -> float:
    return float(Decimal(repr(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def criterion_value(answer_type: str, applicable: bool, answer: Any) -> Any:
    """Map our stored verdict to the organizer value: True/False for Да/Нет, int for scale steps, None for N/A."""
    if not applicable:
        return None
    if answer is None:
        raise ExportError("criterion has no answer")
    a = str(answer).strip().lower()
    if answer_type == "binary":
        if a in ("yes", "да", "true"):
            return True
        if a in ("no", "нет", "false"):
            return False
        raise ExportError(f"binary answer '{answer}' is not yes/no")
    try:
        f = float(a)
    except ValueError as e:
        raise ExportError(f"scale answer '{answer}' is not a number") from e
    return int(f) if f.is_integer() else f


def evidence_turns(evidence: Iterable[dict[str, Any]] | None, checked_span: dict[str, Any] | None, n_turns: int | None) -> list[int]:
    """1-based indices into the ORIGINAL transcript array (client, bot and system events included)."""
    idx: set[int] = set()
    ev = list(evidence or [])
    # prefer turns the judge marked as supporting/contradicting; fall back to context turns only if nothing else
    primary = [e for e in ev if (e.get("role") or "supports") != "context"]
    for e in primary or ev:
        t = e.get("turn_idx")
        if isinstance(t, int) and t >= 0:
            idx.add(t + 1)
    if not idx and checked_span:
        a, b = checked_span.get("from_turn"), checked_span.get("to_turn")
        if isinstance(a, int) and isinstance(b, int) and 0 <= a <= b:
            # where the missed action was expected: cite the bounds of the checked span
            idx.update({a + 1, b + 1})
    out = sorted(i for i in idx if n_turns is None or i <= n_turns)
    return out


def recompute_final_score(values: dict[str, Any], weights: dict[str, float] | None = None, max_points: dict[str, float] | None = None) -> float | None:
    """Organizer rule: N/A excluded, weights redistributed proportionally, 0..100, two decimals half-up."""
    weights = weights or WEIGHTS
    max_points = max_points or MAX_POINTS
    applicable = {k: v for k, v in values.items() if weights.get(k, 0) > 0 and v is not None}
    w_sum = sum(weights[k] for k in applicable)
    if w_sum <= 0:
        return None
    total = 0.0
    for k, v in applicable.items():
        if isinstance(v, bool):
            ratio = 1.0 if v else 0.0
        else:
            ratio = float(v) / float(max_points.get(k, 1))
        total += weights[k] * max(0.0, min(1.0, ratio))
    return _round2(total * 100.0 / w_sum)


def export_mapping(codes: Iterable[str], scoring_rules: dict[str, Any] | None) -> dict[str, str]:
    """{our code: organizer criterion_id} for a checklist version, in organizer order: `scoring_rules.export_ids`
    overrides, else the organizer ids of C1..C8 (cqa.checklist.validation.export_ids_for). Raises ExportError when the
    version cannot fill all eight organizer ids (e.g. C6 replaced by C6b without `export_ids: {"C6b": "dialogue"}`)."""
    from ..checklist.validation import export_ids_for

    ids = export_ids_for(list(codes), scoring_rules or {})
    mapping = {code: eid for code, eid in ids.items() if eid in REQUIRED_IDS}
    missing = [rid for rid in REQUIRED_IDS if rid not in mapping.values()]
    if missing:
        raise ExportError("в версии чек-листа нет критериев для идентификаторов results.json: " + ", ".join(missing)
                          + " (сопоставьте их через scoring_rules.export_ids или выгрузите версию организаторов)")
    return dict(sorted(mapping.items(), key=lambda kv: REQUIRED_IDS.index(kv[1])))


def _row_value(r: dict[str, Any], answers: str) -> tuple[bool, Any]:
    """(applicable, answer) of one criterion row: the judge's answer, or with answers="final" the reviewer-final one."""
    if answers == "final" and r.get("review_state") in ("confirmed", "changed"):
        final = r.get("final_answer")
        if final is None or final == "N/A":
            return False, None
        return True, final
    return bool(r.get("applicable", True)), r.get("answer")


def build_record(conversation_id: str, evaluation: dict[str, Any] | None, *, n_turns: int | None,
                 export_ids: dict[str, str] | None = None, criteria_defs: dict[str, dict[str, Any]] | None = None,
                 answers: str = "ai") -> dict[str, Any]:
    """One results[] item. Returns an error record (never raises) when the dialog could not be evaluated.
    `answers="final"` exports reviewer decisions instead of the judge's answers (opt-in; the default is the AI)."""
    export_ids = export_ids or DEFAULT_EXPORT_IDS
    if evaluation is None:
        return {"conversation_id": conversation_id, "status": "error", "criteria": [], "score": None, "error": "Диалог не был оценён."}
    if evaluation.get("status") not in ("completed",):
        reason = evaluation.get("error") or f"Оценка завершилась со статусом {evaluation.get('status')}."
        return {"conversation_id": conversation_id, "status": "error", "criteria": [], "score": None, "error": str(reason)[:500]}
    rows = {r.get("code"): r for r in evaluation.get("criteria") or []}
    criteria: list[dict[str, Any]] = []
    values: dict[str, Any] = {}
    weights: dict[str, float] = {}
    max_points: dict[str, float] = {}
    try:
        for code, cid in export_ids.items():
            r = rows.get(code)
            if r is None:
                raise ExportError(f"нет вердикта по критерию {code} ({cid})")
            if "error" in (r.get("flags") or []):
                raise ExportError(f"критерий {code} ({cid}) не оценён: {r.get('explanation') or 'ошибка модели'}")
            cdef = (criteria_defs or {}).get(code, {})
            answer_type = r.get("answer_type") or cdef.get("answer_type") or ("scale" if cid in ("consultation", "dialogue") else "binary")
            applicable, answer = _row_value(r, answers)
            value = criterion_value(answer_type, applicable, answer)
            rationale = (r.get("explanation") or r.get("na_reason") or "").strip()
            if not r.get("applicable", True) and r.get("na_reason") and r.get("na_reason") not in rationale:
                rationale = f"N/A — {_sentence(r.get('na_reason'))} {rationale}".strip()
            if answers == "final" and r.get("review_state") == "changed":
                reason = (((r.get("appeal") or {}).get("decision") or {}).get("reason") or "").strip()
                note = f"Решение ревьюера: {reason}" if reason else "Оценка изменена ревьюером"
                rationale = f"{note.rstrip('.')}. Обоснование модели: {rationale}" if rationale else note
            if not rationale:
                rationale = "Обоснование не сформировано."
            criteria.append({
                "criterion_id": cid,
                "value": value,
                "rationale": rationale[:RATIONALE_MAX_CHARS],
                "evidence_turns": evidence_turns(r.get("evidence"), r.get("checked_span"), n_turns),
            })
            values[cid] = value
            weights[cid] = float(r.get("weight") if r.get("weight") is not None else cdef.get("weight", WEIGHTS.get(cid, 0)))
            mp = r.get("max_points") if r.get("max_points") is not None else cdef.get("max_points")
            if mp:
                max_points[cid] = float(mp)
    except ExportError as e:
        return {"conversation_id": conversation_id, "status": "error", "criteria": [], "score": None, "error": str(e)[:500]}
    final_score = recompute_final_score(values, weights or None, max_points or None)
    return {
        "conversation_id": conversation_id,
        "status": "ok",
        "criteria": criteria,
        "score": {"final_score": final_score, "critical_flag": bool(values.get("critical"))},
    }


def model_block(model_strings: Iterable[str | None], providers: Iterable[str | None] = ()) -> dict[str, str]:
    names: list[str] = []
    kinds: set[str] = set()
    for s in model_strings:
        for part in (s or "").split(","):
            part = part.strip()
            if not part:
                continue
            prov, _, name = part.partition(":") if ":" in part else ("", "", part)
            name = name or part
            if name not in names:
                names.append(name)
            kinds.add("local" if prov in LOCAL_PROVIDERS else "external_api")
    for p in providers:
        if p in LOCAL_PROVIDERS:
            kinds.add("local")
        elif p in ("anthropic",):
            kinds.add("external_api")
        elif p == "ensemble":
            pass
    mode = "hybrid" if kinds == {"local", "external_api"} else (next(iter(kinds)) if kinds else "external_api")
    return {"mode": mode, "name": ", ".join(names) if names else "unknown"}


def build_document(records: list[dict[str, Any]], *, team_name: str, model: dict[str, str]) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "team_name": team_name, "model": model, "results": records}


# --------------------------------------------------------------------------------------------- validation


def validate_document(doc: Any, *, expected_ids: list[str] | None = None, expected_count: int | None = None,
                      turn_counts: dict[str, int] | None = None, channels: dict[str, str] | None = None) -> tuple[list[str], list[str]]:
    """Return (errors, warnings). Errors make the file unacceptable; warnings deserve a look."""
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(doc, dict):
        return ["root must be a JSON object"], warnings
    for k in ("schema_version", "team_name", "model", "results"):
        if k not in doc:
            errors.append(f"missing top-level field '{k}'")
    if doc.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be '{SCHEMA_VERSION}'")
    if not isinstance(doc.get("team_name"), str) or not doc.get("team_name", "").strip():
        errors.append("team_name must be a non-empty string")
    m = doc.get("model")
    if not isinstance(m, dict) or m.get("mode") not in ("local", "external_api", "hybrid") or not isinstance(m.get("name"), str) or not m.get("name"):
        errors.append("model must be {mode: local|external_api|hybrid, name: non-empty string}")
    results = doc.get("results")
    if not isinstance(results, list):
        return errors + ["results must be an array"], warnings
    seen: set[str] = set()
    for i, r in enumerate(results):
        where = f"results[{i}]"
        if not isinstance(r, dict):
            errors.append(f"{where} must be an object")
            continue
        cid = r.get("conversation_id")
        if not isinstance(cid, str) or not cid:
            errors.append(f"{where}.conversation_id must be a non-empty string")
            continue
        where = f"{cid}"
        if cid in seen:
            errors.append(f"{where}: duplicate conversation_id")
        seen.add(cid)
        status = r.get("status")
        if status == "error":
            if r.get("criteria") != []:
                errors.append(f"{where}: error record must have criteria []")
            if r.get("score") is not None:
                errors.append(f"{where}: error record must have score null")
            if not isinstance(r.get("error"), str) or not r.get("error"):
                errors.append(f"{where}: error record needs a non-empty 'error' reason")
            warnings.append(f"{where}: status error ({r.get('error')})")
            continue
        if status != "ok":
            errors.append(f"{where}: status must be 'ok' or 'error'")
            continue
        crit = r.get("criteria")
        if not isinstance(crit, list):
            errors.append(f"{where}: criteria must be an array")
            continue
        ids = [c.get("criterion_id") for c in crit if isinstance(c, dict)]
        if sorted(ids) != sorted(REQUIRED_IDS):
            missing = sorted(set(REQUIRED_IDS) - set(ids))
            extra = sorted(set(ids) - set(REQUIRED_IDS))
            dups = sorted({x for x in ids if ids.count(x) > 1})
            errors.append(f"{where}: criteria must contain each of the 8 ids exactly once (missing={missing}, extra={extra}, duplicates={dups})")
        values: dict[str, Any] = {}
        n_turns = (turn_counts or {}).get(cid)
        for c in crit:
            if not isinstance(c, dict):
                errors.append(f"{where}: criterion entries must be objects")
                continue
            k = c.get("criterion_id")
            v = c.get("value")
            if k in ALLOWED_VALUES:
                ok = (v is None and None in ALLOWED_VALUES[k]) or (isinstance(v, bool) and v in ALLOWED_VALUES[k]) or (
                    not isinstance(v, bool) and isinstance(v, (int, float)) and v in ALLOWED_VALUES[k])
                if not ok:
                    if k == "contact" and v is None:
                        # JSON 1.0 appendix: contact is true/false only, calls included (organizer answer 2026-11-08: the
                        # unrecorded-greeting case is ours to decide). A call whose opening was not recorded is credited by
                        # the system rule (cqa.judge.validate.opening_outside_recording), so a null comes from an older
                        # evaluation: re-judge C1 for this dialog
                        errors.append(f"{where}: contact must be true or false (no N/A for contact in JSON 1.0); "
                                      "re-judge C1 for this dialog")
                    else:
                        errors.append(f"{where}: {k} value {v!r} not allowed (allowed: {sorted(map(repr, ALLOWED_VALUES[k]))})")
                values[k] = v
            if not isinstance(c.get("rationale"), str) or not c.get("rationale").strip():
                errors.append(f"{where}: {k} needs a non-empty rationale")
            et = c.get("evidence_turns")
            if not isinstance(et, list) or not all(isinstance(x, int) and not isinstance(x, bool) and x >= 1 for x in et):
                errors.append(f"{where}: {k} evidence_turns must be a list of integers >= 1")
            else:
                if not et:
                    warnings.append(f"{where}: {k} has no evidence_turns")
                if n_turns is not None and any(x > n_turns for x in et):
                    errors.append(f"{where}: {k} evidence_turns {et} exceed transcript length {n_turns}")
        score = r.get("score")
        if not isinstance(score, dict):
            errors.append(f"{where}: score must be an object")
            continue
        fs = score.get("final_score")
        if fs is not None and (not isinstance(fs, (int, float)) or isinstance(fs, bool) or not (0 <= fs <= 100) or round(fs, 2) != fs):
            errors.append(f"{where}: final_score must be a number 0..100 with at most two decimals")
        if fs is None:
            warnings.append(f"{where}: final_score is null (no applicable weighted criteria)")
        if score.get("critical_flag") is not values.get("critical") and "critical" in values:
            errors.append(f"{where}: critical_flag must equal the value of 'critical'")
        if len(values) == 8 and all(k in values for k in REQUIRED_IDS):
            expect = recompute_final_score(values)
            if expect is not None and fs is not None and abs(expect - fs) > 0.011:
                warnings.append(f"{where}: final_score {fs} differs from the organizer weights recomputation {expect} (custom weights?)")
    if expected_ids is not None:
        missing = sorted(set(expected_ids) - seen)
        extra = sorted(seen - set(expected_ids))
        if missing:
            errors.append(f"missing conversation_ids: {missing}")
        if extra:
            errors.append(f"unexpected conversation_ids (not in the dataset): {extra}")
    if expected_count is not None and len(results) != expected_count:
        errors.append(f"expected exactly {expected_count} records, found {len(results)}")
    return errors, warnings


# --------------------------------------------------------------------------------------------- sources


def from_db(db, *, group_id: str | None = None, conversation_ids: list[str] | None = None, checklist_version_id: int | None = None,
            team_name: str, answers: str = "ai") -> tuple[dict[str, Any], dict[str, int], dict[str, str]]:
    """Build the document from the local database (used by the API endpoint and CLI --db).
    Criterion ids follow the version's export ids (organizer ids for the organizer checklist).

    Per dialog: the latest COMPLETED evaluation of the export version; without one, the latest evaluation of that same
    version (a failed one becomes an error record); an evaluation of another version is never exported. An explicit
    empty `conversation_ids` selects no dialog (None = no filter). `from_api` selects exactly the same evaluations."""
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from ..models import ChecklistVersion, Conversation, Dataset, Evaluation
    from ..serializers import evaluation_dict

    if answers not in ANSWER_SOURCES:
        raise ExportError(f"Параметр answers: допустимы {' или '.join(ANSWER_SOURCES)}")

    q = select(Conversation)
    if conversation_ids is not None:
        q = q.where(Conversation.id.in_(list(conversation_ids)))
    elif group_id:
        ds_ids = list(db.execute(select(Dataset.id).where(Dataset.group_id == group_id)).scalars())
        if not ds_ids:
            raise ExportError(f"Выборка с group_id «{group_id}» не импортирована")
        q = q.where(Conversation.dataset_id.in_(ds_ids))
    convs = db.execute(q.order_by(Conversation.imported_at, Conversation.id)).scalars().all()
    if checklist_version_id is None:
        v1 = db.execute(select(ChecklistVersion).where(ChecklistVersion.version_no == 1).order_by(ChecklistVersion.id)).scalars().first()
        checklist_version_id = v1.id if v1 else None
    export_ids: dict[str, str] | None = None
    criteria_defs: dict[str, dict[str, Any]] | None = None
    if checklist_version_id:
        version = db.execute(select(ChecklistVersion).where(ChecklistVersion.id == checklist_version_id)
                             .options(selectinload(ChecklistVersion.criteria))).scalar_one_or_none()
        if version is None:
            raise ExportError(f"Версия чек-листа {checklist_version_id} не найдена")
        export_ids = export_mapping([c.code for c in version.criteria], version.scoring_rules)
        criteria_defs = {c.code: {"answer_type": c.answer_type, "weight": c.weight, "max_points": c.max_points} for c in version.criteria}
    records: list[dict[str, Any]] = []
    models: list[str | None] = []
    providers: list[str | None] = []
    turn_counts: dict[str, int] = {}
    channels: dict[str, str] = {}
    for c in convs:
        turn_counts[c.id] = c.turn_count
        channels[c.id] = c.channel
        q2 = select(Evaluation).where(Evaluation.conversation_id == c.id, Evaluation.status == "completed")
        if checklist_version_id:
            q2 = q2.where(Evaluation.checklist_version_id == checklist_version_id)
        ev = db.execute(q2.order_by(Evaluation.id.desc())).scalars().first()
        if ev is None:  # no completed evaluation of this version: its latest attempt (an error record), never another version's
            q3 = select(Evaluation).where(Evaluation.conversation_id == c.id)
            if checklist_version_id:
                q3 = q3.where(Evaluation.checklist_version_id == checklist_version_id)
            failed = db.execute(q3.order_by(Evaluation.id.desc())).scalars().first()
            evd = evaluation_dict(db, failed) if failed else None
        else:
            evd = evaluation_dict(db, ev)
            models.append(ev.model)
            providers.append(ev.provider)
        records.append(build_record(c.id, evd, n_turns=c.turn_count, export_ids=export_ids, criteria_defs=criteria_defs,
                                    answers=answers))
    doc = build_document(records, team_name=team_name, model=model_block(models, providers))
    return doc, turn_counts, channels


def _api_version_mapping(http: Any, base: str, checklists: list[dict[str, Any]], version_id: int | None) -> dict[str, str] | None:
    """Export ids of a version through the API (`export_ids` in the version JSON); None for older APIs → defaults."""
    if not version_id:
        return None
    for cl in checklists:
        if any(v.get("id") == version_id for v in cl.get("versions", [])):
            r = http.get(f"{base}/api/checklists/{cl['id']}/versions/{version_id}")
            if r.status_code != 200:
                return None
            data = r.json()
            ids = data.get("export_ids")
            if not isinstance(ids, dict):
                return None
            return export_mapping([c.get("code") for c in data.get("criteria") or []], {"export_ids": ids})
    return None


def _api_evaluation(http: Any, base: str, cid: str, checklist_version_id: int | None) -> dict[str, Any] | None:
    """The evaluation `from_db` would export for one dialog, through public read endpoints: the latest completed
    evaluation of the version, else its latest evaluation of the version (error record), else None. Uses the history
    (GET /api/conversations/{id}/evaluations) and GET /api/evaluations/{id}; falls back to the latest-evaluation endpoint
    when the history is unavailable."""
    r = http.get(f"{base}/api/conversations/{quote(cid, safe='')}/evaluations")
    history = r.json() if r.status_code == 200 else None
    if isinstance(history, list):
        mine = [e for e in history if isinstance(e, dict)
                and (not checklist_version_id or e.get("checklist_version_id") == checklist_version_id)]
        mine.sort(key=lambda e: int(e.get("id") or 0), reverse=True)
        pick = next((e for e in mine if e.get("status") == "completed"), None) or (mine[0] if mine else None)
        if pick is None:
            return None
        detail = http.get(f"{base}/api/evaluations/{pick['id']}")
        return detail.json() if detail.status_code == 200 else None
    params = {"checklist_version_id": checklist_version_id} if checklist_version_id else {}
    r = http.get(f"{base}/api/conversations/{quote(cid, safe='')}/evaluation", params=params)
    return r.json() if r.status_code == 200 else None


def from_api(base_url: str, *, group_id: str | None = None, conversation_ids: list[str] | None = None,
             checklist_version_id: int | None = None, team_name: str, timeout: float = 60.0,
             answers: str = "ai") -> tuple[dict[str, Any], dict[str, int], dict[str, str]]:
    """Build the document from a running CQA API (hosted or local) using only public read endpoints. Selects the same
    evaluations as `from_db`; an explicit empty `conversation_ids` selects no dialog (None = no filter)."""
    import httpx

    if answers not in ANSWER_SOURCES:
        raise ExportError(f"Параметр answers: допустимы {' или '.join(ANSWER_SOURCES)}")
    base = base_url.rstrip("/")
    with httpx.Client(timeout=timeout, follow_redirects=True) as http:
        convs = http.get(f"{base}/api/conversations", params={"page_size": 500}).json()["items"]
        if conversation_ids is not None:
            wanted = set(conversation_ids)
            convs = [c for c in convs if c["id"] in wanted]
            missing = wanted - {c["id"] for c in convs}
            if missing:
                raise ExportError(f"conversation ids not found on {base}: {sorted(missing)}")
        elif group_id:
            r = http.get(f"{base}/api/datasets")
            if r.status_code == 200:
                ds_ids = {d["id"] for d in r.json() if d.get("group_id") == group_id}
                if not ds_ids:
                    raise ExportError(f"no dataset with group_id '{group_id}' on {base}")
                convs = [c for c in convs if c.get("dataset_id") in ds_ids]
            elif group_id.upper() == "DEV":
                # older deployments without /api/datasets: DEV ids follow the organizer naming
                convs = [c for c in convs if c["id"].startswith(("p1-", "p4-"))]
            else:
                raise ExportError("this API has no /api/datasets endpoint; pass --ids for a non-DEV group")
        cls = http.get(f"{base}/api/checklists").json()
        if checklist_version_id is None:
            v1 = None
            for cl in cls:
                for v in cl.get("versions", []):
                    if v.get("version_no") == 1 and (v1 is None or v["id"] < v1):
                        v1 = v["id"]
            checklist_version_id = v1
        export_ids = _api_version_mapping(http, base, cls, checklist_version_id)
        records: list[dict[str, Any]] = []
        models: list[str | None] = []
        providers: list[str | None] = []
        turn_counts: dict[str, int] = {}
        channels: dict[str, str] = {}
        for c in convs:
            cid = c["id"]
            turn_counts[cid] = c.get("turn_count")
            channels[cid] = c.get("channel")
            evd = _api_evaluation(http, base, cid, checklist_version_id)
            if evd is not None:
                models.append(evd.get("model"))
                providers.append(evd.get("provider"))
            records.append(build_record(cid, evd, n_turns=c.get("turn_count"), export_ids=export_ids, answers=answers))
    doc = build_document(records, team_name=team_name, model=model_block(models, providers))
    return doc, turn_counts, channels


def write_document(doc: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    json.loads(tmp.read_text(encoding="utf-8"))  # round-trip check
    os.replace(tmp, path)


def summarize(doc: dict[str, Any]) -> str:
    res = doc.get("results", [])
    ok = [r for r in res if r.get("status") == "ok"]
    err = [r for r in res if r.get("status") == "error"]
    crit = sum(1 for r in ok if r.get("score", {}).get("critical_flag"))
    scores = [r["score"]["final_score"] for r in ok if r.get("score", {}).get("final_score") is not None]
    mean = round(sum(scores) / len(scores), 2) if scores else None
    return f"records={len(res)} ok={len(ok)} error={len(err)} critical={crit} mean_final_score={mean} model={doc.get('model')}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build or validate results.json (organizer JSON 1.0)")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--api", help="base URL of a running CQA API (hosted or local)")
    src.add_argument("--db", action="store_true", help="read the local database (api/data/cqa.db or DATABASE_URL)")
    src.add_argument("--validate", metavar="PATH", help="only validate an existing results.json")
    ap.add_argument("--group", help="dataset group_id (DEV or the control group id)")
    ap.add_argument("--ids", help="comma-separated conversation ids (overrides --group)")
    ap.add_argument("--version-id", type=int, help="checklist version id (default: the organizer checklist, version 1)")
    ap.add_argument("--team", default=os.environ.get("TEAM_NAME", "Я Тебя Могну"), help="team_name (env TEAM_NAME)")
    ap.add_argument("--out", help="output path, e.g. ../results/dev/results.json")
    ap.add_argument("--expect", type=int, help="expected number of records (prod: 10)")
    ap.add_argument("--answers", choices=ANSWER_SOURCES, default="ai",
                    help="ai = the judge's answers (default); final = answers after reviewer decisions")
    args = ap.parse_args(argv)

    if args.validate:
        doc = json.loads(Path(args.validate).read_text(encoding="utf-8"))
        errors, warnings = validate_document(doc, expected_count=args.expect)
        print(summarize(doc))
        for w in warnings:
            print("WARN ", w)
        for e in errors:
            print("ERROR", e)
        print("VALID" if not errors else f"INVALID ({len(errors)} errors)")
        return 0 if not errors else 1

    ids = [x.strip() for x in args.ids.split(",")] if args.ids else None
    if args.api:
        doc, turn_counts, channels = from_api(args.api, group_id=args.group, conversation_ids=ids, checklist_version_id=args.version_id,
                                              team_name=args.team, answers=args.answers)
    else:
        from ..db import SessionLocal, init_db

        init_db()
        db = SessionLocal()
        try:
            doc, turn_counts, channels = from_db(db, group_id=args.group, conversation_ids=ids, checklist_version_id=args.version_id,
                                                 team_name=args.team, answers=args.answers)
        finally:
            db.close()
    errors, warnings = validate_document(doc, expected_ids=list(turn_counts), expected_count=args.expect, turn_counts=turn_counts, channels=channels)
    print(summarize(doc))
    for w in warnings:
        print("WARN ", w)
    for e in errors:
        print("ERROR", e)
    if args.out:
        write_document(doc, Path(args.out))
        print(f"written {args.out}")
    print("VALID" if not errors else f"INVALID ({len(errors)} errors)")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
