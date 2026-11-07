"""Offline deterministic judge: provider ``fake``, model ``fake-judge``.

Used by tests, CI and keyless demos. It reads exactly the prompt the real judges receive (cqa.judge.packer):
the packed transcript lines ``[idx] speaker (ts) <segment>: text`` and the criterion block
(``# Criterion C1 · …``, ``Allowed answers: …``, ``N/A: …``), applies simple keyword heuristics and returns a
valid Verdict that cites an existing operator turn and explains the answer in Russian. No network, no tokens,
cost 0, and the same prompt always gives the same verdict.

Heuristics by criterion code (any other code falls back to the generic rule):

- C1  "yes" when the first operator turn contains a greeting (RU / KZ / EN), otherwise "no".
- C2  N/A when the criterion allows N/A and nobody mentions the account; otherwise "yes" when an operator
      turn asks a question.
- C3  N/A when the criterion allows N/A and nobody mentions the account; otherwise "yes" when identity is
      confirmed by a system event, an operator asks for identifiers (ФИО, ИИН, SMS code…) or a transcript
      mask shows identification data.
- C5  "no" when an operator turn is rude, otherwise "yes".
- C7  "yes" when the last operator turn says goodbye, otherwise "no".
- C8  "yes" when an operator turn contains «читать умеете», «на карту» or «личный номер» (plus EN/KZ
      equivalents), otherwise "no".
- scale criteria (C4, C6, …): the top step.
- any other binary criterion: the positive option.

This is a plumbing double, not a quality judge: it never claims to understand the dialog.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from ...ingest.normalize import IDENTITY_OK_RE
from ..contract import CheckedSpan, Evidence, ProviderResult, Usage, Verdict
from .base import ProviderError

__all__ = ["DEFAULT_MODEL", "FakeProvider", "decide", "parse_criterion", "parse_transcript"]

DEFAULT_MODEL = "fake-judge"
FLAG = "fake_judge"
SPEAKERS = ("client", "operator", "bot", "system")

# "[12] operator (01:15) <pre>: text" — the segment tag is absent for the operator segment (pack_transcript).
TURN_RE = re.compile(r"^\[(?P<idx>\d+)\] (?P<speaker>[\w-]+) \((?P<ts>[^)]*)\)(?: <(?P<segment>[\w-]+)>)?: ?(?P<text>.*)$")
HEADER_RE = re.compile(r"^#\s*Criterion\s+(?P<code>\S+)(?:\s*·\s*(?P<title>.+?))?\s*$", re.M)
ALLOWED_RE = re.compile(r"^Allowed answers:\s*(?P<kind>binary|scale)\b(?P<rest>.*)$", re.M)
NA_RE = re.compile(r"^N/A:\s*(?P<rule>not allowed|allowed)", re.M)
FACTS_RE = re.compile(r"## Structural facts\s*\n(?P<json>.*?)\n\s*\n## Transcript", re.S)

GREETING_RE = re.compile(
    r"здравствуй|добр(?:ый|ое|ого)\s+(?:день|утро|вечер|времени)|приветству|\bпривет|"
    r"\bсәлем|\bсалем|қайырлы\s+(?:күн|таң|кеш)|армысыз|"
    r"\bhello\b|\bhi\b|\bhey\b|good\s+(?:morning|afternoon|evening|day)\b|\bgreetings\b|\bwelcome\b",
    re.I,
)
FAREWELL_RE = re.compile(
    r"до\s+свидания|всего\s+(?:доброго|хорошего|наилучшего)|хорошего\s+(?:дня|вечера|выходных)|удачного\s+дня|"
    r"до\s+встречи|сау\s+бол|көріскенше|күніңіз\s+сәтті|"
    r"good\s*-?\s*bye|\bbye\b|have\s+a\s+(?:good|nice|great|wonderful)\s+(?:day|evening|weekend)|take\s+care",
    re.I,
)
QUESTION_RE = re.compile(r"\?")
ACCOUNT_RE = re.compile(
    r"\bсч[её]т|\bбаланс|пополн|зачисл|\bвывод|\bвывест|\bвывел|списан|\bпозици|блокир|"
    r"\bшот|толықтыр|"
    r"\baccount|\bbalance|\bdeposit|\bwithdraw",
    re.I,
)
ID_REQUEST_RE = re.compile(
    r"\bфио\b|\bиин\b|дат[аыу]\s+рождения|кодов\w*\s+слов|код\w*\s+из\s+(?:смс|sms)|\bсмс-?код|\bsms-?код|удостоверени|паспорт|"
    r"\bжсн\b|аты-жөн|жеке\s+куәлік|"
    r"full\s+name|date\s+of\s+birth|verification\s+code|\biin\b|\bid\s+number",
    re.I,
)
ID_MASK_RE = re.compile(r"\[данные идентификации скрыты\]", re.I)
RUDE_RE = re.compile(
    r"читать\s+умеете|не\s+мои\s+проблемы|сами\s+разбирайтесь|не\s+ваше\s+дело|"
    r"can\s+you\s+(?:even\s+)?read|not\s+my\s+problem|figure\s+it\s+out\s+yourself",
    re.I,
)
# C8 stop phrases → (violation kind as in cqa.analytics.violations, Russian explanation)
GROSS_RULES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("insult", re.compile(r"читать\s+умеете|can\s+you\s+(?:even\s+)?read", re.I), "Оператор оскорбил клиента"),
    ("paid_bypass", re.compile(r"на\s+(?:мою\s+|личную\s+|свою\s+)?карту|to\s+my\s+(?:personal\s+)?card|картама", re.I),
     "Оператор предложил перевести деньги на карту в обход компании"),
    ("paid_bypass", re.compile(r"личн\w*\s+номер|personal\s+(?:phone\s+)?number|жеке\s+нөмір", re.I),
     "Оператор попросил личный номер для платной консультации в обход компании"),
)

CONF_RULE = 0.9      # a keyword rule decided the answer
CONF_NA = 0.7        # N/A by the account-words rule
CONF_DEFAULT = 0.6   # default answer (top step, generic yes)

VERDICT_FIELDS = {"criterion_code", "applicable", "answer", "explanation"}
SPEC_FIELDS = {"definition", "decision_steps"}


@dataclass
class PackedTurn:
    idx: int
    speaker: str
    segment: str  # "operator" when the packed line carries no segment tag
    text: str


@dataclass
class CriterionInfo:
    code: str
    title: str
    kind: str  # binary | scale
    values: list[str]
    na_allowed: bool


# ----------------------------------------------------------------------------------------------- parsing


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(b.get("text", "")) for b in content if isinstance(b, dict) and b.get("type", "text") == "text")
    return ""


def parse_transcript(text: str) -> list[PackedTurn]:
    """Turns of a packed transcript. Lines that are not turn lines continue the previous turn (multi-line texts)."""
    has_header = "## Transcript" in text
    body = text.split("## Transcript", 1)[1] if has_header else text
    turns: list[PackedTurn] = []
    for line in body.split("\n"):
        m = TURN_RE.match(line)
        if m and (not turns or int(m["idx"]) > turns[-1].idx):
            turns.append(PackedTurn(idx=int(m["idx"]), speaker=m["speaker"], segment=m["segment"] or "operator", text=m["text"]))
        elif turns and has_header:
            turns[-1].text += "\n" + line
    return turns


def _facts(text: str) -> dict[str, Any]:
    m = FACTS_RE.search(text)
    if not m:
        return {}
    try:
        facts = json.loads(m["json"])
    except ValueError:
        return {}
    return facts if isinstance(facts, dict) else {}


def parse_criterion(texts: list[str]) -> CriterionInfo:
    """Criterion code, title, answer kind, allowed values and N/A rule from the criterion block (last one wins)."""
    for text in reversed(texts):
        h = HEADER_RE.search(text)
        if not h:
            continue
        block = text[h.start():]
        code = h["code"]
        title = (h["title"] or code).strip()
        kind, values = "binary", ["yes", "no"]
        a = ALLOWED_RE.search(block)
        if a:
            kind = a["kind"]
            rest = a["rest"]
            found = re.findall(r'"([^"]*)"\s*\(', rest) if kind == "binary" else []
            values = found or re.findall(r'"([^"]*)"', rest) or (["yes", "no"] if kind == "binary" else [])
        if not values:
            raise ProviderError(f"fake provider: criterion {code} lists no allowed answers")
        n = NA_RE.search(block)
        return CriterionInfo(code=code, title=title, kind=kind, values=values, na_allowed=bool(n and n["rule"] == "allowed"))
    raise ProviderError("fake provider: no criterion block ('# Criterion <code>') in the messages")


def _transcript(texts: list[str]) -> tuple[list[PackedTurn], dict[str, Any]]:
    for text in texts:
        if "## Transcript" in text:
            return parse_transcript(text), _facts(text)
    for text in texts:
        turns = parse_transcript(text)
        if turns:
            return turns, {}
    return [], {}


# ----------------------------------------------------------------------------------------------- deciding


def _short(text: str, limit: int = 80) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def _quote(text: str, m: re.Match[str] | None = None, limit: int = 300) -> str:
    """A verbatim fragment of the turn (≤ limit chars) that contains the match when there is one."""
    if len(text) <= limit:
        return text
    if m is None:
        return text[:limit]
    start = max(0, min(m.start() - 60, len(text) - limit))
    return text[start:start + limit]


def _ev(turn: PackedTurn | None, m: re.Match[str] | None = None, role: str = "supports") -> list[Evidence]:
    if turn is None or turn.speaker not in SPEAKERS:
        return []
    return [Evidence(turn_idx=turn.idx, speaker=turn.speaker, quote=_quote(turn.text, m), role=role)]  # type: ignore[arg-type]


def _span(a: PackedTurn | None, b: PackedTurn | None) -> CheckedSpan | None:
    if a is None or b is None or a.idx > b.idx:
        return None
    return CheckedSpan(from_turn=a.idx, to_turn=b.idx)


def _search(turns: list[PackedTurn], pattern: re.Pattern[str]) -> tuple[PackedTurn | None, re.Match[str] | None]:
    for t in turns:
        m = pattern.search(t.text)
        if m:
            return t, m
    return None, None


def _binary_values(values: list[str]) -> tuple[str, str]:
    pos = next((v for v in values if v.strip().lower() in ("yes", "да", "true", "1")), values[0])
    neg = next((v for v in values if v.strip().lower() in ("no", "нет", "false", "0")), values[-1])
    return pos, neg


def _top_step(values: list[str]) -> str:
    try:
        return max(values, key=lambda v: float(v))
    except ValueError:
        return values[0]


def _based_on(turn: PackedTurn | None) -> str:
    return f" Основание — «{_short(turn.text)}»." if turn is not None else ""


def decide(crit: CriterionInfo, turns: list[PackedTurn], facts: dict[str, Any] | None = None) -> tuple[Verdict, str]:
    """Deterministic verdict for one criterion and the name of the rule that produced it."""
    facts = facts or {}
    code, title = crit.code.upper(), crit.title
    texts = [t for t in turns if t.text.strip()]
    ops = [t for t in texts if t.speaker == "operator" and t.segment == "operator"] or [t for t in texts if t.speaker == "operator"]
    citable = [t for t in texts if t.speaker in SPEAKERS]
    first = ops[0] if ops else (citable[0] if citable else None)
    last = ops[-1] if ops else (citable[-1] if citable else None)

    def verdict(answer: str, explanation: str, evidence: list[Evidence], *, confidence: float, steps: list[str],
                applicable: bool = True, na_reason: str = "", checked_span: CheckedSpan | None = None) -> Verdict:
        return Verdict(criterion_code=crit.code, applicable=applicable, na_reason=na_reason, answer=answer if applicable else "",
                       confidence=confidence, evidence=evidence, checked_span=checked_span, explanation=explanation,
                       step_results=steps, flags=[FLAG], claims=[])

    steps0 = [f"Реплик оператора: {len(ops)}; первая — [{ops[0].idx}]." if ops else "Реплик оператора в транскрипте нет."]

    if crit.kind == "scale":
        top = _top_step(crit.values)
        t = max(ops, key=lambda x: len(x.text)) if ops else first
        return verdict(top, f"{title} — {top}. Эвристика офлайн-судьи выставляет верхнюю ступень шкалы.{_based_on(t)}", _ev(t),
                       confidence=CONF_DEFAULT, steps=steps0 + [f"Шкала {', '.join(crit.values)}: выбрана верхняя ступень {top}."]), "scale_top_step"

    pos, neg = _binary_values(crit.values)

    if code in ("C2", "C3") and crit.na_allowed:
        _, acc = _search([t for t in texts if t.speaker in ("client", "operator")], ACCOUNT_RE)
        if acc is None:
            reason = ("Клиент не обсуждал свой счёт, поэтому уточнять потребность не требовалось." if code == "C2"
                      else "Данные счёта в диалоге не затрагивались, идентификация не требовалась.")
            return verdict("", f"{title} — N/A. {reason}", _ev(first, role="context"), applicable=False, na_reason=reason,
                           confidence=CONF_NA, steps=steps0 + ["Слова о счёте (счёт, баланс, пополнение, вывод, account, шот) не найдены."]), "na_no_account_words"

    if code == "C1":
        m = GREETING_RE.search(first.text) if first is not None else None
        if m:
            return verdict(pos, f"{title} — Да. Оператор поздоровался в первой реплике.{_based_on(first)}", _ev(first, m),
                           confidence=CONF_RULE, steps=steps0 + [f"Приветствие найдено: «{m.group(0)}»."]), "greeting_found"
        return verdict(neg, f"{title} — Нет. В первой реплике оператора нет приветствия.{_based_on(first)}", _ev(first),
                       checked_span=_span(first, first), confidence=CONF_RULE, steps=steps0 + ["Приветствие в первой реплике не найдено."]), "greeting_missing"

    if code == "C2":
        t, m = _search(ops, QUESTION_RE)
        if t is not None:
            return verdict(pos, f"{title} — Да. Оператор задал уточняющий вопрос.{_based_on(t)}", _ev(t, m),
                           confidence=CONF_RULE, steps=steps0 + [f"Вопрос оператора найден в реплике [{t.idx}]."]), "question_found"
        return verdict(neg, f"{title} — Нет. Оператор не задал уточняющих вопросов.{_based_on(first)}", _ev(first),
                       checked_span=_span(first, last), confidence=CONF_RULE, steps=steps0 + ["Вопросов в репликах оператора нет."]), "question_missing"

    if code == "C3":
        t, m = _search(ops, ID_REQUEST_RE)
        if t is not None and m is not None:
            return verdict(pos, f"{title} — Да. Оператор запросил данные для идентификации клиента.{_based_on(t)}", _ev(t, m),
                           confidence=CONF_RULE, steps=steps0 + [f"Запрос идентификационных данных: «{m.group(0)}»."]), "id_requested"
        event, em = _search([x for x in texts if x.speaker == "system"], IDENTITY_OK_RE)
        if facts.get("identity_confirmed_event") or event is not None:
            ev = _ev(event, em) + _ev(first, role="context") if event is not None else _ev(first, role="context")
            return verdict(pos, f"{title} — Да. Личность клиента подтверждена системным событием.{_based_on(event or first)}", ev,
                           confidence=CONF_RULE, steps=steps0 + ["Найдено системное событие о подтверждении личности."]), "identity_event"
        t, m = _search(texts, ID_MASK_RE)
        if t is not None:
            return verdict(pos, f"{title} — Да. Идентификационные данные прозвучали (скрыты маской в транскрипте).{_based_on(t)}", _ev(t, m),
                           confidence=CONF_DEFAULT, steps=steps0 + ["Найдена маска «[Данные идентификации скрыты]»."]), "id_mask"
        return verdict(neg, f"{title} — Нет. Данные счёта обсуждались без запроса идентификационных данных.{_based_on(first)}", _ev(first),
                       checked_span=_span(first, last), confidence=CONF_RULE, steps=steps0 + ["Запроса ФИО, ИИН или кода из SMS не найдено."]), "id_missing"

    if code == "C5":
        t, m = _search(ops, RUDE_RE)
        if t is not None and m is not None:
            return verdict(neg, f"{title} — Нет. Оператор ответил клиенту грубо.{_based_on(t)}", _ev(t, m),
                           confidence=CONF_RULE, steps=steps0 + [f"Грубая фраза: «{m.group(0)}»."]), "rude_phrase"
        return verdict(pos, f"{title} — Да. Грубых или пренебрежительных фраз в репликах оператора не найдено.{_based_on(first)}", _ev(first),
                       confidence=CONF_RULE, steps=steps0 + ["Грубых фраз не найдено."]), "polite"

    if code == "C7":
        m = FAREWELL_RE.search(last.text) if last is not None else None
        if m:
            return verdict(pos, f"{title} — Да. Оператор попрощался с клиентом в последней реплике.{_based_on(last)}", _ev(last, m),
                           confidence=CONF_RULE, steps=steps0 + [f"Прощание найдено: «{m.group(0)}»."]), "farewell_found"
        return verdict(neg, f"{title} — Нет. В последней реплике оператора нет прощания.{_based_on(last)}", _ev(last),
                       checked_span=_span(last, last), confidence=CONF_RULE, steps=steps0 + ["Прощания в последней реплике не найдено."]), "farewell_missing"

    if code == "C8":
        for t in ops:
            for kind, pattern, what in GROSS_RULES:
                m = pattern.search(t.text)
                if m:
                    return verdict(pos, f"{title} — Да. {what}.{_based_on(t)}", _ev(t, m), confidence=CONF_RULE,
                                   steps=steps0 + [f"Стоп-фраза «{m.group(0)}» ({kind}) в реплике [{t.idx}]."]), f"gross_{kind}"
        return verdict(neg, f"{title} — Нет. Стоп-фраз грубых нарушений в репликах оператора не найдено.", _ev(first),
                       confidence=CONF_RULE, steps=steps0 + ["Стоп-фразы не найдены."]), "gross_none"

    return verdict(pos, f"{title} — Да. Офлайн-судья засчитывает критерий по умолчанию.{_based_on(first)}", _ev(first),
                   confidence=CONF_DEFAULT, steps=steps0 + ["Критерий без отдельной эвристики: положительный ответ по умолчанию."]), "generic_positive"


def _judge_spec(texts: list[str]) -> dict[str, Any]:
    """JudgeSpec for the rubric compiler prompt: the deterministic fallback spec of the embedded criterion JSON."""
    from ...checklist.compiler import fallback_spec

    blob = "\n".join(texts)
    decoder = json.JSONDecoder()
    crit: dict[str, Any] = {}
    for m in re.finditer(r"\{", blob):
        try:
            obj, _ = decoder.raw_decode(blob, m.start())
        except ValueError:
            continue
        if isinstance(obj, dict) and ("instruction" in obj or "title" in obj):
            crit = obj
    if not crit:
        crit = {"title": "Критерий", "instruction": "Требование критерия выполнено.", "answer_type": "binary"}
    return fallback_spec(crit)


# ----------------------------------------------------------------------------------------------- provider


def _fields(schema: type[BaseModel]) -> set[str]:
    return set(getattr(schema, "model_fields", None) or {})


class FakeProvider:
    """JudgeProvider double: deterministic, offline, free."""

    name = "fake"

    def __init__(self, model: str | None = None):
        self.model = model or DEFAULT_MODEL

    async def complete(self, system: str, messages: list[dict[str, Any]], schema: type[BaseModel], *, effort: str = "high",
                       max_tokens: int = 4096) -> ProviderResult:
        t0 = time.perf_counter()
        texts = [_text_of(m.get("content")) for m in messages if m.get("role") == "user"]
        fields = _fields(schema)
        if VERDICT_FIELDS <= fields:
            turns, facts = _transcript(texts)
            v, rule = decide(parse_criterion(texts), turns, facts)
            data = v.model_dump()
        elif SPEC_FIELDS <= fields:
            data, rule = _judge_spec(texts), "judge_spec_fallback"
        else:
            raise ProviderError(f"fake provider cannot produce schema {getattr(schema, '__name__', schema)!r}")
        try:
            parsed = schema.model_validate(data).model_dump()
        except ValidationError as e:
            raise ProviderError(f"fake provider output does not fit {schema.__name__}: {e}") from e
        usage = Usage()
        raw = {"provider": self.name, "model": self.model, "rule": rule, "stop_reason": "end_turn", "usage": usage.model_dump(),
               "text": json.dumps(parsed, ensure_ascii=False)[:20000]}
        return ProviderResult(parsed=parsed, usage=usage, raw=raw, model=self.model, provider=self.name,
                              latency_ms=int((time.perf_counter() - t0) * 1000), stop_reason="end_turn")

    def cost(self, usage: Usage) -> float:
        return 0.0
