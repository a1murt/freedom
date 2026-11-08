"""Application settings. Values come from environment variables or a .env file
(looked up in the api/ folder first, then the repository root)."""
from __future__ import annotations

import logging
import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_log = logging.getLogger("cqa.config")
# Railway sets these on every deployment: their presence marks the public hosted instance (CQA_PUBLIC_INSTANCE overrides).
HOSTED_ENV_MARKERS = ("RAILWAY_ENVIRONMENT", "RAILWAY_ENVIRONMENT_NAME", "RAILWAY_PROJECT_ID", "RAILWAY_SERVICE_ID",
                      "RAILWAY_DEPLOYMENT_ID")
CACHE_WRITE_MULTIPLIER = 1.25  # 5-minute prompt-cache writes are billed at 1.25x the input rate (the judge uses the 5 min TTL)

API_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = API_DIR.parent
_ENV_FILES = tuple(str(p) for p in (REPO_DIR / ".env", API_DIR / ".env") if p.exists()) or (".env",)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILES, env_file_encoding="utf-8", extra="ignore")

    anthropic_api_key: str | None = None
    organizer_api_url: str | None = None
    database_url: str = f"sqlite:///{(API_DIR / 'data' / 'cqa.db').as_posix()}"
    data_dir: Path = API_DIR / "data"
    seeds_dir: Path = API_DIR / "seeds"
    prompts_dir: Path = API_DIR / "cqa" / "judge" / "prompts"

    default_provider: str = "anthropic"  # anthropic | local | ensemble
    default_model: str = "claude-opus-5-5"
    secondary_model: str = "claude-sonnet-5-5"
    local_endpoint: str | None = None  # OpenAI-compatible base URL, e.g. http://host:8000/v1
    local_model: str = "qwen3:8b"
    local_api_key: str = "none"
    # extra endpoints PUT /api/settings may select (comma-separated scheme://host[:port][/path]); LOCAL_API_KEY is sent
    # only to LOCAL_ENDPOINT and these. Off the hosted instance loopback and private addresses are accepted as well.
    local_endpoint_allowlist: str | None = None
    local_max_parallel: int = 1       # concurrent requests to the local endpoint (Ollama on a laptop: 1; vLLM on a GPU: 8+)
    local_timeout_s: float = 900.0    # one criterion call on an 8B model on a laptop takes 1-3 minutes
    # sent as "reasoning_effort" to the local endpoint when set: "none" turns a thinking model's reasoning off (Qwen3 on
    # Ollama: one short JSON instead of a long hidden reasoning pass), "low" | "medium" | "high" keep it; empty = not sent
    local_reasoning_effort: str | None = None

    explanation_language: str = "ru"
    pii_masking: bool = True
    fewshot_from_reviews: bool = True
    judge_effort: str = "high"
    # C7 when the operator said goodbye after a resolved question with no summary, no offer of further help and nothing left
    # open (the judge marks it with the flag "bare_farewell"): "yes" | "na" | "no". The organizers answered "N/a" for this case.
    c7_bare_farewell_policy: str = "na"   # ML-owner decision 2026-11-07: organizer answer "N/a" read as the value N/A
    max_parallel_criteria: int = 8
    max_parallel_conversations: int = 3
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])  # env CORS_ORIGINS='["https://cqa-freedom-pi.vercel.app","http://localhost:5173"]'

    # platform guards (public hosted instance: protect the API credit from accidental or abusive runs)
    daily_budget_usd: float = 75.0        # rolling 24h spend cap across all evaluation jobs; 0 disables
    max_dialogs_per_run: int = 200        # one evaluation request may queue at most this many dialogs
    max_concurrent_jobs: int = 2          # running evaluation jobs at the same time
    max_concurrent_trials: int = 2        # single-criterion trials (POST /api/checklists/test-criterion) at the same time
    max_transcript_chars: int = 100_000   # a dialog longer than this is not sent to a judge (≈20x the longest DEV dialog)
    max_transcript_turns: int = 2_000     # same for the number of turns
    spend_ledger_path: Path | None = None  # append-only spend ledger (SQLite) outside the main DB; default <data_dir>/spend_ledger.db
    public_instance: bool | None = None   # env CQA_PUBLIC_INSTANCE; None = detected from the Railway environment
    app_version: str | None = None        # env APP_VERSION; falls back to RAILWAY_GIT_COMMIT_SHA / git
    team_name: str = "Я Тебя Могну"
    # default checklist when several checklists have an active version (imports create their own checklist);
    # None = the active version of the lowest checklist id, i.e. the organizer checklist. A DB setting of the same
    # key (PUT /api/settings {"default_checklist_id": 1}) wins over the environment.
    default_checklist_id: int | None = None

    # integrations (Dynamics 365): public URLs for card/embed links and the manifest, optional shared ingest secret
    web_base_url: str | None = None           # env WEB_BASE_URL, default https://cqa-freedom-pi.vercel.app
    public_api_url: str | None = None         # env PUBLIC_API_URL, else X-Forwarded-Proto/Host of the request
    integrations_api_key: str | None = None   # env INTEGRATIONS_API_KEY → header X-CQA-Integration-Key required

    # admin (database snapshots)
    cqa_admin_token: str | None = None        # env CQA_ADMIN_TOKEN → header X-CQA-Admin-Token required for snapshot writes
    snapshot_keep_pre_restore: int = 5        # automatic pre-restore snapshots kept (manual ones are never pruned)
    snapshot_max_manual: int = 20             # manual snapshots kept at most (a new one beyond this is refused, 409)
    allow_unprotected_snapshots: bool = False  # env ALLOW_UNPROTECTED_SNAPSHOTS=1: snapshot writes without a token on the hosted instance

    # USD per 1M tokens: (input, output, cache_read)
    cost_rates: dict[str, tuple[float, float, float]] = Field(
        default_factory=lambda: {
            "claude-opus-5-5": (4.0, 20.0, 0.20),
            "claude-sonnet-5-5": (2.0, 10.0, 0.20),
            "claude-haiku-5-5": (0.10, 0.50, 0.01),
        }
    )

    def known_model(self, model: str | None) -> str | None:
        """The cost_rates key a model id is priced with: the key itself or a dated variant of it (``<key>-…``); else None."""
        name = (model or "").strip()
        for key in self.cost_rates:
            if name == key or name.startswith(key + "-"):
                return key
        return None

    def is_known_model(self, model: str | None) -> bool:
        """Only models with a configured price may be called: an unpriced model would book $0 and escape the budget."""
        return self.known_model(model) is not None

    def rate_for(self, model: str) -> tuple[float, float, float]:
        """(input, output, cache_read) USD per 1M tokens. An unknown model fails closed: it is priced at the highest
        configured rate (never $0, which would make its spend invisible to the daily budget) and a warning is logged."""
        for key, rate in self.cost_rates.items():
            if (model or "").startswith(key):
                return rate
        if model and model not in _WARNED_MODELS:
            _WARNED_MODELS.add(model)
            _log.warning("no cost rate for model %r: priced at the highest configured rate", model)
        return max_rate(self.cost_rates.values())

    def is_public_instance(self) -> bool:
        """True on the public hosted instance: CQA_PUBLIC_INSTANCE when set, else any Railway deployment variable."""
        if self.public_instance is not None:
            return bool(self.public_instance)
        return any((os.environ.get(name) or "").strip() for name in HOSTED_ENV_MARKERS)


_WARNED_MODELS: set[str] = set()


def max_rate(rates) -> tuple[float, float, float]:
    """Component-wise maximum of (input, output, cache_read) rates; (0, 0, 0) when nothing is configured."""
    rates = [tuple(r) for r in rates]
    if not rates:
        return (0.0, 0.0, 0.0)
    return tuple(max(r[i] for r in rates) for i in range(3))  # type: ignore[return-value]


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
(settings.data_dir / "audio").mkdir(parents=True, exist_ok=True)
