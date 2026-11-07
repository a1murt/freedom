"""Dynamics 365 integration (package "integrations"; docs/arch/integrations.md, admin guide docs/INTEGRATION_D365.md).

POST /api/integrations/dynamics/transcripts  one Omnichannel transcript (note documentbody, decoded JSON or messages) ->
                                             conversation upsert, optionally an evaluation job; returns card and embed links
GET  /api/integrations/dynamics/manifest     packaging info for the Dynamics 365 web resource: single-file build,
                                             window.CQA_CONFIG snippet, form tab steps, Power Automate flow and payload
"""
from __future__ import annotations

import contextlib
import hmac
import inspect
import json
import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from ..checklist import service as svc
from ..config import settings
from ..db import get_db
from ..ingest.service import ConversationBusy, upsert_record
from ..integrations.dynamics import (
    SOURCE,
    DynamicsParseError,
    content_hash,
    encode_documentbody,
    enrich_conversation,
    parse_transcript,
    sample_messages,
)
from ..jobs import estimate as estimator
from ..jobs import runner as jobs
from ..judge.runner import UnknownModelError
from ..models import ChecklistVersion, Conversation, Dataset, Evaluation, Job, Setting

router = APIRouter(prefix="/api/integrations", tags=["integrations"])
log = logging.getLogger("cqa.integrations")

DEFAULT_WEB_BASE_URL = "https://cqa-freedom-pi.vercel.app"
D365_ORG_URL = "https://freedomholdingcorporation.crm11.dynamics.com"
DATASET_GROUP_ID = "D365"  # all pushed conversations land in one dataset (filter, results.json?group_id=D365)
# "fake" = the offline deterministic judge (WP testing), routed natively by cqa.judge.runner._members; cost 0.
KNOWN_PROVIDERS = ("anthropic", "local", "ensemble", "ollama", "openai_compatible", "vllm", "fake")
INTEGRATION_KEY_ENV = "INTEGRATIONS_API_KEY"  # optional shared secret; unset = open like the rest of the API
INTEGRATION_KEY_HEADER = "X-CQA-Integration-Key"
MAX_BODY_BYTES = 8 * 1024 * 1024  # a base64 note of a very long chat is well under 1 MB
BUSY_RETRY_AFTER_S = 60  # a changed transcript of a dialog under evaluation: about one evaluation later

# Fallback copy of docs/ARCH_CONTRACTS.md §1, used only if cqa.security (the source of truth) cannot be imported.
ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "integrations.ingest": frozenset({"demo", "analyst", "lead", "admin"}),
    "evaluations.run": frozenset({"demo", "analyst", "reviewer", "lead", "methodologist", "admin"}),
}
PERMISSION_LABELS = {"integrations.ingest": "приём стенограмм из Dynamics 365", "evaluations.run": "запуск оценки"}


class DynamicsTranscriptIn(BaseModel):
    """Body of POST /api/integrations/dynamics/transcripts. Pass exactly one transcript source: ``annotation`` (the Dataverse
    note with ``documentbody``), ``documentbody``, ``transcript`` (decoded JSON) or ``messages``. Unknown fields are kept:
    Power Automate may forward Dataverse columns (``msdyn_ocliveworkitemid``, ``_msdyn_liveworkitemidid_value``,
    ``msdyn_channel``...) as they are."""

    model_config = ConfigDict(extra="allow")

    conversation_id: str | None = Field(None, description="msdyn_ocliveworkitem id (GUID); default: taken from the payload")
    language: str | int | None = Field(None, description="RU | KZ | EN (ru-RU, kk-KZ, LCID 1049...); default: detected")
    channel: str | int | None = Field(None, description="agent chat | voice call (or a msdyn_channel code); default: detected")
    annotation: Any = Field(None, description="Dataverse note: {annotationid, objecttypecode, _objectid_value, documentbody}")
    documentbody: str | None = Field(None, description="base64 transcript (annotation.documentbody)")
    transcript: Any = Field(None, description="decoded transcript JSON (list of messages or a wrapper object)")
    messages: Any = Field(None, description="plain message list: D365 messages or {speaker|role, text|content, timestamp}")
    evaluate: bool = Field(False, description="start an evaluation job after the upsert")
    checklist_version_id: int | None = Field(None, description="default: the active checklist version")
    provider: str | None = Field(None, description="anthropic | local | ensemble (or anthropic:<model>); default: settings.default_provider")
    model: str | None = None
    force: bool = Field(False, description="evaluate even if this transcript was already evaluated unchanged")
    overwrite: bool = Field(False, description="replace a conversation with the same id that did not come from Dynamics 365")
    audio_url: str | None = Field(None, description="call recording (http/https) for voice conversations")
    private_messages: Literal["system", "skip"] = "system"
    control_messages: Literal["events", "all", "none"] = "events"
    order: Literal["auto", "keep", "chronological"] = "auto"
    participants: dict[str, str] | None = Field(None, description="display name or id -> client | operator | bot | system")


