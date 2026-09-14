"""
Tests for GenericQueryBuilder.

This class is the project's SQL-injection boundary. Everything that reaches
the database as *SQL text* - table name, field list, filter keys, sort column,
sort direction, limit - is built by string interpolation here, so the only
thing standing between a hostile string and the database is the validation in
this module. Everything that reaches the database as *data* must come back in
the params list instead.

So the tests are written as a boundary check, not a formatting check:
  - identifiers must be validated against the discovered schema, and
  - values must never appear in the SQL string.

Where practical the generated SQL is executed against the fixture database.
String-matching alone would happily pass on SQL that SQLite rejects.

query() returns a (sql, params) tuple.

NOTE on sort_by: unlike `fields` and `filters`, an invalid `sort_by` does NOT
raise. It is silently dropped (or, for date-like aliases, remapped). That is
the module's actual behaviour, verified against the source; see
test_invalid_sort_by_is_dropped_not_raised for the full explanation.
"""

from __future__ import annotations

import pytest

# Strings that must never be interpolated into SQL, whichever slot they arrive in.
INJECTION_PAYLOADS = [
    "daily_ohlc; DROP TABLE stocks_master",
    "close; --",
    "close; DROP TABLE stocks_master",
    "1 OR 1=1",
    "close) UNION SELECT symbol FROM stocks_master --",
    "*/ DROP TABLE stocks_master; /*",
]


# ---------------------------------------------------------------------------
# Table validation
# ---------------------------------------------------------------------------

def test_unknown_table_raises_value_error(builder):
    with pytest.raises(ValueError, match="Unknown table"):
        builder.query("no_such_table")


@pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
def test_injection_in_table_position_is_rejected(builder, payload):
    """
    The table name is interpolated straight into 'FROM {table}', so an
    unvalidated table name is arbitrary SQL execution. It must raise before
    any string building happens.
    """
    with pytest.raises(ValueError, match="Unknown table"):
        builder.query(payload)


def test_valid_table_with_no_arguments(builder, db_conn):
    sql, params = builder.query("daily_ohlc")

    assert sql == "SELECT * FROM daily_ohlc"
    assert params == []
    db_conn.execute(sql, params).fetchall()


# ---------------------------------------------------------------------------
# Field validation
# ---------------------------------------------------------------------------

def test_valid_fields_are_selected(builder, db_conn):
    sql, params = builder.query("daily_ohlc", fields=["symbol", "date", "close"])

    assert sql == "SELECT symbol,date,close FROM daily_ohlc"
    rows = db_conn.execute(sql, params).fetchall()
    assert rows and len(rows[0]) == 3


def test_invalid_field_raises_value_error(builder):
    with pytest.raises(ValueError, match="Invalid fields"):
        builder.query("daily_ohlc", fields=["not_a_column"])


@pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
def test_injection_in_fields_position_is_rejected(builder, payload):
    with pytest.raises(ValueError, match="Invalid fields"):
        builder.query("daily_ohlc", fields=[payload])


def test_one_bad_field_rejects_the_whole_query(builder):
    """A valid field alongside a hostile one must not smuggle it through."""
    with pytest.raises(ValueError, match="Invalid fields"):
        builder.query("daily_ohlc", fields=["close", "close; DROP TABLE stocks_master"])


# ---------------------------------------------------------------------------
# Filter key validation
# ---------------------------------------------------------------------------

def test_invalid_filter_field_raises_value_error(builder):
    with pytest.raises(ValueError, match="Invalid field"):
        builder.query("daily_ohlc", filters={"not_a_column": 1})


@pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
def test_injection_in_filter_key_is_rejected(builder, payload):
    """Filter *keys* become SQL identifiers, so they get the same treatment."""
    with pytest.raises(ValueError, match="Invalid field"):
        builder.query("daily_ohlc", filters={payload: "x"})


# ---------------------------------------------------------------------------
# Filter values must be parameterised, never interpolated
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("payload", [
    "AGNIMOTORS'; DROP TABLE stocks_master; --",
    "' OR '1'='1",
    "'; UPDATE stocks_master SET is_active = 0; --",
])
def test_injection_in_filter_value_is_parameterised(builder, db_conn, payload):
    """
    Hostile *values* are fine - they just have to travel in params.

    The assertion that matters is `payload not in sql`: the string reaches the
    driver as data, so SQLite compares it to a column instead of executing it.
    """
    sql, params = builder.query("daily_ohlc", filters={"symbol": payload})

    assert payload not in sql
    assert sql == "SELECT * FROM daily_ohlc WHERE symbol = ?"
    assert params == [payload]

    # Executes harmlessly and matches nothing.
    assert db_conn.execute(sql, params).fetchall() == []
    # The table the payload tried to drop is still there.
    assert db_conn.execute("SELECT COUNT(*) FROM stocks_master").fetchone()[0] > 0


