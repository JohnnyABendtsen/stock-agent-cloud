"""Pure-Python column list + value formatting — mirrors ui/stock_table.py exactly,
minus the PySide6/Qt bits, so it can run where Qt isn't installed (Streamlit Cloud,
GitHub Actions). Keep in sync by hand if the desktop app's columns/formatting change.
"""

COLUMNS = [
    ("ticker",              "Ticker"),
    ("name",                "Name"),
    ("industry",            "Industry"),
    ("exchange",            "Exchange"),
    ("currency",            "Currency"),
    ("stock_rating",        "Rating"),
    ("pe",                  "PE"),
    ("pe_label",            "PE Val"),
    ("pe_2026",             "PE0"),
    ("pe_2027",             "PE1"),
    ("pe_label_1",          "PE Val 1"),
    ("ps",                  "PS"),
    ("pb",                  "PB"),
    ("eps",                 "EPS"),
    ("market_cap",          "Market Cap"),
    ("dividend_yield",      "Div %"),
    ("dividend_amount",     "Div"),
    ("fcf",                 "FCF"),
    ("fcf_yield",           "FCF %"),
    ("debt_equity",         "D/E"),
    ("roe_5yr",             "ROE 5yr %"),
    ("earnings_growth_5yr", "EPS Growth 5yr %"),
    ("revenue_growth_5yr",  "Rev Growth 5yr %"),
    ("gross_margin",        "Gross Margin %"),
    ("gross_margin_trend",  "Margin Trend"),
    ("roe_consistency",     "ROE Consistency"),
    ("insider_ownership",   "Insider Own. %"),
    ("buyback_trend",       "Buyback Trend"),
    ("debt_trend",          "Debt Trend"),
    ("score_moat",          "Moat Score"),
    ("score_management",    "Mgmt Score"),
    ("score_financial",     "Financial Score"),
    ("score_valuation",     "Valuation Score"),
    ("last_updated",        "Updated"),
]

_PCT_COLS = {
    "dividend_yield", "fcf_yield", "roe_5yr",
    "earnings_growth_5yr", "revenue_growth_5yr",
    "gross_margin", "insider_ownership",
}

_ROUND2_COLS = {
    "pe", "ps", "pb", "eps", "pe_2026", "pe_2027",
    "debt_equity", "roe_consistency",
    "score_moat", "score_management", "score_financial",
    "score_valuation", "stock_rating", "dividend_amount",
}

_LABEL_COLORS = {
    "Cheap":     "#c6efce",
    "Mid":       "#ffeb9c",
    "Expensive": "#ffc7ce",
}
_TREND_COLORS = {
    "improving":  "#c6efce",
    "stable":     "#ffffff",
    "declining":  "#ffc7ce",
    "decreasing": "#c6efce",  # debt/shares decreasing = good
    "increasing": "#ffc7ce",
}


def fmt_value(key: str, val):
    """Return the display-ready value for one cell — same rules as the desktop app."""
    if val is None:
        return None
    if key in _PCT_COLS:
        try:
            return round(float(val) * 100, 2)
        except (TypeError, ValueError):
            return val
    if key in ("market_cap", "fcf"):
        try:
            return round(float(val) / 1e9, 2)  # always billions
        except (TypeError, ValueError):
            return val
    if key in _ROUND2_COLS:
        try:
            return round(float(val), 2)
        except (TypeError, ValueError):
            return val
    return val
