"""Checklist payload validation and normalisation (docs/arch/checklists.md, "Validation rules").

`validate_checklist_payload(payload)` returns the problems as `[{"field": "criteria[2].weight", "message": "..."}]`
(messages in Russian, written for people). `prepare_checklist_payload` runs the same checks and also returns the
normalised copy the service stores:

- binary options become exactly `yes`/`no` (labels «Да»/«Нет» and points 1/0 when missing; 0/0 for a gross-error
  criterion), ordered yes → no;
- scale options get numeric values and points (points default to the value), ordered from the top step down;
- `max_points` is always the top points of the options;
- a gross-error criterion gets `critical_value = "yes"` by default;
- sections referenced by criteria but missing from `sections` are created with the code as title (a warning says so).

Normalisation keeps the stored shape of data that is already valid (ints stay ints, unknown option keys are kept, text is
never trimmed), so re-saving an unchanged criterion keeps its judge-relevant signature and therefore its judge_spec.
"""
from __future__ import annotations

import copy
import math
import re
from dataclasses import dataclass, field
from typing import Any

CODE_PATTERN = r"^[A-Za-z][A-Za-z0-9_-]{0,15}$"
CODE_RE = re.compile(CODE_PATTERN)
ANSWER_TYPES = ("binary", "scale")
CHANNELS = ("agent chat", "voice call")
OVERRIDE_FIELDS = ("instruction", "na_allowed", "na_condition")
CRITICAL_MODES = ("flag_only", "flag_and_cap")
NAME_MAX = 200
TITLE_MAX = 200
SECTION_CODE_MAX = 16
# Size limits: every criterion goes into every judge call, and missing judge specs are compiled by a paid LLM call each.
MAX_CRITERIA = 100
INSTRUCTION_MAX = 4000      # the organizer's longest instruction is ~500 characters
NA_CONDITION_MAX = 2000
DESCRIPTION_MAX = 4000
JUDGE_SPEC_MAX = 20000      # characters of the judge_spec JSON (the organizer's specs are ≤ ~4 100)

# Organizer criterion ids of results.json "JSON 1.0" (docs/ORGANIZER_UPDATES.md §1). Must stay equal to
# cqa.export.results_json.DEFAULT_EXPORT_IDS (a test checks it); any other code is exported as code.lower().
ORGANIZER_EXPORT_IDS: dict[str, str] = {
    "C1": "contact", "C2": "discovery", "C3": "identification", "C4": "consultation",
    "C5": "empathy", "C6": "dialogue", "C7": "closure", "C8": "critical",
}

_BINARY_VALUES = {"yes": "yes", "да": "yes", "true": "yes", "no": "no", "нет": "no", "false": "no"}
_BINARY_DEFAULTS: dict[str, tuple[str, int]] = {"yes": ("Да", 1), "no": ("Нет", 0)}
_TRUE = {"true", "1", "yes", "да"}
_FALSE = {"false", "0", "no", "нет", ""}
_INT_RE = re.compile(r"[+-]?\d+")
_WEIGHT_FIELD = re.compile(r"criteria\[\d+\]\.weight")


def field_error(field_path: str, message: str) -> dict[str, str]:
    return {"field": field_path, "message": message}


def _plural(n: int, forms: tuple[str, str, str]) -> str:
    n = abs(n) % 100
    if 11 <= n <= 14:
        return forms[2]
    n %= 10
    if n == 1:
        return forms[0]
    if 2 <= n <= 4:
        return forms[1]
    return forms[2]


def summary_message(errors: list[dict[str, str]], subject: str = "Чек-лист") -> str:
    n = len(errors)
    head = f"{subject} не прошёл проверку: {n} {_plural(n, ('ошибка', 'ошибки', 'ошибок'))}."
    return f"{head} {errors[0]['message']}" if errors else head


