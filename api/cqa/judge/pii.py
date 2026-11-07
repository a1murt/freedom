"""PII masking applied to transcript text before it leaves for a cloud provider.
Names are kept (needed for criterion 1); numbers and addresses that identify a person are replaced."""
from __future__ import annotations

import re

PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("EMAIL", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("IBAN", re.compile(r"\bKZ\d{2}[A-Z0-9]{16}\b", re.I)),
    ("CARD", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
    ("IIN", re.compile(r"\b\d{12}\b")),
    ("PHONE", re.compile(r"(?<!\d)(?:\+7|8|7)[\s(-]*\d{3}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}(?!\d)")),
]


def mask_text(text: str) -> tuple[str, dict[str, str]]:
    """Return masked text and a map placeholder -> original."""
    mapping: dict[str, str] = {}
    counters: dict[str, int] = {}
    out = text
    for kind, pat in PATTERNS:
        def repl(m: re.Match[str]) -> str:
            counters[kind] = counters.get(kind, 0) + 1
            key = f"[{kind}_{counters[kind]}]"
            mapping[key] = m.group(0)
            return key
        out = pat.sub(repl, out)
    return out, mapping


def mask_turns(turns: list[dict]) -> tuple[list[dict], dict[str, str]]:
    all_map: dict[str, str] = {}
    masked = []
    for t in turns:
        mt, m = mask_text(t["text"])
        all_map.update(m)
        masked.append({**t, "text": mt})
    return masked, all_map
