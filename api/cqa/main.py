from __future__ import annotations

import importlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import settings
from .db import SessionLocal, init_db

log = logging.getLogger("cqa")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    from .checklist.service import ensure_seed

    db = SessionLocal()
    try:
        ensure_seed(db)
    finally:
        db.close()
    # optional start-up hooks owned by other modules (each must be idempotent and never raise)
    for hook in ("cqa.jobs.runner:recover_stale_jobs",):
        mod_name, fn_name = hook.split(":")
        try:
            fn = getattr(importlib.import_module(mod_name), fn_name, None)
            if fn is not None:
                fn()
        except Exception as e:  # pragma: no cover - defensive
            log.warning("startup hook %s failed: %s", hook, e)
    yield


app = FastAPI(title="CQA API", version="0.1.0", lifespan=lifespan)
from .platform import install as install_platform  # noqa: E402

install_platform(app)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False, allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(HTTPException)
async def http_exc(request: Request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, dict) else {"code": "error", "message": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content={"error": detail})


@app.exception_handler(Exception)
async def any_exc(request: Request, exc: Exception):
    log.exception("unhandled error")
    return JSONResponse(status_code=500, content={"error": {"code": "internal", "message": str(exc)}})


for mod in ("conversations", "checklists", "evaluations", "appeals", "settings_router", "audio", "analytics", "export", "insights",
            "system", "admin", "integrations", "calibration", "audit", "roles"):
    try:
        m = importlib.import_module(f"cqa.routers.{mod}")
        app.include_router(m.router)
    except Exception as e:  # a missing optional router must not take the API down
        log.warning("router %s not loaded: %s", mod, e)
