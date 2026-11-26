from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:  # optional convenience for local development
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    load_dotenv()
except Exception:  # pragma: no cover - dotenv is optional
    pass

BACKEND_DIR = Path(__file__).resolve().parents[1]
VAR_DIR = Path(os.environ.get("EXAMOS_VAR_DIR", BACKEND_DIR / "var"))
VAR_DIR.mkdir(parents=True, exist_ok=True)


def _bool(name: str, default: str = "false") -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: os.environ.get(
        "EXAMOS_DATABASE_URL", f"sqlite:///{(VAR_DIR / 'examos.db').as_posix()}"
    ))
    daily_minutes: int = field(default_factory=lambda: _int("EXAMOS_DAILY_MINUTES", 240))
    timezone: str = field(default_factory=lambda: os.environ.get("EXAMOS_TIMEZONE", "Asia/Shanghai"))
    default_user: str = field(default_factory=lambda: os.environ.get("EXAMOS_DEFAULT_USER", "default"))

    llm_enabled: bool = field(default_factory=lambda: _bool("EXAMOS_LLM_ENABLED"))
    llm_base_url: str = field(default_factory=lambda: os.environ.get("EXAMOS_LLM_BASE_URL", "https://api.deepseek.com/v1"))
    llm_api_key: str = field(default_factory=lambda: os.environ.get("EXAMOS_LLM_API_KEY", ""))
    llm_model: str = field(default_factory=lambda: os.environ.get("EXAMOS_LLM_MODEL", "deepseek-chat"))
    llm_timeout: int = field(default_factory=lambda: _int("EXAMOS_LLM_TIMEOUT", 45))

    # roi / mastery hyper-parameters (documented in README)
    year_decay: float = field(default_factory=lambda: float(os.environ.get("EXAMOS_YEAR_DECAY", "0.9")))
    mastery_prior_alpha: float = field(default_factory=lambda: float(os.environ.get("EXAMOS_MASTERY_ALPHA0", "1.0")))
    mastery_prior_beta: float = field(default_factory=lambda: float(os.environ.get("EXAMOS_MASTERY_BETA0", "3.0")))
    target_mastery: float = field(default_factory=lambda: float(os.environ.get("EXAMOS_TARGET_MASTERY", "0.9")))
    prerequisite_gate: float = field(default_factory=lambda: float(os.environ.get("EXAMOS_PREREQ_GATE", "0.55")))

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


settings = Settings()