_OPENAPI_BODY = {"requestBody": {"required": True, "content": {"application/json": {"schema": DynamicsTranscriptIn.model_json_schema()}}}}


# --------------------------------------------------------------------------------------------- links


def _config(name: str, env: str) -> str:
    """The environment variable, else a cqa.config setting of that name (once the integration step adds one: values from
    the .env file reach `settings`, not os.environ)."""
    return str(os.environ.get(env) or getattr(settings, name, None) or "").strip()


def web_base_url() -> str:
    return _config("web_base_url", "WEB_BASE_URL").rstrip("/") or DEFAULT_WEB_BASE_URL


def card_url(conversation_id: str) -> str:
    return f"{web_base_url()}/#/conversations/{quote(conversation_id, safe='')}"


def embed_url(conversation_id: str) -> str:
    return f"{web_base_url()}/?embed=1&conversation={quote(conversation_id, safe='')}"


def _public_api_base(request: Request) -> str:
    configured = _config("public_api_url", "PUBLIC_API_URL").rstrip("/")
    if configured:
        return configured
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme).split(",")[0].strip()
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc).split(",")[0].strip()
    return f"{proto}://{host}"


# --------------------------------------------------------------------------------------------- request checks


def _error(status: int, code: str, message: str, **extra: Any) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message, **extra})


def _read_json(raw: bytes) -> dict[str, Any]:
    if not raw.strip():
        raise _error(400, "empty_body", "Пустое тело запроса: ожидается JSON со стенограммой")
    if len(raw) > MAX_BODY_BYTES:
        raise _error(413, "payload_too_large", f"Тело запроса больше {MAX_BODY_BYTES // (1024 * 1024)} МБ")
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as e:
        raise _error(400, "invalid_json", f"Тело запроса не является корректным JSON: {e}") from e
    if not isinstance(data, dict):
        raise _error(422, "invalid_body", "Ожидается JSON-объект (annotation, documentbody, transcript или messages)",
                     errors=[{"field": "body", "message": "ожидается объект"}])
    return data


def _check_integration_key(request: Request) -> None:
    expected = _config("integrations_api_key", INTEGRATION_KEY_ENV)
    if not expected:
        return
    got = (request.headers.get(INTEGRATION_KEY_HEADER) or "").strip()
    if not got:
        raise _error(401, "integration_key_required", f"Нужен заголовок {INTEGRATION_KEY_HEADER} с ключом интеграции")
    if not hmac.compare_digest(got.encode("utf-8"), expected.encode("utf-8")):
        raise _error(401, "invalid_integration_key", f"Неверный ключ интеграции в заголовке {INTEGRATION_KEY_HEADER}")


def _security() -> Any:
    try:
        from ..security import roles as security_roles
    except Exception:  # noqa: BLE001  # pragma: no cover - a broken or missing roles package must not unload this router
        return None
    return security_roles


def _actor_role(request: Request) -> str | None:
    """Role of the caller: the security middleware's actor, or X-CQA-Role resolved by cqa.security (400 unknown_role)."""
    sec = _security()
    if sec is not None:
        return str(sec.current_actor(request).get("role") or "demo")
    actor = getattr(request.state, "actor", None)
    return str(actor.get("role") or "demo").strip().lower() if isinstance(actor, dict) else None


def _has_permission(role: str, permission: str) -> bool:
    sec = _security()
    if sec is not None:
        return bool(sec.has_permission(role, permission))
    return role in ROLE_PERMISSIONS[permission]


def _require_permission(request: Request, permission: str) -> None:
    """The middleware already enforces integrations.ingest for this route (ROUTE_MAP); this repeats it for apps without the
    middleware and adds evaluations.run when the push asks for an evaluation."""
    role = _actor_role(request)
    if role is None or _has_permission(role, permission):
        return
    sec = _security()
    forbidden = getattr(sec, "forbidden_detail", None) if sec is not None else None
    detail = forbidden(role, permission) if callable(forbidden) else {
        "code": "forbidden", "message": f"Роли «{role}» недоступно действие: {PERMISSION_LABELS[permission]}", "role": role,
        "permission": permission}
    raise HTTPException(status_code=403, detail=detail)


def _validate(payload: dict[str, Any]) -> DynamicsTranscriptIn:
    try:
        return DynamicsTranscriptIn.model_validate(payload)
    except ValidationError as e:
        errors = [{"field": ".".join(str(p) for p in err["loc"]) or "body", "message": err["msg"]} for err in e.errors()]
        raise _error(422, "validation_error", "Некорректные поля запроса: " + ", ".join(x["field"] for x in errors), errors=errors) from e


