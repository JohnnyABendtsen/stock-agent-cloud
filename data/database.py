import sqlite3
import json
from datetime import date
from pathlib import Path

from config import DB_PATH, INDUSTRY_PE_PATH

_ALLOWED_COLS = frozenset([
    "ticker", "name", "exchange", "currency", "pe", "ps", "pb", "eps",
    "market_cap", "dividend_yield", "dividend_amount", "fcf", "fcf_yield",
    "debt_equity", "roe_5yr", "earnings_growth_5yr", "revenue_growth_5yr",
    "gross_margin", "gross_margin_trend", "roe_consistency",
    "insider_ownership", "buyback_trend", "debt_trend", "industry", "sector",
    "pe_label", "pe_label_1", "pe_2026", "pe_2027", "pe_2028",
    "score_moat", "score_management", "score_financial", "score_valuation",
    "stock_rating", "last_updated",
])


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def initialize_db() -> None:
    with get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS stocks (
                ticker                TEXT PRIMARY KEY,
                name                  TEXT,
                exchange              TEXT,
                currency              TEXT,
                pe                    REAL,
                ps                    REAL,
                pb                    REAL,
                eps                   REAL,
                market_cap            REAL,
                dividend_yield        REAL,
                dividend_amount       REAL,
                fcf                   REAL,
                fcf_yield             REAL,
                debt_equity           REAL,
                roe_5yr               REAL,
                earnings_growth_5yr   REAL,
                revenue_growth_5yr    REAL,
                gross_margin          REAL,
                gross_margin_trend    TEXT,
                roe_consistency       REAL,
                insider_ownership     REAL,
                buyback_trend         TEXT,
                debt_trend            TEXT,
                industry              TEXT,
                pe_label              TEXT,
                pe_label_1            TEXT,
                pe_2026               REAL,
                pe_2027               REAL,
                pe_2028               REAL,
                score_moat            REAL,
                score_management      REAL,
                score_financial       REAL,
                score_valuation       REAL,
                stock_rating          REAL,
                last_updated          DATETIME
            );

            CREATE TABLE IF NOT EXISTS stock_history (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker                TEXT,
                snapshot_date         DATE,
                pe                    REAL,
                ps                    REAL,
                pb                    REAL,
                eps                   REAL,
                market_cap            REAL,
                dividend_yield        REAL,
                fcf                   REAL,
                fcf_yield             REAL,
                debt_equity           REAL,
                roe_5yr               REAL,
                gross_margin          REAL,
                score_moat            REAL,
                score_management      REAL,
                score_financial       REAL,
                score_valuation       REAL,
                stock_rating          REAL,
                UNIQUE(ticker, snapshot_date)
            );

            CREATE TABLE IF NOT EXISTS industry_pe_benchmarks (
                industry    TEXT PRIMARY KEY,
                billig_max  REAL,
                middel_min  REAL,
                middel_max  REAL,
                dyr_min     REAL
            );

            CREATE TABLE IF NOT EXISTS run_log (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at   TEXT NOT NULL,
                finished_at  TEXT,
                duration_sec INTEGER,
                markets      TEXT,
                stock_count  INTEGER
            );
        """)
    _migrate()
    _seed_industry_pe()


def _migrate() -> None:
    with get_connection() as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(stocks)")}
        if "pe_label_1" not in cols:
            conn.execute("ALTER TABLE stocks ADD COLUMN pe_label_1 TEXT")
        if "sector" not in cols:
            conn.execute("ALTER TABLE stocks ADD COLUMN sector TEXT")


def _seed_industry_pe() -> None:
    if not INDUSTRY_PE_PATH.exists():
        return
    with open(INDUSTRY_PE_PATH, encoding="utf-8") as f:
        data = json.load(f)
    with get_connection() as conn:
        for industry, vals in data.items():
            conn.execute(
                """INSERT OR REPLACE INTO industry_pe_benchmarks
                   (industry, billig_max, middel_min, middel_max, dyr_min)
                   VALUES (?, ?, ?, ?, ?)""",
                (industry, vals["billig_max"], vals["middel_min"],
                 vals["middel_max"], vals["dyr_min"]),
            )


def upsert_stock(row: dict) -> None:
    safe_row = {k: v for k, v in row.items() if k in _ALLOWED_COLS}
    with get_connection() as conn:
        cols = ", ".join(safe_row.keys())
        placeholders = ", ".join(["?"] * len(safe_row))
        updates = ", ".join(f"{k}=excluded.{k}" for k in safe_row if k != "ticker")
        conn.execute(
            f"INSERT INTO stocks ({cols}) VALUES ({placeholders}) "
            f"ON CONFLICT(ticker) DO UPDATE SET {updates}",
            list(safe_row.values()),
        )


def write_daily_snapshot(ticker: str) -> None:
    today = date.today().isoformat()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM stocks WHERE ticker = ?", (ticker,)
        ).fetchone()
        if row is None:
            return
        conn.execute(
            """INSERT OR IGNORE INTO stock_history
               (ticker, snapshot_date, pe, ps, pb, eps, market_cap,
                dividend_yield, fcf, fcf_yield, debt_equity, roe_5yr,
                gross_margin, score_moat, score_management,
                score_financial, score_valuation, stock_rating)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                ticker, today,
                row["pe"], row["ps"], row["pb"], row["eps"], row["market_cap"],
                row["dividend_yield"], row["fcf"], row["fcf_yield"],
                row["debt_equity"], row["roe_5yr"], row["gross_margin"],
                row["score_moat"], row["score_management"],
                row["score_financial"], row["score_valuation"],
                row["stock_rating"],
            ),
        )


