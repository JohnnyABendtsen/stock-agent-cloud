"""Per-parameter display filters for the Stocks tab."""
import json
import logging

from config import ASSETS_DIR

log = logging.getLogger(__name__)
_PATH = ASSETS_DIR / "filters.json"

# ── Spec tables ──────────────────────────────────────────────────────────────
#
# Numeric: (key, label, db_field, direction, default, min, max, scale)
#   scale: multiply DB value by this to get the user-visible number.
#          e.g. gross_margin stored as 0.55 → display 55 %, scale=100
#
NUMERIC_SPEC = [
    ("rating_min",          "Min Rating (1–10)",           "stock_rating",          "min",   1.0,    1.0,   10.0,   1),
    ("pe_max",              "Max P/E Ratio",               "pe",                    "max", 999.0,    0.0,  999.0,   1),
    ("ps_max",              "Max P/S Ratio",               "ps",                    "max", 999.0,    0.0,  999.0,   1),
    ("pb_max",              "Max Price / Book",            "pb",                    "max", 999.0,    0.0,  999.0,   1),
    ("eps_min",             "Min EPS",                     "eps",                   "min",   0.0, -999.0,  999.0,   1),
    ("dividend_yield_min",  "Min Dividend Yield %",        "dividend_yield",        "min",   0.0,    0.0,  100.0, 100),
    ("gross_margin_min",    "Min Gross Margin %",          "gross_margin",          "min",   0.0,    0.0,  100.0, 100),
    ("roe_5yr_min",         "Min ROE 5yr %",               "roe_5yr",               "min",   0.0, -100.0,  200.0, 100),
    ("roe_consistency_max", "Max ROE Consistency (std %)", "roe_consistency",       "max", 100.0,    0.0,  200.0, 100),
    ("debt_equity_max",     "Max Debt / Equity",           "debt_equity",           "max", 999.0,    0.0,  999.0,   1),
    ("fcf_yield_min",       "Min FCF Yield %",             "fcf_yield",             "min",   0.0, -100.0,  100.0, 100),
    ("earnings_growth_min", "Min Earnings Growth 5yr %",   "earnings_growth_5yr",   "min",   0.0, -200.0,  500.0, 100),
    ("revenue_growth_min",  "Min Revenue Growth 5yr %",    "revenue_growth_5yr",    "min",   0.0, -200.0,  500.0, 100),
    ("insider_min",         "Min Insider Ownership %",     "insider_ownership",     "min",   0.0,    0.0,  100.0, 100),
]

# ── Categorical filters ───────────────────────────────────────────────────────
# All categorical filters (fixed-value and DB-driven) now use the SAME shape:
#   settings[key] = {"enabled": bool, "values": list[str]}
# "values" is a whitelist — a row passes if its field is IN that list.
# An empty list means "no restriction from this filter" (nothing to narrow by yet).
#
# Fixed-value categoricals: (key, label, db_field, all_possible_values)
CATEGORICAL_SPEC = [
    ("margin_trend",  "Margin Trend",  "gross_margin_trend", ["improving", "stable", "declining"]),
    ("buyback_trend", "Buyback Trend", "buyback_trend",       ["decreasing", "stable", "increasing"]),
    ("debt_trend",    "Debt Trend",    "debt_trend",          ["decreasing", "stable", "increasing"]),
    ("pe_label",      "PE Val",        "pe_label",            ["Cheap", "Mid", "Expensive", "Unknown"]),
    ("pe_label_1",    "PE Val 1",      "pe_label_1",          ["Cheap", "Mid", "Expensive", "Unknown"]),
]

# DB-driven categoricals: (key, label, db_field, supports_hide_blank)
# supports_hide_blank: whether this filter also offers an independent
# "hide blank/Unknown" toggle (industry/sector have many Unknown rows;
# exchange rarely does, so it's skipped there).
DYNAMIC_CATEGORICAL_SPEC = [
    ("industry", "Industry", "industry", True),
    ("exchange", "Exchange", "exchange", False),
    ("sector",   "Sector",   "sector",   True),
]

