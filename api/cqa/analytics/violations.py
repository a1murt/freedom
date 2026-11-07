"""Gross violations (criterion C8 / export id "critical"): which dialogs, which type, why.

The checklist defines three kinds of gross error. The judge's C8 explanation and flags describe which one it
found; this module classifies them so the UI can list and filter violations by type (organizer live ask:
"show all dialogs with gross errors and explain why they were classified so").
"""

from __future__ import annotations

import re
from bisect import bisect_right
from datetime import UTC
from typing import Any

from ..turnrefs import display_turn_refs

VIOLATION_TYPES: dict[str, dict[str, str]] = {
    "insult": {"ru": "Оскорбление клиента", "en": "Insulting the client", "kk": "Клиентті қорлау"},
    "paid_bypass": {"ru": "Платная услуга в обход компании", "en": "Paid service bypassing the company", "kk": "Компанияны айналып өтетін ақылы қызмет"},
    "disclosure": {"ru": "Раскрытие конфиденциальной информации", "en": "Disclosure of confidential information", "kk": "Құпия ақпаратты жария ету"},
    "other": {"ru": "Иное грубое нарушение", "en": "Other gross violation", "kk": "Басқа өрескел бұзушылық"},
}

# «Почему так классифицировано» (GV-02): the checklist's rule for each type, shown next to the judge's explanation.
RULES: dict[str, dict[str, str]] = {
    "insult": {"ru": "Грубость, сарказм или унижение клиента в сообщениях оператора.",
               "en": "Rudeness, sarcasm or humiliation of the client in the operator's messages.",
               "kk": "Оператор хабарламаларындағы дөрекілік, кекесін немесе клиентті кемсіту."},
    "paid_bypass": {"ru": "Предложение платной услуги или перевода денег в обход компании (личная карта, личный номер, частная консультация).",
                    "en": "Offering a paid service or a money transfer that bypasses the company (personal card, personal number, private advice).",
                    "kk": "Компанияны айналып өтетін ақылы қызмет немесе ақша аударымын ұсыну (жеке карта, жеке нөмір, жеке кеңес)."},
    "disclosure": {"ru": "Раскрытие данных счёта или клиента без подтверждения личности.",
                   "en": "Disclosure of account or client data without identity verification.",
                   "kk": "Жеке басын растамай шот немесе клиент деректерін жария ету."},
    "other": {"ru": "Иное нарушение, отмеченное моделью как грубое.", "en": "Another violation the judge marked as gross.",
              "kk": "Модель өрескел деп белгілеген басқа бұзушылық."},
}

_PATTERNS: dict[str, re.Pattern[str]] = {
    # «rude» needs a word start: «crude oil», «prudent» and «intrude» are not insults
    "insult": re.compile(r"оскорб|унижа|униз|insult|сарказм|читать\s+умеете|\brude|грубост[ьи]\s+в\s+адрес|қорла", re.I),
    "paid_bypass": re.compile(
        r"(?<!бес)платн|за\s+плату|в\s+обход|на\s+(?:личн\w*\s+)?карт|личн\w*\s+(?:номер|телефон|канал|карт)|частн\w*\s+(?:порядк|консульт)|"
        r"paid|private\s+advice|personal\s+(?:number|phone|card)|bypass|ақылы|жеке\s+нөмір",
        re.I,
    ),  # «бесплатная консультация» is not a paid service (GV-06)
    "disclosure": re.compile(
        r"раскры|конфиденц|без\s+(?:подтвержд|идентифик|провер)|до\s+идентифик|disclos|confidential|without\s+(?:identification|verification)|"
        r"жария|растаусыз",
        re.I,
    ),
}