def write_all_snapshots() -> None:
    with get_connection() as conn:
        tickers = [r["ticker"] for r in conn.execute("SELECT ticker FROM stocks").fetchall()]
    for ticker in tickers:
        write_daily_snapshot(ticker)


def clear_stale_stocks(markets: set[str], since) -> None:
    """Delete tickers that belong to the given markets but were NOT updated since `since`.
    Used after a successful fetch to remove delisted stocks without pre-clearing the DB."""
    from datetime import timezone
    # Convert to UTC and format as "+00:00" to match last_updated format in DB
    if hasattr(since, "tzinfo"):
        if since.tzinfo is None:
            since = since.astimezone(timezone.utc)
        else:
            since = since.astimezone(timezone.utc)
    cutoff = since.strftime("%Y-%m-%dT%H:%M:%S+00:00") if hasattr(since, "strftime") else str(since)
    patterns: list[str] = []
    include_us = bool(markets & _US_MARKET_KEYS)
    for key in markets:
        patterns.extend(_MARKET_TICKER_PATTERNS.get(key, []))
    with get_connection() as conn:
        for p in patterns:
            conn.execute(
                "DELETE FROM stocks WHERE ticker LIKE ? AND (last_updated IS NULL OR last_updated < ?)",
                (p, cutoff),
            )
        if include_us:
            conn.execute(
                "DELETE FROM stocks WHERE ticker NOT LIKE '%.%' AND (last_updated IS NULL OR last_updated < ?)",
                (cutoff,),
            )


def clear_all_stocks() -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM stocks")


# Ticker suffix patterns per market key
_MARKET_TICKER_PATTERNS: dict[str, list[str]] = {
    "copenhagen":  ["%.CO"],
    "stockholm":   ["%.ST"],
    "helsinki":    ["%.HE"],
    "oslo":        ["%.OL"],
    "dax40":         ["%.DE"],
    "swiss_full":    ["%.SW"],
    "amsterdam":     ["%.AS"],
    "brussels":      ["%.BR"],
    "luxembourg":    ["%.LU"],
    "xetra":         ["%.DE"],
    "ftse100":       ["%.L"],
    "lse_full":      ["%.L"],
    "cac40":         ["%.PA"],
    "euronext_paris":["%.PA"],
    "tokyo":       ["%.T"],
    "seoul":       ["%.KS", "%.KQ"],
    "hong_kong":   ["%.HK"],
    "australia":   ["%.AX"],
    "india":       ["%.NS"],
    "canada":      ["%.TO"],
    "shanghai":    ["%.SS", "%.SZ"],
}
_US_MARKET_KEYS = {"sp500", "nasdaq_full", "nyse_full"}


def clear_stocks_for_markets(enabled: set[str]) -> None:
    """Delete only stocks belonging to the selected markets."""
    patterns: list[str] = []
    include_us = bool(enabled & _US_MARKET_KEYS)

    for key in enabled:
        patterns.extend(_MARKET_TICKER_PATTERNS.get(key, []))

    with get_connection() as conn:
        for p in patterns:
            conn.execute("DELETE FROM stocks WHERE ticker LIKE ?", (p,))
        if include_us:
            conn.execute("DELETE FROM stocks WHERE ticker NOT LIKE '%.%'")