def _audio_url(value: str | None) -> str | None:
    text = (value or "").strip()
    if not text:
        return None
    if not text.lower().startswith(("https://", "http://")) or len(text) > 2048:
        raise _error(422, "invalid_audio_url", "audio_url: ожидается ссылка http(s) не длиннее 2048 символов",
                     errors=[{"field": "audio_url", "message": "ожидается ссылка http(s)"}])
    return text


def _checklist_version(db: Session, version_id: int | None) -> ChecklistVersion:
    if version_id is not None:
        version = svc.get_version(db, version_id)
        if version is None:
            raise _error(404, "checklist_version_not_found", f"Версия чек-листа {version_id} не найдена")
        return version
    version = svc.active_version(db)
    if version is None:
        raise _error(400, "no_checklist", "Нет ни одной версии чек-листа: создайте чек-лист перед оценкой")
    return version


def _provider(db: Session, provider: str | None, model: str | None) -> tuple[str, str | None]:
    name = (provider or "").strip()
    if not name:
        row = db.get(Setting, "default_provider")
        name = str(row.value).strip() if row is not None and row.value else settings.default_provider
    base, _, tail = name.partition(":")  # "anthropic:claude-sonnet-5-5" = provider + model
    base = base.strip().lower()
    if base not in KNOWN_PROVIDERS:
        message = f"Неизвестный провайдер «{name}»: доступны {', '.join(KNOWN_PROVIDERS)}"
        raise _error(422, "unknown_provider", message, errors=[{"field": "provider", "message": message}])
    return base, ((model or "").strip() or tail.strip() or None)


# --------------------------------------------------------------------------------------------- storage helpers


def _dataset(db: Session) -> Dataset:
    ds = db.execute(select(Dataset).where(Dataset.source == SOURCE).order_by(Dataset.id)).scalars().first()
    if ds is None:
        ds = Dataset(group_id=DATASET_GROUP_ID, source=SOURCE, record_count=0)
        db.add(ds)
        db.flush()
    return ds


def _existing_evaluation(db: Session, conversation_id: str, version_id: int) -> dict[str, Any] | None:
    """A running or completed evaluation (or a queued job) of this conversation with this checklist version."""
    ev = db.execute(select(Evaluation).where(Evaluation.conversation_id == conversation_id, Evaluation.checklist_version_id == version_id,
                                             Evaluation.status.in_(("queued", "running", "completed")))
                    .order_by(Evaluation.id.desc())).scalars().first()
    if ev is not None:
        return {"evaluation_id": ev.id, "existing_job_id": ev.job_id, "evaluation_status": ev.status}
    for job in db.execute(select(Job).where(Job.status.in_(("queued", "running"))).order_by(Job.created_at.desc())).scalars():
        params = job.params or {}
        if params.get("checklist_version_id") == version_id and conversation_id in (params.get("conversation_ids") or []):
            return {"evaluation_id": None, "existing_job_id": job.id, "evaluation_status": job.status}
    return None


def _spent_24h(db: Session) -> float:
    since = datetime.now(UTC) - timedelta(hours=24)
    total = db.execute(select(func.coalesce(func.sum(Evaluation.cost_usd), 0.0)).where(Evaluation.created_at >= since)).scalar()
    return float(total or 0.0)


def _platform_guards() -> Any:
    """cqa.jobs.guards (WP platform): the single source of truth for the §3 guards; None on builds without it."""
    try:
        from ..jobs import guards
    except Exception:  # noqa: BLE001  # pragma: no cover - older builds without cqa.jobs.guards
        return None
    return guards


def _guard(db: Session, estimate: float) -> dict[str, Any] | None:
    """Fallback copy of docs/ARCH_CONTRACTS.md §3 for a one-dialog run (used only without cqa.jobs.guards)."""
    budget = float(getattr(settings, "daily_budget_usd", 0.0) or 0.0)
    if budget > 0 and estimate > 0:
        spent = _spent_24h(db)
        if spent + estimate > budget:
            return {"code": "budget_exceeded", "retry_after": 3600,
                    "message": (f"Дневной лимит расходов на оценку исчерпан: за 24 часа потрачено ${spent:.2f} из ${budget:.2f}, "
                                f"оценка этого диалога ≈ ${estimate:.2f}. Стенограмма сохранена, оценку можно запустить позже."),
                    "budget": {"spent_24h": round(spent, 4), "limit": budget, "remaining": round(max(budget - spent, 0.0), 4),
                               "estimate": estimate}}
    max_jobs = int(getattr(settings, "max_concurrent_jobs", 0) or 0)
    if max_jobs > 0:
        running = int(db.execute(select(func.count(Job.id)).where(Job.status.in_(("queued", "running")))).scalar() or 0)
        if running >= max_jobs:
            return {"code": "too_many_jobs", "retry_after": 60, "running_jobs": running, "limit": max_jobs,
                    "message": f"Уже выполняется заданий оценки: {running} (лимит {max_jobs}). Стенограмма сохранена, повторите запрос позже."}
    return None