# Negation guard. A keyword inside a negated predicate is not evidence of that violation type. The judge lists the types it
# did not find in negated form, and a negated predicate covers its whole object phrase, objects joined by «или» / «и»
# included: «не оскорблял клиента, не предлагал личных каналов или платных услуг в обход компании и не раскрывал данные
# счёта». The scope ends where an affirmative predicate starts («…, не предлагал платных услуг и раскрыл баланс»). A noun
# put before its predicate is negated by that predicate («оскорблений не было», «предложений платных услуг нет»,
# «оскорблений и предложений платных услуг оператор не допустил»). Disclosure's own «без подтверждения» / «without
# verification» is a positive signal unless its own predicate is negated («не раскрывал данные счёта без идентификации»).
_CLAUSE_SPLIT = re.compile(r"[.;!?\n«»“”„\"]|—|,\s*(?:но|однако|а|but|however)\b", re.I)  # quoted text is a clause of its own
_SEGMENT_SPLIT = re.compile(r",|\s(?:и|или|либо|and|or|nor)(?=\s)", re.I)  # coordinated objects and predicates
_WORD = re.compile(r"[^\W_]+(?:['’][^\W_]+)?")  # «didn't» stays one word
_NEGATIONS = frozenset({"не", "ни", "нет", "no", "not", "never", "nor", "none", "nothing", "жоқ", "емес"})
_NOT_A_NEGATION_BEFORE = frozenset({"только", "лишь", "only"})  # «не только …, но и …», «not only»
_LOCAL_NEGATIONS = frozenset({"без", "without"})  # negate the noun phrase right after them: «без оскорблений»
_ABSENCE = re.compile(r"(?:отсутству|absent)", re.I)  # «оскорбления отсутствуют»
_SUBORDINATORS = frozenset({"который", "которая", "которое", "которые", "которого", "которой", "которую", "которых", "что", "чтобы", "когда",
                            "если", "хотя", "потому", "поскольку", "which", "who", "that", "when", "if", "although", "because"})
_RU_PAST = re.compile(r"([^\W\d_]+[аяеёиоуыэю]л)(?:а|о|и|ся|ась|ось|ись)?", re.I)  # предложил, раскрыла, раскрывались
_RU_GERUND = re.compile(r"[^\W\d_]{2,}[аяиы]в(?:ши|шись)?", re.I)  # предложив, указав, обвинив
_RU_PRESENT = re.compile(r"[^\W\d_]{3,}(?:ет|ит|ют|ят|ут|ат|ется|ится|ются|ятся|утся|атся)", re.I)  # оскорбляет (keyword words only)
_EN_PAST = re.compile(r"[a-z]{3,}ed", re.I)
_EN_VERBS = frozenset({"told", "gave", "sent", "said", "made", "wrote", "took", "sold", "was", "were", "is", "are", "has", "had"})
_KK_NEGATED_VERB = re.compile(r"(?:ма|ме|ба|бе|па|пе)(?:ды|ді|ған|ген|йды|йді|йтын|йтін)$", re.I)  # қорламады
# nouns, adverbs and conjunctions that look like a past tense or a gerund (also with a case ending: «канала», «детали»)
_NOT_VERBS = frozenset({
    "или", "ли", "если", "мало", "около", "канал", "сигнал", "капитал", "материал", "журнал", "персонал", "терминал", "портал",
    "потенциал", "интервал", "финал", "идеал", "оригинал", "профессионал", "дел", "тел", "детал", "модел", "недел", "цел", "панел",
    "профил", "стол", "зал", "мал", "окол", "состав", "устав", "архив", "актив", "мотив", "позитив", "негатив", "перерыв", "призыв",
    "отзыв", "срыв", "взрыв", "прорыв", "обрыв", "разрыв", "массив", "креатив", "залив", "рукав", "прав", "коллектив",
    "need", "speed", "seed", "feed", "bleed", "proceed", "indeed", "shed", "bed", "red", "hundred",
})
_FLAG_NEGATION = re.compile(r"(?:^|_)(?:not|no|non|none|nothing|never|without)(?:_|$)", re.I)
_POSITIVE_EVEN_IF_NEGATED = re.compile(r"без\s+(?:подтвержд|идентифик|провер)|without\s+(?:id\b|identi|verif)|растаусыз", re.I)


_RU_INFINITIVE = re.compile(r"[^\W\d_]{3,}(?:ть|ться|ти|тись|чь|чься)")
_INFINITIVE_LOOKALIKES = ("ость", "есть", "асть", "ости", "асти", "почти", "части", "сети", "пути")


def _is_negation(words: list[str], i: int) -> bool:
    w = words[i].lower()
    if w in ("не", "not") and i + 1 < len(words) and words[i + 1].lower() in _NOT_A_NEGATION_BEFORE:
        return False
    return w in _NEGATIONS or w.endswith(("n't", "n’t")) or bool(_ABSENCE.match(w))