def fetch_all_tickers() -> set[str]:
    with get_connection() as conn:
        rows = conn.execute("SELECT ticker FROM stocks").fetchall()
    return {r["ticker"] for r in rows}


def fetch_all_stocks() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM stocks ORDER BY stock_rating DESC").fetchall()
    return [dict(r) for r in rows]


def fetch_history(ticker: str) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM stock_history WHERE ticker = ? ORDER BY snapshot_date DESC",
            (ticker,),
        ).fetchall()
    return [dict(r) for r in rows]


def sync_industry_benchmarks() -> int:
    """Add missing industries from stocks table to industry_pe_benchmarks with NULL values.
    Returns number of new rows inserted."""
    with get_connection() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO industry_pe_benchmarks (industry, billig_max, middel_min, middel_max, dyr_min)
            SELECT DISTINCT industry, NULL, NULL, NULL, NULL
            FROM stocks
            WHERE industry IS NOT NULL
              AND industry != ''
              AND industry != 'Unknown'
        """)
        return conn.execute("SELECT changes()").fetchone()[0]


def fetch_industry_benchmarks() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT ipb.*,
                   COALESCE(
                       (SELECT s.sector FROM stocks s
                        WHERE s.industry = ipb.industry
                          AND s.sector IS NOT NULL AND s.sector != ''
                        LIMIT 1),
                       ''
                   ) AS sector
            FROM industry_pe_benchmarks ipb
            ORDER BY ipb.industry
        """).fetchall()
    return [dict(r) for r in rows]


def save_industry_benchmarks(rows: list[dict]) -> None:
    """Upsert industry PE benchmark rows."""
    with get_connection() as con:
        for row in rows:
            con.execute(
                """
                INSERT INTO industry_pe_benchmarks
                    (industry, billig_max, middel_min, middel_max, dyr_min)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(industry) DO UPDATE SET
                    billig_max = excluded.billig_max,
                    middel_min = excluded.middel_min,
                    middel_max = excluded.middel_max,
                    dyr_min    = excluded.dyr_min
                """,
                (
                    row["industry"],
                    row.get("billig_max"),
                    row.get("middel_min", row.get("billig_max")),
                    row.get("middel_max"),
                    row.get("dyr_min"),
                ),
            )


def run_log_start(markets: set[str]) -> int:
    """Insert a run record at start; returns the new run id."""
    from datetime import datetime
    started_at = datetime.now().isoformat(timespec="seconds")
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO run_log (started_at, markets) VALUES (?, ?)",
            (started_at, ", ".join(sorted(markets))),
        )
        return cur.lastrowid


def run_log_finish(run_id: int, stock_count: int) -> None:
    """Update the run record with finish time and duration."""
    from datetime import datetime
    finished_at = datetime.now().isoformat(timespec="seconds")
    with get_connection() as conn:
        row = conn.execute(
            "SELECT started_at FROM run_log WHERE id=?", (run_id,)
        ).fetchone()
        if row:
            from datetime import datetime as dt
            started = dt.fromisoformat(row["started_at"])
            duration = int((dt.fromisoformat(finished_at) - started).total_seconds())
        else:
            duration = None
        conn.execute(
            "UPDATE run_log SET finished_at=?, duration_sec=?, stock_count=? WHERE id=?",
            (finished_at, duration, stock_count, run_id),
        )


def fetch_run_log() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM run_log ORDER BY id DESC LIMIT 100"
        ).fetchall()
    return [dict(r) for r in rows]


def fetch_distinct_industries() -> list[str]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT industry FROM stocks WHERE industry IS NOT NULL AND industry != '' ORDER BY industry"
        ).fetchall()
    return [r["industry"] for r in rows]


def fetch_distinct_exchanges() -> list[str]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT exchange FROM stocks WHERE exchange IS NOT NULL AND exchange != '' ORDER BY exchange"
        ).fetchall()
    return [r["exchange"] for r in rows]


def fetch_distinct_sectors() -> list[str]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT sector FROM stocks WHERE sector IS NOT NULL AND sector != '' ORDER BY sector"
        ).fetchall()
    return [r["sector"] for r in rows]


def snapshot_taken_today() -> bool:
    today = date.today().isoformat()
    with get_connection() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM stock_history WHERE snapshot_date = ?", (today,)
        ).fetchone()[0]
    return count > 0
