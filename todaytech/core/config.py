"""Application settings loaded from .env and validated at startup.

Secrets are NEVER logged or printed. If a required key is missing the process
exits immediately with a human-readable error message.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from todaytech.core.models import UserProfile

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class Settings(BaseSettings):
    """All environment-level configuration.  Loaded from ``.env``."""

    model_config = SettingsConfigDict(
        env_file=(".env", str(Path(__file__).resolve().parent.parent.parent / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Telegram ──────────────────────────────────────────────
    telegram_bot_token: str = ""
    telegram_user_id: int = 0

    # ── Search APIs (at least one required) ───────────────────
    tavily_api_key: str = ""
    brave_search_api_key: str = ""
    google_search_api_key: str = ""
    google_search_cx: str = ""
    serper_api_key: str = ""
    serpapi_api_key: str = ""

    # ── LLM providers (at least one required) ─────────────────
    gemini_api_key: str = ""
    groq_api_key: str = ""
    openrouter_api_key: str = ""
    ollama_base_url: str = "http://localhost:11434"

    # ── Reddit (optional) ────────────────────────────────────
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "TodayTech/1.0"

    # ── Telegram channels (optional) ─────────────────────────
    telegram_api_id: int = 0
    telegram_api_hash: str = ""

    # ── Email ingestion (optional) ───────────────────────────
    imap_host: str = ""
    imap_port: int = 993
    imap_user: str = ""
    imap_password: str = ""

    # ── Database ─────────────────────────────────────────────
    database_url: str = "sqlite+aiosqlite:///data/todaytech.db"

    # ── IP Watcher ───────────────────────────────────────────
    ip_watcher_interval_minutes: int = Field(
        default=5, validation_alias="IP_WATCHER_INTERVAL_MINUTES"
    )

    # ── Logging ──────────────────────────────────────────────
    log_level: str = "INFO"

    # ── Derived paths (not from .env) ────────────────────────
    profile_path: Path = Field(default=Path("profile.yaml"))
    sources_path: Path = Field(default=Path("sources.yaml"))
    packs_dir: Path = Field(default=Path("sources/packs"))
    data_dir: Path = Field(default=Path("data"))

    @field_validator(
        "telegram_api_id",
        "telegram_user_id",
        "imap_port",
        "ip_watcher_interval_minutes",
        mode="before",
    )
    @classmethod
    def _coerce_empty_int(cls, v: Any, info: Any) -> int:
        if v is None or v == "":
            if info.field_name == "imap_port":
                return 993
            if info.field_name == "ip_watcher_interval_minutes":
                return 5
            return 0
        try:
            return int(str(v).strip())
        except (ValueError, TypeError):
            return 5 if info.field_name == "ip_watcher_interval_minutes" else 0

    # ── Validation helpers ───────────────────────────────────

    def validate_runtime(self, require_search_and_llm: bool = True) -> None:
        """Fail fast if critical keys are missing for live bot or scan operations."""
        import os

        if os.environ.get("TODAYTECH_SKIP_KEY_VALIDATION") == "1":
            return

        errors: list[str] = []

        if not self.telegram_bot_token or str(self.telegram_bot_token).startswith("your"):
            errors.append("TELEGRAM_BOT_TOKEN is missing — create a bot via @BotFather.")
        if not self.telegram_user_id:
            errors.append(
                "TELEGRAM_USER_ID is missing — send /start to @userinfobot to find yours."
            )

        if require_search_and_llm:
            has_search = any(
                [
                    self.tavily_api_key,
                    self.brave_search_api_key,
                    self.google_search_api_key and self.google_search_cx,
                    self.serper_api_key,
                    self.serpapi_api_key,
                ]
            )
            if not has_search:
                errors.append(
                    "At least one Search API key is required "
                    "(TAVILY, BRAVE_SEARCH, GOOGLE_SEARCH, SERPER, or SERPAPI)."
                )

            has_llm = any(
                [
                    self.gemini_api_key,
                    self.groq_api_key,
                    self.openrouter_api_key,
                ]
            )
            if not has_llm:
                errors.append(
                    "At least one LLM provider key is required "
                    "(GEMINI, GROQ, or OPENROUTER). Ollama alone is not validated at startup."
                )

        if errors:
            msg = (
                "\n❌  TodayTech configuration errors:\n\n"
                + "\n".join(f"   • {e}" for e in errors)
                + "\n\n   Copy .env.example → .env and fill in the values.\n"
            )
            raise ValueError(msg)

    # ── Helpers ───────────────────────────────────────────────

    @property
    def available_search_providers(self) -> list[str]:
        """Return names of configured search providers."""
        providers: list[str] = []
        if self.tavily_api_key:
            providers.append("tavily")
        if self.brave_search_api_key:
            providers.append("brave")
        if self.google_search_api_key and self.google_search_cx:
            providers.append("google")
        if self.serper_api_key:
            providers.append("serper")
        if self.serpapi_api_key:
            providers.append("serpapi")
        return providers

    @property
    def available_llm_providers(self) -> list[str]:
        """Return names of configured LLM providers in priority order."""
        providers: list[str] = []
        if self.gemini_api_key:
            providers.append("gemini")
        if self.groq_api_key:
            providers.append("groq")
        if self.openrouter_api_key:
            providers.append("openrouter")
        # Ollama always available as fallback
        providers.append("ollama")
        return providers


# ---------------------------------------------------------------------------
# Profile loader
# ---------------------------------------------------------------------------


def load_profile(path: Path | None = None) -> UserProfile | None:
    """Load user profile from YAML. Falls back to profile.example.yaml if profile.yaml is missing."""
    p = path or Path("profile.yaml")
    if not p.exists():
        if Path("profile.example.yaml").exists():
            p = Path("profile.example.yaml")
        else:
            return None
    with open(p, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return UserProfile.model_validate(data)


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached Settings instance."""
    return Settings()  # type: ignore[call-arg]