def _start_job(conversation_id: str, version_id: int, *, provider: str, model: str | None, estimate: float,
               paid: bool | None = None) -> str:
    """cqa.jobs.runner.start_evaluation_job for one dialog; the estimate goes into Job.params when the runner accepts
    extra_params (the platform guards count it as reserved budget while the job runs)."""
    start = jobs.start_evaluation_job
    kwargs: dict[str, Any] = {"provider": provider, "model": model, "mode": "full", "base_version_id": None, "only_codes": None}
    try:
        params = inspect.signature(start).parameters
    except (TypeError, ValueError):  # pragma: no cover - builtins/C callables
        params = {}  # type: ignore[assignment]
    if "extra_params" in params or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        kwargs["extra_params"] = {"estimated_cost_usd": estimate, "source": SOURCE}
        if paid is not None:
            kwargs["extra_params"]["paid"] = paid
    return start([conversation_id], version_id, **kwargs)


def _warnings(stats: dict[str, Any]) -> list[str]:
    out: list[str] = []
    source = stats.get("conversation_id_source")
    if source == "content_hash":
        out.append("Идентификатор диалога не передан: использован хэш содержимого. Передайте msdyn_ocliveworkitemid, "
                   "чтобы карточка открывалась из формы беседы.")
    elif source in ("msdyn_transcriptid", "_objectid_value", "objectid"):
        out.append("Использован идентификатор стенограммы (msdyn_transcript), а не беседы: форма Conversation передаёт id беседы "
                   "(msdyn_ocliveworkitem), передайте его в conversation_id.")
    if stats.get("order") == "reversed":
        out.append("Сообщения шли от новых к старым: порядок развёрнут в хронологический.")
    if stats.get("private_ignored"):
        out.append("Все сообщения агента помечены private: тег проигнорирован, сообщения сохранены как реплики оператора.")
    speakers = stats.get("speakers") or {}
    if not speakers.get("client"):
        out.append("В стенограмме нет реплик клиента.")
    if not speakers.get("operator"):
        out.append("В стенограмме нет реплик оператора.")
    return out


def _result(conv: Conversation, *, created: bool, stats: dict[str, Any]) -> dict[str, Any]:
    return {
        "conversation_id": conv.id,
        "turns": conv.turn_count,
        "language": conv.language,
        "job_id": None,
        "card_url": card_url(conv.id),
        "embed_url": embed_url(conv.id),
        "channel": conv.channel,
        "created": created,
        "operator_name": conv.operator_name,
        "agent_name": conv.agent_name,
        "first_response_seconds": conv.first_response_seconds,
        "dataset_id": conv.dataset_id,
        "language_detected": bool(stats.get("language_detected")),
        "speakers": stats.get("speakers") or {},
        "d365": {k: stats.get(k) for k in ("liveworkitem_id", "transcript_id", "annotation_id", "conversation_id_source", "channel_source",
                                          "source_field", "messages_total", "skipped", "internal_messages", "order", "agents")},
        "warnings": _warnings(stats),
    }


# --------------------------------------------------------------------------------------------- POST transcripts


@router.post("/dynamics/transcripts", openapi_extra=_OPENAPI_BODY)
async def ingest_dynamics_transcript(request: Request, db: Session = Depends(get_db)):
    """Push one Dynamics 365 Omnichannel transcript. Idempotent per conversation id, with the re-import rules of every
    source: an unchanged re-post keeps the stored turns and a lead's manual operator attribution, a changed one replaces
    the turns and marks earlier evaluations stale (409 ``conversation_busy`` while the dialog is being evaluated). With
    ``evaluate: true`` an evaluation job starts unless the same content was already evaluated (``force`` overrides)."""
    _check_integration_key(request)
    _require_permission(request, "integrations.ingest")
    length = request.headers.get("content-length") or "0"
    if length.isdigit() and int(length) > MAX_BODY_BYTES:
        raise _error(413, "payload_too_large", f"Тело запроса больше {MAX_BODY_BYTES // (1024 * 1024)} МБ")
    payload = _read_json(await request.body())
    return await run_in_threadpool(_ingest, request, payload, db)


