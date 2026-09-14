"""
Tier-by-tier tests for TickerResolver.

The resolver is a waterfall: each tier only runs if every tier above it
declined. That makes tier *order* part of the contract, not an implementation
detail, so every test asserts the resolution method as well as the symbol and
the confidence band. A test that only checked the symbol would still pass if
the answer came from the wrong tier for the wrong reason.

Return shape (verified against the source, not assumed):

    {'resolved_ticker': str | None,        # stocks and ETFs
     'resolved_index_name': str,           # indices only - the key differs!
     'original_ticker': str,
     'resolution_method': str,
     'confidence': int,
     'metadata': dict,
     'entity_type': 'stock' | 'etf' | 'index' | 'unknown'}

Index results carry ``resolved_index_name`` and have no ``resolved_ticker``
key at all. See test_tier_2_6_* below.

All data comes from tests/fixtures/build_fixture_db.py. See tests/README.md
for the symbol-to-tier map and for how to add a case.
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Tier 1 - direct match against active stocks_master
# ---------------------------------------------------------------------------

def test_tier_1_direct_active_symbol(resolver):
    """An active ticker resolves to itself at full confidence, no lookups."""
    result = resolver.resolve("AGNIMOTORS")

    assert result["resolution_method"] == "direct"
    assert result["resolved_ticker"] == "AGNIMOTORS"
    assert result["confidence"] == 100
    assert result["entity_type"] == "stock"
    assert result["metadata"]["status"] == "active"


def test_tier_1_skips_inactive_rows(resolver):
    """
    is_active = 0 must not satisfy Tier 1.

    ORIONMET is in stocks_master but inactive, so it has to fall all the way
    through to the delisting tier.
    """
    result = resolver.resolve("ORIONMET")

    assert result["resolution_method"] != "direct"
    assert result["resolved_ticker"] is None


# ---------------------------------------------------------------------------
# Tier 1b / 1c - ETFs
# ---------------------------------------------------------------------------

def test_tier_1b_etf_direct(resolver):
    """
    ETF symbols are stored in market_etfs.index_name with an '-EQ' suffix
    ('NIFTYBEES-EQ') but queried without it. _load_etf_symbols() strips the
    suffix, so the user-facing form matches directly at 100.
    """
    result = resolver.resolve("NIFTYBEES")

    assert result["resolution_method"] == "etf_direct"
    assert result["resolved_ticker"] == "NIFTYBEES"
    assert result["confidence"] == 100
    assert result["entity_type"] == "etf"


@pytest.mark.parametrize(
    "query, expected",
    [
        ("NIFTY BEES", "NIFTYBEES"),   # whitespace stripped by normalisation
        ("GOLDBEE", "GOLDBEES"),       # trailing 'BEE' gets its 'S' back
        ("bank-bees", "BANKBEES"),     # punctuation and case
    ],
)
def test_tier_1c_etf_normalized(resolver, query, expected):
    """Normalised ETF matches land one tier below direct, at 98."""
    result = resolver.resolve(query)

    assert result["resolution_method"] == "etf_normalized"
    assert result["resolved_ticker"] == expected
    assert result["confidence"] == 98
    assert result["entity_type"] == "etf"


# ---------------------------------------------------------------------------
# Tier 2 - recursive symbol change
# ---------------------------------------------------------------------------

def test_tier_2_recursive_symbol_change_two_hops(resolver):
    """
    A -> B -> C must collapse to C in one call.

    The fixture chains PURVAENG -> PURVAINFRA -> SETUINFRA precisely because a
    single-hop chain would still pass if the loop in resolve() were replaced
    by one lookup. Assert the full chain string, not just the endpoint.
    """
    result = resolver.resolve("PURVAENG")

    assert result["resolution_method"] == "symbol_change"
    assert result["resolved_ticker"] == "SETUINFRA"
    assert result["confidence"] == 100
    assert result["entity_type"] == "stock"

    chain = result["metadata"]["change_chain"]
    assert chain == "PURVAENG -> PURVAINFRA -> SETUINFRA"
    assert result["metadata"]["status"] == "active"


def test_tier_2_resumes_midway_through_a_chain(resolver):
    """Entering the chain at its midpoint still lands on the terminal symbol."""
    result = resolver.resolve("PURVAINFRA")

    assert result["resolution_method"] == "symbol_change"
    assert result["resolved_ticker"] == "SETUINFRA"


def test_tier_2_single_hop(resolver):
    """The common one-hop rename still works."""
    result = resolver.resolve("VAYUCEMENT")

    assert result["resolution_method"] == "symbol_change"
    assert result["resolved_ticker"] == "VAYUCEM"
    assert result["confidence"] == 100


# ---------------------------------------------------------------------------
# Tier 2.5 - high-confidence fuzzy match on active tickers
# ---------------------------------------------------------------------------

def test_tier_2_5_fuzzy_match_on_active_symbol(resolver):
    """A typo'd symbol snaps to the active ticker at >= 85."""
    result = resolver.resolve("TARAPHARM")

    assert result["resolution_method"] == "fuzzy_match_high_conf"
    assert result["resolved_ticker"] == "TARAPHARMA"
    assert 85 <= result["confidence"] <= 99
    assert result["metadata"]["match_name"] == "Tara Pharmaceuticals Limited"


