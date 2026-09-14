"""
Shared pytest fixtures.

Two things here are deliberate and worth reading before you change them.

1. Modules under test are loaded *by file path*, not by package import.
   ``App/src/data_fetcher/__init__.py`` imports ``universal_data_fetcher``,
   which drags in jugaad-data, nselib, TA-Lib and pandas-ta. TA-Lib needs a C
   toolchain, so a plain ``from App.src.data_fetcher.ticker_resolver import ...``
   would make CI slow and brittle for no benefit. Loading the file directly
   executes only ``ticker_resolver.py`` / ``generic_query_builder.py`` and their
   own imports (stdlib + pandas).

2. The fixture database is generated, not committed. ``.gitignore`` excludes
   ``*.db`` repository-wide, so a checked-in fixture would be silently dropped
   and CI would fail on a fresh clone. Building it takes milliseconds and it
   can never drift from ``tests/fixtures/build_fixture_db.py``.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest

TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent

# Needed by test_config.py, which imports the real ``App.config``.
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _load_isolated(module_name: str, file_path: Path):
    """Execute a single .py file as a module without importing its package."""
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"Could not build a module spec for {file_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_DATA_FETCHER_DIR = PROJECT_ROOT / "App" / "src" / "data_fetcher"

ticker_resolver = _load_isolated(
    "dalal_ticker_resolver", _DATA_FETCHER_DIR / "ticker_resolver.py"
)
generic_query_builder = _load_isolated(
    "dalal_generic_query_builder", _DATA_FETCHER_DIR / "generic_query_builder.py"
)
build_fixture_db = _load_isolated(
    "dalal_build_fixture_db", TESTS_DIR / "fixtures" / "build_fixture_db.py"
)

TickerResolver = ticker_resolver.TickerResolver
GenericQueryBuilder = generic_query_builder.GenericQueryBuilder


# ---------------------------------------------------------------------------
# Integration marker: opt-in only
# ---------------------------------------------------------------------------

def pytest_addoption(parser):
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="Also run @pytest.mark.integration tests, which need the "
             "production database, the network or an API key.",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-integration"):
        return
    skip_integration = pytest.mark.skip(
        reason="integration test; pass --run-integration to enable"
    )
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_integration)


# ---------------------------------------------------------------------------
# Fixture database
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def fixture_db_path(tmp_path_factory) -> Path:
    """Build the synthetic database once per test session."""
    dest = tmp_path_factory.mktemp("dalal-fixture-db") / "fixture_market.db"
    return build_fixture_db.build(dest)


@pytest.fixture(scope="session")
def fixture_schemas(fixture_db_path: Path) -> dict[str, dict[str, Any]]:
    """
    Schema dictionary in the exact shape GenericQueryBuilder expects.

    Mirrors UniversalDataFetcher._discover_sqlite_schema(): every table from
    sqlite_master, columns and types from PRAGMA table_info, plus a row count.
    """
    conn = sqlite3.connect(f"file:{fixture_db_path}?mode=ro", uri=True)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]

        schemas: dict[str, dict[str, Any]] = {}
        for table in tables:
            cursor.execute(f"PRAGMA table_info({table})")
            columns_info = cursor.fetchall()
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            schemas[table] = {
                "columns": [col[1] for col in columns_info],
                "types": {col[1]: col[2] for col in columns_info},
                "row_count": cursor.fetchone()[0],
            }
        return schemas
    finally:
        conn.close()


@pytest.fixture
def resolver(fixture_db_path: Path):
    """A TickerResolver bound to the synthetic database."""
    instance = TickerResolver(str(fixture_db_path))
    yield instance
    instance.conn.close()


@pytest.fixture
def builder(fixture_schemas: dict[str, dict[str, Any]]) -> GenericQueryBuilder:
    """A GenericQueryBuilder wired to the fixture database's real schema."""
    return GenericQueryBuilder(fixture_schemas)


@pytest.fixture
def db_conn(fixture_db_path: Path):
    """
    Read-only connection, so generated SQL can be executed for real.

    A query builder test that only string-matches proves nothing about
    whether the SQL is valid; running it against the fixture does.
    """
    conn = sqlite3.connect(f"file:{fixture_db_path}?mode=ro", uri=True)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def real_db_path() -> Path:
    """
    Path to the production database, for @pytest.mark.integration tests.

    Skips when it is absent, which is the normal case on CI and on a fresh
    clone (the file is ~3.9 GB and is not distributed with the repository).
    """
    path = PROJECT_ROOT / "App" / "database" / "stock_market_new.db"
    if not path.exists():
        pytest.skip(f"production database not present at {path}")
    return path
