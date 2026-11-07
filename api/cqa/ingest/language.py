"""Language of a pasted or uploaded dialog when the client does not say (CONV-04, CONV-15): RU, KZ or EN.

Script and letters, not a model: Kazakh-specific Cyrillic letters (ә ғ қ ң ө ұ ү һ і) mark KZ, other Cyrillic marks RU,
Latin marks EN. Bot/system turns are ignored (the IVR greets in every language); masks such as [Имя скрыто] are removed.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

KZ_LETTERS = set("әғқңөұүһіӘҒҚҢӨҰҮҺІ")
_MASK = re.compile(r"\[[^\]]{0,40}\]")
_CYR = re.compile(r"[А-Яа-яЁё" + "".join(KZ_LETTERS) + r"]")
_LAT = re.compile(r"[A-Za-z]")
_KZ_WORDS = re.compile(r"\b(сәлем|рахмет|сіз|бар|жоқ|қалай|көмек|бойынша|және|үшін|керек)\b", re.I)


def detect_language(turns: Iterable[Any], default: str = "RU") -> dict[str, Any]:
    """{"language": "RU|KZ|EN", "confidence": 0..1, "counts": {...}} from the client and operator turns."""
    text = []
    for t in turns:
        speaker = (t.get("speaker") if isinstance(t, dict) else getattr(t, "speaker", None)) or ""
        if speaker in ("bot", "system"):
            continue
        body = (t.get("text") if isinstance(t, dict) else getattr(t, "text", None)) or ""
        text.append(_MASK.sub(" ", str(body)))
    joined = " ".join(text)
    kz = sum(1 for ch in joined if ch in KZ_LETTERS) + 3 * len(_KZ_WORDS.findall(joined))
    cyr = len(_CYR.findall(joined))
    lat = len(_LAT.findall(joined))
    letters = cyr + lat
    if letters < 8:
        return {"language": default, "confidence": 0.0, "counts": {"cyrillic": cyr, "latin": lat, "kazakh": kz}}
    if lat > cyr:
        lang, conf = "EN", lat / letters
    elif kz >= max(3, 0.01 * cyr):
        lang, conf = "KZ", min(1.0, 0.6 + kz / max(cyr, 1) * 10)
    else:
        lang, conf = "RU", cyr / letters
    return {"language": lang, "confidence": round(min(conf, 1.0), 2), "counts": {"cyrillic": cyr, "latin": lat, "kazakh": kz}}