def test_tier_2_5_fuzzy_match_on_company_name(resolver):
    """
    Company names reach the same tier via token similarity after the
    ' LIMITED' suffix is stripped.
    """
    result = resolver.resolve("Agni Motors Limited")

    assert result["resolution_method"] == "fuzzy_match_high_conf"
    assert result["resolved_ticker"] == "AGNIMOTORS"
    assert result["confidence"] >= 85


def test_tier_2_5_runs_before_2_6_so_stocks_beat_indices(resolver):
    """
    ORDERING REGRESSION - do not reorder Tier 2.5 and Tier 2.6.

    Index resolution used to sit at Tier 1d, above the fuzzy stock match. That
    made a company whose name shares words with an index collapse into the
    index. The upstream case is 'Jio Financial Services' being swallowed by
    the 'Nifty Financial Services' index; the fixture reproduces it with
    JIVANFIN ('Jivan Financial Services Limited') against the
    'NIFTY FINANCIAL SERVICES' index.

    The decoy is live: resolve_index() on its own *does* match the index at
    ~81 confidence. The only reason resolve() returns the stock is that Tier
    2.5 gets there first. If someone moves index resolution back above the
    fuzzy stock match, this test fails - which is the point.
    """
    # The index really would match if it got the chance.
    index_only = resolver.resolve_index("Jivan Financial Services")
    assert index_only is not None
    assert index_only["resolved_index_name"] == "NIFTY FINANCIAL SERVICES"

    # But the full waterfall must return the stock.
    result = resolver.resolve("Jivan Financial Services")

    assert result["resolution_method"] == "fuzzy_match_high_conf"
    assert result["resolved_ticker"] == "JIVANFIN"
    assert result["entity_type"] == "stock"
    assert result["confidence"] >= 85
    assert "resolved_index_name" not in result


# ---------------------------------------------------------------------------
# Tier 2.6 - index aliases
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "query, expected_index",
    [
        ("BANKNIFTY", "NIFTY BANK"),          # the alias every trader types
        ("NIFTY", "NIFTY 50"),                # bare NIFTY means the 50
        ("nifty bank", "NIFTY BANK"),         # case
        ("NIFTY50", "NIFTY 50"),              # no space
        ("NIFTY IT", "NIFTY IT"),             # exact
    ],
)
def test_tier_2_6_index_alias(resolver, query, expected_index):
    """
    Index hits use a different result key: 'resolved_index_name'.

    Callers that only look at 'resolved_ticker' will read None here, which is
    why resolve_any() exists.
    """
    result = resolver.resolve(query)

    assert result["resolution_method"] == "index_alias"
    assert result["resolved_index_name"] == expected_index
    assert result["confidence"] == 95
    assert result["entity_type"] == "index"
    assert result["metadata"]["type"] == "index"