def test_injection_inside_a_list_filter_is_parameterised(builder, db_conn):
    payload = "X'; DROP TABLE stocks_master; --"
    sql, params = builder.query("daily_ohlc", filters={"symbol": [payload, "AGNIMOTORS"]})

    assert payload not in sql
    assert sql == "SELECT * FROM daily_ohlc WHERE symbol IN (?,?)"
    assert params == [payload, "AGNIMOTORS"]
    assert db_conn.execute(sql, params).fetchall()


def test_injection_inside_a_range_filter_is_parameterised(builder):
    payload = "0 OR 1=1"
    sql, params = builder.query("daily_ohlc", filters={"close": {"min": payload}})

    assert payload not in sql
    assert sql == "SELECT * FROM daily_ohlc WHERE close >= ?"
    assert params == [payload]


# ---------------------------------------------------------------------------
# Filter grammar
# ---------------------------------------------------------------------------

def test_scalar_filter_becomes_equals(builder, db_conn):
    sql, params = builder.query("daily_ohlc", filters={"symbol": "AGNIMOTORS"})

    assert sql == "SELECT * FROM daily_ohlc WHERE symbol = ?"
    assert params == ["AGNIMOTORS"]
    assert db_conn.execute(sql, params).fetchall()


def test_dict_filter_becomes_a_bounded_range(builder, db_conn):
    """{'min': x, 'max': y} -> '>= ? AND <= ?', in that order."""
    sql, params = builder.query("daily_ohlc", filters={"close": {"min": 100, "max": 2000}})

    assert sql == "SELECT * FROM daily_ohlc WHERE close >= ? AND close <= ?"
    assert params == [100, 2000]
    assert db_conn.execute(sql, params).fetchall()


@pytest.mark.parametrize(
    "condition, expected_sql, expected_params",
    [
        ({"min": 500}, "SELECT * FROM daily_ohlc WHERE close >= ?", [500]),
        ({"max": 500}, "SELECT * FROM daily_ohlc WHERE close <= ?", [500]),
    ],
)
def test_half_open_ranges(builder, condition, expected_sql, expected_params):
    sql, params = builder.query("daily_ohlc", filters={"close": condition})

    assert sql == expected_sql
    assert params == expected_params


def test_list_filter_becomes_in_clause_with_one_placeholder_per_item(builder, db_conn):
    symbols = ["AGNIMOTORS", "VAYUCEM", "SETUINFRA"]
    sql, params = builder.query("daily_ohlc", filters={"symbol": symbols})

    assert sql == "SELECT * FROM daily_ohlc WHERE symbol IN (?,?,?)"
    assert sql.count("?") == len(symbols)
    assert params == symbols
    assert db_conn.execute(sql, params).fetchall()


def test_multiple_filters_are_anded_together(builder, db_conn):
    sql, params = builder.query(
        "daily_ohlc",
        filters={"symbol": "AGNIMOTORS", "close": {"min": 1}},
    )

    assert sql == "SELECT * FROM daily_ohlc WHERE symbol = ? AND close >= ?"
    assert params == ["AGNIMOTORS", 1]
    assert db_conn.execute(sql, params).fetchall()


def test_empty_filters_produce_no_where_clause(builder):
    assert builder.query("daily_ohlc", filters={})[0] == "SELECT * FROM daily_ohlc"
    assert builder.query("daily_ohlc", filters=None)[0] == "SELECT * FROM daily_ohlc"


def test_a_range_filter_with_neither_bound_is_a_no_op(builder, db_conn):
    """
    {'close': {}} contributes no condition, so no WHERE keyword is emitted.

    Worth pinning: the field name is still validated, but a dict with neither
    'min' nor 'max' must produce 'SELECT * FROM daily_ohlc' rather than a
    dangling 'WHERE'.
    """
    sql, params = builder.query("daily_ohlc", filters={"close": {}})

    assert sql == "SELECT * FROM daily_ohlc"
    assert params == []
    db_conn.execute(sql, params).fetchall()

    # An unknown field is still rejected even when the condition is empty.
    with pytest.raises(ValueError, match="Invalid field"):
        builder.query("daily_ohlc", filters={"not_a_column": {}})


