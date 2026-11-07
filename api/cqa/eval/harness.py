"""Evaluation harness: compare stored verdicts (or a fresh run) with a gold set.

Usage:
  uv run python -m cqa.eval.harness --gold eval/gold.json                       # latest stored evaluations (local DB)
  uv run python -m cqa.eval.harness --gold eval/gold.json --split validation
  uv run python -m cqa.eval.harness --gold eval/gold.json --api https://api-production-58ab4.up.railway.app   # hosted verdicts
  uv run python -m cqa.eval.harness --gold eval/gold.json --run --provider anthropic --version 1
  uv run python -m cqa.eval.harness --gold eval/gold.json --run --only C4,C7    # partial re-judge, other criteria copied
  uv run python -m cqa.eval.harness --gold eval/gold.json --c7-bare N/A         # score under another C7 policy
  uv run python -m cqa.eval.harness --gold eval/gold.json --errors              # list every disagreement
  uv run python -m cqa.eval.harness --gold eval/gold_ai_panel.json --api https://api-production-58ab4.up.railway.app   # panel gold works too

Gold file format (eval/gold.json):
{
  "split": {"train": ["p1-ru-001", ...], "validation": ["p1-kz-002", ...]},
  "labels": {
    "p1-ru-029": {"C1": "yes", "C2": "N/A", "C3": "N/A", "C4": 20, "C5": "no", "C6": 7, "C7": "no", "C8": "yes", "total": 62.31,
                  "_c7_bare_farewell": false}
  }
}
Keys that start with "_" are metadata and never scored. "_c7_bare_farewell": true marks a farewell with no summary, no offer of
further help and nothing left open; --c7-bare yes|no|N/A rescores those dialogs under another C7 policy without editing the file.

Metrics per criterion: n, exact match, MAE (scale criteria), quadratic-weighted kappa (scale), Cohen kappa (binary incl. N/A as a
class), N/A agreement; plus the mean per-criterion agreement (macro over criteria and micro over every label: the organizers
compare each answer separately), total-score MAE and cost per dialog. Results are appended to eval/runs.csv.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

from ..config import API_DIR
from ..db import SessionLocal, init_db
from ..models import Evaluation

CODES = ["C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8"]


def _norm(v: Any) -> str:
    if v is None:
        return "N/A"
    if isinstance(v, bool):
        return "yes" if v else "no"
    s = str(v).strip()
    if s.lower() in ("n/a", "na", "none", "", "null"):
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


def _is_bare(flags: Any) -> bool:
    return any(f == "bare_farewell" or str(f).startswith("rule_c7_bare_farewell") for f in (flags or []))


def apply_c7_policy(preds: dict[str, dict[str, Any]], c7_bare: str | None) -> None:
    """Score predictions under the same C7 policy as the gold: a verdict the judge flagged as a bare farewell gets the policy value."""
    if not c7_bare:
        return
    for p in preds.values():
        if p.get("_c7_bare") and "C7" in p:
            p["C7"] = c7_bare


def gold_labels(doc: dict[str, Any], ids: list[str], c7_bare: str | None) -> dict[str, dict[str, Any]]:
    """Scored part of each gold row: criterion codes and total; '_' metadata dropped; optional C7 policy override."""
    out: dict[str, dict[str, Any]] = {}
    for cid in ids:
        row = doc["labels"][cid]
        g = {k: v for k, v in row.items() if not k.startswith("_")}
        if c7_bare and row.get("_c7_bare_farewell") and "C7" in g:
            g["C7"] = c7_bare
            g.pop("total", None)  # the stored total assumed the file's C7 value
        out[cid] = g
    return out


def load_predictions(db, ids: list[str], version_id: int | None, since: datetime | None = None,
                     id_range: tuple[int | None, int | None] = (None, None)) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for cid in ids:
        q = select(Evaluation).where(Evaluation.conversation_id == cid, Evaluation.status == "completed")
        if version_id:
            q = q.where(Evaluation.checklist_version_id == version_id)
        if since is not None:
            q = q.where(Evaluation.created_at >= since)
        if id_range[0] is not None:
            q = q.where(Evaluation.id >= id_range[0])
        if id_range[1] is not None:
            q = q.where(Evaluation.id <= id_range[1])
        ev = db.execute(q.order_by(Evaluation.id.desc())).scalars().first()
        if ev is None:
            continue
        pred = {r.code: ("N/A" if not r.applicable else r.answer) for r in ev.criteria}
        pred["_c7_bare"] = any(r.code == "C7" and _is_bare(r.flags) for r in ev.criteria)
        pred["total"] = ev.total_score
        pred["_cost"] = ev.cost_usd
        pred["_model"] = ev.model
        pred["_prompt"] = ev.prompt_version
        pred["_evaluation_id"] = ev.id
        out[cid] = pred
    return out


def load_predictions_api(base_url: str, ids: list[str]) -> dict[str, dict[str, Any]]:
    """Latest evaluation of each dialog from a running CQA API (e.g. the hosted instance); read-only GETs."""
    base = base_url.rstrip("/")

    def one(cid: str) -> tuple[str, dict[str, Any] | None]:
        try:
            with urllib.request.urlopen(f"{base}/api/conversations/{cid}/evaluation", timeout=60) as r:
                ev = json.loads(r.read().decode("utf-8"))
        except Exception:
            return cid, None
        if not ev or ev.get("status") != "completed":
            return cid, None
        pred: dict[str, Any] = {c["code"]: ("N/A" if not c["applicable"] else c["answer"]) for c in ev.get("criteria", [])}
        pred["_c7_bare"] = any(c["code"] == "C7" and _is_bare(c.get("flags")) for c in ev.get("criteria", []))
        pred["total"] = ev.get("total_score")
        pred["_cost"] = ev.get("cost_usd")
        pred["_model"] = ev.get("model")
        pred["_prompt"] = ev.get("prompt_version")
        pred["_evaluation_id"] = ev.get("id")
        return cid, pred

    with ThreadPoolExecutor(8) as ex:
        return {cid: p for cid, p in ex.map(one, ids) if p is not None}


def gold_alternatives(doc: dict[str, Any], ids: list[str]) -> dict[str, dict[str, set[str]]]:
    """'_alt': {"C5": "no"} or {"C4": [10, 5]} marks a disputed label and the other values a careful expert could give."""
    out: dict[str, dict[str, set[str]]] = {}
    for cid in ids:
        alt = doc["labels"][cid].get("_alt") or {}
        out[cid] = {code: {_norm(x) for x in (v if isinstance(v, list) else [v])} for code, v in alt.items()}
    return out


def score(gold: dict[str, dict[str, Any]], preds: dict[str, dict[str, Any]], scale_levels: dict[str, list[float]],
          alts: dict[str, dict[str, set[str]]] | None = None) -> dict[str, Any]:
    per: dict[str, dict[str, Any]] = {}
    errors: dict[str, list[tuple[str, str, str]]] = {}
    codes = [c for c in CODES if any(c in g for g in gold.values())]
    codes += sorted({c for g in gold.values() for c in g if c not in CODES and c != "total" and not c.startswith("_")})
    matched = total_pairs = lenient = 0
    alts = alts or {}
    for code in codes:
        trip = [(cid, _norm(gold[cid][code]), _norm(preds[cid].get(code))) for cid in gold if cid in preds and code in gold[cid]]
        pairs = [(a, b) for _, a, b in trip]
        if not pairs:
            continue
        hits = sum(1 for a, b in pairs if a == b)
        lenient += sum(1 for cid, a, b in trip if a == b or b in alts.get(cid, {}).get(code, set()))
        matched += hits
        total_pairs += len(pairs)
        exact = hits / len(pairs)
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
        errors[code] = [(cid, a, b) for cid, a, b in trip if a != b]
    totals = [(float(gold[cid]["total"]), float(preds[cid]["total"])) for cid in gold
              if cid in preds and gold[cid].get("total") is not None and preds[cid].get("total") is not None]
    total_mae = round(sum(abs(a - b) for a, b in totals) / len(totals), 2) if totals else None
    costs = [preds[cid]["_cost"] for cid in gold if cid in preds and preds[cid].get("_cost") is not None]
    macro = round(sum(r["exact"] for r in per.values()) / len(per), 3) if per else None
    micro = round(matched / total_pairs, 3) if total_pairs else None
    return {"criteria": per, "agree_macro": macro, "agree_micro": micro,
            "agree_lenient": round(lenient / total_pairs, 3) if total_pairs else None, "total_mae": total_mae,
            "n_dialogs": len([c for c in gold if c in preds]), "errors": errors,
            "cost_per_dialog": round(sum(costs) / len(costs), 4) if costs else None}


def print_report(res: dict[str, Any], header: str, show_errors: bool) -> None:
    print(f"\n{header}")
    print(f"{'crit':5} {'n':>3} {'exact':>6} {'mae':>5} {'qwk':>6} {'kappa':>6} {'na_ok':>6}")
    for code, r in res["criteria"].items():
        print(f"{code:5} {r['n']:>3} {r['exact']:>6.2f} {str(r.get('mae', '-')):>5} {str(r.get('qwk', '-')):>6} "
              f"{str(r.get('kappa', '-')):>6} {r['na_agree']:>6.2f}")
    print(f"per-criterion agreement: macro {res['agree_macro']}  micro {res['agree_micro']}  "
          f"lenient {res['agree_lenient']} (documented alternatives count)   total MAE {res['total_mae']}   cost/dialog ${res['cost_per_dialog']}")
    if show_errors:
        for code, rows in res["errors"].items():
            for cid, a, b in rows:
                print(f"  {code} {cid:12} gold={a:5} pred={b}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default=str(API_DIR / "eval" / "gold.json"))
    ap.add_argument("--split", default=None, help="train | validation | all | any split name in the gold file")
    ap.add_argument("--version", type=int, default=None, help="checklist version id")
    ap.add_argument("--run", action="store_true", help="re-evaluate the gold dialogs before scoring")
    ap.add_argument("--only", default="", help="with --run: comma-separated criterion codes to re-judge (partial run on top of the latest evaluation)")
    ap.add_argument("--provider", default="anthropic")
    ap.add_argument("--model", default=None)
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--api", default=None, help="score the latest evaluations of a running CQA API instead of the local DB")
    ap.add_argument("--c7-bare", default=None, choices=["yes", "no", "N/A"],
                    help="score under this C7 policy: gold rows marked _c7_bare_farewell and verdicts flagged bare_farewell get this value")
    ap.add_argument("--errors", action="store_true", help="list every gold/prediction disagreement")
    ap.add_argument("--json-out", default=None, help="also write the metrics and disagreements to this JSON file")
    ap.add_argument("--note", default="")
    ap.add_argument("--no-log", action="store_true", help="do not append this run to eval/runs.csv")
    ap.add_argument("--min-eval-id", type=int, default=None, help="score only evaluations with id >= this (compare runs kept in one DB)")
    ap.add_argument("--max-eval-id", type=int, default=None, help="score only evaluations with id <= this")
    args = ap.parse_args()

    gold_doc = json.loads(Path(args.gold).read_text(encoding="utf-8"))
    if "gold" in gold_doc and isinstance(gold_doc.get("gold"), dict):
        # panel format (eval/gold_ai_panel.json): {"gold": {id: {...}}, "labels": {lens: {...}}}
        gold_doc = {"labels": gold_doc["gold"], "split": gold_doc.get("split", {})}
    ids = list(gold_doc["labels"])
    if args.split and args.split != "all":
        ids = [i for i in gold_doc.get("split", {}).get(args.split, []) if i in gold_doc["labels"]]
    labels = gold_labels(gold_doc, ids, args.c7_bare)
    only = {c.strip().upper() for c in args.only.split(",") if c.strip()}

    since: datetime | None = None
    if args.api:
        preds = load_predictions_api(args.api, ids)
        version_id = None
        scale_levels = {"C4": [20.0, 15.0, 10.0, 5.0, 0.0], "C6": [10.0, 7.0, 4.0, 0.0]}
        source = f"api {args.api}"
    else:
        init_db()
        db = SessionLocal()
        try:
            from ..checklist.service import active_version, get_version

            version = get_version(db, args.version) if args.version else active_version(db)
            version_id = version.id
            scale_levels = {c.code: [float(o["value"]) for o in c.options] for c in version.criteria if c.answer_type == "scale"}
            if args.run:
                from ..judge.runner import evaluate_conversation

                base_ids: dict[str, int] = {}
                if only:
                    for cid, p in load_predictions(db, ids, version.id).items():
                        base_ids[cid] = p["_evaluation_id"]
                since = datetime.now(timezone.utc)

                async def run_all():
                    sem = asyncio.Semaphore(args.concurrency)

                    async def one(cid):
                        async with sem:
                            if only and cid in base_ids:
                                await evaluate_conversation(cid, version.id, provider_name=args.provider, model=args.model,
                                                            mode="partial", base_evaluation_id=base_ids[cid], only_codes=only)
                            else:
                                await evaluate_conversation(cid, version.id, provider_name=args.provider, model=args.model)

                    await asyncio.gather(*[one(cid) for cid in ids])

                t0 = time.time()
                asyncio.run(run_all())
                print(f"re-evaluated {len(ids)} dialogs in {time.time() - t0:.0f}s" + (f" (only {','.join(sorted(only))})" if only else ""))
            preds = load_predictions(db, ids, version.id, since=since, id_range=(args.min_eval_id, args.max_eval_id))
        finally:
            db.close()
        source = f"local db, version {version_id}"

    apply_c7_policy(preds, args.c7_bare)
    res = score(labels, preds, scale_levels, gold_alternatives(gold_doc, list(labels)))
    header = (f"gold dialogs: {len(labels)}  with predictions: {res['n_dialogs']}  split: {args.split or 'all'}  source: {source}  "
              f"provider: {args.provider if args.run else 'stored'}" + (f"  c7-bare: {args.c7_bare}" if args.c7_bare else ""))
    print_report(res, header, args.errors)
    missing = [cid for cid in labels if cid not in preds]
    if missing:
        print(f"no prediction for: {', '.join(missing)}")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    if args.no_log:
        return

    runs = API_DIR / "eval" / "runs.csv"
    runs.parent.mkdir(exist_ok=True)
    new = not runs.exists()
    with runs.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["ts", "split", "version", "provider", "model", "n", "agree_macro", "agree_micro", "total_mae", "cost_per_dialog",
                        "per_criterion_json", "note"])
        w.writerow([datetime.now(timezone.utc).isoformat(), args.split or "all", version_id, args.provider if args.run else "stored",
                    args.model or "", res["n_dialogs"], res["agree_macro"], res["agree_micro"], res["total_mae"], res["cost_per_dialog"],
                    json.dumps(res["criteria"], ensure_ascii=False),
                    " ".join(x for x in (args.note, f"only={','.join(sorted(only))}" if only else "", f"c7-bare={args.c7_bare}" if args.c7_bare else "",
                                         "api" if args.api else "") if x)])


if __name__ == "__main__":
    main()