def test_resolve_any_passes_index_results_through(resolver):
    """resolve_any() is the entry point that handles stocks and indices."""
    assert resolver.resolve_any("BANKNIFTY")["resolved_index_name"] == "NIFTY BANK"
    assert resolver.resolve_any("AGNIMOTORS")["resolved_ticker"] == "AGNIMOTORS"


def test_resolve_index_returns_none_for_non_indices(resolver):
    assert resolver.resolve_index("NOT AN INDEX AT ALL") is None
    assert resolver.resolve_index("") is None


# ---------------------------------------------------------------------------
# Tier 3 - company name change
# ---------------------------------------------------------------------------

def test_tier_3_name_change_exact(resolver):
    """
    A former company name resolves to the current symbol at 100.

    The LIKE branch fires when the query is a substring of old_name or
    new_name in name_change_events.
    """
    result = resolver.resolve("Kaveri Mills Limited")

    assert result["resolution_method"] == "name_change"
    assert result["resolved_ticker"] == "KAVERITEX"
    assert result["confidence"] == 100
    assert result["entity_type"] == "stock"
    assert result["metadata"]["new_name"] == "Kaveri Textiles Limited"
    assert result["metadata"]["status"] == "active"


def test_tier_3_name_change_fuzzy(resolver):
    """
    A misspelled former name misses LIKE and falls to the >= 75 fuzzy branch.

    'Chemcials' is a deliberate transposition: it cannot substring-match
    'Rudra Chemicals Limited', so only _fuzzy_match_name_change can catch it.
    """
    result = resolver.resolve("Rudra Chemcials Ltd")

    assert result["resolution_method"] == "name_change_fuzzy"
    assert result["resolved_ticker"] == "RUDRACHEM"
    assert result["confidence"] >= 75
    assert result["metadata"]["match_name"] == "Rudra Chemicals Limited"


# ---------------------------------------------------------------------------
# Tier 4 - delisted
# ---------------------------------------------------------------------------

def test_tier_4_delisted(resolver):
    """
    Delisting is a confident *negative*: confidence 100, ticker None.

    Callers must branch on resolved_ticker, not on confidence.
    """
    result = resolver.resolve("ORIONMET")

    assert result["resolution_method"] == "delisted"
    assert result["resolved_ticker"] is None
    assert result["confidence"] == 100
    assert result["metadata"]["status"] == "DELISTED"
    assert result["metadata"]["last_traded_date"] == "12-JUN-2019"
    assert "Voluntary delisting" in result["metadata"]["reason"]


def test_tier_4_delisted_symbol_absent_from_stocks_master(resolver):
    """A delisting row is enough; the symbol need not be in stocks_master."""
    result = resolver.resolve("ZENITHAGRO")

    assert result["resolution_method"] == "delisted"
    assert result["resolved_ticker"] is None


# ---------------------------------------------------------------------------
# Tier 5 - demergers
# ---------------------------------------------------------------------------

def test_tier_5_demerger_single_child(resolver):
    """
    One child listed within 30 days of the ex-date resolves unambiguously.

    Confidence is 85 rather than 100 because the parent/child link is inferred
    from listing-date proximity, not stated by the exchange.
    """
    result = resolver.resolve("HIMGIRICON")

    assert result["resolution_method"] == "demerger_single_child"
    assert result["resolved_ticker"] == "HIMGIRIRE"
    assert result["confidence"] == 85
    assert result["metadata"]["parent_symbol"] == "HIMGIRICON"
    assert result["metadata"]["demerger_date"] == "15-Jan-2023"


def test_tier_5_demerger_multiple_children(resolver):
    """
    Two children means the resolver must refuse to guess.

    It returns None with the candidate list so the caller can ask the user.
    """
    result = resolver.resolve("MERUGROUP")

    assert result["resolution_method"] == "demerger_multiple_children"
    assert result["resolved_ticker"] is None
    assert result["confidence"] == 75
    assert sorted(result["metadata"]["children"]) == ["MERUCHEM", "MERUPOWER"]


def test_tier_5_ignores_non_demerger_corporate_events(resolver):
    """The DIVIDEND row for AGNIMOTORS must never be treated as a demerger."""
    result = resolver.resolve("AGNIMOTORS")

    assert not result["resolution_method"].startswith("demerger")