# ---------------------------------------------------------------------------
# The market_indices UPPER() special case
# ---------------------------------------------------------------------------

def test_market_indices_index_name_is_compared_case_insensitively(builder, db_conn):
    """
    market_indices holds the same index under different casings ('NIFTY 50'
    vs 'Nifty 50'), so index_name gets UPPER() on both sides. The value is
    still a bound parameter.
    """
    sql, params = builder.query("market_indices", filters={"index_name": "nifty 50"})

    assert sql == "SELECT * FROM market_indices WHERE UPPER(index_name) = UPPER(?)"
    assert params == ["nifty 50"]
    assert db_conn.execute(sql, params).fetchall(), "lower-case lookup should match"


def test_upper_special_case_is_scoped_to_that_one_table_and_column(builder):
    """
    market_etfs has an index_name column too. It must not get UPPER(), and no
    other column of market_indices may get it either - the special case is
    keyed on both table and field.
    """
    etf_sql, _ = builder.query("market_etfs", filters={"index_name": "NIFTYBEES-EQ"})
    assert etf_sql == "SELECT * FROM market_etfs WHERE index_name = ?"

    other_col_sql, _ = builder.query("market_indices", filters={"date": "2024-01-02"})
    assert other_col_sql == "SELECT * FROM market_indices WHERE date = ?"


def test_upper_special_case_does_not_apply_to_list_or_range_filters(builder):
    """Only the scalar branch carries UPPER(); IN lists take the plain path."""
    sql, _ = builder.query("market_indices", filters={"index_name": ["NIFTY 50", "NIFTY IT"]})

    assert sql == "SELECT * FROM market_indices WHERE index_name IN (?,?)"
    assert "UPPER" not in sql


# ---------------------------------------------------------------------------
# ORDER BY
# ---------------------------------------------------------------------------

def test_valid_sort_column(builder, db_conn):
    sql, params = builder.query("daily_ohlc", sort_by="close", sort_order="asc")

    assert sql == "SELECT * FROM daily_ohlc ORDER BY close ASC"
    assert db_conn.execute(sql, params).fetchall()


def test_sort_order_defaults_to_desc(builder):
    assert builder.query("daily_ohlc", sort_by="close")[0].endswith("ORDER BY close DESC")


def test_invalid_sort_order_raises_value_error(builder):
    """sort_order is interpolated, so anything but asc/desc must raise."""
    with pytest.raises(ValueError, match="Invalid sort_order"):
        builder.query("daily_ohlc", sort_by="close", sort_order="DESC; DROP TABLE stocks_master")


@pytest.mark.parametrize(
    "table, alias, expected_column",
    [
        # daily_ohlc calls it 'date', so 'dt' and 'timestamp' both land there.
        ("daily_ohlc", "dt", "date"),
        ("daily_ohlc", "timestamp", "date"),
        # stocks_master has no plain 'date'; the *_date column wins.
        ("stocks_master", "date", "listing_date"),
        ("stocks_master", "dt", "listing_date"),
        ("stocks_master", "timestamp", "listing_date"),
        ("symbol_change_events", "date", "change_date"),
        ("delisting_events", "date", "last_traded_date"),
        ("corporate_events", "date", "ex_date"),
    ],
)
def test_date_like_sort_aliases_are_remapped_to_the_real_column(
    builder, db_conn, table, alias, expected_column
):
    """
    'date', 'dt' and 'timestamp' are treated as "sort by whatever this table
    calls its date column". Every other invalid name is dropped instead.
    """
    sql, params = builder.query(table, sort_by=alias)

    assert sql == f"SELECT * FROM {table} ORDER BY {expected_column} DESC"
    db_conn.execute(sql, params).fetchall()