class ChecklistValidationError(ValueError):
    """A payload failed validation. The router answers 422 {"error": {"code", "message", "errors", "warnings"}}."""

    def __init__(self, errors: list[dict[str, str]], warnings: list[dict[str, str]] | None = None, *,
                 code: str = "invalid_checklist", message: str | None = None):
        self.errors = list(errors)
        self.warnings = list(warnings or [])
        self.code = code
        self.message = message or summary_message(self.errors)
        super().__init__(self.message)

    def to_error(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "errors": self.errors, "warnings": self.warnings}


@dataclass
class PreparedChecklist:
    payload: dict[str, Any]
    errors: list[dict[str, str]] = field(default_factory=list)
    warnings: list[dict[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


# ------------------------------------------------------------------------------------------------ export ids


def default_export_id(code: str) -> str:
    return ORGANIZER_EXPORT_IDS.get(code, code.lower())


def export_ids_for(codes: list[str], scoring_rules: dict[str, Any] | None) -> dict[str, str]:
    """{code: export id}: scoring_rules.export_ids wins, then the organizer ids for C1..C8, then code.lower()."""
    overrides = scoring_rules.get("export_ids") if isinstance(scoring_rules, dict) else None
    overrides = overrides if isinstance(overrides, dict) else {}
    out: dict[str, str] = {}
    for code in codes:
        v = overrides.get(code)
        out[code] = v.strip() if isinstance(v, str) and v.strip() else default_export_id(code)
    return out


# ------------------------------------------------------------------------------------------------ scalar helpers


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _number(value: Any) -> int | float | None:
    """A number as given (ints stay ints) or parsed from a string ("15", "7.5", "7,5"); None when not a number."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        s = value.strip().replace(",", ".")
        if not s:
            return None
        if _INT_RE.fullmatch(s):
            return int(s)
        try:
            f = float(s)
        except ValueError:
            return None
        return f if math.isfinite(f) else None
    return None


def _fmt(n: int | float) -> str:
    f = float(n)
    return str(int(f)) if f.is_integer() else f"{f:.4f}".rstrip("0").rstrip(".")


def _flag(value: Any, default: bool = False) -> bool | None:
    """Booleans as given; 0/1 and "true"/"false"/"да"/"нет" are accepted. None means the value is not a boolean."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        s = value.strip().lower()
        if s in _TRUE:
            return True
        if s in _FALSE:
            return False
    return None


def _binary_value(value: Any) -> str | None:
    if isinstance(value, (str, bool)):
        return _BINARY_VALUES.get(str(value).strip().lower())
    return None


# ------------------------------------------------------------------------------------------------ options


def _check_anchor(item: dict[str, Any], path: str, errors: list[dict[str, str]]) -> bool:
    if "anchor" not in item:
        return True
    anchor = item["anchor"]
    if _blank(anchor):
        item.pop("anchor")
        return True
    if not isinstance(anchor, str):
        errors.append(field_error(f"{path}.anchor", "Описание шага (anchor) должно быть текстом."))
        return False
    return True


def _check_binary_options(opts: Any, path: str, errors: list[dict[str, str]], *, critical: bool) -> list[dict[str, Any]] | None:
    if opts is None or opts == []:
        return [{"value": v, "label": label, "points": 0 if critical else pts} for v, (label, pts) in _BINARY_DEFAULTS.items()]
    if not isinstance(opts, list):
        errors.append(field_error(path, "Варианты ответа задаются списком: [{\"value\": \"yes\"}, {\"value\": \"no\"}]."))
        return None
    found: dict[str, dict[str, Any]] = {}
    ok = True
    for j, o in enumerate(opts):
        p = f"{path}[{j}]"
        if not isinstance(o, dict):
            errors.append(field_error(p, "Вариант ответа должен быть объектом {value, label, points}."))
            ok = False
            continue
        key = _binary_value(o.get("value"))
        if key is None:
            errors.append(field_error(f"{p}.value", f"Для типа «Да/Нет» допустимы только значения «yes» и «no», указано «{o.get('value')}»."))
            ok = False
            continue
        if key in found:
            errors.append(field_error(f"{p}.value", f"Вариант «{key}» указан дважды."))
            ok = False
            continue
        item = dict(o)
        item["value"] = key
        label = o.get("label")
        if _blank(label):
            item["label"] = _BINARY_DEFAULTS[key][0]
        elif not isinstance(label, str):
            errors.append(field_error(f"{p}.label", "Подпись варианта должна быть текстом."))
            ok = False
        if _blank(o.get("points")):
            item["points"] = 0 if critical else _BINARY_DEFAULTS[key][1]
        else:
            pts = _number(o.get("points"))
            if pts is None or pts < 0:
                errors.append(field_error(f"{p}.points", "Баллы варианта должны быть числом не меньше 0."))
                ok = False
            else:
                item["points"] = pts
        ok = _check_anchor(item, p, errors) and ok
        found[key] = item
    if not ok:
        return None
    if set(found) != {"yes", "no"}:
        errors.append(field_error(path, "Для типа «Да/Нет» нужны ровно два варианта: «yes» (Да) и «no» (Нет)."))
        return None
    yes, no = found["yes"], found["no"]
    if float(no["points"]) > float(yes["points"]):
        errors.append(field_error(path, "Баллы за «Нет» не могут быть больше баллов за «Да»."))
        return None
    return [yes, no]


def _check_scale_options(opts: Any, path: str, errors: list[dict[str, str]]) -> list[dict[str, Any]] | None:
    if not isinstance(opts, list) or len(opts) < 2:
        errors.append(field_error(path, "Шкала должна содержать не меньше двух шагов с числовыми значениями, например 0, 5, 10."))
        return None
    items: list[dict[str, Any]] = []
    seen: dict[float, int] = {}
    ok = True
    for j, o in enumerate(opts):
        p = f"{path}[{j}]"
        if not isinstance(o, dict):
            errors.append(field_error(p, "Шаг шкалы должен быть объектом {value, label, points, anchor}."))
            ok = False
            continue
        val = _number(o.get("value"))
        if val is None:
            errors.append(field_error(f"{p}.value", "Значение шага шкалы должно быть числом."))
            ok = False
            continue
        if float(val) in seen:
            errors.append(field_error(f"{p}.value", f"Шаг «{_fmt(val)}» повторяется (уже есть в варианте №{seen[float(val)] + 1})."))
            ok = False
            continue
        seen[float(val)] = j
        item = dict(o)
        item["value"] = val
        if _blank(o.get("points")):
            if val < 0:
                errors.append(field_error(f"{p}.points", "Укажите баллы шага: значение отрицательное, баллы по умолчанию не подставить."))
                ok = False
                continue
            item["points"] = val
        else:
            pts = _number(o.get("points"))
            if pts is None or pts < 0:
                errors.append(field_error(f"{p}.points", "Баллы шага должны быть числом не меньше 0."))
                ok = False
                continue
            item["points"] = pts
        label = o.get("label")
        if _blank(label):
            item["label"] = _fmt(val)
        elif isinstance(label, (int, float)) and not isinstance(label, bool):
            item["label"] = _fmt(label)
        elif not isinstance(label, str):
            errors.append(field_error(f"{p}.label", "Подпись шага должна быть текстом."))
            ok = False
        ok = _check_anchor(item, p, errors) and ok
        items.append(item)
    if not ok:
        return None
    # top step first: the judge prompt lists anchors in this order and verdict validation treats options[0] as full marks
    items.sort(key=lambda o: (-float(o["points"]), -float(o["value"])))
    return items


# ------------------------------------------------------------------------------------------------ channel overrides


def _check_overrides(ov: Any, path: str, errors: list[dict[str, str]], *, base_na_condition: str | None) -> dict[str, Any]:
    if ov is None:
        return {}
    if not isinstance(ov, dict):
        errors.append(field_error(path, "Переопределения по каналам задаются объектом вида {\"voice call\": {\"instruction\": \"…\"}}."))
        return {}
    out: dict[str, Any] = {}
    for key, val in ov.items():
        p = f'{path}["{key}"]'
        channel = key.strip().lower().replace("_", " ") if isinstance(key, str) else None
        if channel not in CHANNELS:
            errors.append(field_error(p, f"Канал «{key}» не поддерживается: допустимы «agent chat» и «voice call»."))
            continue
        if channel in out:
            errors.append(field_error(p, f"Канал «{channel}» указан дважды."))
            continue
        if val is None:
            continue
        if not isinstance(val, dict):
            errors.append(field_error(p, "Переопределение канала должно быть объектом с полями instruction, na_allowed, na_condition."))
            continue
        for f in val:
            if f not in OVERRIDE_FIELDS:
                errors.append(field_error(f"{p}.{f}", f"Поле «{f}» не поддерживается: в переопределении канала допустимы instruction, na_allowed, na_condition."))
        item = dict(val)
        if val.get("instruction") is not None and not isinstance(val.get("instruction"), str):
            errors.append(field_error(f"{p}.instruction", "Инструкция для канала должна быть текстом."))
        elif isinstance(val.get("instruction"), str) and len(val["instruction"]) > INSTRUCTION_MAX:
            errors.append(field_error(f"{p}.instruction", f"Инструкция для канала длиннее {INSTRUCTION_MAX} символов."))
        if "na_allowed" in val:
            flag = _flag(val.get("na_allowed"))
            if flag is None:
                errors.append(field_error(f"{p}.na_allowed", "Ожидается true или false."))
            else:
                item["na_allowed"] = flag
        cond = val.get("na_condition")
        if cond is not None and not isinstance(cond, str):
            errors.append(field_error(f"{p}.na_condition", "Условие N/A для канала должно быть текстом."))
            cond = None
        elif isinstance(cond, str) and len(cond) > NA_CONDITION_MAX:
            errors.append(field_error(f"{p}.na_condition", f"Условие N/A для канала длиннее {NA_CONDITION_MAX} символов."))
        if item.get("na_allowed") is True and _blank(cond) and _blank(base_na_condition):
            errors.append(field_error(f"{p}.na_condition", f"Для канала «{channel}» разрешён N/A, но условие не описано: заполните na_condition."))
        out[channel] = item
    return out


# ------------------------------------------------------------------------------------------------ one criterion


def _check_criterion(raw: Any, path: str, errors: list[dict[str, str]], warnings: list[dict[str, str]]) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        errors.append(field_error(path, "Критерий должен быть объектом с полями code, title, answer_type, options, weight."))
        return None
    out: dict[str, Any] = {}

    code = raw.get("code")
    if not isinstance(code, str) or not code.strip():
        errors.append(field_error(f"{path}.code", "Укажите код критерия латиницей, например «C9»."))
        code = None
    elif not CODE_RE.fullmatch(code.strip()):
        errors.append(field_error(f"{path}.code", f"Код «{code}» не подходит: он начинается с латинской буквы и содержит только латинские буквы, "
                                                  "цифры, «_» или «-», всего не больше 16 символов."))
        code = None
    else:
        code = code.strip()
    out["code"] = code

    title = raw.get("title")
    if not isinstance(title, str) or not title.strip():
        errors.append(field_error(f"{path}.title", "Укажите название критерия."))
    elif len(title) > TITLE_MAX:
        errors.append(field_error(f"{path}.title", f"Название длиннее {TITLE_MAX} символов ({len(title)})."))
    out["title"] = title if isinstance(title, str) else None

    instruction = raw.get("instruction")
    if instruction is None:
        instruction = ""
    if not isinstance(instruction, str):
        errors.append(field_error(f"{path}.instruction", "Инструкция должна быть текстом."))
        instruction = ""
    elif not instruction.strip():
        warnings.append(field_error(f"{path}.instruction", "Инструкция пустая: модель будет опираться только на название критерия. "
                                                           "Опишите, что именно проверять."))
    elif len(instruction) > INSTRUCTION_MAX:
        errors.append(field_error(f"{path}.instruction", f"Инструкция длиннее {INSTRUCTION_MAX} символов ({len(instruction)}): "
                                                         "сократите её — она уходит в каждый запрос к модели."))
    out["instruction"] = instruction

    section = raw.get("section")
    if _blank(section):
        section = None
    elif not isinstance(section, str):
        errors.append(field_error(f"{path}.section", "Раздел задаётся кодом-строкой, например «S1»."))
        section = None
    elif len(section.strip()) > SECTION_CODE_MAX:
        errors.append(field_error(f"{path}.section", f"Код раздела длиннее {SECTION_CODE_MAX} символов."))
        section = None
    else:
        section = section.strip()
    out["section"] = section

    answer_type = raw.get("answer_type")
    if _blank(answer_type):
        answer_type = "binary"
    elif not isinstance(answer_type, str) or answer_type.strip().lower() not in ANSWER_TYPES:
        errors.append(field_error(f"{path}.answer_type", "Тип ответа: «binary» (Да/Нет) или «scale» (шкала баллов)."))
        answer_type = None
    else:
        answer_type = answer_type.strip().lower()
    out["answer_type"] = answer_type

    is_critical = _flag(raw.get("is_critical"))
    if is_critical is None:
        errors.append(field_error(f"{path}.is_critical", "Ожидается true или false."))
        is_critical = False
    out["is_critical"] = is_critical

    options: list[dict[str, Any]] | None = None
    if answer_type == "binary":
        options = _check_binary_options(raw.get("options"), f"{path}.options", errors, critical=is_critical)
    elif answer_type == "scale":
        options = _check_scale_options(raw.get("options"), f"{path}.options", errors)
    out["options"] = options if options is not None else copy.deepcopy(raw.get("options") or [])

    weight: int | float | None
    if _blank(raw.get("weight")):
        weight = 0
    else:
        weight = _number(raw.get("weight"))
        if weight is None:
            errors.append(field_error(f"{path}.weight", "Вес должен быть числом, например 10."))
        elif weight < 0:
            errors.append(field_error(f"{path}.weight", "Вес не может быть отрицательным."))
            weight = None
    out["weight"] = weight

    if options:
        top = max(o["points"] for o in options)
        given = _number(raw.get("max_points"))
        if given is not None and float(given) != float(top) and (weight or 0) > 0:
            warnings.append(field_error(f"{path}.max_points", f"max_points ({_fmt(given)}) заменён на наибольший балл среди вариантов ({_fmt(top)})."))
        out["max_points"] = top
        if (weight or 0) > 0 and float(top) <= 0 and not is_critical:  # for a gross error the weight error says it all
            errors.append(field_error(f"{path}.options", "Ни один вариант не даёт баллов: при весе больше 0 лучший вариант должен давать больше 0 баллов."))
    else:
        out["max_points"] = raw.get("max_points")

    na_allowed = _flag(raw.get("na_allowed"))
    if na_allowed is None:
        errors.append(field_error(f"{path}.na_allowed", "Ожидается true или false."))
        na_allowed = False
    na_condition = raw.get("na_condition")
    if na_condition is not None and not isinstance(na_condition, str):
        errors.append(field_error(f"{path}.na_condition", "Условие N/A должно быть текстом."))
        na_condition = None
    elif isinstance(na_condition, str) and len(na_condition) > NA_CONDITION_MAX:
        errors.append(field_error(f"{path}.na_condition", f"Условие N/A длиннее {NA_CONDITION_MAX} символов ({len(na_condition)})."))
    if na_allowed and _blank(na_condition):
        errors.append(field_error(f"{path}.na_condition", "Опишите условие N/A: без него модель не сможет обосновать неприменимость критерия."))
    out["na_allowed"] = na_allowed
    out["na_condition"] = na_condition

    if is_critical:
        if answer_type == "scale":
            errors.append(field_error(f"{path}.answer_type", "Грубая ошибка фиксируется ответом «Да/Нет»: выберите тип «binary»."))
        if weight is not None and weight > 0:
            errors.append(field_error(f"{path}.weight", "Критерий грубой ошибки не начисляет баллы и отмечается флагом отдельно: укажите вес 0."))
        cv_raw = raw.get("critical_value")
        critical_value = "yes" if _blank(cv_raw) else _binary_value(cv_raw)
        if critical_value is None:
            errors.append(field_error(f"{path}.critical_value", "Значение, при котором фиксируется грубая ошибка, — «yes» или «no»."))
        out["critical_value"] = critical_value
    else:
        out["critical_value"] = None

    out["channel_overrides"] = _check_overrides(raw.get("channel_overrides"), f"{path}.channel_overrides", errors,
                                                base_na_condition=na_condition)

    order = raw.get("order")
    if order is not None:
        n = _number(order)
        if n is None or float(n) != int(n):
            errors.append(field_error(f"{path}.order", "Порядок должен быть целым числом."))
        else:
            out["order"] = int(n)

    spec = raw.get("judge_spec")
    if spec is None or spec == {} or spec == "":
        spec = None
    elif not isinstance(spec, dict):
        errors.append(field_error(f"{path}.judge_spec", "judge_spec должен быть объектом (definition, decision_steps, …) или null."))
        spec = None
    else:
        from pydantic import ValidationError

        from ..judge.contract import JudgeSpec

        try:
            JudgeSpec.model_validate(spec)
        except ValidationError as e:
            names = sorted({".".join(str(x) for x in err.get("loc", ())) or "?" for err in e.errors()})
            errors.append(field_error(f"{path}.judge_spec", f"judge_spec не соответствует схеме (поля: {', '.join(names)}); "
                                                            "уберите его, чтобы спецификация собралась заново."))
        else:
            import json

            size = len(json.dumps(spec, ensure_ascii=False))
            if size > JUDGE_SPEC_MAX:
                errors.append(field_error(f"{path}.judge_spec", f"judge_spec длиннее {JUDGE_SPEC_MAX} символов ({size})."))
        spec = copy.deepcopy(spec)
    out["judge_spec"] = spec
    return out


def prepare_criterion_payload(raw: Any, *, path: str = "criterion") -> tuple[dict[str, Any] | None, list[dict[str, str]], list[dict[str, str]]]:
    """One criterion on its own (POST /api/checklists/test-criterion): the per-criterion rules only."""
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    if raw is None:
        errors.append(field_error(path, "Передайте критерий для проверки."))
        return None, errors, warnings
    c = _check_criterion(raw, path, errors, warnings)
    if c is not None:
        c.setdefault("order", 0)
    return c, errors, warnings


# ------------------------------------------------------------------------------------------------ whole payload


def _check_scoring_rules(rules: dict[str, Any], errors: list[dict[str, str]]) -> None:
    ts = rules.get("total_scale")
    if ts is not None:
        n = _number(ts)
        if n is None or n <= 0:
            errors.append(field_error("scoring_rules.total_scale", "Шкала итога должна быть положительным числом, обычно 100."))
    rnd = rules.get("rounding")
    if rnd is not None:
        if not isinstance(rnd, dict):
            errors.append(field_error("scoring_rules.rounding", "rounding задаётся объектом {\"mode\": \"half_up\", \"decimals\": 2}."))
        elif rnd.get("decimals") is not None:
            n = _number(rnd.get("decimals"))
            if n is None or float(n) != int(n) or not 0 <= n <= 6:
                errors.append(field_error("scoring_rules.rounding.decimals", "Число знаков после запятой — целое от 0 до 6."))
    crit = rules.get("critical")
    if crit is not None:
        if not isinstance(crit, dict):
            errors.append(field_error("scoring_rules.critical", "critical задаётся объектом {\"mode\": \"flag_only\"}."))
        else:
            mode = crit.get("mode")
            if mode is not None and mode not in CRITICAL_MODES:
                errors.append(field_error("scoring_rules.critical.mode", "Режим грубых ошибок: «flag_only» (только флаг) или «flag_and_cap» "
                                                                         "(флаг и ограничение итога)."))
            if crit.get("cap_total") is not None and _number(crit.get("cap_total")) is None:
                errors.append(field_error("scoring_rules.critical.cap_total", "cap_total должен быть числом."))
    ex = rules.get("export_ids")
    if ex is not None:
        if not isinstance(ex, dict):
            errors.append(field_error("scoring_rules.export_ids", "export_ids задаётся объектом {\"код критерия\": \"идентификатор в results.json\"}."))
        else:
            for k, v in ex.items():
                if not isinstance(v, str) or not v.strip():
                    errors.append(field_error(f"scoring_rules.export_ids.{k}", "Идентификатор выгрузки должен быть непустой строкой."))


def _check_sections(sections: list[Any], errors: list[dict[str, str]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, s in enumerate(sections):
        p = f"sections[{i}]"
        if not isinstance(s, dict):
            errors.append(field_error(p, "Раздел задаётся объектом {\"code\": \"S1\", \"title\": \"…\"}."))
            continue
        code = s.get("code")
        if not isinstance(code, str) or not code.strip():
            errors.append(field_error(f"{p}.code", "Укажите код раздела, например «S1»."))
            continue
        code = code.strip()
        if len(code) > SECTION_CODE_MAX:
            errors.append(field_error(f"{p}.code", f"Код раздела длиннее {SECTION_CODE_MAX} символов."))
            continue
        if code in seen:
            errors.append(field_error(f"{p}.code", f"Раздел «{code}» указан дважды."))
            continue
        seen.add(code)
        item = dict(s)
        item["code"] = code
        title = s.get("title")
        if _blank(title):
            item["title"] = code
        elif not isinstance(title, str):
            errors.append(field_error(f"{p}.title", "Название раздела должно быть текстом."))
        out.append(item)
    return out


def prepare_checklist_payload(payload: Any, *, inherited_sections: list[dict[str, Any]] | None = None,
                              inherited_scoring_rules: dict[str, Any] | None = None) -> PreparedChecklist:
    """Validate a checklist / version body and return its normalised copy with errors and warnings.

    `inherited_*` are the previous version's values: a new version without `sections` / `scoring_rules` keeps them,
    so references and export ids are checked against what will actually be stored.
    """
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    if not isinstance(payload, dict):
        return PreparedChecklist({}, [field_error("", "Тело запроса должно быть JSON-объектом с полем criteria.")], [])
    out: dict[str, Any] = {k: copy.deepcopy(v) for k, v in payload.items() if k not in ("criteria", "sections", "scoring_rules")}

    name = payload.get("name")
    if name is not None:
        if not isinstance(name, str):
            errors.append(field_error("name", "Название чек-листа должно быть текстом."))
        elif len(name) > NAME_MAX:
            errors.append(field_error("name", f"Название чек-листа длиннее {NAME_MAX} символов ({len(name)})."))
    if payload.get("description") is not None and not isinstance(payload.get("description"), str):
        errors.append(field_error("description", "Описание чек-листа должно быть текстом."))
    elif isinstance(payload.get("description"), str) and len(payload["description"]) > DESCRIPTION_MAX:
        errors.append(field_error("description", f"Описание чек-листа длиннее {DESCRIPTION_MAX} символов."))

    rules_given = payload.get("scoring_rules")
    if rules_given is not None and not isinstance(rules_given, dict):
        errors.append(field_error("scoring_rules", "scoring_rules задаётся объектом (total_scale, rounding, critical, export_ids)."))
        rules_given = None
    if rules_given:
        _check_scoring_rules(rules_given, errors)
    effective_rules = rules_given or (inherited_scoring_rules if isinstance(inherited_scoring_rules, dict) else None) or {}

    sections_given = payload.get("sections")
    if sections_given is not None and not isinstance(sections_given, list):
        errors.append(field_error("sections", "sections задаётся списком [{\"code\": \"S1\", \"title\": \"…\"}]."))
        sections_given = None
    if sections_given:
        sections = _check_sections(sections_given, errors)
    else:
        sections = [copy.deepcopy(s) for s in (inherited_sections or []) if isinstance(s, dict)]
    section_codes = {s.get("code") for s in sections}

    criteria = payload.get("criteria")
    if criteria is None or criteria == []:
        errors.append(field_error("criteria", "Добавьте хотя бы один критерий."))
        criteria = []
    elif not isinstance(criteria, list):
        errors.append(field_error("criteria", "criteria задаётся списком критериев."))
        criteria = []
    elif len(criteria) > MAX_CRITERIA:
        errors.append(field_error("criteria", f"Слишком много критериев: {len(criteria)}, допускается не больше {MAX_CRITERIA}. "
                                              "Каждый критерий — отдельный запрос к модели в каждой оценке."))
        criteria = []

    norm: list[tuple[int, dict[str, Any]]] = []
    for i, raw in enumerate(criteria):
        c = _check_criterion(raw, f"criteria[{i}]", errors, warnings)
        if c is not None:
            c.setdefault("order", i)
            norm.append((i, c))

    seen: dict[str, tuple[int, str]] = {}
    duplicates: set[int] = set()
    for i, c in norm:
        code = c.get("code")
        if not code:
            continue
        if code.lower() in seen:
            j, other = seen[code.lower()]
            hint = "" if other == code else " (коды не должны отличаться только регистром)"
            errors.append(field_error(f"criteria[{i}].code", f"Код «{code}» уже используется в критерии №{j + 1}{hint}."))
            duplicates.add(i)
        else:
            seen[code.lower()] = (i, code)

    weights = [c["weight"] for _, c in norm if isinstance(c.get("weight"), (int, float))]
    weight_errors = any(_WEIGHT_FIELD.fullmatch(e["field"]) for e in errors)
    if norm and not weight_errors and not any(w > 0 for w in weights):
        errors.append(field_error("criteria", "Хотя бы один критерий должен иметь вес больше 0 — иначе итоговый балл не рассчитать."))
    total = sum(weights)
    if total > 0 and not weight_errors and abs(total - 100) > 1e-9:
        warnings.append(field_error("criteria", f"Сумма весов {_fmt(total)}, а не 100: итоговый балл всё равно нормируется к 100 "
                                                "пропорционально весам."))

    added_sections = False
    for i, c in norm:
        s = c.get("section")
        if s and s not in section_codes:
            sections.append({"code": s, "title": s})
            section_codes.add(s)
            added_sections = True
            warnings.append(field_error(f"criteria[{i}].section", f"Раздел «{s}» не найден в sections — создан автоматически "
                                                                  f"с названием «{s}». Переименуйте его при необходимости."))

    codes = [c["code"] for _, c in norm if c.get("code")]
    overrides = effective_rules.get("export_ids") if isinstance(effective_rules.get("export_ids"), dict) else {}
    for k in overrides:
        if k not in codes:
            warnings.append(field_error(f"scoring_rules.export_ids.{k}", f"Критерия «{k}» нет в чек-листе — это сопоставление не используется."))
    ids = export_ids_for(codes, effective_rules)
    owner: dict[str, str] = {}
    for i, c in norm:
        code = c.get("code")
        if not code or i in duplicates:
            continue
        eid = ids[code]
        if eid in owner:
            errors.append(field_error(f"criteria[{i}].code", f"Идентификатор выгрузки results.json «{eid}» уже занят критерием «{owner[eid]}»: "
                                                             "задайте другой в scoring_rules.export_ids."))
        else:
            owner[eid] = code

    out["criteria"] = [c for _, c in norm]
    if sections_given or added_sections:
        out["sections"] = sections
    if rules_given:
        out["scoring_rules"] = copy.deepcopy(rules_given)
    return PreparedChecklist(out, errors, warnings)


def validate_checklist_payload(payload: Any, *, inherited_sections: list[dict[str, Any]] | None = None,
                               inherited_scoring_rules: dict[str, Any] | None = None) -> list[dict[str, str]]:
    """Problems of a checklist / version body as [{"field", "message"}] (empty list = valid)."""
    return prepare_checklist_payload(payload, inherited_sections=inherited_sections, inherited_scoring_rules=inherited_scoring_rules).errors
