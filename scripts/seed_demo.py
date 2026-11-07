#!/usr/bin/env python3
"""Seed the demo state the live asks need on the hosted instance (docs/DEMO.md, readiness check `appeals_seeded`).

  api/.venv/bin/python scripts/seed_demo.py --dry-run     # show what would be created
  api/.venv/bin/python scripts/seed_demo.py               # create (hosted by default; --api for another instance)

Leaves the live-demo dialogs untouched: p1-ru-029 (block 2: the dialog result) and p1-ru-005 (block 3: operator Нина
files an appeal on C6 live). Among the other chats with C7 (closure) = «Нет» and a named operator, best total first:
1. the first gets an OPEN appeal from its operator (fills the review queue);
2. the second gets a DECIDED appeal: the QA analyst disputes it, the reviewer changes the answer (the card then shows
   original → final and the History timeline).
Idempotent: an open operator appeal, or a decided appeal on the dialog's latest evaluation, on any chat other than the
live-demo dialogs counts as already seeded (appeals filed on p1-ru-029 / p1-ru-005 during rehearsals never do); a chat
that already has an appeal on C7 is never picked, and a chat whose latest evaluation is not completed is skipped.
Decisions are saved with add_to_fewshot = false, so seeding never changes what the judge sees in the control run.
"""
from __future__ import annotations

import argparse
import sys
from urllib.parse import quote

import httpx

HOSTED = "https://api-production-58ab4.up.railway.app"
LIVE_DEMO = {"p1-ru-029", "p1-ru-005"}
ANALYST, REVIEWER = "Жанар Б.", "Айгерим С."  # staff signatures used across docs/DEMO.md and docs/ROLES.md


def hdr(role: str, name: str) -> dict[str, str]:
    return {"X-CQA-Role": role, "X-CQA-User": quote(name), "X-Request-ID": f"seed-demo-{role}"}


def criterion(http: httpx.Client, base: str, cid: str, code: str) -> dict | None:
    """The `code` row of the dialog's evaluation (GET …/evaluation, the one the card shows); None unless that evaluation
    is completed: a failed attempt holds no verdict to appeal, so the dialog is skipped."""
    r = http.get(f"{base}/api/conversations/{cid}/evaluation")
    if r.status_code != 200:
        return None
    ev = r.json()
    if ev.get("status") != "completed":
        return None
    return next((c for c in ev.get("criteria", []) if c["code"] == code), None)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--api", default=HOSTED)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    base = args.api.rstrip("/")
    http = httpx.Client(timeout=90.0)
    appeals = http.get(f"{base}/api/appeals").json()
    has_c7_appeal = {a.get("conversation_id") for a in appeals if a.get("code") == "C7"}
    # appeals on the live-demo dialogs come from rehearsals of the live asks: they never count as the seeded state
    seeded = [a for a in appeals if a.get("conversation_id") not in LIVE_DEMO]
    open_op = [a for a in seeded if a.get("status") == "open" and a.get("author_role") == "operator"]
    # a decision on an older evaluation is not what the card shows (original → final, History): only the latest counts
    decided = [a for a in seeded if a.get("status") == "decided" and a.get("is_latest_evaluation") is True]

    r = http.get(f"{base}/api/conversations", params={"criterion": "C7", "answer": "no", "sort": "total_desc", "page_size": 100})
    if r.status_code != 200:
        print(f"FAIL  the API does not support the criterion filter (HTTP {r.status_code}); deploy the current build first")
        return 1
    candidates = []
    for it in r.json().get("items", []):
        if it["id"] in LIVE_DEMO or it["id"] in has_c7_appeal or not it.get("operator_name") or it.get("channel") != "agent chat":
            continue
        c7 = criterion(http, base, it["id"], "C7")
        if c7 and c7.get("final_answer") == "no" and c7.get("review_state") in (None, "none"):
            candidates.append((it, c7))
        if len(candidates) == 2:
            break

    if open_op:
        print(f"skip  open appeal: operator appeal #{open_op[0]['id']} is already open ({open_op[0]['conversation_id']})")
    elif not candidates:
        print("skip  open appeal: no free chat with C7 = «Нет» and a named operator")
    else:
        it, c7 = candidates.pop(0)
        if args.dry_run:
            print(f"would create: OPEN appeal by operator {it['operator_name']} on {it['id']} C7 (total {it['latest_evaluation']['final_total_score']})")
        else:
            res = http.post(f"{base}/api/appeals", headers=hdr("operator", it["operator_name"]), json={
                "evaluation_criterion_id": c7["id"],
                "comment": "В конце я уточнил(а), остались ли вопросы, и попрощался(лась) с клиентом — завершение было."})
            if res.status_code != 200:
                print(f"FAIL  open appeal: HTTP {res.status_code} {res.text[:200]}")
                return 1
            print(f"done  open appeal #{res.json()['id']} by operator {it['operator_name']} on {it['id']} C7")

    if decided:
        print(f"skip  decided appeal: appeal #{decided[0]['id']} is already decided ({decided[0]['conversation_id']} {decided[0]['code']})")
    elif not candidates:
        print("skip  decided appeal: no second free chat with C7 = «Нет»")
    else:
        it, c7 = candidates.pop(0)
        if args.dry_run:
            print(f"would create: DECIDED appeal on {it['id']} C7: analyst {ANALYST} disputes, reviewer {REVIEWER} changes «Нет» → «Да»")
        else:
            res = http.post(f"{base}/api/appeals", headers=hdr("analyst", ANALYST), json={
                "evaluation_criterion_id": c7["id"],
                "comment": "Оператор предложил дальнейшую помощь и корректно попрощался: «Нет» по завершению не подтверждается."})
            res.raise_for_status()
            appeal = res.json()
            d = http.post(f"{base}/api/appeals/{appeal['id']}/decision", headers=hdr("reviewer", REVIEWER), json={
                "action": "change", "new_answer": "yes", "add_to_fewshot": False,
                "reason": "В финальных репликах оператор уточнил, остались ли вопросы, и попрощался: завершение соответствует критерию."})
            d.raise_for_status()
            ev = d.json()["evaluation"]
            print(f"done  decided appeal #{appeal['id']} on {it['id']} C7: «Нет» → «Да», total {ev['total_score']} → {ev['final_total_score']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
