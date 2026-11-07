#!/usr/bin/env python3
"""Read-only check of the four live asks (docs/BUILD_BACKLOG.md WP-11, docs/DEMO.md) against a CQA API.

  api/.venv/bin/python scripts/smoke_asks.py                       # hosted (Railway)
  api/.venv/bin/python scripts/smoke_asks.py --api http://localhost:8000

Only GETs and the computing POSTs that store nothing (appeal preview, checklist what-if). Proves it wrote nothing by
comparing the appeal count before and after. Exit code 1 when a check fails. Expected numbers are the hosted DEV values;
override them with the flags when the data changes.
"""
from __future__ import annotations

import argparse
import sys
import time

import httpx

HOSTED = "https://api-production-58ab4.up.railway.app"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--api", default=HOSTED)
    ap.add_argument("--violations", default="insult=1,paid_bypass=4,disclosure=3", help="expected counts per type")
    ap.add_argument("--rude", default="p1-ru-029", help="the gross-insult dialog of block 2")
    ap.add_argument("--rude-total", type=float, default=51.25)
    ap.add_argument("--rude-turn", type=int, default=10, help="1-based turn of the insult quote")
    ap.add_argument("--appeal", default="p1-ru-005", help="dialog of the live operator appeal (block 3)")
    ap.add_argument("--appeal-code", default="C6")
    ap.add_argument("--appeal-answer", default="7")
    ap.add_argument("--appeal-before", type=float, default=78.46)
    ap.add_argument("--appeal-after", type=float, default=85.38)
    ap.add_argument("--group", default="DEV")
    ap.add_argument("--expect", type=int, default=65)
    args = ap.parse_args()
    base = args.api.rstrip("/")
    http = httpx.Client(timeout=90.0, headers={"X-Request-ID": f"smoke-asks-{int(time.time())}"})
    results: list[tuple[bool, str, str]] = []

    def report(ok: bool, name: str, note: str) -> None:
        results.append((ok, name, note))
        print(f"{'PASS' if ok else 'FAIL'}  {name:<40} {note}")

    def run(name, fn):
        try:
            ok, note = fn()
        except Exception as e:  # network, missing fields, unexpected shapes
            ok, note = False, f"{type(e).__name__}: {e}"
        report(ok, name, note)

    appeals_before = len(http.get(f"{base}/api/appeals").json())

    # ask 1: gross violations with explanations
    def ask1():
        d = http.get(f"{base}/api/violations").json()
        want = {k: int(v) for k, v in (p.split("=") for p in args.violations.split(","))}
        counts = {k: d["counts"].get(k, 0) for k in want}
        rude = next((i for i in d["items"] if i["conversation_id"] == args.rude), None)
        turn = (rude["evidence"][0]["turn_idx"] + 1) if rude and rude.get("evidence") else None
        ok = counts == want and d["total"] == sum(want.values()) and rude is not None and turn == args.rude_turn and bool(rude.get("explanation"))
        return ok, f"total {d['total']} {counts}; {args.rude} quote at turn {turn}, rules {[r['code'] for r in (rude or {}).get('rules', [])]}"
    run("ask 1 · gross violations", ask1)

    # ask 4 (block 2): one dialog with its justification
    def ask4():
        ev = http.get(f"{base}/api/conversations/{args.rude}/evaluation").json()
        na = [c["code"] for c in ev["criteria"] if not c["applicable"]]
        explained = all(c.get("explanation") or c.get("na_reason") for c in ev["criteria"])
        ok = abs((ev["final_total_score"] or 0) - args.rude_total) < 0.005 and ev["critical_error"] and explained
        return ok, f"{args.rude}: {ev['final_total_score']} / 100, gross {ev['critical_error']}, N/A {na}, every criterion explained {explained}"
    run("ask 4 · dialog result", ask4)

    # ask 2: operator appeal (preview only; the live demo files it)
    def ask2():
        ev = http.get(f"{base}/api/conversations/{args.appeal}/evaluation").json()
        c = next(c for c in ev["criteria"] if c["code"] == args.appeal_code)
        open_appeal = (c.get("appeal") or {}).get("status") == "open"
        answer: object = int(args.appeal_answer) if args.appeal_answer.isdigit() else args.appeal_answer
        p = http.post(f"{base}/api/appeals/preview", json={"evaluation_criterion_id": c["id"], "new_answer": answer}).json()
        ok = (abs(ev["final_total_score"] - args.appeal_before) < 0.005 and abs(p["final_total_score"] - args.appeal_after) < 0.005
              and not open_appeal and c.get("review_state") in (None, "none"))
        conv = http.get(f"{base}/api/conversations/{args.appeal}").json()
        return ok, (f"{args.appeal} ({conv.get('operator_name')}) {args.appeal_code} {c.get('final_answer')} → {answer}: "
                    f"{ev['final_total_score']} → {p['final_total_score']}; open appeal {open_appeal}, review_state {c.get('review_state')}")
    run("ask 2 · operator appeal (preview)", ask2)

    # ask 3: change a weight, see the impact, no model calls
    def ask3():
        cl = http.get(f"{base}/api/checklists").json()[0]
        v = cl.get("active_version") or http.get(f"{base}/api/checklists/{cl['id']}/versions/{cl['versions'][0]['id']}").json()
        crits = [{k: c.get(k) for k in ("code", "section", "title", "instruction", "answer_type", "options", "max_points", "weight",
                                         "na_allowed", "na_condition", "is_critical", "critical_value", "channel_overrides")}
                 for c in v["criteria"]]
        for c in crits:
            if c["code"] == "C7":
                c["weight"] = 10
        r = http.post(f"{base}/api/checklists/{cl['id']}/versions/{v['id']}/impact", json={"criteria": crits}).json()
        ch = r["changes"]
        ok = ch["weight_only"] and not ch["rejudge_codes"] and r["model_calls"] == 0 and v.get("version_no") == 1
        s = r["summary"]
        return ok, (f"active v{v.get('version_no')}; C7 5 → 10: {s['changed']} dialogs change (↑{s['up']} ↓{s['down']}), "
                    f"mean {s['mean_before']} → {s['mean_after']}, weight_only {ch['weight_only']}, model calls {r['model_calls']}")
    run("ask 3 · checklist weight what-if", ask3)

    def results_json():
        d = http.get(f"{base}/api/export/results/validate", params={"group_id": args.group, "expect": args.expect}).json()
        return bool(d.get("valid")), d.get("summary") or str(d)[:160]
    run("results.json validates", results_json)

    def readiness():
        d = http.get(f"{base}/api/system/readiness", params={"group_id": args.group, "expect": args.expect}).json()
        return bool(d.get("ready_for_demo")), d.get("summary")
    run("readiness ready_for_demo", readiness)

    appeals_after = len(http.get(f"{base}/api/appeals").json())
    report(appeals_after == appeals_before, "wrote nothing", f"appeals {appeals_before} → {appeals_after}")
    failed = [n for ok, n, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed against {base}" + (f"; failed: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
