"""scripts/control_run.py rehearsal guards: no paid rehearsal on the jury instance by accident, and a rehearsal never
re-evaluates the live-demo dialogs (docs/DEMO.md) or dialogs with appeals or decisions (the seeded ones)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def cr():
    spec = importlib.util.spec_from_file_location("control_run_round3", ROOT / "scripts" / "control_run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_live_demo_list_matches_readiness(cr):
    from cqa.platform.readiness import LIVE_DEMO

    assert tuple(cr.LIVE_DEMO) == tuple(LIVE_DEMO)


def test_hosted_rehearsal_needs_the_explicit_flag_and_sends_nothing(cr, monkeypatch):
    def no_requests(*a, **k):
        raise AssertionError("no request may be sent before the guard")

    monkeypatch.setattr(cr.httpx, "Client", no_requests)
    monkeypatch.setattr(sys, "argv", ["control_run.py", "--rehearse"])
    with pytest.raises(SystemExit) as e:
        cr.main()
    assert "--allow-hosted-rehearsal" in str(e.value)


def test_rehearsal_picks_skip_the_live_demo_and_reviewed_dialogs(cr):
    convs = [{"id": f"p1-ru-{i:03d}", "review_state": "none"} for i in range(1, 31)]
    convs += [{"id": f"p1-kz-{i:03d}", "review_state": "none"} for i in range(1, 16)]
    convs += [{"id": f"p1-en-{i:03d}", "review_state": "none"} for i in range(1, 16)]
    convs += [{"id": "p4-ru-001", "review_state": "none"}]  # calls are not rehearsal material
    for c in convs:
        if c["id"] == "p1-kz-005":
            c["review_state"] = "under_review"
        if c["id"] == "p1-en-007":
            c["review_state"] = "changed"
    ids = cr.rehearsal_ids(convs)
    assert len(ids) == 10 and len(set(ids)) == 10
    assert not set(ids) & {"p1-ru-029", "p1-ru-005", "p1-kz-005", "p1-en-007", "p4-ru-001"}
    assert {i[3:5] for i in ids} == {"ru", "kz", "en"}  # spread over the three languages
    # whatever the order, a protected dialog is never picked
    assert not set(cr.rehearsal_ids(list(reversed(convs)))) & {"p1-ru-029", "p1-ru-005", "p1-kz-005", "p1-en-007"}
