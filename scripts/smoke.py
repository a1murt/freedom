#!/usr/bin/env python3
"""Read-only smoke test of a CQA API (hosted or local) after a deploy.

  api/.venv/bin/python scripts/smoke.py                       # hosted (Railway)
  api/.venv/bin/python scripts/smoke.py --api http://localhost:8000

Every request is a read (GET, or the what-if POST that computes and stores nothing): safe on the jury instance.
Exit code 1 when a check fails. Prints one line per check and the readiness summary.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from urllib.parse import quote

import httpx

HOSTED = "https://api-production-58ab4.up.railway.app"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--api", default=HOSTED)
    ap.add_argument("--group", default="DEV")
    ap.add_argument("--expect", type=int, default=65)
    ap.add_argument("--version", help="fail unless GET /api/version reports this build")
    args = ap.parse_args()
    base = args.api.rstrip("/")
    http = httpx.Client(timeout=60.0, headers={"X-Request-ID": f"smoke-{int(time.time())}"})
    results: list[tuple[bool, str, str]] = []

    def check(name: str, method: str, path: str, ok, *, headers=None, body=None, params=None):
        t0 = time.perf_counter()
        try:
            r = http.request(method, base + path, headers=headers, json=body, params=params)
            ms = (time.perf_counter() - t0) * 1000
            try:
                data = r.json() if "json" in r.headers.get("content-type", "") else r.text
            except ValueError:
                data = r.text
            verdict, note = ok(r, data)
        except Exception as e:  # network, timeout, assertion inside ok()
            ms, verdict, note = (time.perf_counter() - t0) * 1000, False, f"{type(e).__name__}: {e}"
        results.append((verdict, name, note))
        print(f"{'PASS' if verdict else 'FAIL'}  {name:<34} {ms:7.0f} ms  {note}")
        return None

    check("health", "GET", "/api/health", lambda r, d: (r.status_code == 200 and d.get("ok"), f"providers {d.get('providers')}"))

    def version_ok(r, d):
        if r.status_code != 200:
            return False, f"HTTP {r.status_code} (build without /api/version?)"
        good = not args.version or str(d.get("version", "")).startswith(args.version)
        return good, f"version {d.get('version')} ({d.get('source')}), started {d.get('started_at')}"
    check("version", "GET", "/api/version", version_ok)
    check("system health", "GET", "/api/system/health",
          lambda r, d: (r.status_code == 200 and d.get("ok"), f"budget {d.get('budget', {}).get('remaining')} left, warnings {[w.get('code') for w in d.get('warnings', [])]}"))
    check("roles", "GET", "/api/roles", lambda r, d: (r.status_code == 200 and len(d.get("roles", [])) >= 9, f"{len(d.get('roles', []))} roles"))
    check("me (operator Алина)", "GET", "/api/me",
          lambda r, d: (r.status_code == 200 and d.get("name") == "Алина" and d.get("role") == "operator", d.get("display")),
          headers={"X-CQA-Role": "operator", "X-CQA-User": quote("Алина")})
    check("conversations", "GET", "/api/conversations", lambda r, d: (r.status_code == 200 and d.get("total", 0) >= args.expect,
                                                                      f"{d.get('total')} dialogs"), params={"page_size": 1})
    check("drill-down C7 = no", "GET", "/api/conversations", lambda r, d: (r.status_code == 200, f"{d.get('total')} dialogs"),
          params={"criterion": "C7", "answer": "no", "page_size": 1})
    check("search snippet", "GET", "/api/conversations",
          lambda r, d: (r.status_code == 200 and (d.get("total", 0) == 0 or "match" in d["items"][0]), f"{d.get('total')} hits"),
          params={"q": "читать умеете"})
    check("violations", "GET", "/api/violations",
          lambda r, d: (r.status_code == 200 and d.get("total", 0) > 0 and "rules" in (d.get("items") or [{}])[0], f"{d.get('total')} {d.get('counts')}"))
    check("overview", "GET", "/api/overview", lambda r, d: (r.status_code == 200 and "quality" in d,
                                                            f"mean {d.get('quality', {}).get('mean')}, critical {d.get('critical', {}).get('count')}"))
    check("operators", "GET", "/api/operators", lambda r, d: (r.status_code == 200 and len(d) > 0, f"{len(d)} operators"))
    check("appeals", "GET", "/api/appeals", lambda r, d: (r.status_code == 200, f"{len(d)} appeals, open {sum(1 for a in d if a.get('status') == 'open')}"))
    check("checklists", "GET", "/api/checklists", lambda r, d: (r.status_code == 200 and len(d) > 0, f"{len(d)} checklists"))

    def impact_ok(r, d):
        return r.status_code == 200 and d.get("model_calls") == 0, f"{d.get('summary', {}).get('n')} dialogs, changed {d.get('summary', {}).get('changed')}"
    try:
        cl = http.get(base + "/api/checklists").json()[0]
        v = cl["active_version"] if cl.get("active_version") else http.get(base + f"/api/checklists/{cl['id']}/versions/{cl['versions'][0]['id']}").json()
        crits = [{k: c.get(k) for k in ("code", "section", "title", "instruction", "answer_type", "options", "max_points", "weight", "na_allowed",
                                         "na_condition", "is_critical", "critical_value", "channel_overrides")} for c in v["criteria"]]
        check("what-if (no writes)", "POST", f"/api/checklists/{cl['id']}/versions/{v['id']}/impact", impact_ok, body={"criteria": crits})
    except Exception as e:
        results.append((False, "what-if (no writes)", str(e)))
        print(f"FAIL  what-if (no writes)  {e}")
    check("results.json validate", "GET", "/api/export/results/validate",
          lambda r, d: (r.status_code == 200 and d.get("valid"), d.get("summary") if isinstance(d, dict) else str(d)[:120]),
          params={"group_id": args.group, "expect": args.expect})
    check("violations.csv", "GET", "/api/export/violations.csv", lambda r, d: (r.status_code == 200 and "conversation_id" in r.text[:200], f"{len(r.text.splitlines()) - 1} rows"))
    check("decisions.csv", "GET", "/api/export/decisions.csv", lambda r, d: (r.status_code == 200 and "appeal_id" in r.text[:200], f"{len(r.text.splitlines()) - 1} rows"))

    readiness = {}

    def ready_ok(r, d):
        readiness.update(d if isinstance(d, dict) else {})
        return r.status_code == 200 and d.get("ready"), d.get("summary")
    check("readiness", "GET", "/api/system/readiness", ready_ok, params={"group_id": args.group, "expect": args.expect})
    for c in readiness.get("checks", []):
        if not c.get("ok"):
            print(f"      - {c['severity']:<11} {c['code']}: {c['message']}")
    failed = [n for ok, n, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed against {base}" + (f"; failed: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
