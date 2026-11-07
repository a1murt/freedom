"""Platform hook: optional middlewares installed at app start-up.

Each installer lives in its own module so independent work packages never edit main.py:
- cqa.platform.requestlog  → request id, timing, structured access log (WP platform)
- cqa.security.middleware  → actor (role/name) context, permission checks, audit trail (WP roles)
A missing or failing installer is logged and skipped so the API always starts.
"""
from __future__ import annotations

import importlib
import logging

from fastapi import FastAPI

log = logging.getLogger("cqa.platform")

INSTALLERS = ("cqa.platform.requestlog", "cqa.security.middleware")


def install(app: FastAPI) -> list[str]:
    installed: list[str] = []
    for mod_name in INSTALLERS:
        try:
            mod = importlib.import_module(mod_name)
        except ModuleNotFoundError:
            continue
        except Exception as e:  # pragma: no cover - defensive
            log.warning("platform installer %s failed to import: %s", mod_name, e)
            continue
        fn = getattr(mod, "install", None)
        if fn is None:
            continue
        try:
            fn(app)
            installed.append(mod_name)
        except Exception as e:  # pragma: no cover - defensive
            log.warning("platform installer %s failed: %s", mod_name, e)
    return installed
