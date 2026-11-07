"""Application settings. Values come from environment variables or a .env file
(looked up in the api/ folder first, then the repository root)."""
from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

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

    explanation_language: str = "ru"
    pii_masking: bool = True
    fewshot_from_reviews: bool = True
    judge_effort: str = "high"
    max_parallel_criteria: int = 8
    max_parallel_conversations: int = 3
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])

    # USD per 1M tokens: (input, output, cache_read)
    cost_rates: dict[str, tuple[float, float, float]] = Field(
        default_factory=lambda: {
            "claude-opus-5-5": (4.0, 20.0, 0.20),
            "claude-sonnet-5-5": (2.0, 10.0, 0.20),
            "claude-haiku-5-5": (0.10, 0.50, 0.01),
        }
    )

    def rate_for(self, model: str) -> tuple[float, float, float]:
        for key, rate in self.cost_rates.items():
            if model.startswith(key):
                return rate
        return (0.0, 0.0, 0.0)


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
(settings.data_dir / "audio").mkdir(parents=True, exist_ok=True)