def _is_predicate(words: list[str], i: int, *, keyword: bool = False) -> bool:
    """A verb that heads a predicate: past tense, gerund (RU), past tense and a few auxiliaries (EN); for the keyword
    itself also the present tense. A capitalised word inside the clause is a name («Павел», «Камила»), not a verb."""
    word = words[i]
    w = word.lower()
    if w in _NOT_VERBS or (i > 0 and word[:1].isupper()) or w.endswith("слав"):
        return False
    m = _RU_PAST.fullmatch(w)
    if m:
        return len(m.group(1)) >= 3 and m.group(1) not in _NOT_VERBS
    if _RU_GERUND.fullmatch(w) or w in _EN_VERBS or _EN_PAST.fullmatch(w):
        return True
    # infinitives head their own predicate («…не оформлять через компанию и перевести деньги ему на карту»), and so does
    # an English «to <verb>» («…not to use the app and to send money»): otherwise the backward scan walks across «и» to
    # another predicate's «не». Nouns in -ость/-асть/-ости… are not infinitives.
    if _RU_INFINITIVE.fullmatch(w) and not w.endswith(_INFINITIVE_LOOKALIKES):
        return True
    if i > 0 and words[i - 1].lower() == "to" and w.isascii() and w.isalpha() and len(w) > 2:
        return True
    return keyword and bool(_RU_PRESENT.fullmatch(w))


def _scan_back(words: list[str], lo: int, hi: int) -> bool | None:
    """Nearest predicate before ``hi`` (down to ``lo``): True negated, False affirmative, None when there is none."""
    for j in range(hi - 1, lo - 1, -1):
        if words[j].lower() in _SUBORDINATORS:  # «…не скрывал, что консультация платная»: the «не» belongs to another clause
            return None
        if _is_negation(words, j):
            return True
        if _is_predicate(words, j):
            return j > 0 and _is_negation(words, j - 1)
    return None


def _scan_forward(words: list[str], lo: int, hi: int, *, verbs_decide: bool) -> bool | None:
    """First predicate from ``lo`` on (up to ``hi``, stopping at a subordinate clause): True when it is negated. An
    affirmative one decides only inside the keyword's own segment («платную консультацию предложил»); further on it starts
    a new predicate with its own object («…, личных каналов и раскрыл баланс»)."""
    for j in range(lo, hi):
        if words[j].lower() in _SUBORDINATORS:
            return None
        if _is_negation(words, j):
            return True
        if _is_predicate(words, j):
            return False if verbs_decide else None
    return None


def _negated(clause: str, start: int) -> bool:
    """Whether the keyword match at ``start`` sits in a negated predicate of the clause (see the comment above)."""
    tokens = list(_WORD.finditer(clause))
    k = next((i for i, t in enumerate(tokens) if t.end() > start), None)
    if k is None:
        return False
    words = [t.group() for t in tokens]
    if _KK_NEGATED_VERB.search(words[k]):
        return True
    if _is_predicate(words, k, keyword=True):  # «раскрыл», «не раскрывал», «disclosed»: only its own negation counts
        return k > 0 and _is_negation(words, k - 1)
    for j in range(k - 1, max(-1, k - 4), -1):  # «без оскорблений», «без каких-либо оскорблений», «without any insult»
        if words[j].lower() in _LOCAL_NEGATIONS:
            return True
        if _is_predicate(words, j) or _is_negation(words, j):
            break
    cuts = [m.end() for m in _SEGMENT_SPLIT.finditer(clause)]
    segment = [bisect_right(cuts, t.start()) for t in tokens]
    lo = segment.index(segment[k])
    hi = len(segment) - segment[::-1].index(segment[k])
    for decision in (_scan_back(words, lo, k), _scan_forward(words, k + 1, hi, verbs_decide=True),
                     _scan_forward(words, hi, len(words), verbs_decide=False), _scan_back(words, 0, lo)):
        if decision is not None:
            return decision
    return False


