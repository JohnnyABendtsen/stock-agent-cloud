"""Stock Analyzer — Warren Buffett Criteria (web dashboard)

Read-only Streamlit view over the shared data/stocks.db, kept up to date by a
GitHub Actions cron job (update_data.py) — no user's PC needs to be on for the
data to refresh. Filter logic is byte-identical to the desktop app's Filters tab
(reuses data.filters_config directly), so behaviour never drifts between the two.
"""
import pandas as pd
import streamlit as st

from data import database, filters_config
import display_format as fmt

st.set_page_config(page_title="Stock Analyzer — Warren Buffett Criteria", layout="wide")


@st.cache_data(ttl=300)
def load_rows() -> list[dict]:
    return database.fetch_all_stocks()


@st.cache_data(ttl=300)
def load_dynamic_options() -> dict[str, list[str]]:
    return {
        "industry": database.fetch_distinct_industries(),
        "exchange": database.fetch_distinct_exchanges(),
        "sector":   database.fetch_distinct_sectors(),
    }


def build_settings_from_sidebar(rows: list[dict], dyn_options: dict[str, list[str]]) -> dict:
    settings = filters_config._build_defaults()

    st.sidebar.header("Numeric Thresholds")
    for key, label, db_field, direction, default, mn, mx, scale in filters_config.NUMERIC_SPEC:
        enabled = st.sidebar.checkbox(label, key=f"cb_{key}", value=False)
        val = st.sidebar.number_input(
            label, min_value=float(mn), max_value=float(mx), value=float(default),
            step=(1.0 if scale == 1 else 0.5), key=f"num_{key}", label_visibility="collapsed",
        )
        settings[key] = {"enabled": enabled, "value": val}

    st.sidebar.header("Categorical Filters")
    for key, label, db_field, all_values in filters_config.CATEGORICAL_SPEC:
        enabled = st.sidebar.checkbox(f"Enable: {label}", key=f"cb_{key}", value=False)
        picked = st.sidebar.multiselect(label, options=all_values, default=[], key=f"cat_{key}")
        settings[key] = {"enabled": enabled, "values": picked}

    st.sidebar.header("Data Filters")
    for key, label, db_field, supports_hide in filters_config.DYNAMIC_CATEGORICAL_SPEC:
        enabled = st.sidebar.checkbox(f"Enable: {label}", key=f"cb_dyn_{key}", value=False)
        options = dyn_options.get(key, [])
        picked = st.sidebar.multiselect(label, options=options, default=[], key=f"dyn_{key}")
        hide_blank = False
        if supports_hide:
            hide_blank = st.sidebar.checkbox(f"{label}: hide blank/Unknown", key=f"hide_{key}", value=False)
        settings[key] = {"enabled": enabled, "values": picked, "hide_blank": hide_blank}

    return settings


def to_dataframe(rows: list[dict]) -> pd.DataFrame:
    cols = filters_config  # noop, just for clarity
    data = []
    for row in rows:
        out = {}
        for key, label in fmt.COLUMNS:
            out[label] = fmt.fmt_value(key, row.get(key))
        data.append(out)
    return pd.DataFrame(data)


def style_table(df: pd.DataFrame):
    def color_pe_val(val):
        return f"background-color: {fmt._LABEL_COLORS.get(val, '')}" if val in fmt._LABEL_COLORS else ""

    def color_trend(val):
        return f"background-color: {fmt._TREND_COLORS.get(val, '')}" if val in fmt._TREND_COLORS else ""

    def color_rating(val):
        try:
            r = float(val)
        except (TypeError, ValueError):
            return ""
        if r >= 7:
            return "background-color: #c6efce"
        if r >= 4:
            return "background-color: #ffeb9c"
        return "background-color: #ffc7ce"

    styler = df.style
    if "PE Val" in df.columns:
        styler = styler.map(color_pe_val, subset=["PE Val"])
    if "PE Val 1" in df.columns:
        styler = styler.map(color_pe_val, subset=["PE Val 1"])
    if "Margin Trend" in df.columns:
        styler = styler.map(color_trend, subset=["Margin Trend"])
    if "Buyback Trend" in df.columns:
        styler = styler.map(color_trend, subset=["Buyback Trend"])
    if "Debt Trend" in df.columns:
        styler = styler.map(color_trend, subset=["Debt Trend"])
    if "Rating" in df.columns:
        styler = styler.map(color_rating, subset=["Rating"])
    return styler


def main() -> None:
    st.title("Stock Analyzer — Warren Buffett Criteria")

    rows = load_rows()
    dyn_options = load_dynamic_options()

    if not rows:
        st.warning("No data yet — the scheduled update hasn't run, or hasn't finished, yet.")
        return

    settings = build_settings_from_sidebar(rows, dyn_options)
    visible = filters_config.apply(rows, settings)

    search = st.text_input("Search ticker, name, industry…", "")
    if search:
        s = search.lower()
        visible = [
            r for r in visible
            if s in str(r.get("ticker", "")).lower()
            or s in str(r.get("name", "")).lower()
            or s in str(r.get("industry", "")).lower()
        ]

    total = len(rows)
    shown = len(visible)
    last_updated = rows[0].get("last_updated", "") if rows else ""
    if shown < total:
        st.caption(f"Showing {shown} of {total} stocks ({total - shown} hidden by filters) — last updated: {last_updated}")
    else:
        st.caption(f"Showing all {total} stocks — last updated: {last_updated}")

    df = to_dataframe(visible)
    df = df.drop(columns=["Ticker"], errors="ignore")  # hidden in the desktop app too
    st.dataframe(style_table(df), use_container_width=True, height=700)

    st.download_button(
        "Export to Excel (CSV)",
        data=df.to_csv(index=False).encode("utf-8"),
        file_name="stock_analysis.csv",
        mime="text/csv",
    )


if __name__ == "__main__":
    main()