# ---------------------------------------------------------------------------
# Tier 6 - not found
# ---------------------------------------------------------------------------

def test_tier_6_returns_suggestions_not_an_exception(resolver):
    """
    An unresolvable string is a normal return value, never a raise.

    The API layer turns metadata['suggestions'] into a 'did you mean' reply,
    so the contract is: a list of {symbol, name, confidence} dicts.
    """
    result = resolver.resolve("ZZZQQQ")

    assert result["resolution_method"] == "not_found"
    assert result["resolved_ticker"] is None
    assert result["entity_type"] == "unknown"

    suggestions = result["metadata"]["suggestions"]
    assert isinstance(suggestions, list)
    assert 0 < len(suggestions) <= 5
    for item in suggestions:
        assert set(item) == {"symbol", "name", "confidence"}
        assert isinstance(item["confidence"], int)


@pytest.mark.parametrize("junk", ["ZZZQQQ", "QQXXZZ TRADING", "FLIBBERTY"])
def test_confidence_floor_for_unresolvable_input(resolver, junk):
    """
    Unresolvable input must score below 50.

    The API rejects anything under 50 rather than answering with a guess, so
    this threshold is a real behavioural boundary, not a style preference.
    """
    result = resolver.resolve(junk)

    assert result["confidence"] < 50
    assert result["resolved_ticker"] is None


# ---------------------------------------------------------------------------
# Input handling and idempotency
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "query",
    ["AGNIMOTORS", "agnimotors", "AgniMotors", "  AGNIMOTORS  ", "\tagnimotors\n"],
)
def test_case_and_whitespace_tolerance(resolver, query):
    """resolve() upper-cases and strips before doing anything else."""
    result = resolver.resolve(query)

    assert result["resolved_ticker"] == "AGNIMOTORS"
    assert result["resolution_method"] == "direct"
    assert result["confidence"] == 100


def test_original_input_is_echoed_back(resolver):
    """The untouched user string comes back for logging and error messages."""
    assert resolver.resolve("  agnimotors  ")["original_ticker"] == "  agnimotors  "


@pytest.mark.parametrize("query", ["AGNIMOTORS", "  purvaeng ", "KAVERI MILLS", "TARAPHARM"])
def test_resolution_is_idempotent(resolver, query):
    """
    Re-resolving an answer must return that answer at confidence 100.

    Without this, a caller that resolves twice (say, once at parse time and
    again at fetch time) could drift onto a different symbol.
    """
    first = resolver.resolve(query)
    assert first["resolved_ticker"] is not None

    second = resolver.resolve(first["resolved_ticker"])

    assert second["resolved_ticker"] == first["resolved_ticker"]
    assert second["confidence"] == 100
    assert second["resolution_method"] in {"direct", "etf_direct"}

    # And a third pass changes nothing.
    third = resolver.resolve(second["resolved_ticker"])
    assert third["resolved_ticker"] == second["resolved_ticker"]


def test_suffixes_are_stripped_before_lookup(resolver):
    """' LTD', ' LIMITED' and friends are noise, not part of the symbol."""
    for query in ("SETUINFRA LTD", "SETUINFRA LIMITED", "SETUINFRA."):
        assert resolver.resolve(query)["resolved_ticker"] == "SETUINFRA"


# ---------------------------------------------------------------------------
# Integration - opt in with --run-integration
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_production_database_has_every_table_the_resolver_reads(real_db_path):
    """
    Schema-drift canary against the real database.

    Skipped by default: the production file is ~3.9 GB and is not distributed
    with the repository. Run with --run-integration on a machine that has it.
    """
    import sqlite3

    required = {
        "stocks_master",
        "market_etfs",
        "market_indices",
        "symbol_change_events",
        "name_change_events",
        "delisting_events",
        "corporate_events",
    }

    conn = sqlite3.connect(f"file:{real_db_path}?mode=ro", uri=True)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        present = {row[0] for row in cursor.fetchall()}
    finally:
        conn.close()

    assert required <= present, f"missing tables: {sorted(required - present)}"
