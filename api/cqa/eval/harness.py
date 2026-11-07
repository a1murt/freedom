"""Evaluation harness: compare stored verdicts (or a fresh run) with a gold set.

Usage:
  uv run python -m cqa.eval.harness --gold eval/gold.json                 # use latest stored evaluations
  uv run python -m cqa.eval.harness --gold eval/gold.json --run --provider anthropic --version 1
  uv run python -m cqa.eval.harness --gold eval/gold.json --split validation

Gold file format (eval/gold.json):
{
  "split": {"train": ["p1-ru-001", ...], "validation": ["p1-kz-002", ...]},
  "labels": {
    "p1-ru-029": {"C1": "yes", "C2": "N/A", "C3": "N/A", "C4": 20, "C5": "no", "C6": 7, "C7": "no", "C8": "yes", "total": 62.31}
  }
}
Metrics per criterion: n, exact match, MAE (scale criteria), quadratic-weighted kappa (scale), Cohen kappa (binary incl. N/A as a class),
N/A agreement; plus total-score MAE and cost per dialog. Results are appended to eval/runs.csv.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

from ..config import API_DIR
from ..db import SessionLocal, init_db
from ..models import Evaluation


def _norm(v: Any) -> str:
    if v is None:
        return "N/A"
    s = str(v).strip()
    if s.lower() in ("n/a", "na", "none", ""):
        return "N/A"
    if s.lower() in ("да", "yes", "true"):
        return "yes"
    if s.lower() in ("нет", "no", "false"):
        return "no"
    try:
        f = float(s)
        return str(int(f)) if f.is_integer() else str(f)
    except ValueError:
        return s


def cohen_kappa(pairs: list[tuple[str, str]]) -> float | None:
    if len(pairs) < 2:
        return None
    cats = sorted({a for a, _ in pairs} | {b for _, b in pairs})
    n = len(pairs)
    po = sum(1 for a, b in pairs if a == b) / n
    pe = sum((sum(1 for a, _ in pairs if a == c) / n) * (sum(1 for _, b in pairs if b == c) / n) for c in cats)
    if pe == 1:
        return 1.0
    return (po - pe) / (1 - pe)


def quadratic_weighted_kappa(pairs: list[tuple[float, float]], levels: list[float]) -> float | None:
    if len(pairs) < 2 or len(levels) < 2:
        return None
    idx = {v: i for i, v in enumerate(levels)}
    k = len(levels)
    o = [[0.0] * k for _ in range(k)]
    for a, b in pairs:
        if a in idx and b in idx:
            o[idx[a]][idx[b]] += 1
    n = sum(map(sum, o))
    if n == 0:
        return None
    ha = [sum(r) for r in o]
    hb = [sum(o[i][j] for i in range(k)) for j in range(k)]
    num = den = 0.0
    for i in range(k):
        for j in range(k):
            w = ((i - j) ** 2) / ((k - 1) ** 2)
            e = ha[i] * hb[j] / n
            num += w * o[i][j]
            den += w * e
    return None if den == 0 else 1 - num / den


def load_predictions(db, ids: list[str], version_id: int | None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for cid in ids:
        q = select(Evaluation).where(Evaluation.conversation_id == cid, Evaluation.status == "completed")
        if version_id:
            q = q.where(Evaluation.checklist_version_id == version_id)
        ev = db.execute(q.order_by(Evaluation.id.desc())).scalars().first()
        if ev is None:
            continue
        pred = {r.code: ("N/A" if not r.applicable else r.answer) for r in ev.criteria}
        pred["total"] = ev.total_score
        pred["_cost"] = ev.cost_usd
        pred["_model"] = ev.model
        pred["_prompt"] = ev.prompt_version
        out[cid] = pred
    return out


def score(gold: dict[str, dict[str, Any]], preds: dict[str, dict[str, Any]], scale_levels: dict[str, list[float]]) -> dict[str, Any]:
    per: dict[str, dict[str, Any]] = {}
    codes = sorted({c for g in gold.values() for c in g if c != "total"})
    for code in codes:
        pairs = [(_norm(gold[cid][code]), _norm(preds[cid].get(code))) for cid in gold if cid in preds and code in gold[cid]]
        if not pairs:
            continue
        exact = sum(1 for a, b in pairs if a == b) / len(pairs)
        na_pairs = [(a == "N/A", b == "N/A") for a, b in pairs]
        na_agree = sum(1 for a, b in na_pairs if a == b) / len(pairs)
        row: dict[str, Any] = {"n": len(pairs), "exact": round(exact, 3), "na_agree": round(na_agree, 3)}
        if code in scale_levels:
            nums = [(float(a), float(b)) for a, b in pairs if a != "N/A" and b != "N/A"]
            if nums:
                row["mae"] = round(sum(abs(a - b) for a, b in nums) / len(nums), 2)
                qwk = quadratic_weighted_kappa(nums, sorted(scale_levels[code]))
                row["qwk"] = round(qwk, 3) if qwk is not None else None
        else:
            k = cohen_kappa(pairs)
            row["kappa"] = round(k, 3) if k is not None else None
        per[code] = row
    totals = [(float(gold[cid]["total"]), float(preds[cid]["total"])) for cid in gold if cid in preds and gold[cid].get("total") is not None and preds[cid].get("total") is not None]
    total_mae = round(sum(abs(a - b) for a, b in totals) / len(totals), 2) if totals else None
    costs = [preds[cid]["_cost"] for cid in gold if cid in preds]
    return {"criteria": per, "total_mae": total_mae, "n_dialogs": len([c for c in gold if c in preds]),
            "cost_per_dialog": round(sum(costs) / len(costs), 4) if costs else None}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default=str(API_DIR / "eval" / "gold.json"))
    ap.add_argument("--split", default=None, help="train | validation | all")
    ap.add_argument("--version", type=int, default=None, help="checklist version id")
    ap.add_argument("--run", action="store_true", help="re-evaluate the gold dialogs before scoring")
    ap.add_argument("--provider", default="anthropic")
    ap.add_argument("--model", default=None)
    ap.add_argument("--note", default="")
    args = ap.parse_args()

    init_db()
    gold_doc = json.loads(Path(args.gold).read_text(encoding="utf-8"))
    labels: dict[str, dict[str, Any]] = gold_doc["labels"]
    ids = list(labels)
    if args.split and args.split != "all":
        ids = [i for i in gold_doc.get("split", {}).get(args.split, []) if i in labels]
    labels = {i: labels[i] for i in ids}

    db = SessionLocal()
    try:
        from ..checklist.service import active_version, get_version

        version = get_version(db, args.version) if args.version else active_version(db)
        scale_levels = {c.code: [float(o["value"]) for o in c.options] for c in version.criteria if c.answer_type == "scale"}
        if args.run:
            from ..judge.runner import evaluate_conversation

            async def run_all():
                sem = asyncio.Semaphore(3)

                async def one(cid):
                    async with sem:
                        await evaluate_conversation(cid, version.id, provider_name=args.provider, model=args.model)

                await asyncio.gather(*[one(cid) for cid in ids])

            t0 = time.time()
            asyncio.run(run_all())
            print(f"re-evaluated {len(ids)} dialogs in {time.time() - t0:.0f}s")
        preds = load_predictions(db, ids, version.id)
    finally:
        db.close()

    res = score(labels, preds, scale_levels)
    print(f"\ngold dialogs: {len(labels)}  with predictions: {res['n_dialogs']}  version: {version.id}  provider: {args.provider}")
    print(f"{'crit':5} {'n':>3} {'exact':>6} {'mae':>5} {'qwk':>6} {'kappa':>6} {'na_ok':>6}")
    for code, r in res["criteria"].items():
        print(f"{code:5} {r['n']:>3} {r['exact']:>6.2f} {str(r.get('mae', '-')):>5} {str(r.get('qwk', '-')):>6} {str(r.get('kappa', '-')):>6} {r['na_agree']:>6.2f}")
    print(f"total MAE {res['total_mae']}   cost/dialog ${res['cost_per_dialog']}")

    runs = API_DIR / "eval" / "runs.csv"
    runs.parent.mkdir(exist_ok=True)
    new = not runs.exists()
    with runs.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["ts", "split", "version", "provider", "model", "n", "total_mae", "cost_per_dialog", "per_criterion_json", "note"])
        w.writerow([datetime.now(timezone.utc).isoformat(), args.split or "all", version.id, args.provider, args.model or "", res["n_dialogs"],
                    res["total_mae"], res["cost_per_dialog"], json.dumps(res["criteria"], ensure_ascii=False), args.note])


if __name__ == "__main__":
    main()