def _affirmed(pattern: re.Pattern[str], text: str) -> bool:
    """True when the pattern occurs in at least one clause where it is not negated."""
    for clause in _CLAUSE_SPLIT.split(text or ""):
        for m in pattern.finditer(clause):
            if not _negated(clause, m.start()):
                return True
    return False


def _judge_text(explanation: str | None, flags: list[str] | None) -> str:
    """The judge's explanation plus its flags read as words, one clause per flag. A flag that states an absence
    («curt_tone_not_insult», «rudeness_without_insult», «no_account_data_disclosed») is left out; «…_without_identification»
    names the disclosure itself and is kept."""
    clauses = [explanation or ""]
    for flag in flags or []:
        words = str(flag).replace("_", " ").strip()
        if _FLAG_NEGATION.search(str(flag)) and not _POSITIVE_EVEN_IF_NEGATED.search(words):
            continue
        clauses.append(words)
    return ". ".join(c for c in clauses if c)


def classify(explanation: str | None, flags: list[str] | None = None, evidence_quotes: list[str] | None = None, c3_answer: Any = None) -> list[str]:
    """Return one or more violation types for a critical = Да verdict (ordered by checklist order).

    The judge's explanation and flags decide; a type named only inside a negated predicate does not count. Evidence quotes
    are the operator's own words, so they are matched without the negation guard (an insult quote is an insult): pass
    only the operator's quotes. Without a type, identification (C3) = «Нет» means disclosure, else «other»."""
    text = _judge_text(explanation, flags)
    quotes_text = " ".join(evidence_quotes or [])
    found = [k for k in ("insult", "paid_bypass", "disclosure")
             if _affirmed(_PATTERNS[k], text) or (k == "insult" and _PATTERNS[k].search(quotes_text))]
    if not found and str(c3_answer).lower() == "no":
        found.append("disclosure")  # account data shown without identification is the typical disclosure case
    return found or ["other"]


DEFAULT_CRITICAL: list[tuple[str, str]] = [("C8", "yes")]


def critical_codes(db, version_ids) -> dict[int, list[tuple[str, str]]]:
    """checklist_version_id → [(criterion code, critical value)] for the version's gross-error criteria, in checklist order."""
    from sqlalchemy import select

    from ..models import Criterion

    ids = [v for v in version_ids if v is not None]
    out: dict[int, list[tuple[str, str]]] = {}
    if not ids:
        return out
    q = select(Criterion).where(Criterion.checklist_version_id.in_(ids), Criterion.is_critical.is_(True)).order_by(Criterion.order)
    for c in db.execute(q).scalars():
        out.setdefault(c.checklist_version_id, []).append((c.code, str(c.critical_value or "yes").lower()))
    return out


def label(kind: str, lang: str = "ru") -> str:
    return VIOLATION_TYPES.get(kind, VIOLATION_TYPES["other"]).get(lang, VIOLATION_TYPES[kind]["ru"] if kind in VIOLATION_TYPES else "")


def _final(row: Any) -> Any:
    """The reviewer's answer when a decision exists, else the AI answer ("N/A" when the judge found the criterion not applicable)."""
    return row.final_answer if row.final_answer is not None else (row.answer if row.applicable else "N/A")


def _critical_rows(rows: dict[str, Any], critical: list[tuple[str, str]]) -> list[tuple[Any, str]]:
    """(row, critical value) of every gross-error criterion the evaluation has, in checklist order."""
    return [(rows[code], value) for code, value in critical if code in rows]


def _critical_hit(crit_rows: list[tuple[Any, str]], include_reviewed_out: bool) -> tuple[Any, bool, Any, bool] | None:
    """(row, ai_yes, final, final_yes) of the gross-error criterion that lists the dialog, or None.

    Every critical criterion counts, as in the scoring engine's critical flag: the first one still positive after review
    wins, so a dialog is not shown as overturned while another gross error stands; with include_reviewed_out the first one
    the AI marked positive comes next."""
    states = []
    for row, value in crit_rows:
        ai_yes = bool(row.applicable) and str(row.answer).lower() == value
        final = row.final_answer if row.final_answer is not None else row.answer
        states.append((row, ai_yes, final, str(final).lower() == value))
    hit = next((s for s in states if s[3]), None)
    if hit is None and include_reviewed_out:
        hit = next((s for s in states if s[1]), None)
    return hit