def _ingest(request: Request, payload: dict[str, Any], db: Session) -> Any:
    body = _validate(payload)
    if body.evaluate:
        _require_permission(request, "evaluations.run")
    audio_url = _audio_url(body.audio_url)
    try:
        record = parse_transcript(payload, conversation_id=body.conversation_id, language=body.language, channel=body.channel,
                                  private_messages=body.private_messages, control_messages=body.control_messages, order=body.order,
                                  participants=body.participants, audio_url=audio_url)
    except DynamicsParseError as e:
        raise HTTPException(status_code=e.status, detail=e.as_detail()) from e
    stats = record.pop("d365")
    cid = record["conversation_id"]

    existing = db.get(Conversation, cid)
    if existing is not None and (existing.meta or {}).get("source") != SOURCE and not body.overwrite:
        raise _error(409, "conversation_exists", f"Диалог «{cid}» уже существует и загружен не из Dynamics 365. "
                     "Передайте overwrite: true, чтобы заменить его стенограммой из Dynamics 365.", conversation_id=cid)
    version: ChecklistVersion | None = None
    provider, model = None, body.model
    if body.evaluate:  # validate before writing anything
        version = _checklist_version(db, body.checklist_version_id)
        provider, model = _provider(db, body.provider, body.model)
        try:
            estimator.members_for(db, provider, model)  # an unpriced Claude model would book $0 and escape the budget
        except UnknownModelError as e:
            raise _error(422, "unknown_model", str(e), model=e.model, errors=[{"field": "model", "message": str(e)}]) from e

    previous_fingerprint = ((existing.meta or {}).get("d365") or {}).get("content_hash") if existing is not None else None
    fingerprint = content_hash(record)
    dataset = _dataset(db)
    try:
        conv = upsert_record(db, record, dataset_id=dataset.id)
    except ConversationBusy as e:  # same rule as paste/upload: an evaluation in progress keeps the transcript it judges
        db.rollback()
        raise HTTPException(status_code=409, headers={"Retry-After": str(BUSY_RETRY_AFTER_S)}, detail={
            "code": "conversation_busy", "conversation_id": cid,
            "message": f"Диалог «{cid}» сейчас оценивается: отправьте стенограмму после завершения оценки."}) from e
    enrich_conversation(conv, stats, fingerprint=fingerprint)
    # source-system identity (D365 display name of the agent with most messages); operator_name stays the greeting-detected
    # name, or a lead's manual attribution (re-posts keep it)
    agents = [a for a in (stats.get("agents") or []) if isinstance(a, str) and a.strip()]
    conv.agent_name = agents[0].strip()[:128] if agents else None
    db.flush()
    dataset.record_count = int(db.execute(select(func.count(Conversation.id)).where(Conversation.dataset_id == dataset.id)).scalar() or 0)
    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise _error(409, "concurrent_update", f"Диалог «{cid}» одновременно обновляется другим запросом, повторите отправку",
                     conversation_id=cid) from e
    log.info("d365 transcript %s: %s turns, %s, %s, source=%s", cid, conv.turn_count, conv.language, conv.channel, stats.get("source_field"))

    result = _result(conv, created=existing is None, stats=stats)
    if not body.evaluate or version is None or provider is None:
        result["evaluation"] = {"status": "not_requested"}
        return result
    ingested = {"ingested": True, **{k: result[k] for k in ("conversation_id", "turns", "language", "card_url", "embed_url")}}
    try:  # per-dialog, token-based estimate of what the worker will judge (members, criteria, transcript length)
        est = estimator.estimate_run(db, [conv], version, provider=provider, model=model)
    except UnknownModelError as e:  # pragma: no cover - validated before the upsert
        return JSONResponse(status_code=422, content={"error": {"code": "unknown_model", "message": str(e), **ingested}})
    if est.too_long:
        too_long = est.too_long[0]
        return JSONResponse(status_code=422, content={"error": {
            "code": "transcript_too_long", **ingested, "chars": too_long["chars"],
            "message": (f"Стенограмма сохранена, но слишком длинная для оценки: {too_long['chars']} символов и {too_long['turns']} "
                        f"реплик (допускается до {settings.max_transcript_chars} символов и {settings.max_transcript_turns} реплик).")}})
    estimate = est.total_usd
    guards = _platform_guards()
    # dedupe + guards + job creation are atomic in this process (like /evaluations/run): two identical pushes start one job
    with getattr(jobs, "start_lock", None) or contextlib.nullcontext():
        if not body.force and previous_fingerprint == fingerprint:
            found = _existing_evaluation(db, cid, version.id)
            if found is not None:
                result["evaluation"] = {"status": "skipped_unchanged", "checklist_version_id": version.id, **found}
                return result
        if guards is not None:
            try:
                guards.check_run(db, 1, estimate, free=est.free)
            except guards.GuardError as e:
                retry = e.retry_after_s
                return JSONResponse(status_code=e.status, content={"error": {**e.envelope()["error"], **ingested}},
                                    headers={"Retry-After": str(retry)} if retry else None)
        else:
            blocked = _guard(db, estimate)
            if blocked is not None:
                retry_after = blocked.pop("retry_after")
                return JSONResponse(status_code=429, content={"error": {**blocked, **ingested}}, headers={"Retry-After": str(retry_after)})
        try:
            job_id = _start_job(cid, version.id, provider=provider, model=model, estimate=estimate, paid=not est.free)
        except HTTPException:
            raise
        except Exception as e:  # the transcript is stored; tell the caller the evaluation did not start
            log.exception("d365 transcript %s: evaluation job did not start", cid)
            return JSONResponse(status_code=503, content={"error": {"code": "evaluation_not_started", **ingested,
                                                                    "message": f"Стенограмма сохранена, но оценка не запущена: {e}"}},
                                headers={"Retry-After": "60"})
    result["job_id"] = job_id
    result["evaluation"] = {"status": "started", "job_id": job_id, "checklist_version_id": version.id, "provider": provider, "model": model,
                            "estimated_cost_usd": estimate}
    return result