def test_order_by_is_dropped_when_the_table_has_no_date_column(builder):
    """
    With no date-ish column to fall back on, the ORDER BY disappears rather
    than emitting invalid SQL.

    Built from a hand-written schema because every table in the production
    database happens to have a date column, so the fixture cannot cover this
    branch on its own.
    """
    dateless = type(builder)(
        {"watchlist": {"columns": ["id", "symbol", "note"], "types": {}, "row_count": 0}}
    )

    for alias in ("date", "dt", "timestamp"):
        sql, params = dateless.query("watchlist", sort_by=alias)
        assert sql == "SELECT * FROM watchlist"
        assert "ORDER BY" not in sql
        assert params == []


@pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
def test_invalid_sort_by_is_dropped_not_raised(builder, db_conn, payload):
    """
    ACTUAL BEHAVIOUR, and it differs from `fields` and `filters`.

    An invalid sort_by that is not a date alias is silently set to '' and the
    ORDER BY clause is omitted - no ValueError. That is inconsistent with the
    other identifier slots, which raise, and it means a caller typo is
    swallowed rather than reported. It is not a security hole: the string is
    discarded, never interpolated, which is what these assertions pin down.

    If the module is ever changed to raise here, this test should be updated
    to expect ValueError - deliberately, not by accident.
    """
    sql, params = builder.query("daily_ohlc", sort_by=payload)

    assert payload not in sql
    assert "ORDER BY" not in sql
    assert "DROP" not in sql.upper()
    assert sql == "SELECT * FROM daily_ohlc"
    assert params == []

    db_conn.execute(sql, params).fetchall()
    assert db_conn.execute("SELECT COUNT(*) FROM stocks_master").fetchone()[0] > 0


def test_invalid_sort_by_also_skips_sort_order_validation(builder):
    """
    Consequence of the early return above: with an invalid sort_by, a bogus
    sort_order is never checked either. Pinned so the coupling is visible.
    """
    sql, _ = builder.query("daily_ohlc", sort_by="not_a_column", sort_order="sideways")

    assert sql == "SELECT * FROM daily_ohlc"


# ---------------------------------------------------------------------------
# LIMIT
# ---------------------------------------------------------------------------

def test_limit_is_applied(builder, db_conn):
    sql, params = builder.query("daily_ohlc", limit=3)

    assert sql == "SELECT * FROM daily_ohlc LIMIT 3"
    assert len(db_conn.execute(sql, params).fetchall()) == 3


@pytest.mark.parametrize(
    "bad_limit",
    ["10", "10; DROP TABLE stocks_master", "abc", 0, -1, 2.5],
)
def test_limit_must_be_a_positive_integer(builder, bad_limit):
    """
    LIMIT is interpolated, not bound, so a non-int limit is an injection
    vector. A numeric *string* must be rejected rather than coerced.
    """
    with pytest.raises(ValueError, match="Invalid limit"):
        builder.query("daily_ohlc", limit=bad_limit)


def test_limit_none_means_no_limit_clause(builder):
    assert "LIMIT" not in builder.query("daily_ohlc", limit=None)[0]


# ---------------------------------------------------------------------------
# Whole-clause assembly
# ---------------------------------------------------------------------------

def test_all_clauses_combine_in_sql_order(builder, db_conn):
    sql, params = builder.query(
        "daily_ohlc",
        fields=["symbol", "date", "close"],
        filters={"symbol": "AGNIMOTORS", "close": {"min": 1, "max": 100000}},
        sort_by="date",
        sort_order="asc",
        limit=2,
    )

    assert sql == (
        "SELECT symbol,date,close FROM daily_ohlc "
        "WHERE symbol = ? AND close >= ? AND close <= ? "
        "ORDER BY date ASC LIMIT 2"
    )
    assert params == ["AGNIMOTORS", 1, 100000]

    rows = db_conn.execute(sql, params).fetchall()
    assert len(rows) == 2
    assert [r[1] for r in rows] == sorted(r[1] for r in rows)


def test_placeholder_count_always_matches_param_count(builder):
    """A mismatch here is a runtime error in production, so pin the invariant."""
    cases = [
        {"filters": {"symbol": "AGNIMOTORS"}},
        {"filters": {"symbol": ["A", "B", "C", "D"]}},
        {"filters": {"close": {"min": 1, "max": 2}}},
        {"filters": {"symbol": "A", "close": {"min": 1}}, "limit": 5},
        {"filters": {"index_name": "nifty 50"}, "table": "market_indices"},
    ]
    for case in cases:
        table = case.pop("table", "daily_ohlc")
        sql, params = builder.query(table, **case)
        assert sql.count("?") == len(params), sql