def violations(db, *, checklist_version_id: int | None = None, kind: str | None = None, language: str | None = None,
               channel: str | None = None, operator: str | None = None, include_reviewed_out: bool = False,
               dataset_id: int | None = None, since: Any = None, until: Any = None) -> dict[str, Any]:
    """Latest evaluation per conversation whose gross-error criterion is positive (after reviewer decisions unless
    include_reviewed_out). The gross-error criteria are the version's ``is_critical`` criteria (C8 in the organizer
    checklist) with their ``critical_value`` (default "yes"); a dialog is listed once, under the first one that is positive.

    The type comes from the judge's text only when the judge itself gave the critical answer: after a reviewer's «Нет → Да»
    the text explains why there is no violation, so the item falls back to identification (C3 = «Нет» after review →
    disclosure) or «other» (needs manual typing). ``operator="—"`` is the unnamed-operator group of GET /api/operators;
    ``since`` / ``until`` may carry any UTC offset."""
    from .service import UNKNOWN_OPERATOR, latest_evaluations

    unnamed = operator is not None and operator.strip() == UNKNOWN_OPERATOR
    items: list[dict[str, Any]] = []
    counts = {k: 0 for k in VIOLATION_TYPES}
    evaluations = list(latest_evaluations(db, checklist_version_id))
    critical = critical_codes(db, {ev.checklist_version_id for ev in evaluations})
    for ev in evaluations:
        conv = ev.conversation
        if language and conv.language != language.upper():
            continue
        if channel and conv.channel != channel:
            continue
        if operator:
            if unnamed:
                if (conv.operator_name or "").strip():
                    continue
            elif conv.operator_name != operator:
                continue
        if dataset_id is not None and conv.dataset_id != dataset_id:
            continue
        done = ev.completed_at
        if since is not None and (done is None or _naive(done) < _naive(since)):
            continue
        if until is not None and (done is None or _naive(done) > _naive(until)):
            continue
        rows = {r.code: r for r in ev.criteria}
        hit = _critical_hit(_critical_rows(rows, critical.get(ev.checklist_version_id) or DEFAULT_CRITICAL), include_reviewed_out)
        if hit is None:
            continue
        c8, ai_yes, final, final_yes = hit
        turns = {t.idx: t for t in conv.turns}
        c3 = rows.get("C3")
        c3_final = _final(c3) if c3 is not None else None
        explanation, flags, quotes = None, None, []
        if ai_yes:  # the judge's own critical answer: its explanation, flags and the operator's quoted words name the type
            explanation, flags = c8.explanation, c8.flags
            quotes = [e.get("quote") or "" for e in c8.evidence or []
                      if isinstance(e, dict) and _speaker(e, turns) == "operator"]
        kinds = classify(explanation, flags, quotes, c3_final)
        judge_text = _judge_text(explanation, flags)
        classified_by = ("explanation" if any(_affirmed(_PATTERNS[k], judge_text) for k in kinds if k in _PATTERNS)
                         else "quote" if any(k in _PATTERNS and _PATTERNS[k].search(" ".join(quotes)) for k in kinds)
                         else "identification" if kinds == ["disclosure"] else "default")
        if kind and kind not in kinds:
            continue
        for k in kinds:
            counts[k] += 1
        evidence = []
        for e in c8.evidence or []:
            if not isinstance(e, dict):
                continue
            t = turns.get(e.get("turn_idx"))
            evidence.append({"turn_idx": e.get("turn_idx"), "speaker": _speaker(e, turns), "quote": e.get("quote"),
                             "timestamp": t.timestamp_raw if t else None, "offset_seconds": t.offset_seconds if t else None})
        items.append({
            "conversation_id": conv.id, "channel": conv.channel, "language": conv.language, "operator_name": conv.operator_name,
            "evaluation_id": ev.id, "evaluation_criterion_id": c8.id, "total_score": ev.total_score, "final_total_score": ev.final_total_score,
            "types": kinds, "type_labels": [label(k) for k in kinds], "explanation": display_turn_refs(c8.explanation, (conv.turn_count - 1) if conv.turn_count else None), "confidence": c8.confidence,
            "rules": [{"code": k, **RULES.get(k, RULES["other"])} for k in kinds], "classified_by": classified_by,
            "criterion_code": c8.code,
            "open_appeal_id": next((a.id for a in sorted(c8.appeals, key=lambda a: a.id) if a.status == "open"), None),
            "decision": (lambda d: {"action": d.action, "new_answer": d.new_answer, "reason": d.reason, "reviewer": d.reviewer_label,
                                    "decided_at": d.created_at.isoformat() if d.created_at else None} if d else None)(
                next((a.decision for a in sorted(c8.appeals, key=lambda a: a.id, reverse=True) if a.decision is not None), None)),
            "related": {"C3": {"answer": c3_final}} if c3 else {},
            "evidence": evidence, "review_state": c8.review_state, "ai_answer": c8.answer, "final_answer": final,
            "confirmed_by_reviewer": c8.review_state == "confirmed", "overturned_by_reviewer": ai_yes and not final_yes,
            "evaluated_at": ev.completed_at.isoformat() if ev.completed_at else None,
        })
    items.sort(key=lambda x: (x["final_total_score"] is None, x["final_total_score"] if x["final_total_score"] is not None else 0))
    return {"total": len(items), "counts": counts, "types": [{"code": k, **v} for k, v in VIOLATION_TYPES.items()], "items": items}


