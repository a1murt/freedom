"""Turn references in judge texts, shown 1-based.

The judge reads the transcript with 0-based labels (``[idx] speaker (ts) <segment>: text``, see
``cqa.ingest.normalize.pack_transcript``) and cites turns with those labels in explanations and N/A reasons:
«реплика [9]», «реплики 9–13», «реплики 9, 11 и 13», «реплики оператора 9 и 11», «turn 0». Everything people read
(API, results.json rationale, PDF) numbers turns from 1, like ``evidence_turns`` in results.json (``idx + 1``).

``display_turn_refs`` shifts the cited numbers at output time. Stored texts, few-shot excerpts and the raw model
responses in traces keep the judge's labels. Two forms are rewritten:

- bracketed labels, single or grouped: ``[9]`` → ``[10]``, ``[9–13]`` → ``[10–14]``, ``[4, 6]`` → ``[5, 7]``;
- numbers right after a turn word (RU, KK, EN), optionally with the speaker in between: «реплики 9, 11 и 13» →
  «реплики 10, 12 и 14», «реплики оператора 9 и 11» → «реплики оператора 10 и 12», «turn 0» → «turn 1».

Everything else is left alone:

- quoted text — «…», “…”, „…“, "…" — is copied verbatim: the judge quotes the transcript («Please turn 2FA on»);
- a number glued to a letter or digit is not a turn label (2FA, 3x, 10к, 12345), nor is a time or a decimal (12:30, 12.5);
- a count, duration, date, score or amount after the turn word: «сообщение 3 раза», «на сообщение 15 минут спустя»,
  «message 2 minutes later», «сообщение 5 марта», «сообщение 5%», «сообщения бота 24/7»;
- with ``max_index`` (the dialog's last turn index): any number beyond it, which cannot be a turn of this dialog
  («сообщение системы 404»);
- numbers anywhere else (scores, counts, times, money).

If the packer ever switches to 1-based labels, set ``JUDGE_TURN_LABEL_BASE = 1`` and nothing is shifted.
"""
from __future__ import annotations

import re
from typing import Any

JUDGE_TURN_LABEL_BASE = 0  # labels the judge sees (pack_transcript prints Turn.idx)
DISPLAY_TURN_BASE = 1  # labels people see (results.json evidence_turns = idx + 1)

_NUM = r"(?:№\s*)?\d{1,4}(?!\d)"
# separators inside a list of turn numbers: ranges, commas, «и», «and», «және», «или», «or», «to», «до»
_SEP = r"(?:\s*(?:[-–—]|\.\.|,|;)\s*|\s+(?:и|или|and|or|to|до|және|немесе)\s+)"
_LIST = rf"{_NUM}(?:{_SEP}{_NUM})*"
_TURN_WORDS = r"реплик\w*|сообщени\w*|хабарлам\w*|turns?|messages?|utterances?"
# the speaker between the turn word and the numbers: «реплики оператора 9 и 11», «реплики клиента 33 и 41»
_SPEAKERS = r"оператора|клиента|клиентки|бота|системы|operator|client|customer|agent|bot"
# quoted transcript text, copied verbatim; a quote never contains its own opening mark, so an unclosed one quotes nothing
_QUOTE = r"«[^«»]*»|“[^“”]*”|„[^„“”]*[“”]|\"[^\"]*\""

_REFS = re.compile(
    rf"(?P<quote>{_QUOTE})"
    rf"|(?P<bracket>\[(?P<blist>{_LIST})\])"
    # the list is atomic: when its end is glued to a letter/digit («сообщения 2–3х») or is a time/decimal, nothing is
    # shifted (no backtracking to a shorter list that would shift only «2»)
    rf"|(?P<word>(?<!\w)(?:{_TURN_WORDS})(?!\w))(?P<gap>\s*(?:(?:{_SPEAKERS})\s+)?)(?P<wlist>(?>{_LIST}))(?!\w)(?![:.,]\d)(?!/)",
    re.IGNORECASE,
)
_DIGITS = re.compile(r"\d{1,4}")
# a count, duration, date, score or amount right after the numbers: «сообщение 3 раза», «сообщение 15 минут спустя»,
# «сообщение 5 марта». Exact word forms, so a real reference followed by a similar word is still shifted («в реплике 9
# разъяснил», «turn 3 minimizes», «turn 5 may»).
_UNIT_AFTER = re.compile(
    r"\s*(?:(?:раза?|минут\w*|мин|секунд\w*|сек|час(?:а|ов)?|суток|сутки|дн(?:я|ей)|день|год(?:а|у)?|лет|балл(?:а|ов|ы)?|тенге|тг"
    r"|января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря"
    r"|рет|сағат\w*|күн\w*|жыл\w*|қаңтар\w*|ақпан\w*|наурыз\w*|сәуір\w*|мамыр\w*|маусым\w*|шілде\w*|тамыз\w*|қыркүйек\w*"
    r"|қазан\w*|қараша\w*|желтоқсан\w*"
    r"|times?|minutes?|mins?|seconds?|secs?|hours?|hrs?|days?|years?|percent|tenge|january|february|march|april|june"
    r"|july|august|september|october|november|december)(?!\w)|г\.|[%₸$])",
    re.IGNORECASE,
)


def _shift_numbers(chunk: str, shift: int) -> str:
    return _DIGITS.sub(lambda m: str(max(int(m.group(0)) + shift, 0)), chunk)


def _beyond(chunk: str, max_index: int | None) -> bool:
    return max_index is not None and any(int(n) > max_index for n in _DIGITS.findall(chunk))


def display_turn_refs(text: Any, max_index: int | None = None) -> Any:
    """Judge text with turn references shifted to the 1-based display numbering. Non-strings are returned unchanged.
    `max_index`: the dialog's last turn index (turn_count - 1) when known; a list with a larger number is left alone."""
    if not isinstance(text, str) or not text:
        return text
    shift = DISPLAY_TURN_BASE - JUDGE_TURN_LABEL_BASE
    if shift == 0:
        return text

    def repl(m: re.Match[str]) -> str:
        if m.group("quote") is not None:  # quoted transcript text
            return m.group(0)
        if m.group("bracket") is not None:
            if _beyond(m.group("blist"), max_index):
                return m.group(0)
            return "[" + _shift_numbers(m.group("blist"), shift) + "]"
        if _UNIT_AFTER.match(m.string, m.end()):  # a count, duration, date or amount, not a turn label
            return m.group(0)
        if _beyond(m.group("wlist"), max_index):  # «сообщение системы 404»: not a turn of this dialog
            return m.group(0)
        return m.group("word") + m.group("gap") + _shift_numbers(m.group("wlist"), shift)

    return _REFS.sub(repl, text)


def display_turn_number(idx: Any) -> Any:
    """Turn.idx (0-based) → the number shown to people (1-based); None and non-integers pass through."""
    if isinstance(idx, bool) or not isinstance(idx, int):
        return idx
    return idx + 1
