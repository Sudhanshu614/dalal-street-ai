"""
Tests for App/config.py.

Config reads everything at *import* time into class attributes, so the only
way to test a different environment is to set the variable and reload the
module. Every test here goes through the `reload_config` fixture, which does
that and restores the original module afterwards.

One trap worth knowing about: config.py calls python-dotenv's load_dotenv()
on `.env` and `App/.env` at import. Both files are gitignored, so CI never
sees them - but a contributor's machine usually has `App/.env` with a real
GEMINI_API_KEY in it, and a reload would quietly inject it and turn the
"unset key" test green for the wrong reason. `reload_config` neutralises
load_dotenv so results depend only on what the test set.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

# Every environment variable config.py reads. Cleared before each reload so a
# developer's real shell environment cannot influence the result.
CONFIG_ENV_VARS = [
    "CACHE_DIR",
    "LOG_DIR",
    "DB_PATH",
    "CSV_DIRECTORY",
    "GEMINI_API_KEY",
    "GEMINI_MODEL",
    "GROQ_API_KEY",
    "LLM_PROVIDER",
    "API_HOST",
    "API_PORT",
    "DEV_RELOAD",
    "LOG_LEVEL",
    "CORS_ORIGINS",
    "ADMIN_TOKEN",
    "API_BASE_URL",
    "PRICE_CACHE_TTL_SEC",
]


@pytest.fixture
def reload_config(monkeypatch):
    """
    Return a callable that re-imports App.config with a controlled environment.

    Usage:
        cfg = reload_config(GEMINI_API_KEY="abc", DEV_RELOAD="true")
    """
    # Stop .env files from leaking into the test environment.
    try:
        import dotenv

        monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)
    except ImportError:  # python-dotenv is optional
        pass

    original = sys.modules.get("App.config")

    def _reload(**env):
        for name in CONFIG_ENV_VARS:
            monkeypatch.delenv(name, raising=False)
        for name, value in env.items():
            monkeypatch.setenv(name, value)

        module = importlib.import_module("App.config")
        return importlib.reload(module)

    yield _reload

    # Leave the module in a sane state for anything that imports it later.
    if original is not None:
        importlib.reload(original)


# ---------------------------------------------------------------------------
# Environment variable precedence
# ---------------------------------------------------------------------------

def test_defaults_apply_when_nothing_is_set(reload_config):
    cfg = reload_config()

    assert cfg.Config.API_HOST == "127.0.0.1"
    assert cfg.Config.API_PORT == 8000
    assert cfg.Config.GEMINI_MODEL == "gemini-2.5-flash"
    assert cfg.Config.LLM_PROVIDER == "gemini"
    assert cfg.Config.API_BASE_URL == "http://localhost:8000"
    assert cfg.Config.PRICE_CACHE_TTL_SEC == 60
    assert cfg.Config.CORS_ORIGINS == ["http://localhost:8501"]
    assert cfg.Config.ADMIN_TOKEN == ""


def test_environment_overrides_defaults(reload_config):
    cfg = reload_config(
        API_HOST="0.0.0.0",
        API_PORT="9001",
        GEMINI_MODEL="gemini-1.5-pro",
        API_BASE_URL="https://api.example.test",
        PRICE_CACHE_TTL_SEC="300",
        ADMIN_TOKEN="s3cr3t",
    )

    assert cfg.Config.API_HOST == "0.0.0.0"
    assert cfg.Config.API_PORT == 9001
    assert cfg.Config.GEMINI_MODEL == "gemini-1.5-pro"
    assert cfg.Config.API_BASE_URL == "https://api.example.test"
    assert cfg.Config.PRICE_CACHE_TTL_SEC == 300
    assert cfg.Config.ADMIN_TOKEN == "s3cr3t"


@pytest.mark.parametrize("name, value", [("API_PORT", "9001"), ("PRICE_CACHE_TTL_SEC", "300")])
def test_numeric_settings_are_coerced_to_int(reload_config, name, value):
    """These are handed to uvicorn and to arithmetic; strings would break both."""
    cfg = reload_config(**{name: value})

    assert isinstance(getattr(cfg.Config, name), int)


def test_log_level_follows_dev_reload_unless_set_explicitly(reload_config):
    """LOG_LEVEL has a computed default: debug in dev, info otherwise."""
    assert reload_config(DEV_RELOAD="true").Config.LOG_LEVEL == "debug"
    assert reload_config(DEV_RELOAD="false").Config.LOG_LEVEL == "info"
    assert reload_config(DEV_RELOAD="true", LOG_LEVEL="warning").Config.LOG_LEVEL == "warning"


def test_module_level_singleton_matches_the_class(reload_config):
    cfg = reload_config(API_PORT="7777")

    assert isinstance(cfg.config, cfg.Config)
    assert cfg.config.API_PORT == 7777


# ---------------------------------------------------------------------------
# _as_bool
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", ["1", "true", "TRUE", "True", "yes", "YES", "on", "ON", " true ", "  on"])
def test_as_bool_truthy(reload_config, value):
    assert reload_config()._as_bool(value) is True


@pytest.mark.parametrize(
    "value",
    ["0", "false", "FALSE", "no", "off", "", "   ", "maybe", "2", "y", "t", "enabled", "null", "None"],
)
def test_as_bool_falsy(reload_config, value):
    """
    Only the exact set {1, true, yes, on} is truthy.

    'y', 't' and '2' being false is deliberate: a narrow allowlist beats a
    clever parser when the value decides whether debug mode is on.
    """
    assert reload_config()._as_bool(value) is False


@pytest.mark.parametrize("value, expected", [(True, True), (False, False), (1, True), (0, False), (None, False)])
def test_as_bool_accepts_non_strings(reload_config, value, expected):
    """_as_bool str()-casts first, so non-string input cannot raise."""
    assert reload_config()._as_bool(value) is expected


def test_dev_reload_reads_through_as_bool(reload_config):
    assert reload_config(DEV_RELOAD="yes").Config.DEV_RELOAD is True
    assert reload_config(DEV_RELOAD="off").Config.DEV_RELOAD is False
    assert reload_config().Config.DEV_RELOAD is False


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

def test_relative_paths_resolve_against_the_repository_root(reload_config):
    """
    A relative path in the environment is anchored to PROJECT_ROOT, not to the
    process working directory - so the app behaves the same however it is
    launched.
    """
    cfg = reload_config(CACHE_DIR="var/cache", LOG_DIR="var/logs")

    assert cfg.Config.CACHE_DIR == cfg.PROJECT_ROOT / "var" / "cache"
    assert cfg.Config.LOG_DIR == cfg.PROJECT_ROOT / "var" / "logs"
    assert cfg.Config.CACHE_DIR.is_absolute()


def test_absolute_paths_are_left_alone(reload_config, tmp_path):
    absolute = tmp_path / "somewhere" / "else"
    cfg = reload_config(CACHE_DIR=str(absolute), DB_PATH=str(absolute / "x.db"))

    assert cfg.Config.CACHE_DIR == absolute
    assert cfg.Config.DB_PATH == absolute / "x.db"


def test_default_paths_are_relative_to_the_repository_root(reload_config):
    cfg = reload_config()

    assert cfg.Config.CACHE_DIR == cfg.PROJECT_ROOT / "cache"
    assert cfg.Config.LOG_DIR == cfg.PROJECT_ROOT / "logs"
    assert cfg.Config.APP_DIR == cfg.PROJECT_ROOT / "App"
    assert cfg.Config.DATABASE_DIR == cfg.PROJECT_ROOT / "App" / "database"
    assert cfg.Config.CSV_DIRECTORY == cfg.PROJECT_ROOT / "App" / "database"


def test_project_root_points_at_the_repository(reload_config):
    """config.py lives at App/config.py, so PROJECT_ROOT is its grandparent."""
    cfg = reload_config()

    assert cfg.PROJECT_ROOT == Path(cfg.__file__).resolve().parents[1]
    assert (cfg.PROJECT_ROOT / "App" / "config.py").exists()


def test_resolve_expands_a_user_home_path(reload_config):
    cfg = reload_config(CACHE_DIR="~/dalal-cache")

    assert "~" not in str(cfg.Config.CACHE_DIR)
    assert cfg.Config.CACHE_DIR.is_absolute()


def test_db_path_default_is_inside_the_repository(reload_config):
    """
    The default is App/database/stock_market_new.db unless a platform disk is
    mounted at /data - which no CI runner has.
    """
    cfg = reload_config()

    assert cfg.Config.DB_PATH.name == "stock_market_new.db"
    assert cfg.Config.DB_PATH.is_absolute()


# ---------------------------------------------------------------------------
# CORS_ORIGINS
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("http://a.test", ["http://a.test"]),
        ("http://a.test,http://b.test", ["http://a.test", "http://b.test"]),
        (" http://a.test , http://b.test ", ["http://a.test", "http://b.test"]),
        ("http://a.test,,http://b.test", ["http://a.test", "http://b.test"]),
        ("http://a.test,   ,http://b.test", ["http://a.test", "http://b.test"]),
        ("", []),
        (",", []),
        ("   ", []),
    ],
)
def test_cors_origins_splitting(reload_config, raw, expected):
    """
    Comma separated, whitespace trimmed, empties dropped.

    Blank entries matter: a trailing comma producing an empty origin would
    otherwise be handed to the CORS middleware as a real allowed origin.
    """
    assert reload_config(CORS_ORIGINS=raw).Config.CORS_ORIGINS == expected


# ---------------------------------------------------------------------------
# require_gemini_key
# ---------------------------------------------------------------------------

def test_require_gemini_key_raises_with_a_helpful_message(reload_config):
    """
    The error has to tell a new contributor what to do, not just what failed.

    It names the variable, the file to copy, and where to get a key.
    """
    cfg = reload_config()
    assert cfg.Config.GEMINI_API_KEY == ""

    with pytest.raises(RuntimeError) as excinfo:
        cfg.Config.require_gemini_key()

    message = str(excinfo.value)
    assert "GEMINI_API_KEY" in message
    assert ".env.example" in message
    assert ".env" in message
    assert "https://aistudio.google.com/apikey" in message


def test_require_gemini_key_returns_the_key_when_set(reload_config):
    cfg = reload_config(GEMINI_API_KEY="test-key-not-a-real-secret")

    assert cfg.Config.require_gemini_key() == "test-key-not-a-real-secret"


def test_require_gemini_key_treats_an_empty_string_as_unset(reload_config):
    """An exported-but-blank variable is the classic broken-.env symptom."""
    cfg = reload_config(GEMINI_API_KEY="")

    with pytest.raises(RuntimeError):
        cfg.Config.require_gemini_key()


def test_no_secret_is_baked_into_the_defaults(reload_config):
    """Credentials must come from the environment, never from source."""
    cfg = reload_config()

    assert cfg.Config.GEMINI_API_KEY == ""
    assert cfg.Config.GROQ_API_KEY == ""
    assert cfg.Config.ADMIN_TOKEN == ""
