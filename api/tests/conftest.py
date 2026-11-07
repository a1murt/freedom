"""Shared test isolation.

The spend ledger (cqa.jobs.ledger) is a file next to the database: tests must never write it into api/data, where the
local API would count test spend in its daily budget. Every test gets its own ledger file in tmp_path; the module-level
default points at a throw-away directory, so a worker thread that books after its test ended still writes nowhere real.
"""
from __future__ import annotations

import atexit
import shutil
import tempfile
from pathlib import Path

import pytest

from cqa.config import settings
from cqa.jobs import ledger

_SESSION_DIR = Path(tempfile.mkdtemp(prefix="cqa-test-ledger-"))
settings.spend_ledger_path = _SESSION_DIR / "spend_ledger.db"
atexit.register(shutil.rmtree, _SESSION_DIR, ignore_errors=True)


@pytest.fixture(autouse=True)
def _isolated_spend_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "spend_ledger_path", tmp_path / "spend_ledger.db")
    ledger.reset_cache()
    yield
    ledger.reset_cache()


@pytest.fixture(autouse=True)
def _no_developer_admin_token(monkeypatch):
    """The developer's .env may hold CQA_ADMIN_TOKEN (the deploy token for snapshots); tests start without it and set
    their own when they need one, so the suite gives the same result on every machine and in CI."""
    monkeypatch.setattr(settings, "cqa_admin_token", None)
    monkeypatch.delenv("CQA_ADMIN_TOKEN", raising=False)
