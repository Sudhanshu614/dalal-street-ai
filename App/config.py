"""
Central configuration.

Everything is read from environment variables with sensible defaults, so the
project runs on any machine without editing code. Copy `.env.example` to
`.env` at the repository root and fill it in.

No absolute paths. No machine-specific values. If you need a new setting,
add it here and document it in `.env.example` and the README table.
"""

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # python-dotenv is optional at runtime
    load_dotenv = None

# Repository root, resolved from this file: App/config.py -> App -> <root>
PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Load .env from the repository root, then App/.env as a legacy fallback.
# Existing environment variables always win over file values.
if load_dotenv is not None:
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    load_dotenv(PROJECT_ROOT / "App" / ".env", override=False)


def _as_bool(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _resolve(path_str: str) -> Path:
    """Resolve a path relative to the repository root unless it is absolute."""
    p = Path(path_str).expanduser()
    return p if p.is_absolute() else (PROJECT_ROOT / p)


class Config:
    # --- Paths ---------------------------------------------------------
    PROJECT_ROOT = PROJECT_ROOT
    APP_DIR = PROJECT_ROOT / "App"
    DATABASE_DIR = APP_DIR / "database"
    CACHE_DIR = _resolve(os.getenv("CACHE_DIR", "cache"))
    LOG_DIR = _resolve(os.getenv("LOG_DIR", "logs"))

    # Deployment platforms that mount a persistent disk at /data win, so a
    # container can override without changing configuration.
    _DEFAULT_DB = (
        "/data/stock_market_new.db"
        if Path("/data/stock_market_new.db").exists()
        else "App/database/stock_market_new.db"
    )
    DB_PATH = _resolve(os.getenv("DB_PATH", _DEFAULT_DB))

    # Directory the CF-CA / IPO / master CSVs are discovered from.
    CSV_DIRECTORY = _resolve(os.getenv("CSV_DIRECTORY", "App/database"))

    # --- LLM -----------------------------------------------------------
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

    # Reserved for the multi-provider abstraction, which is not yet wired
    # into the request path. See docs/ROADMAP.md.
    GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
    LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini")

    # --- Backend -------------------------------------------------------
    API_HOST = os.getenv("API_HOST", "127.0.0.1")
    API_PORT = int(os.getenv("API_PORT", "8000"))
    DEV_RELOAD = _as_bool(os.getenv("DEV_RELOAD", "false"))
    LOG_LEVEL = os.getenv("LOG_LEVEL", "debug" if DEV_RELOAD else "info")

    CORS_ORIGINS = [
        o.strip()
        for o in os.getenv("CORS_ORIGINS", "http://localhost:8501").split(",")
        if o.strip()
    ]

    # Bearer token for /admin/*. Empty disables those endpoints entirely.
    ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")

    # --- Frontend ------------------------------------------------------
    API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")

    # --- Tuning --------------------------------------------------------
    PRICE_CACHE_TTL_SEC = int(os.getenv("PRICE_CACHE_TTL_SEC", "60"))

    @classmethod
    def require_gemini_key(cls) -> str:
        if not cls.GEMINI_API_KEY:
            raise RuntimeError(
                "GEMINI_API_KEY is not set. Copy .env.example to .env and add "
                "a key from https://aistudio.google.com/apikey"
            )
        return cls.GEMINI_API_KEY


config = Config()
