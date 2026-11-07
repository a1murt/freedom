"""Spreadsheet-safe CSV cells, one neutraliser for every CSV export (evaluations, registers, audit log).

A text cell that a spreadsheet would read as a formula (it starts with ``=``, ``+``, ``-``, ``@``, a tab or a carriage
return) gets a leading apostrophe, so Excel shows it as text and never runs it (CSV injection, OWASP): operator names,
dialog ids, comments, quotes and actor names come from people, uploads and request headers.

The first character is not enough. Excel in ru-RU and kk-KZ, this project's audience, splits a double-clicked .csv on
``;`` (its list separator), and there the quotes csv.writer puts around a middle cell are plain text, so a ``;``, CR or
LF inside the text starts a new cell or row: ``Иван;=1+1;`` gives a ``=1+1`` cell, and so does ``Иван;"=1+1;`` (the
doubled quote reads as an empty quoted part). A tab splits cells the same way when rows are pasted into a sheet. So an
apostrophe also goes right after every ``;``, tab, CR or LF that a formula character follows, directly or behind double
quotes: ``Иван;'=1+1;``. Only strings are touched: ints and floats pass unchanged, and the evaluations CSV passes only
its text columns, so its rendered numbers stay numbers.
"""
from __future__ import annotations

import re
from typing import Any

FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
# where a reader may end a cell or a row inside the text: ";" (ru/kk Excel), a tab (pasted rows), CR, LF
CELL_BREAKS = (";", "\t", "\r", "\n")


def _one_of(chars: tuple[str, ...]) -> str:
    return "[" + re.escape("".join(chars)) + "]"


# a break, then a formula character, maybe behind double quotes: one unnested quantifier, so linear time
_BREAK_BEFORE_FORMULA = re.compile(f'({_one_of(CELL_BREAKS)})(?="*{_one_of(FORMULA_START)})')


def safe_cell(value: Any) -> Any:
    """A text cell made inert: an apostrophe in front of a formula-like start and right after every in-text cell break
    that a formula character follows; any other value unchanged."""
    if not isinstance(value, str):
        return value
    value = _BREAK_BEFORE_FORMULA.sub(r"\1'", value)
    return "'" + value if value.startswith(FORMULA_START) else value