def _speaker(evidence: dict[str, Any], turns: dict[int, Any]) -> str | None:
    """Speaker of an evidence item: as the judge recorded it, else the speaker of the cited turn."""
    t = turns.get(evidence.get("turn_idx"))
    return evidence.get("speaker") or (t.speaker if t else None)


NEAR_MISS_CONFIDENCE = 0.8


def near_misses(db, *, checklist_version_id: int | None = None, language: str | None = None, channel: str | None = None) -> dict[str, Any]:
    """«Проверено: не нарушение» (GV-08): dialogs whose gross-error criterion is negative although something points the
    other way, each with the judge's explanation of why it is not a violation. Signals:
    - ``identification_failed``: identification (C3) is «Нет» (account data may have been given out before identification);
    - ``low_confidence``: the judge's confidence in «Нет» is below 0.8;
    - ``split_votes``: ensemble members disagreed on the gross-error criterion;
    - ``steering``: the dialog contains text addressed to the evaluator.
    A reviewer-confirmed «Нет» is listed with ``review_state`` so the register shows it was checked by a person. A dialog is
    listed only when every gross-error criterion has a verdict and none is positive: a criterion the judge failed on (no
    answer, no reviewer decision) was never checked, so nothing was cleared."""
    from .service import latest_evaluations

    evaluations = list(latest_evaluations(db, checklist_version_id))
    critical = critical_codes(db, {ev.checklist_version_id for ev in evaluations})
    items: list[dict[str, Any]] = []
    for ev in evaluations:
        conv = ev.conversation
        if language and conv.language != language.upper():
            continue
        if channel and conv.channel != channel:
            continue
        rows = {r.code: r for r in ev.criteria}
        crit_rows = _critical_rows(rows, critical.get(ev.checklist_version_id) or DEFAULT_CRITICAL)
        if not crit_rows:
            continue
        finals = [_final(r) for r, _ in crit_rows]
        if any(f is None for f in finals):
            continue  # no verdict (judge error row, no reviewer decision): not checked, so not a near miss
        if any(str(f).lower() == value for f, (_, value) in zip(finals, crit_rows)):
            continue  # a violation: listed by violations()
        (row, _), final = crit_rows[0], finals[0]
        signals = []
        c3 = rows.get("C3")
        c3_final = _final(c3) if c3 is not None else None
        if str(c3_final).lower() == "no":
            signals.append("identification_failed")
        if row.confidence is not None and row.confidence < NEAR_MISS_CONFIDENCE:
            signals.append("low_confidence")
        if len({str(v.get("answer")) for v in (row.votes or []) if isinstance(v, dict) and v.get("answer") is not None}) > 1:
            signals.append("split_votes")
        # no keyword signal: «Нет» explanations list every violation type in negated form by design (pure noise)
        named: list[str] = []
        if (conv.meta or {}).get("steering_turns"):
            signals.append("steering")
        if not signals:
            continue
        items.append({"conversation_id": conv.id, "language": conv.language, "channel": conv.channel, "operator_name": conv.operator_name,
                      "evaluation_id": ev.id, "evaluation_criterion_id": row.id, "criterion_code": row.code,
                      "final_answer": final, "confidence": row.confidence, "signals": signals, "named_types": named,
                      "identification": c3_final, "explanation": display_turn_refs(row.explanation, (conv.turn_count - 1) if conv.turn_count else None),
                      "review_state": row.review_state, "final_total_score": ev.final_total_score})
    items.sort(key=lambda i: (-len(i["signals"]), i["confidence"] if i["confidence"] is not None else 1.0, i["conversation_id"]))
    counts: dict[str, int] = {}
    for i in items:
        for sig in i["signals"]:
            counts[sig] = counts.get(sig, 0) + 1
    return {"total": len(items), "signal_counts": counts, "items": items}


