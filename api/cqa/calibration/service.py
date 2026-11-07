"""Calibration service: human gold labels, AI-vs-human agreement and run-to-run stability.

Gold labels (model ``GoldLabel``) are stored per (conversation, criterion code, labeler). Several people can
label the same dialogs (for example "alina", "kassym" and the reconciled set "team"). Two pseudo codes let the
teammate's ``eval/gold.json`` round-trip without loss:

* ``total``: the human total score of the dialog. Optional: agreement recomputes the human total from the
  labels with the scoring engine whenever every weighted criterion is labeled, and falls back to this value.
* ``_note``: a free-text note about the whole dialog, kept in the ``note`` column (``value`` stays null).

Values are stored canonically: ``"yes"`` | ``"no"`` | ``"N/A"`` | number (int when integral). Input also accepts
Да/Нет, Иә/Жоқ, true/false, numeric strings and null (null means N/A, as in the harness and in results.json).
Every value is validated against the criterion of the reference checklist version (organizer version 1 by
default): binary criteria take yes/no, scale criteria one of their listed steps, and N/A only where the
criterion or its channel override allows it (C1 on calls, per the organizers).

The split (train / validation) is a property of the dialog for one labeler: setting it on one label applies it
to every label of that dialog and labeler, and new labels inherit it.

Metrics reuse ``cqa.eval.harness`` (``_norm``, ``cohen_kappa``, ``quadratic_weighted_kappa``) with the same
formulas and rounding, so the API and ``python -m cqa.eval.harness`` report the same per-criterion numbers.
One deliberate difference: an AI criterion row that failed (flag ``error``) or is absent is skipped and
reported in ``coverage.skipped``, while the CLI counts it as N/A.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import combinations
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..eval.harness import _norm, cohen_kappa, quadratic_weighted_kappa
from ..models import ChecklistVersion, Conversation, Criterion, Evaluation, EvaluationCriterion, GoldLabel
from ..scoring.engine import CriterionInput, answer_points, compute_score

DEFAULT_LABELER = "team"
RECONCILED_LABELER = "team"  # the reconciled gold set; left out of the human-human ceiling
TOTAL_CODE = "total"
NOTE_CODE = "_note"
NOTES_KEY = "_notes"  # gold.json extension: {"C4": "why 15"} per dialog (export with include_notes)
META_KEY = "_meta"
NA = "N/A"
SOURCES = ("ai", "final")
SPLIT_ORDER = ("train", "validation")
COPIED_FLAG = "copied_from_previous_version"  # partial re-evaluation copied the row from the base evaluation
ERROR_FLAG = "error"  # the judge failed on this criterion
MAX_LABELER_LEN = 64
MAX_CODE_LEN = 16
MAX_SPLIT_LEN = 16
MAX_NOTE_LEN = 4000
MAX_ITEMS = 10_000
_CHUNK = 500

_YES = frozenset({"yes", "да", "иә", "true"})
_NO = frozenset({"no", "нет", "жоқ", "false"})
_NA_WORDS = frozenset({"n/a", "na", "none", "null", ""})


# --------------------------------------------------------------------------------------------- errors


class CalibrationError(Exception):
    """Base error. ``code`` and ``status`` map to the API envelope {"error": {"code", "message", ...extra}}."""

    code = "calibration_error"
    status = 400

    def __init__(self, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.message = message
        self.extra = extra


class ValidationFailed(CalibrationError):
    code = "validation_error"
    status = 422

    def __init__(self, errors: list[dict[str, str]], message: str | None = None) -> None:
        super().__init__(message or f"Данные не приняты: ошибок — {len(errors)}, подробности в errors.", errors=errors)
        self.errors = errors


class VersionNotFound(CalibrationError):
    code = "version_not_found"
    status = 404

    def __init__(self, version_id: Any) -> None:
        super().__init__(f"Версия чек-листа {version_id} не найдена", checklist_version_id=version_id)


def _err(field: str, message: str) -> dict[str, str]:
    return {"field": field, "message": message}


# --------------------------------------------------------------------------------------------- small helpers


def _chunked(items: Sequence[Any], size: int = _CHUNK) -> Iterable[Sequence[Any]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _num(x: float) -> int | float:
    f = float(x)
    return int(f) if f.is_integer() else f


def _as_float(x: Any) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        f = float(x)
    elif isinstance(x, str):
        try:
            f = float(x.strip().replace(",", "."))
        except ValueError:
            return None
    else:
        return None
    return f if math.isfinite(f) else None


def _same(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is None and b is None
    fa, fb = _as_float(a), _as_float(b)
    if fa is not None and fb is not None:
        return fa == fb
    return str(a).strip().lower() == str(b).strip().lower()


def _rate(k: int, n: int) -> float | None:
    return round(k / n, 3) if n else None


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _r(x: float | None, digits: int) -> float | None:
    return None if x is None else round(x, digits)


def _natural(code: str) -> tuple[tuple[int, int, str], ...]:
    parts = [p for p in re.split(r"(\d+)", code) if p]
    return tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in parts)


def _kind(code: str) -> str:
    if code == TOTAL_CODE:
        return "total"
    if code.startswith("_"):
        return "note"
    return "criterion"


def _code_sort_key(code: str, order: dict[str, int]) -> tuple[Any, ...]:
    kind_rank = {"criterion": 0, "total": 1, "note": 2}[_kind(code)]
    return (kind_rank, order.get(code, 10**6), _natural(code))


# --------------------------------------------------------------------------------------------- values


def canonical_value(raw: Any) -> Any:
    """Canonical stored form of a label value: "yes" | "no" | "N/A" | int | float, or a trimmed string that the
    criterion check accepts or rejects. Raises ValueError for objects, lists and non-finite numbers."""
    if raw is None:
        return NA
    if isinstance(raw, bool):
        return "yes" if raw else "no"
    if isinstance(raw, (int, float)):
        if not math.isfinite(float(raw)):
            raise ValueError("число должно быть конечным")
        return _num(raw)
    if isinstance(raw, str):
        s = raw.strip()
        low = s.lower()
        if low in _NA_WORDS:
            return NA
        if low in _YES:
            return "yes"
        if low in _NO:
            return "no"
        f = _as_float(s)
        return _num(f) if f is not None else s
    raise ValueError(f"тип {type(raw).__name__} не подходит: ожидается Да/Нет, число или N/A")


def _display(v: Any) -> Any:
    """Canonical form for output; never raises (unknown shapes are returned as stored)."""
    try:
        return canonical_value(v)
    except ValueError:
        return v


def na_allowed(crit: Criterion, channel: str | None) -> bool:
    """N/A availability, same rule as the judge (cqa.judge.validate.effective_na): a channel override may allow
    N/A where the base criterion does not. With no channel, any override counts."""
    if crit.na_allowed:
        return True
    overrides = crit.channel_overrides or {}
    if channel is None:
        return any(isinstance(ov, dict) and bool(ov.get("na_allowed")) for ov in overrides.values())
    ov = overrides.get(channel)
    return isinstance(ov, dict) and bool(ov.get("na_allowed"))


def _option_values(crit: Criterion) -> list[Any]:
    return [o.get("value") for o in (crit.options or []) if isinstance(o, dict) and "value" in o]


def _scale_levels(crit: Criterion) -> list[float]:
    """Same levels as the harness: the numeric option values, sorted."""
    return sorted({f for f in (_as_float(v) for v in _option_values(crit)) if f is not None})


def check_value(value: Any, crit: Criterion, channel: str | None) -> tuple[Any, str | None]:
    """Validate a canonical value against a criterion. Returns (value as stored, error message or None)."""
    code = crit.code
    na_ok = na_allowed(crit, channel)
    if value == NA:
        if na_ok:
            return NA, None
        where = f" в канале «{channel}»" if channel else ""
        return value, f"N/A недопустимо для {code}{where}: критерий применим всегда"
    tail = " или N/A" if na_ok else ""
    if crit.answer_type == "binary":
        allowed = [str(v).strip().lower() for v in _option_values(crit)] or ["yes", "no"]
        if isinstance(value, (int, float)) and value in (0, 1) and {"yes", "no"} <= set(allowed):
            value = "yes" if value == 1 else "no"
        if isinstance(value, str) and value.lower() in allowed:
            return value.lower(), None
        return value, f"Значение «{value}» недопустимо для {code}: ожидается Да или Нет (yes/no){tail}"
    if crit.answer_type == "scale":
        options = _option_values(crit)
        for opt in options:
            if _same(opt, value):
                f = _as_float(opt)
                return (_num(f) if f is not None else opt), None
        steps = ", ".join(str(_display(o)) for o in options)
        return value, f"Значение «{value}» недопустимо для {code}: допустимые шаги {steps}{tail}"
    return value, None


# --------------------------------------------------------------------------------------------- versions


def _version_query():
    return select(ChecklistVersion).options(selectinload(ChecklistVersion.criteria))


def organizer_version(db: Session) -> ChecklistVersion | None:
    """The organizer checklist: the first version with version_no 1 (same rule as the results.json export);
    falls back to the active version, then to the newest one."""
    v = db.scalars(_version_query().where(ChecklistVersion.version_no == 1).order_by(ChecklistVersion.id)).first()
    if v is None:
        v = db.scalars(_version_query().where(ChecklistVersion.is_active.is_(True)).order_by(ChecklistVersion.id.desc())).first()
    if v is None:
        v = db.scalars(_version_query().order_by(ChecklistVersion.id.desc())).first()
    return v


def resolve_version(db: Session, checklist_version_id: int | None = None) -> ChecklistVersion | None:
    """The requested version (VersionNotFound if missing) or the organizer version; None on an empty database."""
    if checklist_version_id is None:
        return organizer_version(db)
    v = db.scalars(_version_query().where(ChecklistVersion.id == checklist_version_id)).first()
    if v is None:
        raise VersionNotFound(checklist_version_id)
    return v


def _criteria(version: ChecklistVersion | None) -> list[Criterion]:
    return sorted(version.criteria, key=lambda c: (c.order, c.code)) if version is not None else []


def _order_map(version: ChecklistVersion | None) -> dict[str, int]:
    return {c.code: c.order for c in version.criteria} if version is not None else {}


# --------------------------------------------------------------------------------------------- labels: read


def label_dict(row: GoldLabel) -> dict[str, Any]:
    return {"id": row.id, "conversation_id": row.conversation_id, "criterion_code": row.criterion_code,
            "kind": _kind(row.criterion_code), "value": row.value, "labeler": row.labeler, "note": row.note,
            "split": row.split, "created_at": _iso(_aware(row.created_at)), "updated_at": _iso(_aware(row.updated_at))}


def _split_filter(split: str | None) -> str | None:
    if split is None:
        return None
    s = split.strip().lower()
    return None if s in ("", "all") else s


def list_labels(db: Session, conversation_id: str | None = None, labeler: str | None = None, *,
                criterion_code: str | None = None, split: str | None = None) -> list[dict[str, Any]]:
    """Label rows ordered by labeler, conversation and criterion order (criteria, then total, then the note)."""
    stmt = select(GoldLabel)
    if conversation_id and conversation_id.strip():
        stmt = stmt.where(GoldLabel.conversation_id == conversation_id.strip())
    if labeler and labeler.strip():
        stmt = stmt.where(GoldLabel.labeler == labeler.strip())
    if criterion_code and criterion_code.strip():
        stmt = stmt.where(GoldLabel.criterion_code == criterion_code.strip())
    split_f = _split_filter(split)
    if split_f:
        stmt = stmt.where(GoldLabel.split == split_f)
    rows = list(db.scalars(stmt))
    order = _order_map(organizer_version(db))
    rows.sort(key=lambda r: (r.labeler, r.conversation_id, _code_sort_key(r.criterion_code, order)))
    return [label_dict(r) for r in rows]


def labelers(db: Session) -> list[dict[str, Any]]:
    """Per labeler: criterion labels, labeled dialogs, total labels and the last change."""
    agg: dict[str, dict[str, Any]] = {}
    stmt = select(GoldLabel.labeler, GoldLabel.conversation_id, GoldLabel.criterion_code, GoldLabel.updated_at)
    for lab, cid, code, updated in db.execute(stmt):
        a = agg.setdefault(lab, {"labels": 0, "dialogs": set(), "totals": 0, "updated_at": None})
        kind = _kind(code)
        if kind == "criterion":
            a["labels"] += 1
            a["dialogs"].add(cid)
        elif kind == "total":
            a["totals"] += 1
        ts = _aware(updated)
        if ts is not None and (a["updated_at"] is None or ts > a["updated_at"]):
            a["updated_at"] = ts
    return [{"labeler": lab, "labels": a["labels"], "dialogs": len(a["dialogs"]), "totals": a["totals"],
             "updated_at": _iso(a["updated_at"])} for lab, a in sorted(agg.items())]


def _labeler_names(db: Session) -> list[str]:
    return sorted(db.scalars(select(GoldLabel.labeler).distinct()))


def default_labeler(db: Session) -> str | None:
    """The reference labeler: "team" (the reconciled set) when it has labels, else the one with the most labels."""
    counts: Counter[str] = Counter()
    for lab, code in db.execute(select(GoldLabel.labeler, GoldLabel.criterion_code)):
        if _kind(code) == "criterion":
            counts[lab] += 1
    if not counts:
        return None
    if counts.get(DEFAULT_LABELER):
        return DEFAULT_LABELER
    return min(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0]


# --------------------------------------------------------------------------------------------- labels: write


@dataclass
class _Item:
    """One label to write, parsed from a PUT item or from a gold.json entry."""

    field: str  # error path of the item, e.g. items[3] or doc.labels.p1-ru-001.C4
    value_field: str
    code_field: str
    conversation_id: str
    code: str
    labeler: str
    has_value: bool = False
    raw_value: Any = None
    value: Any = None
    has_note: bool = False
    note: str | None = None
    has_split: bool = False
    split: str | None = None
    remove: bool = False

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.conversation_id, self.code, self.labeler)

    @property
    def dialog(self) -> tuple[str, str]:
        return (self.conversation_id, self.labeler)


def _clean_labeler(raw: Any, default: str = DEFAULT_LABELER) -> str:
    if raw is None:
        return default
    if not isinstance(raw, str):
        raise ValueError("labeler должен быть строкой, например team или имя проверяющего")
    s = raw.strip()
    if not s:
        return default
    if len(s) > MAX_LABELER_LEN:
        raise ValueError(f"labeler длиннее {MAX_LABELER_LEN} символов")
    return s


def _clean_split(raw: Any) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ValueError("split должен быть строкой (train, validation) или null")
    s = raw.strip().lower()
    if not s:
        return None
    if len(s) > MAX_SPLIT_LEN:
        raise ValueError(f"имя выборки длиннее {MAX_SPLIT_LEN} символов")
    return s


def _clean_note(raw: Any) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ValueError("заметка должна быть строкой")
    s = raw.strip()
    if len(s) > MAX_NOTE_LEN:
        raise ValueError(f"заметка длиннее {MAX_NOTE_LEN} символов")
    return s or None


def _clean_code(raw: Any, crit_map: dict[str, Criterion] | None) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("criterion_code обязателен (C1…C8, total или _note)")
    s = raw.strip()
    low = s.lower()
    if low == TOTAL_CODE:
        return TOTAL_CODE
    if low == NOTE_CODE:
        return NOTE_CODE
    if crit_map:
        if s in crit_map:
            return s
        if s.upper() in crit_map:
            return s.upper()
    if len(s) > MAX_CODE_LEN:
        raise ValueError(f"код критерия длиннее {MAX_CODE_LEN} символов")
    return s


def _conversation_channels(db: Session, cids: Iterable[str]) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    ids = sorted({c for c in cids if isinstance(c, str)})
    for chunk in _chunked(ids):
        for cid, channel in db.execute(select(Conversation.id, Conversation.channel).where(Conversation.id.in_(list(chunk)))):
            out[cid] = channel
    return out


def _parse_put_items(raw_items: Any, default_labeler: str, crit_map: dict[str, Criterion] | None) -> tuple[list[_Item], list[dict[str, str]]]:
    if not isinstance(raw_items, list):
        return [], [_err("items", "Ожидается список меток items: [{conversation_id, criterion_code, value}]")]
    if not raw_items:
        return [], [_err("items", "Список items пуст")]
    if len(raw_items) > MAX_ITEMS:
        return [], [_err("items", f"Слишком много меток в одном запросе (больше {MAX_ITEMS})")]
    items: list[_Item] = []
    errors: list[dict[str, str]] = []
    for i, raw in enumerate(raw_items):
        f = f"items[{i}]"
        if not isinstance(raw, dict):
            errors.append(_err(f, "Метка должна быть объектом {conversation_id, criterion_code, value}"))
            continue
        cid = raw.get("conversation_id")
        if not isinstance(cid, str) or not cid.strip():
            errors.append(_err(f"{f}.conversation_id", "Поле conversation_id обязательно"))
            continue
        try:
            code = _clean_code(raw.get("criterion_code"), crit_map)
        except ValueError as e:
            errors.append(_err(f"{f}.criterion_code", str(e)))
            continue
        try:
            lab = _clean_labeler(raw.get("labeler"), default_labeler)
        except ValueError as e:
            errors.append(_err(f"{f}.labeler", str(e)))
            continue
        it = _Item(field=f, value_field=f"{f}.value", code_field=f"{f}.criterion_code", conversation_id=cid.strip(), code=code,
                   labeler=lab, remove=raw.get("remove") is True)
        if "value" in raw:
            it.has_value, it.raw_value = True, raw["value"]
        if "note" in raw:
            try:
                it.note, it.has_note = _clean_note(raw["note"]), True
            except ValueError as e:
                errors.append(_err(f"{f}.note", str(e)))
                continue
        if "split" in raw:
            try:
                it.split, it.has_split = _clean_split(raw["split"]), True
            except ValueError as e:
                errors.append(_err(f"{f}.split", str(e)))
                continue
        items.append(it)
    return items, errors


def _validate_items(db: Session, items: list[_Item], version: ChecklistVersion | None) -> list[dict[str, str]]:
    """Check conversations, codes, values, duplicates and split consistency; fills ``item.value``."""
    errors: list[dict[str, str]] = []
    crit_map = {c.code: c for c in version.criteria} if version is not None else None
    total_scale = float((version.scoring_rules or {}).get("total_scale", 100)) if version is not None else 100.0
    channels = _conversation_channels(db, (it.conversation_id for it in items))
    seen: dict[tuple[str, str, str], str] = {}
    dialog_split: dict[tuple[str, str], tuple[str | None, str]] = {}
    for it in items:
        if it.conversation_id not in channels:
            errors.append(_err(f"{it.field}.conversation_id" if it.field.startswith("items[") else it.field,
                               f"Диалог «{it.conversation_id}» не найден"))
            continue
        channel = channels[it.conversation_id]
        if it.key in seen:
            errors.append(_err(it.field, f"Метка {it.conversation_id}/{it.code} ({it.labeler}) уже указана в {seen[it.key]}"))
            continue
        seen[it.key] = it.field
        if it.has_split:
            prev = dialog_split.get(it.dialog)
            if prev is not None and prev[0] != it.split:
                errors.append(_err(it.field, f"Для диалога {it.conversation_id} указаны разные выборки: {prev[0]} ({prev[1]}) и {it.split}"))
                continue
            dialog_split[it.dialog] = (it.split, it.field)
        if it.remove:
            continue
        if it.code == TOTAL_CODE:
            msg = f"Итоговый балл должен быть числом от 0 до {_num(total_scale)}"
            if not it.has_value:
                errors.append(_err(it.value_field, msg))
                continue
            try:
                v = canonical_value(it.raw_value)
            except ValueError:
                errors.append(_err(it.value_field, msg))
                continue
            f = _as_float(v)
            if f is None or not (0 <= f <= total_scale):
                errors.append(_err(it.value_field, msg))
                continue
            it.value = _num(f)
        elif it.code == NOTE_CODE:
            text = it.note if it.has_note else None
            if text is None and isinstance(it.raw_value, str):
                text = it.raw_value.strip() or None
            if not text:
                errors.append(_err(it.value_field, "Для заметки к диалогу (_note) нужен текст в поле note"))
                continue
            if len(text) > MAX_NOTE_LEN:
                errors.append(_err(it.value_field, f"заметка длиннее {MAX_NOTE_LEN} символов"))
                continue
            it.note, it.has_note, it.value = text, True, None
        else:
            crit = crit_map.get(it.code) if crit_map is not None else None
            if crit_map is not None and crit is None:
                known = ", ".join(c.code for c in _criteria(version))
                errors.append(_err(it.code_field, f"Критерия «{it.code}» нет в версии чек-листа {version.id} (есть: {known})"))
                continue
            if not it.has_value:
                errors.append(_err(it.value_field, "Поле value обязательно: Да/Нет, шаг шкалы или N/A"))
                continue
            try:
                v = canonical_value(it.raw_value)
            except ValueError as e:
                errors.append(_err(it.value_field, f"{it.code}: {e}"))
                continue
            if crit is not None:
                v, msg = check_value(v, crit, channel)
                if msg:
                    errors.append(_err(it.value_field, msg))
                    continue
            elif not (v in ("yes", "no", NA) or isinstance(v, (int, float))):
                errors.append(_err(it.value_field, f"Значение «{v}» недопустимо: ожидается Да/Нет, число или N/A"))
                continue
            it.value = v
    return errors


def _rows_for_dialogs(db: Session, dialogs: set[tuple[str, str]]) -> list[GoldLabel]:
    labs = sorted({lab for _, lab in dialogs})
    cids = sorted({cid for cid, _ in dialogs})
    rows: list[GoldLabel] = []
    for chunk in _chunked(cids):
        stmt = select(GoldLabel).where(GoldLabel.labeler.in_(labs), GoldLabel.conversation_id.in_(list(chunk))).order_by(GoldLabel.id)
        rows.extend(r for r in db.scalars(stmt) if (r.conversation_id, r.labeler) in dialogs)
    return rows


def _apply_items(db: Session, items: list[_Item]) -> tuple[dict[str, int], list[GoldLabel]]:
    """Create, update or remove label rows (no commit). Returns counts and the rows of the non-removed items."""
    counts = {"created": 0, "updated": 0, "unchanged": 0, "removed": 0, "split_propagated": 0}
    dialogs = {it.dialog for it in items}
    existing = _rows_for_dialogs(db, dialogs)
    by_key = {(r.conversation_id, r.criterion_code, r.labeler): r for r in existing}
    by_dialog: dict[tuple[str, str], list[GoldLabel]] = defaultdict(list)
    for r in existing:
        by_dialog[(r.conversation_id, r.labeler)].append(r)
    inherited = {d: next((r.split for r in rows if r.split), None) for d, rows in by_dialog.items()}
    explicit = {it.dialog: it.split for it in items if it.has_split}
    touched: list[GoldLabel] = []
    touched_ids: set[int] = set()
    for it in items:
        row = by_key.get(it.key)
        if it.remove:
            if row is None:
                counts["unchanged"] += 1
            else:
                db.delete(row)
                del by_key[it.key]
                by_dialog[it.dialog].remove(row)
                counts["removed"] += 1
            continue
        split = explicit[it.dialog] if it.dialog in explicit else inherited.get(it.dialog)
        if row is None:
            row = GoldLabel(conversation_id=it.conversation_id, criterion_code=it.code, labeler=it.labeler, value=it.value,
                            note=it.note if it.has_note else None, split=split)
            db.add(row)
            by_key[it.key] = row
            by_dialog[it.dialog].append(row)
            counts["created"] += 1
        else:
            changed = False
            if it.code != NOTE_CODE and not _same(row.value, it.value):
                row.value, changed = it.value, True
            if it.has_note and row.note != it.note:
                row.note, changed = it.note, True
            if it.dialog in explicit and row.split != split:
                row.split, changed = split, True
            counts["updated" if changed else "unchanged"] += 1
        touched.append(row)
        touched_ids.add(id(row))
    for dialog, split in explicit.items():
        for r in by_dialog.get(dialog, []):
            if id(r) not in touched_ids and r.split != split:
                r.split = split
                counts["split_propagated"] += 1
    db.flush()
    return counts, touched


def _finish(db: Session, dry_run: bool) -> None:
    if dry_run:
        db.rollback()
    else:
        db.commit()


def upsert_labels(db: Session, items: Any, *, labeler: str | None = None, checklist_version_id: int | None = None,
                  dry_run: bool = False) -> dict[str, Any]:
    """Bulk create/update/remove labels (atomic: any invalid item rejects the whole batch with ValidationFailed).

    Item: {conversation_id, criterion_code, value, labeler?, note?, split?, remove?}. ``value`` is required except
    for ``remove: true`` and ``_note`` rows. ``note`` and ``split`` change only when present in the item.
    """
    version = resolve_version(db, checklist_version_id)
    try:
        default_lab = _clean_labeler(labeler)
    except ValueError as e:
        raise ValidationFailed([_err("labeler", str(e))]) from None
    crit_map = {c.code: c for c in version.criteria} if version is not None else None
    parsed, errors = _parse_put_items(items, default_lab, crit_map)
    if parsed:
        errors += _validate_items(db, parsed, version)
    if errors:
        raise ValidationFailed(errors, f"Метки не сохранены: ошибок — {len(errors)}, подробности в errors.")
    counts, rows = _apply_items(db, parsed)
    result = {"labelers": sorted({it.labeler for it in parsed}), "checklist_version_id": version.id if version else None,
              **counts, "dry_run": dry_run, "items": [label_dict(r) for r in rows]}
    _finish(db, dry_run)
    return result


# --------------------------------------------------------------------------------------------- gold.json import / export


def _parse_split(split_doc: Any) -> tuple[dict[str, str], list[str], list[dict[str, str]]]:
    """{"train": [ids], "validation": [ids]} → ({cid: split}, warnings, errors). A dialog listed in several
    splits goes to validation when listed there (never train on a validation dialog)."""
    if split_doc is None:
        return {}, [], []
    if not isinstance(split_doc, dict):
        return {}, [], [_err("doc.split", "Поле split должно быть объектом {train: [...], validation: [...]}")]
    membership: dict[str, list[str]] = defaultdict(list)
    errors: list[dict[str, str]] = []
    for name, ids in split_doc.items():
        f = f"doc.split.{name}"
        try:
            clean = _clean_split(name)
        except ValueError as e:
            errors.append(_err(f, str(e)))
            continue
        if clean is None:
            errors.append(_err(f, "Пустое имя выборки"))
            continue
        if not isinstance(ids, list):
            errors.append(_err(f, "Ожидается список id диалогов"))
            continue
        for j, cid in enumerate(ids):
            if not isinstance(cid, str) or not cid.strip():
                errors.append(_err(f"{f}[{j}]", "Ожидается id диалога (строка)"))
                continue
            if clean not in membership[cid.strip()]:
                membership[cid.strip()].append(clean)
    out: dict[str, str] = {}
    warnings: list[str] = []
    for cid, names in membership.items():
        chosen = "validation" if "validation" in names else names[0]
        if len(names) > 1:
            warnings.append(f"Диалог {cid} указан в нескольких выборках ({', '.join(names)}): использована {chosen}")
        out[cid] = chosen
    return out, warnings, errors


def import_gold(db: Session, doc: Any, labeler: str | None = DEFAULT_LABELER, *, replace: bool = False, dry_run: bool = False,
                checklist_version_id: int | None = None) -> dict[str, Any]:
    """Import the teammate's gold file ({split: {train, validation}, labels: {cid: {C1: "yes", C4: 15, total, _note}}}).

    * merges by default (labels not in the file stay); ``replace=True`` makes the file the labeler's whole set;
    * dialogs unknown to this database are skipped and listed, not rejected;
    * ``_note`` is kept as the dialog note, ``_notes`` ({code: text}) as per-label notes, other ``_*`` keys ignored;
    * atomic: any invalid value rejects the file (ValidationFailed with paths like doc.labels.<cid>.C4);
    * ``dry_run=True`` reports what would change and writes nothing.
    """
    version = resolve_version(db, checklist_version_id)
    crit_map = {c.code: c for c in version.criteria} if version is not None else None
    try:
        lab = _clean_labeler(labeler)
    except ValueError as e:
        raise ValidationFailed([_err("labeler", str(e))]) from None
    if isinstance(doc, str):
        try:
            doc = json.loads(doc)
        except json.JSONDecodeError as e:
            raise ValidationFailed([_err("doc", f"Некорректный JSON: строка {e.lineno}, позиция {e.colno}")]) from None
    if not isinstance(doc, dict):
        raise ValidationFailed([_err("doc", "Ожидается JSON-объект в формате eval/gold.json: {split, labels}")])
    labels = doc.get("labels")
    if not isinstance(labels, dict):
        raise ValidationFailed([_err("doc.labels", "Поле labels должно быть объектом {conversation_id: {C1: …, C4: …}}")])
    split_map, warnings, errors = _parse_split(doc.get("split"))
    channels = _conversation_channels(db, labels.keys())
    skipped = sorted(cid for cid in labels if cid not in channels)
    items: list[_Item] = []
    ignored: set[str] = set()
    counted = {"dialogs": 0, "labels": 0, "totals": 0, "notes": 0}
    per_split: Counter[str] = Counter()
    for cid, entry in labels.items():
        base = f"doc.labels.{cid}"
        if not isinstance(entry, dict):
            errors.append(_err(base, "Ожидается объект {C1: …, C4: …}"))
            continue
        if cid not in channels:
            continue
        notes = entry.get(NOTES_KEY) or {}
        if not isinstance(notes, dict):
            errors.append(_err(f"{base}.{NOTES_KEY}", "Ожидается объект {C4: \"комментарий\"}"))
            notes = {}
        n_criteria = 0
        for key, raw in entry.items():
            f = f"{base}.{key}"
            if key == NOTES_KEY:
                continue
            if key == NOTE_CODE:
                if raw is None or (isinstance(raw, str) and not raw.strip()):
                    continue
                if not isinstance(raw, str):
                    errors.append(_err(f, "Заметка к диалогу (_note) должна быть строкой"))
                    continue
                it = _Item(field=f, value_field=f, code_field=f, conversation_id=cid, code=NOTE_CODE, labeler=lab,
                           has_note=True, note=raw.strip())
                counted["notes"] += 1
            elif key.startswith("_"):
                ignored.add(key)
                continue
            elif key.strip().lower() == TOTAL_CODE:
                if raw is None:
                    continue
                it = _Item(field=f, value_field=f, code_field=f, conversation_id=cid, code=TOTAL_CODE, labeler=lab,
                           has_value=True, raw_value=raw)
                counted["totals"] += 1
            else:
                try:
                    code = _clean_code(key, crit_map)
                except ValueError as e:
                    errors.append(_err(f, str(e)))
                    continue
                it = _Item(field=f, value_field=f, code_field=f, conversation_id=cid, code=code, labeler=lab,
                           has_value=True, raw_value=raw)
                note_raw = notes.get(key, notes.get(code))
                if note_raw is not None:
                    try:
                        it.note, it.has_note = _clean_note(note_raw), True
                    except ValueError as e:
                        errors.append(_err(f"{base}.{NOTES_KEY}.{key}", str(e)))
                        continue
                n_criteria += 1
            it.has_split, it.split = True, split_map.get(cid)
            items.append(it)
        if n_criteria:
            counted["dialogs"] += 1
            counted["labels"] += n_criteria
            per_split[split_map.get(cid) or "none"] += 1
    if items:
        errors += _validate_items(db, items, version)
    elif replace and not errors:
        errors.append(_err("doc.labels", "Ни одна метка не относится к диалогам этой базы: замена (replace) отменена, "
                                         "чтобы не удалить всю разметку разметчика"))
    if errors:
        raise ValidationFailed(errors, f"Файл не импортирован: ошибок — {len(errors)}, подробности в errors.")
    counts, _rows = _apply_items(db, items) if items else ({"created": 0, "updated": 0, "unchanged": 0, "removed": 0,
                                                             "split_propagated": 0}, [])
    if replace:
        keep = {it.key for it in items}
        for r in list(db.scalars(select(GoldLabel).where(GoldLabel.labeler == lab))):
            if (r.conversation_id, r.criterion_code, r.labeler) not in keep:
                db.delete(r)
                counts["removed"] += 1
        db.flush()
    result = {"labeler": lab, "checklist_version_id": version.id if version else None, "dry_run": dry_run, "replace": replace,
              **counted, **counts, "split": dict(sorted(per_split.items())), "skipped_conversations": skipped,
              "split_unlabeled": sorted(cid for cid in split_map if cid not in labels), "ignored_keys": sorted(ignored),
              "warnings": warnings}
    _finish(db, dry_run)
    return result


def _dialog_split(rows: Iterable[GoldLabel]) -> str | None:
    splits = sorted({r.split for r in rows if r.split})
    if not splits:
        return None
    return "validation" if "validation" in splits else splits[0]


def export_gold(db: Session, labeler: str | None = DEFAULT_LABELER, *, include_notes: bool = False) -> dict[str, Any]:
    """One labeler's labels in the teammate's gold.json format (readable by ``python -m cqa.eval.harness``).

    ``include_notes=True`` adds ``_note`` (dialog) and ``_notes`` (per label); leave it off for the harness, which
    would treat those keys as criteria. The top-level ``_meta`` block is ignored by the harness and by import.
    """
    lab = (labeler or DEFAULT_LABELER).strip() or DEFAULT_LABELER
    rows = list(db.scalars(select(GoldLabel).where(GoldLabel.labeler == lab)))
    order = _order_map(organizer_version(db))
    by_cid: dict[str, list[GoldLabel]] = defaultdict(list)
    for r in rows:
        by_cid[r.conversation_id].append(r)
    labels_out: dict[str, dict[str, Any]] = {}
    members: dict[str, list[str]] = defaultdict(list)
    n_labels = 0
    for cid in sorted(by_cid):
        entry: dict[str, Any] = {}
        notes: dict[str, str] = {}
        dialog_note: str | None = None
        for r in sorted(by_cid[cid], key=lambda r: _code_sort_key(r.criterion_code, order)):
            kind = _kind(r.criterion_code)
            if kind == "criterion":
                entry[r.criterion_code] = _display(r.value)
                n_labels += 1
                if r.note:
                    notes[r.criterion_code] = r.note
            elif kind == "total":
                if r.value is not None:
                    entry[TOTAL_CODE] = _display(r.value)
            elif r.criterion_code == NOTE_CODE:
                dialog_note = r.note
        if include_notes:
            if dialog_note:
                entry[NOTE_CODE] = dialog_note
            if notes:
                entry[NOTES_KEY] = notes
        if not entry:
            continue
        labels_out[cid] = entry
        split = _dialog_split(by_cid[cid])
        if split:
            members[split].append(cid)
    split_out: dict[str, list[str]] = {name: [] for name in SPLIT_ORDER}
    for name in sorted(members):
        split_out[name] = sorted(members[name])
    meta = {"format": "cqa gold v1 (python -m cqa.eval.harness --gold <file>)", "labeler": lab,
            "exported_at": datetime.now(timezone.utc).isoformat(), "dialogs": len(labels_out), "labels": n_labels,
            "notes_included": include_notes}
    return {META_KEY: meta, "split": split_out, "labels": labels_out}


# --------------------------------------------------------------------------------------------- shared metrics


@dataclass
class _Pair:
    conversation_id: str
    code: str
    h: str  # human value, harness-normalised (_norm)
    a: str  # AI value, harness-normalised
    human: Any  # canonical values for display
    ai: Any
    row: EvaluationCriterion
    evaluation: Evaluation


def _pair_stats(pairs: list[tuple[str, str]], crit: Criterion | None) -> dict[str, Any]:
    """n, exact, na_agree, kappa (binary, N/A as a class), qwk and mae (scale): cqa.eval.harness.score formulas."""
    n = len(pairs)
    out: dict[str, Any] = {"n": n, "exact": _rate(sum(1 for a, b in pairs if a == b), n),
                           "na_agree": _rate(sum(1 for a, b in pairs if (a == NA) == (b == NA)), n),
                           "kappa": None, "qwk": None, "mae": None}
    if crit is not None and crit.answer_type == "scale":
        nums: list[tuple[float, float]] = []
        for a, b in pairs:
            fa, fb = (_as_float(a), _as_float(b)) if a != NA and b != NA else (None, None)
            if fa is not None and fb is not None:
                nums.append((fa, fb))
        if nums:
            out["mae"] = round(sum(abs(x - y) for x, y in nums) / len(nums), 2)
            q = quadratic_weighted_kappa(nums, _scale_levels(crit))
            out["qwk"] = round(q, 3) if q is not None else None
    else:
        k = cohen_kappa(pairs)
        out["kappa"] = round(k, 3) if k is not None else None
    return out


def _confusion_labels(crit: Criterion, observed: set[str]) -> list[str]:
    values = _option_values(crit)
    if crit.answer_type == "scale":
        values = sorted(values, key=lambda v: -(_as_float(v) if _as_float(v) is not None else -1e18))
    base: list[str] = []
    for v in values or (["yes", "no"] if crit.answer_type == "binary" else []):
        nv = _norm(v)
        if nv != NA and nv not in base:
            base.append(nv)
    extras = sorted(observed - set(base) - {NA}, key=lambda s: (_as_float(s) is None, -(_as_float(s) or 0.0), s))
    labels = base + extras
    if NA in observed or na_allowed(crit, None):
        labels.append(NA)
    return labels


def _confusion(pairs: list[tuple[str, str]], crit: Criterion) -> dict[str, Any]:
    labels = _confusion_labels(crit, {a for a, _ in pairs} | {b for _, b in pairs})
    idx = {label: i for i, label in enumerate(labels)}
    matrix = [[0] * len(labels) for _ in labels]
    for a, b in pairs:
        matrix[idx[a]][idx[b]] += 1
    return {"labels": labels, "rows": "human", "columns": "ai", "matrix": matrix}


def _is_error_row(row: EvaluationCriterion) -> bool:
    return ERROR_FLAG in (row.flags or []) or bool(row.applicable and row.answer is None)


def _ai_value(row: EvaluationCriterion, source: str) -> tuple[Any, bool]:
    """(canonical AI value, usable). source="final" applies reviewer decisions (final_answer)."""
    if source == "final" and row.final_answer is not None:
        return _display(row.final_answer), True
    if _is_error_row(row):
        return None, False
    if not row.applicable:
        return NA, True
    return _display(row.answer), True


def _latest_evaluations(db: Session, version_id: int, cids: Sequence[str], provider: str | None) -> dict[str, Evaluation]:
    """Latest completed evaluation (greatest id) per conversation on the version, criteria and conversation loaded."""
    latest_ids: list[int] = []
    for chunk in _chunked(sorted(set(cids))):
        stmt = (select(func.max(Evaluation.id))
                .where(Evaluation.status == "completed", Evaluation.checklist_version_id == version_id,
                       Evaluation.conversation_id.in_(list(chunk)))
                .group_by(Evaluation.conversation_id))
        if provider:
            stmt = stmt.where(Evaluation.provider == provider)
        latest_ids.extend(i for i in db.scalars(stmt) if i is not None)
    out: dict[str, Evaluation] = {}
    for chunk in _chunked(sorted(latest_ids)):
        stmt = (select(Evaluation).where(Evaluation.id.in_(list(chunk)))
                .options(selectinload(Evaluation.criteria), selectinload(Evaluation.conversation)))
        for ev in db.scalars(stmt):
            out[ev.conversation_id] = ev
    return out


def _human_total(version: ChecklistVersion, labels: dict[str, Any]) -> float | None:
    """Human total from the labels with the scoring engine; None unless every weighted criterion is labeled
    (and the critical one when the version caps totals on a critical error)."""
    rules = version.scoring_rules or {}
    cap = (rules.get("critical") or {}).get("mode") == "flag_and_cap"
    required = [c for c in version.criteria if (c.weight or 0) > 0 or (cap and c.is_critical)]
    if not required or any(c.code not in labels for c in required):
        return None
    items: list[CriterionInput] = []
    for c in version.criteria:
        if c.code not in labels:
            continue
        v = labels[c.code]
        if v == NA:
            items.append(CriterionInput(c.code, c.weight, c.max_points, None, applicable=False, section=c.section, is_critical=c.is_critical))
            continue
        pts = answer_points(c.options or [], v)
        if pts is None:
            return None
        hit = bool(c.is_critical and str(v).lower() == str(c.critical_value or "yes").lower())
        items.append(CriterionInput(c.code, c.weight, c.max_points, pts, True, c.section, c.is_critical, hit))
    return compute_score(rules, items, version.sections or []).total


def _empty_overall() -> dict[str, Any]:
    return {"n_dialogs": 0, "n_pairs": 0, "exact_mean": None, "exact_micro": None, "kappa_mean": None, "qwk_mean": None,
            "total_mae": None, "total_bias": None, "n_totals": 0}


def _group_rows(pairs: list[_Pair], crits: list[Criterion], key: str, totals: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[_Pair]] = defaultdict(list)
    for p in pairs:
        conv = p.evaluation.conversation
        groups[(getattr(conv, key, None) if conv is not None else None) or "—"].append(p)
    out: list[dict[str, Any]] = []
    for name in sorted(groups):
        ps = groups[name]
        per: list[dict[str, Any]] = []
        for c in crits:
            cps = [p for p in ps if p.code == c.code]
            if cps:
                per.append({"code": c.code, "n": len(cps), "exact": _rate(sum(1 for p in cps if p.h == p.a), len(cps))})
        cids = {p.conversation_id for p in ps}
        deltas = [abs(t["delta"]) for cid, t in totals.items() if cid in cids]
        out.append({key: name, "n_dialogs": len(cids), "n_pairs": len(ps),
                    "exact": _rate(sum(1 for p in ps if p.h == p.a), len(ps)),
                    "exact_mean": _r(_mean([r["exact"] for r in per]), 3),
                    "total_mae": _r(_mean(deltas), 2), "criteria": per})
    return out


def _human_agreement(db: Session, crits: list[Criterion], split_f: str | None) -> dict[str, Any] | None:
    """Human-human ceiling: pooled agreement over every pair of labelers other than the reconciled "team"."""
    crit_map = {c.code: c for c in crits}
    stmt = select(GoldLabel).where(GoldLabel.labeler != RECONCILED_LABELER)
    if split_f:
        stmt = stmt.where(GoldLabel.split == split_f)
    by_lab: dict[str, dict[tuple[str, str], Any]] = defaultdict(dict)
    for r in db.scalars(stmt):
        if r.criterion_code in crit_map:
            by_lab[r.labeler][(r.conversation_id, r.criterion_code)] = r.value
    labs = sorted(by_lab)
    if len(labs) < 2:
        return None
    pooled: dict[str, list[tuple[str, str]]] = defaultdict(list)
    pairs_out: list[dict[str, Any]] = []
    for a, b in combinations(labs, 2):
        shared = sorted(set(by_lab[a]) & set(by_lab[b]))
        if not shared:
            continue
        match = 0
        for key in shared:
            va, vb = _norm(by_lab[a][key]), _norm(by_lab[b][key])
            pooled[key[1]].append((va, vb))
            match += va == vb
        pairs_out.append({"labelers": [a, b], "n": len(shared), "exact": _rate(match, len(shared))})
    rows = [{"code": c.code, "title": c.title, **_pair_stats(pooled[c.code], c)} for c in crits if pooled.get(c.code)]
    return {"labelers": labs, "excluded": [RECONCILED_LABELER], "pairs": pairs_out, "criteria": rows,
            "exact_mean": _r(_mean([r["exact"] for r in rows]), 3)}


# --------------------------------------------------------------------------------------------- agreement


def agreement(db: Session, labeler: str | None = None, checklist_version_id: int | None = None, split: str | None = None, *,
              source: str = "ai", provider: str | None = None) -> dict[str, Any]:
    """AI vs human gold labels, per criterion (the organizers compare per criterion).

    Compares the latest completed evaluation per conversation on the version (default: organizer version 1)
    with the labeler's labels (default: "team", else the labeler with the most labels). ``split`` restricts to
    train/validation dialogs; ``source="final"`` uses reviewer-corrected answers and final totals; ``provider``
    restricts the evaluations (e.g. "ensemble").
    """
    if source not in SOURCES:
        raise ValidationFailed([_err("source", "Допустимые значения: ai (ответы модели) или final (после решений по апелляциям)")])
    version = resolve_version(db, checklist_version_id)
    crits = _criteria(version)
    crit_map = {c.code: c for c in crits}
    names = _labeler_names(db)
    ref = labeler.strip() if labeler and labeler.strip() else default_labeler(db)
    split_f = _split_filter(split)
    result: dict[str, Any] = {
        "checklist_version_id": version.id if version else None, "labeler": ref, "labelers": names, "split": split_f,
        "source": source, "provider": provider or None,
        "criteria": [], "overall": _empty_overall(), "totals": [], "by_language": [], "by_channel": [], "disagreements": [],
        "coverage": {"labeled_dialogs": 0, "missing_evaluation": [], "skipped": [], "unknown_codes": []},
        "human_agreement": None, "warnings": [],
    }
    if version is None:
        result["warnings"].append("В базе нет ни одной версии чек-листа")
        return result
    result["human_agreement"] = _human_agreement(db, crits, split_f)

    stmt = select(GoldLabel).where(GoldLabel.labeler == ref) if ref else None
    gold: dict[str, dict[str, Any]] = defaultdict(dict)
    gold_total: dict[str, float] = {}
    human_note: dict[tuple[str, str], str] = {}
    gold_split: dict[str, str] = {}
    unknown: set[str] = set()
    if stmt is not None:
        if split_f:
            stmt = stmt.where(GoldLabel.split == split_f)
        for r in db.scalars(stmt):
            kind = _kind(r.criterion_code)
            if kind == "criterion":
                if r.criterion_code not in crit_map:
                    unknown.add(r.criterion_code)
                    continue
                gold[r.conversation_id][r.criterion_code] = r.value
                if r.note:
                    human_note[(r.conversation_id, r.criterion_code)] = r.note
            elif kind == "total":
                f = _as_float(r.value)
                if f is not None:
                    gold_total[r.conversation_id] = f
            if r.split:
                gold_split[r.conversation_id] = r.split
    labeled = sorted(set(gold) | set(gold_total))
    evals = _latest_evaluations(db, version.id, labeled, provider) if labeled else {}

    pairs_by_code: dict[str, list[_Pair]] = defaultdict(list)
    skipped: list[dict[str, Any]] = []
    for cid in labeled:
        ev = evals.get(cid)
        if ev is None:
            continue
        rows = {r.code: r for r in ev.criteria}
        for code in sorted(gold.get(cid, {}), key=lambda c: (crit_map[c].order, c)):
            hv = gold[cid][code]
            row = rows.get(code)
            if row is None:
                skipped.append({"conversation_id": cid, "code": code, "reason": "no_ai_verdict"})
                continue
            av, ok = _ai_value(row, source)
            if not ok:
                skipped.append({"conversation_id": cid, "code": code, "reason": "ai_error"})
                continue
            pairs_by_code[code].append(_Pair(cid, code, _norm(hv), _norm(av), _display(hv), av, row, ev))

    criteria_out: list[dict[str, Any]] = []
    for c in crits:
        ps = pairs_by_code.get(c.code, [])
        np_ = [(p.h, p.a) for p in ps]
        criteria_out.append({"code": c.code, "title": c.title, "answer_type": c.answer_type, "weight": c.weight,
                             **_pair_stats(np_, c), "na_human": sum(1 for p in ps if p.h == NA),
                             "na_ai": sum(1 for p in ps if p.a == NA), "confusion": _confusion(np_, c)})

    # totals: AI total vs the human total computed from the labels (or the file's total when labels are incomplete)
    totals: dict[str, dict[str, Any]] = {}
    for cid in labeled:
        ev = evals.get(cid)
        if ev is None or any(_is_error_row(r) for r in ev.criteria):
            continue
        ai_total = ev.final_total_score if source == "final" else ev.total_score
        if ai_total is None:
            continue
        computed = _human_total(version, gold.get(cid, {}))
        explicit = gold_total.get(cid)
        human = computed if computed is not None else explicit
        if human is None:
            continue
        if computed is not None and explicit is not None and abs(computed - explicit) > 0.01:
            result["warnings"].append(f"{cid}: итог в разметке {_num(explicit)} не совпадает с пересчётом по меткам {_num(computed)}")
        totals[cid] = {"conversation_id": cid, "ai": round(float(ai_total), 2), "human": round(float(human), 2),
                       "delta": round(float(ai_total) - float(human), 2), "source": "computed" if computed is not None else "label",
                       "evaluation_id": ev.id}

    all_pairs = [p for c in crits for p in pairs_by_code.get(c.code, [])]
    with_pairs = [r for r in criteria_out if r["n"]]
    abs_deltas = [abs(float(ev_tot["ai"]) - float(ev_tot["human"])) for ev_tot in totals.values()]
    result["criteria"] = criteria_out
    result["overall"] = {
        "n_dialogs": len({p.conversation_id for p in all_pairs}),
        "n_pairs": len(all_pairs),
        "exact_mean": _r(_mean([r["exact"] for r in with_pairs]), 3),
        "exact_micro": _rate(sum(1 for p in all_pairs if p.h == p.a), len(all_pairs)),
        "kappa_mean": _r(_mean([r["kappa"] for r in with_pairs if r["kappa"] is not None]), 3),
        "qwk_mean": _r(_mean([r["qwk"] for r in with_pairs if r["qwk"] is not None]), 3),
        "total_mae": _r(_mean(abs_deltas), 2),
        "total_bias": _r(_mean([float(t["ai"]) - float(t["human"]) for t in totals.values()]), 2),
        "n_totals": len(totals),
    }
    result["totals"] = sorted(totals.values(), key=lambda t: (-abs(t["delta"]), t["conversation_id"]))
    result["by_language"] = _group_rows(all_pairs, crits, "language", totals)
    result["by_channel"] = _group_rows(all_pairs, crits, "channel", totals)
    disagreements: list[dict[str, Any]] = []
    for c in crits:
        for p in sorted(pairs_by_code.get(c.code, []), key=lambda p: p.conversation_id):
            if p.h == p.a:
                continue
            row, conv = p.row, p.evaluation.conversation
            disagreements.append({
                "conversation_id": p.conversation_id, "code": c.code, "title": c.title, "ai": p.ai, "human": p.human,
                "explanation": row.explanation, "na_reason": row.na_reason if not row.applicable else None,
                "confidence": row.confidence, "evaluation_id": p.evaluation.id, "evaluation_criterion_id": row.id,
                "review_state": row.review_state, "final_answer": row.final_answer,
                "note": human_note.get((p.conversation_id, c.code)), "split": gold_split.get(p.conversation_id),
                "language": conv.language if conv is not None else None, "channel": conv.channel if conv is not None else None,
                "operator_name": conv.operator_name if conv is not None else None,
            })
    result["disagreements"] = disagreements
    result["coverage"] = {"labeled_dialogs": len(labeled), "missing_evaluation": [cid for cid in labeled if cid not in evals],
                          "skipped": skipped, "unknown_codes": sorted(unknown, key=_natural)}
    return result


# --------------------------------------------------------------------------------------------- stability


def stability(db: Session, checklist_version_id: int | None = None, *, provider: str | None = None) -> dict[str, Any]:
    """Run-to-run stability over conversations with at least two completed evaluations on the version.

    Per criterion: agreement between the two latest independent judgments (rows copied by a partial
    re-evaluation and failed rows are not judgments and are skipped) and the majority share over all runs.
    Totals: mean |Δtotal| between the two latest full runs without failed criteria. ``provider`` restricts the
    runs (e.g. compare Opus with Opus only).
    """
    version = resolve_version(db, checklist_version_id)
    crits = _criteria(version)
    empty_overall = {"agreement_mean": None, "majority_share_mean": None, "n_total_pairs": 0, "mean_abs_delta_total": None,
                     "max_abs_delta_total": None, "max_delta_conversation_id": None, "flipped_conversations": 0, "n_flips": 0}
    result: dict[str, Any] = {"checklist_version_id": version.id if version else None, "provider": provider or None,
                              "n_conversations": 0, "n_evaluations": 0, "single_run_conversations": 0, "runs": [],
                              "criteria": [], "overall": empty_overall, "total_deltas": [], "flips": []}
    if version is None:
        return result
    stmt = (select(Evaluation).where(Evaluation.status == "completed", Evaluation.checklist_version_id == version.id)
            .options(selectinload(Evaluation.criteria), selectinload(Evaluation.conversation)).order_by(Evaluation.id))
    if provider:
        stmt = stmt.where(Evaluation.provider == provider)
    by_conv: dict[str, list[Evaluation]] = defaultdict(list)
    for ev in db.scalars(stmt):
        by_conv[ev.conversation_id].append(ev)
    multi = {cid: evs for cid, evs in sorted(by_conv.items()) if len(evs) >= 2}
    rows_of = {ev.id: {r.code: r for r in ev.criteria} for evs in multi.values() for ev in evs}

    criteria_out: list[dict[str, Any]] = []
    flips: list[dict[str, Any]] = []
    for c in crits:
        last_pairs: list[tuple[str, str]] = []
        majority: list[float] = []
        unanimous = 0
        n_flips = 0
        for cid, evs in multi.items():
            judged: list[tuple[int, str, Any]] = []
            for ev in evs:
                row = rows_of[ev.id].get(c.code)
                if row is None or COPIED_FLAG in (row.flags or []) or _is_error_row(row):
                    continue
                v = NA if not row.applicable else _display(row.answer)
                judged.append((ev.id, _norm(v), v))
            if len(judged) < 2:
                continue
            norms = [j[1] for j in judged]
            last_pairs.append((norms[-2], norms[-1]))
            counts = Counter(norms)
            majority.append(max(counts.values()) / len(norms))
            if len(counts) == 1:
                unanimous += 1
                continue
            n_flips += 1
            conv = evs[-1].conversation
            flips.append({"conversation_id": cid, "code": c.code, "title": c.title, "values": [j[2] for j in judged],
                          "evaluation_ids": [j[0] for j in judged], "latest_pair_differs": norms[-1] != norms[-2],
                          "language": conv.language if conv is not None else None,
                          "channel": conv.channel if conv is not None else None})
        stats = _pair_stats(last_pairs, c)
        n = len(last_pairs)
        criteria_out.append({"code": c.code, "title": c.title, "answer_type": c.answer_type, "n": n,
                             "agree": sum(1 for a, b in last_pairs if a == b), "agreement": stats["exact"],
                             "kappa": stats["kappa"], "qwk": stats["qwk"], "mae": stats["mae"],
                             "majority_share": _r(_mean(majority), 3), "unanimous": _rate(unanimous, n), "flips": n_flips})

    deltas: list[dict[str, Any]] = []
    for cid, evs in multi.items():
        clean = [ev for ev in evs if ev.mode != "partial" and ev.total_score is not None
                 and not any(_is_error_row(r) for r in rows_of[ev.id].values())]
        if len(clean) < 2:
            continue
        a, b = clean[-2], clean[-1]
        deltas.append({"conversation_id": cid, "evaluation_ids": [a.id, b.id], "totals": [a.total_score, b.total_score],
                       "delta": round(abs(float(b.total_score) - float(a.total_score)), 2)})
    deltas.sort(key=lambda d: (-d["delta"], d["conversation_id"]))

    runs = Counter((ev.provider, ev.model, ev.mode) for evs in multi.values() for ev in evs)
    with_pairs = [r for r in criteria_out if r["n"]]
    result.update({
        "n_conversations": len(multi),
        "n_evaluations": sum(len(evs) for evs in multi.values()),
        "single_run_conversations": sum(1 for evs in by_conv.values() if len(evs) == 1),
        "runs": [{"provider": p, "model": m, "mode": mode, "n": n}
                 for (p, m, mode), n in sorted(runs.items(), key=lambda kv: (-kv[1], str(kv[0])))],
        "criteria": criteria_out,
        "overall": {
            "agreement_mean": _r(_mean([r["agreement"] for r in with_pairs]), 3),
            "majority_share_mean": _r(_mean([r["majority_share"] for r in with_pairs if r["majority_share"] is not None]), 3),
            "n_total_pairs": len(deltas),
            "mean_abs_delta_total": _r(_mean([d["delta"] for d in deltas]), 2),
            "max_abs_delta_total": deltas[0]["delta"] if deltas else None,
            "max_delta_conversation_id": deltas[0]["conversation_id"] if deltas else None,
            "flipped_conversations": len({f["conversation_id"] for f in flips}),
            "n_flips": len(flips),
        },
        "total_deltas": deltas,
        "flips": flips,
    })
    return result
