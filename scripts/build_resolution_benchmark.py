#!/usr/bin/env python3
"""
Build a labelled ticker-resolution benchmark for the Indian market.

    python scripts/build_resolution_benchmark.py
    python scripts/build_resolution_benchmark.py --per-category 500 --output dist/benchmark

Why this exists
---------------
Every Indian-market project hits the same wall: a company renames itself, or
changes its trading symbol, and suddenly historical data and user queries no
longer join. Most projects solve it badly and none of them publish an
evaluation set, so there is no way to compare approaches.

This produces one. The labelling work is derived from NSE's published filings
but the *benchmark* — the query strings, the category assignment, the
difficulty rule, the multi-hop chain reconstruction — is original. That makes
it the most defensible artefact this project can publish, and the most useful.

Output
------
    benchmark/ticker_resolution_benchmark.jsonl
    benchmark/ticker_resolution_benchmark.csv
    benchmark/README.md
    benchmark/baseline_results.json     (if the resolver can be loaded)
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import random
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from App.config import config
    DEFAULT_DB = Path(config.DB_PATH)
except Exception:  # pragma: no cover
    DEFAULT_DB = PROJECT_ROOT / "App" / "database" / "stock_market_new.db"


CATEGORIES = (
    "direct",
    "fuzzy_name",
    "renamed_company",
    "changed_symbol",
    "multi_hop_symbol",
    "demerger_child",
    "delisted",
    "index_alias",
)

# difficulty is assigned by rule, documented in the card
DIFFICULTY: Dict[str, str] = {
    "direct": "easy",
    "index_alias": "easy",
    "fuzzy_name": "medium",
    "renamed_company": "medium",
    "changed_symbol": "medium",
    "delisted": "medium",
    "demerger_child": "hard",
    "multi_hop_symbol": "hard",
}


class Case(dict):
    """One benchmark case. A dict so it serialises straight to JSONL."""

    def __init__(self, query: str, expected: str, category: str, evidence: str) -> None:
        super().__init__(
            query=query,
            expected_symbol=expected,
            category=category,
            difficulty=DIFFICULTY[category],
            source_evidence=evidence,
        )


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _connect_ro(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise SystemExit(
            f"Database not found: {db_path}\n"
            f"Build one first:  python scripts/bootstrap_db.py --sample"
        )
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def _tables(conn: sqlite3.Connection) -> set:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _clean(text: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _is_fund_like(name: str) -> bool:
    """Mutual-fund and debt-series rows dominate the raw NSE filings and make a
    poor benchmark - they are not companies anyone queries by name."""
    low = name.lower()
    return any(
        token in low
        for token in (" mf - ", "mutual fund", " ftf ", "fmp ", "series", "-gp", "-dp", "-rp")
    )


def _name_variants(name: str) -> List[str]:
    """Realistic ways a person would type a company name."""
    out = []
    base = _clean(name)
    if not base:
        return out
    stripped = re.sub(r"\s+(limited|ltd\.?|ltd)\.?$", "", base, flags=re.I).strip()
    if stripped and stripped != base:
        out.append(stripped)
    if " Limited" in base:
        out.append(base.replace(" Limited", " Ltd"))
    if "&" in base:
        out.append(base.replace("&", "and"))
    if "." in base:
        out.append(base.replace(".", ""))
    return [v for v in dict.fromkeys(out) if v and v.lower() != base.lower()]


# --------------------------------------------------------------------------
# case builders
# --------------------------------------------------------------------------

def build_direct(conn: sqlite3.Connection, n: int, rng: random.Random) -> List[Case]:
    rows = conn.execute(
        "SELECT symbol, company_name FROM stocks_master "
        "WHERE is_active = 1 AND company_name IS NOT NULL LIMIT 6000"
    ).fetchall()
    rows = [(s, c) for s, c in rows if s and not _is_fund_like(c or "")]
    rng.shuffle(rows)
    return [
        Case(sym, sym, "direct", "stocks_master.is_active=1")
        for sym, _ in rows[:n]
    ]


def build_fuzzy_name(conn: sqlite3.Connection, n: int, rng: random.Random) -> List[Case]:
    rows = conn.execute(
        "SELECT symbol, company_name FROM stocks_master "
        "WHERE is_active = 1 AND company_name IS NOT NULL LIMIT 8000"
    ).fetchall()
    rows = [(s, _clean(c)) for s, c in rows if s and c and not _is_fund_like(c)]
    rng.shuffle(rows)
    cases: List[Case] = []
    for sym, name in rows:
        for variant in _name_variants(name):
            cases.append(Case(variant, sym, "fuzzy_name", f"stocks_master.company_name={name!r}"))
            break
        if len(cases) >= n:
            break
    return cases[:n]


def build_renamed_company(conn: sqlite3.Connection, n: int, rng: random.Random) -> List[Case]:
    rows = conn.execute(
        "SELECT n.old_name, n.new_name, n.symbol, n.change_date "
        "FROM name_change_events n "
        "JOIN stocks_master s ON s.symbol = n.symbol "
        "WHERE s.is_active = 1 AND n.old_name IS NOT NULL LIMIT 8000"
    ).fetchall()
    out: List[Case] = []
    for old, new, sym, when in rows:
        old_c = _clean(old)
        if not old_c or _is_fund_like(old_c) or _clean(new).lower() == old_c.lower():
            continue
        out.append(Case(old_c, sym, "renamed_company",
                        f"name_change_events: {old_c!r} -> {_clean(new)!r} on {when}"))
    rng.shuffle(out)
    return out[:n]


def _symbol_chains(conn: sqlite3.Connection) -> Dict[str, List[str]]:
    """Follow symbol changes transitively. Returns start_symbol -> full chain."""
    edges: Dict[str, str] = {}
    for old, new in conn.execute(
        "SELECT old_symbol, new_symbol FROM symbol_change_events "
        "WHERE old_symbol IS NOT NULL AND new_symbol IS NOT NULL"
    ):
        old, new = _clean(old).upper(), _clean(new).upper()
        if old and new and old != new:
            edges[old] = new

    chains: Dict[str, List[str]] = {}
    for start in edges:
        chain = [start]
        seen = {start}
        node = start
        while node in edges:
            node = edges[node]
            if node in seen:      # cycle guard
                break
            chain.append(node)
            seen.add(node)
        if len(chain) > 1:
            chains[start] = chain
    return chains


def build_symbol_cases(
    conn: sqlite3.Connection, n: int, rng: random.Random
) -> Tuple[List[Case], List[Case]]:
    """Single-hop and multi-hop symbol changes, split by chain length."""
    chains = _symbol_chains(conn)
    active = {
        _clean(r[0]).upper()
        for r in conn.execute("SELECT symbol FROM stocks_master WHERE is_active = 1")
        if r[0]
    }

    single: List[Case] = []
    multi: List[Case] = []
    for start, chain in chains.items():
        final = chain[-1]
        if final not in active:
            continue
        arrow = " -> ".join(chain)
        if len(chain) == 2:
            single.append(Case(start, final, "changed_symbol", f"symbol_change_events: {arrow}"))
        else:
            multi.append(Case(start, final, "multi_hop_symbol",
                              f"symbol_change_events ({len(chain) - 1} hops): {arrow}"))
    rng.shuffle(single)
    rng.shuffle(multi)
    return single[:n], multi[:n]


def build_demerger_child(conn: sqlite3.Connection, n: int, rng: random.Random) -> List[Case]:
    cols = {r[1].lower() for r in conn.execute("PRAGMA table_info(corporate_events)")}
    purpose_col = next((c for c in ("purpose", "subject", "action_type", "event_type") if c in cols), None)
    symbol_col = next((c for c in ("symbol", "ticker") if c in cols), None)
    name_col = next((c for c in ("company_name", "security_name", "company") if c in cols), None)
    if not (purpose_col and symbol_col):
        return []

    name_expr = f'"{name_col}"' if name_col else "NULL"
    rows = conn.execute(
        f'SELECT "{symbol_col}", {name_expr}, "{purpose_col}" '
        f'FROM corporate_events WHERE LOWER("{purpose_col}") LIKE ? LIMIT 4000',
        ("%demerg%",),
    ).fetchall()

    active = {
        _clean(r[0]).upper()
        for r in conn.execute("SELECT symbol FROM stocks_master WHERE is_active = 1")
        if r[0]
    }
    out: List[Case] = []
    for sym, name, purpose in rows:
        sym_c = _clean(sym).upper()
        if not sym_c or sym_c not in active:
            continue
        query = _clean(name) or sym_c
        if _is_fund_like(query):
            continue
        out.append(Case(query, sym_c, "demerger_child",
                        f"corporate_events.{purpose_col}={_clean(purpose)[:80]!r}"))
    rng.shuffle(out)
    return out[:n]


def build_delisted(conn: sqlite3.Connection, n: int, rng: random.Random) -> List[Case]:
    cols = {r[1].lower() for r in conn.execute("PRAGMA table_info(delisting_events)")}
    symbol_col = next((c for c in ("symbol", "ticker") if c in cols), None)
    name_col = next((c for c in ("company_name", "security_name", "company") if c in cols), None)
    if not symbol_col:
        return []
    name_expr = f'"{name_col}"' if name_col else "NULL"
    rows = conn.execute(
        f'SELECT "{symbol_col}", {name_expr} FROM delisting_events LIMIT 8000'
    ).fetchall()
    out: List[Case] = []
    for sym, name in rows:
        sym_c = _clean(sym).upper()
        query = _clean(name) or sym_c
        if not sym_c or _is_fund_like(query):
            continue
        # expected is the symbol; a correct system also flags it as delisted
        out.append(Case(query, sym_c, "delisted", "delisting_events"))
    rng.shuffle(out)
    return out[:n]


def build_index_alias(conn: sqlite3.Connection, n: int, rng: random.Random) -> List[Case]:
    if "market_indices" not in _tables(conn):
        return []
    names = [
        _clean(r[0])
        for r in conn.execute("SELECT DISTINCT index_name FROM market_indices LIMIT 500")
        if r[0]
    ]
    out: List[Case] = []
    for name in names:
        compact = name.replace(" ", "")
        if compact.lower() != name.lower():
            out.append(Case(compact, name, "index_alias", f"market_indices.index_name={name!r}"))
        lower = name.lower()
        if lower != name:
            out.append(Case(lower, name, "index_alias", f"market_indices.index_name={name!r}"))
    rng.shuffle(out)
    return out[:n]


# --------------------------------------------------------------------------
# baseline
# --------------------------------------------------------------------------

def _load_resolver(db_path: Path):
    """Load TickerResolver without importing the whole data_fetcher package,
    which pulls in TA-Lib, jugaad-data and nselib."""
    mod_path = PROJECT_ROOT / "App" / "src" / "data_fetcher" / "ticker_resolver.py"
    spec = importlib.util.spec_from_file_location("_bench_ticker_resolver", mod_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {mod_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.TickerResolver(str(db_path))


def run_baseline(cases: Sequence[Case], db_path: Path) -> Optional[dict]:
    try:
        resolver = _load_resolver(db_path)
    except Exception as exc:
        print(f"  ! baseline skipped - could not load TickerResolver: {exc}")
        return None

    per_cat: Dict[str, Counter] = defaultdict(Counter)
    conf_right: List[float] = []
    conf_wrong: List[float] = []
    correct = 0

    for case in cases:
        try:
            result = resolver.resolve_any(case["query"])
        except Exception:
            per_cat[case["category"]]["error"] += 1
            continue
        got = (result or {}).get("resolved_ticker") or (result or {}).get("ticker") or ""
        conf = float((result or {}).get("confidence") or 0)
        ok = _clean(str(got)).upper() == _clean(case["expected_symbol"]).upper()
        per_cat[case["category"]]["total"] += 1
        if ok:
            correct += 1
            per_cat[case["category"]]["correct"] += 1
            conf_right.append(conf)
        else:
            conf_wrong.append(conf)

    total = len(cases)
    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cases": total,
        "accuracy": round(correct / total, 4) if total else 0.0,
        "mean_confidence_correct": round(sum(conf_right) / len(conf_right), 2) if conf_right else None,
        "mean_confidence_incorrect": round(sum(conf_wrong) / len(conf_wrong), 2) if conf_wrong else None,
        "per_category": {
            cat: {
                "total": c["total"],
                "correct": c["correct"],
                "errors": c["error"],
                "accuracy": round(c["correct"] / c["total"], 4) if c["total"] else 0.0,
            }
            for cat, c in sorted(per_cat.items())
        },
    }


# --------------------------------------------------------------------------
# card
# --------------------------------------------------------------------------

def render_card(cases: Sequence[Case], baseline: Optional[dict], seed: int, per_cat_cap: int) -> str:
    counts = Counter(c["category"] for c in cases)
    diffs = Counter(c["difficulty"] for c in cases)
    L: List[str] = []
    a = L.append

    a("# Ticker Resolution Benchmark (NSE India)")
    a("")
    a("A labelled evaluation set for the problem of mapping an arbitrary user string —")
    a("a company name, an old trading symbol, a nickname — onto the NSE symbol that trades")
    a("today. As far as we know this is the first published benchmark for the task on the")
    a("Indian market.")
    a("")
    a(f"- **Cases:** {len(cases):,}")
    a(f"- **Categories:** {len(counts)}")
    a(f"- **Seed:** {seed} (regenerating with the same seed and database reproduces this exactly)")
    a(f"- **Cap per category:** {per_cat_cap}")
    a(f"- **Generated:** {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    a("")
    a("## Schema")
    a("")
    a("JSONL, one object per line. A `.csv` with the same columns is provided alongside.")
    a("")
    a("| Field | Meaning |")
    a("|---|---|")
    a("| `query` | The input string a system must resolve |")
    a("| `expected_symbol` | The correct current NSE symbol (or index name) |")
    a("| `category` | Which resolution problem this case exercises |")
    a("| `difficulty` | `easy` / `medium` / `hard`, assigned by the rule below |")
    a("| `source_evidence` | The filing or table row the label was derived from |")
    a("")
    a("## Categories")
    a("")
    a("| Category | Cases | Difficulty | What it tests |")
    a("|---|---:|---|---|")
    descriptions = {
        "direct": "Symbol is already current. Control group.",
        "fuzzy_name": "Company name with punctuation or suffix variation (Ltd / Limited / & / and).",
        "renamed_company": "Company changed its name; query uses the old one.",
        "changed_symbol": "Trading symbol changed once; query uses the old one.",
        "multi_hop_symbol": "Symbol changed two or more times. Requires transitive chasing.",
        "demerger_child": "Entity created by a demerger. Requires corporate-action reasoning.",
        "delisted": "Security no longer trades. A correct system says so rather than guessing.",
        "index_alias": "Index nickname or spacing variant (NIFTY50 -> NIFTY 50).",
    }
    for cat in CATEGORIES:
        if counts.get(cat):
            a(f"| `{cat}` | {counts[cat]:,} | {DIFFICULTY[cat]} | {descriptions[cat]} |")
    a("")
    a("**Difficulty rule.** `easy` = no lookup beyond a direct or alias match. `medium` = one")
    a("indirection through a filing table. `hard` = multi-step reasoning — a transitive symbol")
    a("chain, or inferring a demerger child from a corporate action.")
    a("")
    a(f"Distribution: " + ", ".join(f"{k} {v:,}" for k, v in sorted(diffs.items())))
    a("")
    a("## How the labels were derived")
    a("")
    a("Every case is grounded in a row of NSE's published filings, recorded in")
    a("`source_evidence`. Mutual-fund and debt-series rows were filtered out — they dominate")
    a("the raw filings and nobody queries them by name. Multi-hop chains were reconstructed by")
    a("following `symbol_change_events` transitively with a cycle guard; cases are only kept")
    a("when the final symbol is still active.")
    a("")
    a("Labels are best-effort, derived from public filings. If you find an incorrect one, open")
    a("an issue — corrections are the most useful contribution to this dataset.")
    a("")
    a("## Scoring")
    a("")
    a("```python")
    a("import json")
    a("")
    a("cases = [json.loads(l) for l in open('ticker_resolution_benchmark.jsonl')]")
    a("")
    a("correct = 0")
    a("for c in cases:")
    a("    got = your_resolver(c['query'])")
    a("    correct += (got or '').upper() == c['expected_symbol'].upper()")
    a("")
    a("print('accuracy', correct / len(cases))")
    a("```")
    a("")
    a("Report **overall accuracy, per-category accuracy, and per-difficulty accuracy.** Overall")
    a("accuracy alone is misleading — the `direct` control group is trivially easy and will")
    a("flatter any system.")
    a("")
    a("If your resolver emits a confidence score, also report **mean confidence on correct vs")
    a("incorrect answers**. A system that is confidently wrong is worse than one that abstains,")
    a("and this is the number that reveals it.")
    a("")

    if baseline:
        a("## Baseline")
        a("")
        a("The 7-tier resolver from the Dalal Street AI project, run over this exact set.")
        a("")
        a(f"- **Overall accuracy:** {baseline['accuracy']:.1%} ({baseline['cases']:,} cases)")
        if baseline.get("mean_confidence_correct") is not None:
            a(f"- **Mean confidence when correct:** {baseline['mean_confidence_correct']}")
        if baseline.get("mean_confidence_incorrect") is not None:
            a(f"- **Mean confidence when incorrect:** {baseline['mean_confidence_incorrect']}")
        a("")
        a("| Category | Cases | Correct | Accuracy |")
        a("|---|---:|---:|---:|")
        for cat, s in baseline["per_category"].items():
            a(f"| `{cat}` | {s['total']:,} | {s['correct']:,} | {s['accuracy']:.1%} |")
        a("")
        a("Full detail in `baseline_results.json`.")
        a("")

    a("## Licence")
    a("")
    a("The benchmark **construction** — query selection, categorisation, difficulty rule,")
    a("chain reconstruction — is released under CC BY 4.0. Attribution appreciated.")
    a("")
    a("The underlying facts derive from NSE India's published filings and remain subject to")
    a("NSE's terms. See `docs/DATA.md` in the source repository.")
    a("")
    a("## Regenerating")
    a("")
    a("```bash")
    a(f"python scripts/build_resolution_benchmark.py --per-category {per_cat_cap} --seed {seed}")
    a("```")
    a("")
    return "\n".join(L)


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build a ticker-resolution benchmark from the warehouse.")
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument("--output", type=Path, default=PROJECT_ROOT / "dist" / "benchmark")
    p.add_argument("--per-category", type=int, default=300)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--no-baseline", action="store_true", help="Skip running the reference resolver.")
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    rng = random.Random(args.seed)
    conn = _connect_ro(Path(args.db))

    try:
        n = args.per_category
        print(f"Building benchmark (cap {n} per category, seed {args.seed})\n")

        cases: List[Case] = []
        single, multi = build_symbol_cases(conn, n, rng)
        builders: List[Tuple[str, List[Case]]] = [
            ("direct", build_direct(conn, n, rng)),
            ("fuzzy_name", build_fuzzy_name(conn, n, rng)),
            ("renamed_company", build_renamed_company(conn, n, rng)),
            ("changed_symbol", single),
            ("multi_hop_symbol", multi),
            ("demerger_child", build_demerger_child(conn, n, rng)),
            ("delisted", build_delisted(conn, n, rng)),
            ("index_alias", build_index_alias(conn, n, rng)),
        ]
        for label, built in builders:
            print(f"  {label:<20} {len(built):>5}")
            cases.extend(built)

        # de-duplicate on (query, expected) while preserving order
        seen = set()
        deduped: List[Case] = []
        for c in cases:
            key = (c["query"].lower(), c["expected_symbol"].upper())
            if key not in seen:
                seen.add(key)
                deduped.append(c)
        cases = deduped
        rng.shuffle(cases)

        print(f"\n  total (deduplicated) {len(cases):,}")

        out: Path = args.output
        out.mkdir(parents=True, exist_ok=True)

        jsonl = out / "ticker_resolution_benchmark.jsonl"
        with jsonl.open("w", encoding="utf-8") as fh:
            for c in cases:
                fh.write(json.dumps(c, ensure_ascii=False) + "\n")

        csv_path = out / "ticker_resolution_benchmark.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(cases[0].keys()) if cases else
                               ["query", "expected_symbol", "category", "difficulty", "source_evidence"])
            w.writeheader()
            w.writerows(cases)

        baseline = None
        if not args.no_baseline:
            print("\n  running baseline resolver...")
            baseline = run_baseline(cases, Path(args.db))
            if baseline:
                (out / "baseline_results.json").write_text(
                    json.dumps(baseline, indent=2), encoding="utf-8"
                )
                print(f"    accuracy {baseline['accuracy']:.1%}")

        (out / "README.md").write_text(
            render_card(cases, baseline, args.seed, args.per_category), encoding="utf-8"
        )

        print(f"\n  written to {out}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