def _naive(dt):
    """Stored (naive UTC) and requested datetimes on one footing: an aware value is converted to UTC first (10:00+05:00 is
    05:00 UTC), a naive one is taken as UTC already."""
    return dt.astimezone(UTC).replace(tzinfo=None) if getattr(dt, "tzinfo", None) is not None else dt


def operators(db, *, checklist_version_id: int | None = None, language: str | None = None, channel: str | None = None,
              dataset_id: int | None = None) -> list[dict[str, Any]]:
    """Operators found in the data with their evaluation stats (operator portal and coaching); optional scope filters.
    With a checklist version, ``open_appeals`` counts only appeals on that version's evaluations, like the other stats."""
    from sqlalchemy import select

    from ..models import Appeal, Conversation, Evaluation, EvaluationCriterion
    from .service import latest_evaluations

    by_op: dict[str, dict[str, Any]] = {}
    def in_scope(conv) -> bool:
        return ((not language or conv.language == language.upper()) and (not channel or conv.channel == channel)
                and (dataset_id is None or conv.dataset_id == dataset_id))

    for conv in db.execute(select(Conversation)).scalars():
        if not in_scope(conv):
            continue
        name = conv.operator_name or "—"
        o = by_op.setdefault(name, {"operator_name": name, "conversations": 0, "evaluated": 0, "totals": [], "critical_count": 0,
                                    "languages": set(), "channels": set(), "open_appeals": 0, "last_evaluated_at": None})
        o["conversations"] += 1
        o["languages"].add(conv.language)
        o["channels"].add(conv.channel)
    for ev in latest_evaluations(db, checklist_version_id):
        if not in_scope(ev.conversation):
            continue
        name = ev.conversation.operator_name or "—"
        o = by_op.get(name)
        if o is None:
            continue
        o["evaluated"] += 1
        if ev.final_total_score is not None:
            o["totals"].append(ev.final_total_score)
        if ev.critical_error:
            o["critical_count"] += 1
        ts = ev.completed_at.isoformat() if ev.completed_at else None
        if ts and (o["last_evaluated_at"] is None or ts > o["last_evaluated_at"]):
            o["last_evaluated_at"] = ts
    open_stmt = select(Appeal).where(Appeal.status == "open")
    if checklist_version_id is not None:  # same scope as overview._appeals
        open_stmt = (open_stmt.join(EvaluationCriterion, Appeal.evaluation_criterion_id == EvaluationCriterion.id)
                     .join(Evaluation, EvaluationCriterion.evaluation_id == Evaluation.id)
                     .where(Evaluation.checklist_version_id == checklist_version_id))
    open_rows = db.execute(open_stmt).scalars().all()
    for ap in open_rows:
        ec: EvaluationCriterion = ap.evaluation_criterion
        if not in_scope(ec.evaluation.conversation):
            continue
        name = ec.evaluation.conversation.operator_name or "—"
        if name in by_op:
            by_op[name]["open_appeals"] += 1
    out = []
    for o in by_op.values():
        totals = o.pop("totals")
        o["mean_total"] = round(sum(totals) / len(totals), 2) if totals else None
        o["languages"] = sorted(o["languages"])
        o["channels"] = sorted(o["channels"])
        out.append(o)
    out.sort(key=lambda x: (x["operator_name"] == "—", x["operator_name"]))
    return out