# --------------------------------------------------------------------------------------------- GET manifest


def config_snippet(api_base: str) -> str:
    """<script> for the <head> of dist-single/index.html: maps the D365 web resource parameters (id, data) to CQA_CONFIG."""
    return (
        "<script>\n"
        "  /* CQA: Dynamics 365 passes ?id={record GUID}&data=<custom parameter>; the card reads window.CQA_CONFIG. */\n"
        "  (function () {\n"
        "    var q = new URLSearchParams(window.location.search);\n"
        "    var data = new URLSearchParams(q.get(\"data\") || \"\");\n"
        "    var id = (data.get(\"conversation\") || q.get(\"conversation\") || q.get(\"id\") || \"\").replace(/[{}]/g, \"\").toLowerCase();\n"
        "    window.CQA_CONFIG = {\n"
        f"      apiUrl: data.get(\"api\") || \"{api_base}\",\n"
        "      embed: true,\n"
        "      conversation: id || undefined\n"
        "    };\n"
        "  })();\n"
        "</script>"
    )


def loader_html(api_base: str) -> str:
    """Alternative to editing the build: a second web resource next to index.html that forwards the D365 parameters in the
    URL hash (the card reads ?embed/&conversation/&api also from the hash; the hash is never validated by Dynamics 365)."""
    return (
        "<!doctype html>\n<html><head><meta charset=\"utf-8\"><title>CQA</title></head><body>\n<script>\n"
        "  (function () {\n"
        "    var q = new URLSearchParams(window.location.search);\n"
        "    var data = new URLSearchParams(q.get(\"data\") || \"\");\n"
        "    var id = (data.get(\"conversation\") || q.get(\"id\") || \"\").replace(/[{}]/g, \"\").toLowerCase();\n"
        f"    var api = data.get(\"api\") || \"{api_base}\";\n"
        "    window.location.replace(\"index.html#?embed=1&conversation=\" + encodeURIComponent(id) + \"&api=\" + encodeURIComponent(api));\n"
        "  })();\n"
        "</script>\n</body></html>"
    )


def form_script(web_base: str) -> str:
    """OnLoad handler for the IFRAME alternative (an IFRAME control named IFRAME_cqa_card on the Conversation form)."""
    return (
        "function cqaConversationOnLoad(executionContext) {\n"
        "  var formContext = executionContext.getFormContext();\n"
        "  var id = (formContext.data.entity.getId() || \"\").replace(/[{}]/g, \"\").toLowerCase();\n"
        "  var frame = formContext.getControl(\"IFRAME_cqa_card\");\n"
        "  if (frame && id) {\n"
        f"    frame.setSrc(\"{web_base}/?embed=1&conversation=\" + encodeURIComponent(id));\n"
        "  }\n"
        "}"
    )


def example_payload() -> dict[str, Any]:
    return {
        "conversation_id": "7f0c2a4e-1b3d-4c5e-9f60-00000000d365",
        "annotation": {"annotationid": "3a5c7e90-0000-4000-8000-00000000a365", "objecttypecode": "msdyn_transcript",
                       "_objectid_value": "9b1d3f50-0000-4000-8000-00000000f365", "documentbody": encode_documentbody(sample_messages())},
        "evaluate": False,
    }