# Legacy sentinel — only kept so old saved filters.json files can be migrated.
HIDE_BLANK = "__hide_blank__"


# ── Defaults (all disabled) ──────────────────────────────────────────────────

def _build_defaults() -> dict:
    d = {}
    for key, _, _, _, default, *_ in NUMERIC_SPEC:
        d[key] = {"enabled": False, "value": default}
    for key, _, _, _ in CATEGORICAL_SPEC:
        d[key] = {"enabled": False, "values": []}
    for key, _, _, _ in DYNAMIC_CATEGORICAL_SPEC:
        d[key] = {"enabled": False, "values": [], "hide_blank": False}
    return d


def _migrate_categorical(data: dict) -> None:
    """Upgrade old single-value categorical settings to the new 'values' list shape.

    Old shape was {"enabled": bool, "value": None | "SomeValue" | ["A", "B"] | HIDE_BLANK}.
    New shape is  {"enabled": bool, "values": [...]}  (+ "hide_blank" for dynamic ones).
    """
    for key, _, _, _ in CATEGORICAL_SPEC:
        s = data.get(key)
        if not isinstance(s, dict) or "values" in s:
            continue
        old = s.pop("value", None)
        if isinstance(old, list):
            s["values"] = old
        elif isinstance(old, str) and old != HIDE_BLANK:
            s["values"] = [old]
        else:
            s["values"] = []
        data[key] = s

    for key, _, _, _ in DYNAMIC_CATEGORICAL_SPEC:
        s = data.get(key)
        if not isinstance(s, dict) or "values" in s:
            continue
        old = s.pop("value", None)
        if old == HIDE_BLANK:
            s["values"] = []
            s["hide_blank"] = True
        elif isinstance(old, list):
            s["values"] = old
            s.setdefault("hide_blank", False)
        elif isinstance(old, str):
            s["values"] = [old]
            s.setdefault("hide_blank", False)
        else:
            s["values"] = []
            s.setdefault("hide_blank", False)
        data[key] = s


# ── Persistence ──────────────────────────────────────────────────────────────

def load() -> dict:
    defaults = _build_defaults()
    if not _PATH.exists():
        return defaults
    try:
        data = json.loads(_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("expected dict")
        # Merge with defaults so new keys always appear
        for k, v in defaults.items():
            if k not in data:
                data[k] = v
        _migrate_categorical(data)
        return data
    except Exception as exc:
        log.warning("Could not read %s, using defaults: %s", _PATH, exc)
        return defaults


def save(filters: dict) -> None:
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    _PATH.write_text(
        json.dumps(filters, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


# ── Filter application ───────────────────────────────────────────────────────

def apply(rows: list[dict], settings: dict) -> list[dict]:
    """Return only the rows that pass all enabled filters."""
    return [r for r in rows if _passes(r, settings)]


def _passes(row: dict, settings: dict) -> bool:
    for key, _, db_field, direction, default, _mn, _mx, scale in NUMERIC_SPEC:
        s = settings.get(key, {})
        if not s.get("enabled", False):
            continue
        val = row.get(db_field)
        if val is None:
            return False
        threshold = s.get("value", default) / scale
        if direction == "min" and val < threshold:
            return False
        if direction == "max" and val > threshold:
            return False

    for key, _, db_field, _all_values in CATEGORICAL_SPEC:
        s = settings.get(key, {})
        if not s.get("enabled", False):
            continue
        allowed = s.get("values") or []
        if not allowed:
            continue  # nothing checked = no restriction from this filter
        if row.get(db_field) not in allowed:
            return False

    for key, _, db_field, _supports_hide in DYNAMIC_CATEGORICAL_SPEC:
        s = settings.get(key, {})
        if not s.get("enabled", False):
            continue
        cell = row.get(db_field)
        if s.get("hide_blank") and (not cell or cell == "Unknown"):
            return False
        allowed = s.get("values") or []
        if allowed and cell not in allowed:
            return False

    return True
