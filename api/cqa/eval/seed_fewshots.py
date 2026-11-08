"""Seed few-shot examples for the judge from the gold set (eval/gold.json + eval/gold_rationale.json).

The judge shows up to three examples per criterion, matched by channel and language (cqa.judge.runner._load_fewshots): an
excerpt of the dialog and the verdict. For a cloud model they change little; for a local open model they are the main
accuracy lever, because the decision procedure alone leaves it guessing where the checklist is implicit (C2 N/A, C7 N/A,
the 15/20 boundary). Examples are built only from dialogs in the chosen split, so a run on the other split stays honest.

  uv run python -m cqa.eval.seed_fewshots --split tuned_on                  # measure on holdout afterwards
  uv run python -m cqa.eval.seed_fewshots --split all --replace              # before the control run
  DATABASE_URL=sqlite:///... uv run python -m cqa.eval.seed_fewshots ...      # any database

Per (criterion, channel, language) it keeps up to --per examples with distinct answers (yes / no / N/A, or distinct scale
steps), preferring unanimous, undisputed labels. Rows are inserted with source="gold"; --replace deletes earlier gold rows.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from ..config import API_DIR
from ..db import SessionLocal, init_db
from ..models import Conversation, FewshotExample

CODES = ["C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8"]
TITLES = {"C1": "Установление контакта", "C2": "Выявление потребности", "C3": "Идентификация", "C4": "Корректная и полная консультация",
          "C5": "Эмпатия и клиентоориентированность", "C6": "Управление диалогом", "C7": "Завершение", "C8": "Грубые ошибки"}
BARE_FAREWELL_EXPLANATION = ("Завершение — N/A. Оператор попрощался после решённого вопроса, но не подвёл итог и не предложил дальнейшую "
                             "помощь; по ответу организаторов такой случай не оценивается (флаг bare_farewell).")


def excerpt_for(conv: Conversation, turn_ids: list[int], limit: int = 600) -> str:
    by_idx = {t.idx: t for t in conv.turns}
    picked = [by_idx[i] for i in turn_ids if i in by_idx]
    if not picked:  # fall back to the first operator turns
        picked = [t for t in conv.turns if t.speaker == "operator"][:2]
    lines = []
    for t in sorted(picked, key=lambda t: t.idx):
        text = " ".join((t.text or "").split())
        lines.append(f"[{t.idx}] {t.speaker}: {text[:220]}")
    out = "\n".join(lines)
    return out[:limit]


def verdict_for(code: str, value: Any, reason: str, bare: bool) -> dict[str, Any]:
    v = "N/A" if value is None else str(value)
    if v == "N/A":
        expl = BARE_FAREWELL_EXPLANATION if (code == "C7" and bare) else reason
        return {"applicable": False, "answer": "", "na_reason": expl, "explanation": expl}
    return {"applicable": True, "answer": v, "explanation": reason}


def pick(cands: list[dict[str, Any]], per: int) -> list[dict[str, Any]]:
    """Up to `per` examples with distinct answers, best-supported first; fill with the best remaining ones."""
    rank = {"unanimous": 0, "unanimous_of_2": 1, "reconciled": 2, "team_decision": 2, "arbitration_changed": 3}
    cands = sorted(cands, key=lambda c: (c["disputed"], rank.get(c["source"], 4), -c["turns"]))
    out, seen = [], set()
    for c in cands:
        if c["value"] not in seen:
            out.append(c)
            seen.add(c["value"])
        if len(out) >= per:
            return out
    for c in cands:
        if c not in out:
            out.append(c)
        if len(out) >= per:
            break
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default=str(API_DIR / "eval" / "gold.json"))
    ap.add_argument("--rationale", default=str(API_DIR / "eval" / "gold_rationale.json"))
    ap.add_argument("--split", default="tuned_on", help="dialogs the examples may come from: a split name or 'all'")
    ap.add_argument("--per", type=int, default=3, help="examples per criterion, channel and language")
    ap.add_argument("--replace", action="store_true", help="delete earlier gold-sourced examples first")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    gold = json.loads(Path(args.gold).read_text(encoding="utf-8"))
    rat = json.loads(Path(args.rationale).read_text(encoding="utf-8"))
    ids = list(gold["labels"]) if args.split == "all" else list(gold["split"][args.split])
    init_db()
    db = SessionLocal()
    try:
        convs = {c.id: c for c in db.execute(select(Conversation).where(Conversation.id.in_(ids)).options(selectinload(Conversation.turns))).scalars()}
        missing = [i for i in ids if i not in convs]
        if missing:
            print(f"not in this database, skipped: {', '.join(missing)}")
        groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
        for cid, conv in convs.items():
            row, r = gold["labels"][cid], rat.get(cid, {})
            for code in CODES:
                value = row.get(code)
                info = r.get(code, {})
                groups[(code, conv.channel, conv.language)].append({
                    "cid": cid, "value": "N/A" if value is None else str(value), "reason": info.get("reason") or "",
                    "evidence": info.get("evidence") or [], "source": info.get("source") or "", "turns": len(conv.turns),
                    "disputed": 1 if code in (row.get("_alt") or {}) else 0, "bare": bool(row.get("_c7_bare_farewell")) and code == "C7"})
        if args.replace and not args.dry_run:
            n = db.execute(delete(FewshotExample).where(FewshotExample.source == "gold")).rowcount
            print(f"deleted {n} earlier gold examples")
        added = 0
        for (code, channel, language), cands in sorted(groups.items()):
            for c in pick(cands, args.per):
                ex = FewshotExample(criterion_code=code, language=language, channel=channel, source="gold",
                                    transcript_excerpt=excerpt_for(convs[c["cid"]], c["evidence"]),
                                    verdict=verdict_for(code, c["value"], c["reason"], c["bare"]) | {"dialog": c["cid"]})
                added += 1
                if args.dry_run:
                    print(f"{code} {channel:10} {language} {c['cid']:11} -> {c['value']:4} ({c['source']})")
                else:
                    db.add(ex)
        if not args.dry_run:
            db.commit()
        print(f"{'would add' if args.dry_run else 'added'} {added} examples from {len(convs)} dialogs (split {args.split})")
    finally:
        db.close()


if __name__ == "__main__":
    main()
