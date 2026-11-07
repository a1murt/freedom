"""Reference facts for criterion C4: the DEV claims ledger (authoritative) selected by topic keywords."""
from __future__ import annotations

import json
from functools import lru_cache

from ..config import settings


@lru_cache(maxsize=1)
def _ledger() -> list[dict]:
    p = settings.seeds_dir / "claims_ledger.json"
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8")).get("ledger", [])


def reference_facts_for(transcript_text: str, criterion_code: str, limit: int = 12) -> list[str]:
    if criterion_code not in ("C4", "C8"):
        return []
    text = transcript_text.lower()
    scored = []
    for item in _ledger():
        hits = sum(1 for kw in item.get("topic", []) if kw.lower() in text)
        if hits:
            scored.append((hits, item["fact"]))
    scored.sort(key=lambda x: -x[0])
    facts = [f for _, f in scored[:limit]]
    if criterion_code == "C8":
        # compliance facts always included
        for item in _ledger():
            if any(k in item.get("topic", []) for k in ("пароль", "совет")) and item["fact"] not in facts:
                facts.append(item["fact"])
    return facts