@router.get("/dynamics/manifest")
def dynamics_manifest(request: Request):
    """Everything a Dynamics 365 administrator needs to embed the evaluation card and push transcripts (no secrets)."""
    api = _public_api_base(request)
    web = web_base_url()
    ingest_url = f"{api}/api/integrations/dynamics/transcripts"
    key_required = bool(_config("integrations_api_key", INTEGRATION_KEY_ENV))
    headers = {"Content-Type": "application/json", "X-CQA-Role": "analyst", "X-CQA-User": "Power%20Automate"}
    if key_required:
        headers[INTEGRATION_KEY_HEADER] = "<ключ интеграции>"
    flow_body = {
        "conversation_id": "@{outputs('Get_transcript')?['body/_msdyn_liveworkitemidid_value']}",
        "msdyn_channel": "@{outputs('Get_conversation')?['body/msdyn_channel']}",
        "annotation": {
            "annotationid": "@{triggerOutputs()?['body/annotationid']}",
            "objecttypecode": "@{triggerOutputs()?['body/objecttypecode']}",
            "_objectid_value": "@{triggerOutputs()?['body/_objectid_value']}",
            "documentbody": "@{triggerOutputs()?['body/documentbody']}",
        },
        "evaluate": "@{equals(outputs('Get_conversation')?['body/statecode'], 1)}",
    }
    return {
        "integration": "dynamics365",
        "manifest_version": 1,
        "name": "CQA — карточка оценки диалога",
        "team_name": getattr(settings, "team_name", None),
        "app_version": getattr(settings, "app_version", None),
        "organization_url": D365_ORG_URL,
        "api_base_url": api,
        "web_base_url": web,
        "docs": "docs/INTEGRATION_D365.md",
        "endpoints": {
            "ingest": {"method": "POST", "url": ingest_url, "permission": "integrations.ingest", "audit_action": "integration.dynamics.ingest"},
            "manifest": {"method": "GET", "url": f"{api}/api/integrations/dynamics/manifest", "permission": "read"},
            "conversation": {"method": "GET", "url": f"{api}/api/conversations/{{conversation_id}}"},
            "evaluation": {"method": "GET", "url": f"{api}/api/conversations/{{conversation_id}}/evaluation"},
            "job": {"method": "GET", "url": f"{api}/api/jobs/{{job_id}}"},
            "job_events": {"method": "GET", "url": f"{api}/api/jobs/{{job_id}}/events", "content_type": "text/event-stream"},
        },
        "links": {"card": f"{web}/#/conversations/{{conversation_id}}", "embed": f"{web}/?embed=1&conversation={{conversation_id}}"},
        "web_resource": {
            "build_command": "pnpm build:single",
            "working_directory": "web",
            "output": "dist-single/index.html",
            "output_path": "web/dist-single/index.html",
            "approx_size_mb": 1.7,
            "type": "Webpage (HTML)",
            "suggested_name": "cqa_/card/index.html",
            "display_name": "CQA — карточка оценки",
            "runtime_config": {"apiUrl": api, "embed": True, "conversation": "<msdyn_ocliveworkitem id>"},
            "config_snippet": config_snippet(api),
            "config_snippet_placement": "внутрь <head> файла dist-single/index.html, до первого <script>",
            "query_parameters": {"embed": "1", "conversation": "<conversation id>", "api": "<API base URL>"},
            "d365_parameters": {"id": "GUID записи формы ({...}, регистр не важен)", "data": f"api={api}"},
            "loader_html": loader_html(api),
            "loader_suggested_name": "cqa_/card/loader.html",
            "size_note": "Размер веб-ресурса ограничен тем же параметром, что и вложения почты (Organization.MaxUploadFileSize).",
        },
        "form_tab": {
            "table": "msdyn_ocliveworkitem",
            "form": "Conversation (основная форма беседы)",
            "tab_label": "Оценка качества",
            "steps": [
                "Соберите файл: cd web && pnpm install && pnpm build:single → web/dist-single/index.html.",
                "Вставьте config_snippet в <head> файла dist-single/index.html до первого <script> (или используйте loader_html, не меняя сборку).",
                ("make.powerapps.com → Решения → ваше решение → Создать → Другое → Веб-ресурс: тип «Веб-страница (HTML)», имя "
                "cqa_/card/index.html (префикс издателя вашего решения), загрузите файл, сохраните и опубликуйте."),
                "Откройте основную форму таблицы «Беседа» (msdyn_ocliveworkitem) в конструкторе форм и добавьте вкладку «Оценка качества».",
                "Добавьте на вкладку компонент «Веб-ресурс HTML» и выберите cqa_/card/index.html (или cqa_/card/loader.html).",
                ("В свойствах веб-ресурса включите «Передавать код типа объекта и уникальный идентификатор записи в качестве параметров» "
                f"(форма передаст ?id={{GUID}}) и укажите настраиваемый параметр (data): api={api}."),
                "Растяните компонент на всю вкладку (например, 30 строк), сохраните и опубликуйте форму.",
                "Откройте закрытую беседу, стенограмма которой уже отправлена потоком Power Automate: вкладка покажет карточку оценки.",
            ],
        },
        "iframe_alternative": {
            "description": "Вместо веб-ресурса: элемент IFRAME на форме беседы с размещённым веб-приложением и скрипт OnLoad.",
            "iframe_name": "IFRAME_cqa_card",
            "form_script": form_script(web),
            "steps": [
                "Добавьте на вкладку элемент IFRAME с именем IFRAME_cqa_card (URL можно оставить about:blank).",
                "Добавьте веб-ресурс JavaScript с form_script и обработчик OnLoad формы cqaConversationOnLoad (передавать контекст выполнения).",
                f"Запросы к API идут с {web}: этот источник должен быть в CORS_ORIGINS (или «*»). При строгой CSP добавьте {web} в frame-src.",
            ],
        },
        "power_automate": {
            "trigger": {"connector": "Microsoft Dataverse", "operation": "When a row is added, modified or deleted",
                        "change_type": "Added or Modified", "table": "Notes (annotations)", "scope": "Organization",
                        "filter_rows": "objecttypecode eq 'msdyn_transcript'"},
            "steps": [
                "Get a row by ID: таблица Transcripts (msdyn_transcripts), Row ID = _objectid_value заметки, Select columns: _msdyn_liveworkitemidid_value (шаг Get_transcript).",
                "Get a row by ID: таблица Conversations (msdyn_ocliveworkitems), Row ID = _msdyn_liveworkitemidid_value, Select columns: statecode,msdyn_channel (шаг Get_conversation).",
                f"HTTP (премиум-коннектор): POST {ingest_url}, заголовки и тело из http_action; evaluate = true только для закрытой беседы (statecode = 1).",
                "Необязательно: Parse JSON ответа и запись card_url в настраиваемый столбец беседы (Update a row).",
            ],
            "http_action": {"method": "POST", "uri": ingest_url, "headers": headers, "body": flow_body},
            "retry_policy": "По умолчанию HTTP-действие повторяет 408/429/5xx; повторная отправка безопасна (upsert по id беседы, без повторной оценки неизменённой стенограммы).",
            "example_payload": example_payload(),
            "example_curl": (f"curl -X POST {ingest_url} -H 'Content-Type: application/json' "
                             "-d @payload.json  # payload.json = power_automate.example_payload"),
        },
        "transcript_format": {
            "storage": "annotation (Notes): objecttypecode = 'msdyn_transcript', objectid → msdyn_transcript, documentbody = base64(JSON)",
            "conversation_link": "msdyn_transcript.msdyn_liveworkitemidid → msdyn_ocliveworkitem.activityid",
            "accepted_inputs": ["annotation.documentbody", "documentbody", "transcript (decoded JSON)", "messages (plain list)"],
            "speaker_mapping": {"customer / end user": "client", "agent / user": "operator", "bot / application": "bot",
                                "control / system": "system", "private agent message": "system «[internal] …» (private_messages=skip — пропустить)"},
            "language": "RU | KZ | EN; без language — по буквам: әғқңөұүһі → KZ, иная кириллица → RU, иначе EN",
            "channel": "agent chat; voice call при msdyn_channel 192370000 (Voice) / 192440000 (Voice call), channel=voice или audio_url",
        },
        "security": {
            "permission": "integrations.ingest (роли analyst, lead, admin; без X-CQA-Role — demo)",
            "integration_key": {"header": INTEGRATION_KEY_HEADER, "env": INTEGRATION_KEY_ENV, "required": key_required},
            "cors": {"configured_origins": getattr(settings, "cors_origins", None),
                     "needed_origins": [D365_ORG_URL, web],
                     "note": "Веб-ресурс работает с домена организации D365: добавьте его в CORS_ORIGINS, если там не «*»."},
            "csp": ("Стандартная CSP моделируемых приложений разрешает встроенные скрипты и внешние запросы. В строгом режиме (Strict CSP) "
                    "добавьте домен API в connect-src и разрешите встроенный скрипт сборки в script-src (или используйте IFRAME с frame-src)."),
            "pii": ("Стенограммы содержат персональные данные. Перед отправкой в облачную модель телефоны, ИИН, карты, IBAN и e-mail "
                    "маскируются (настройка pii_masking); локальный провайдер получает текст без маскирования. Имена клиентов из "
                    "Dynamics 365 (displayName) не сохраняются."),
        },
        "limitations": [
            "Голосовые стенограммы из файловых столбцов msdyn_transcript (msdyn_voicetranscript) не разбираются: для звонков передайте messages и audio_url.",
            "Вложения не загружаются: в реплике остаётся пометка «[вложение: имя файла]».",
            "Форма беседы передаёт веб-ресурсу только id и data: без config_snippet или loader_html карточка не узнает id беседы.",
            "Карточка показывает диалог, только если стенограмма уже отправлена в API (поток Power Automate при закрытии беседы).",
        ],
    }
