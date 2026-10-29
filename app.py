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
    """Vectorized cell coloring — np.select evaluates each column's condition in
    one C-level pass instead of calling a Python function per cell. A naive
    Styler.map() here took over a minute on the full ~5,000-row table (tested
    live and killed it); this renders instantly at the same row counts."""
    import numpy as np

    def label_colors(col: pd.Series) -> "np.ndarray":
        conditions = [col == label for label in fmt._LABEL_COLORS]
        choices = [f"background-color: {color}" for color in fmt._LABEL_COLORS.values()]
        return np.select(conditions, choices, default="")

    def trend_colors(col: pd.Series) -> "np.ndarray":
        conditions = [col == trend for trend in fmt._TREND_COLORS]
        choices = [f"background-color: {color}" for color in fmt._TREND_COLORS.values()]
        return np.select(conditions, choices, default="")

    def rating_colors(col: pd.Series) -> "np.ndarray":
        r = pd.to_numeric(col, errors="coerce")
        conditions = [r >= 7, r >= 4]
        choices = ["background-color: #c6efce", "background-color: #ffeb9c"]
        return np.select(conditions, choices, default="background-color: #ffc7ce")

    styler = df.style
    for col_name in ("PE Val", "PE Val 1"):
        if col_name in df.columns:
            styler = styler.apply(label_colors, subset=[col_name])
    for col_name in ("Margin Trend", "Buyback Trend", "Debt Trend"):
        if col_name in df.columns:
            styler = styler.apply(trend_colors, subset=[col_name])
    if "Rating" in df.columns:
        styler = styler.apply(rating_colors, subset=["Rating"])
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
    last_updated = max((r.get("last_updated") or "" for r in rows), default="")
    if shown < total:
        st.caption(f"Showing {shown} of {total} stocks ({total - shown} hidden by filters) — last updated: {last_updated}")
    else:
        st.caption(f"Showing all {total} stocks — last updated: {last_updated}")

    df = to_dataframe(visible)
    df = df.drop(columns=["Ticker"], errors="ignore")  # hidden in the desktop app too

    # Plain HTML table with narrow (content-width) columns and an always-visible,
    # explicitly styled horizontal scrollbar — st.dataframe's own grid component
    # only scrolls via shift+wheel with no visible drag handle, which wasn't
    # usable. Trade-off: no click-to-sort-by-column here (unlike the desktop
    # app) since this is a static table, not the interactive grid widget.
    #
    # Paginated (fixed page size) rather than rendering all rows as HTML at
    # once — tested and confirmed: the full ~5,400-row table as raw HTML froze
    # the browser tab solid (even `document.title` stopped responding for 45s+).
    # glide-data-grid (st.dataframe) avoids this via virtualization; a plain
    # HTML table has no such thing, so row count must stay small per render.
    _PAGE_SIZE = 50
    n_pages = max(1, -(-len(df) // _PAGE_SIZE))  # ceil div
    if "stock_page" not in st.session_state:
        st.session_state.stock_page = 1
    st.session_state.stock_page = min(st.session_state.stock_page, n_pages)

    pcol1, pcol2, pcol3 = st.columns([1, 2, 1])
    with pcol1:
        if st.button("◀ Previous", disabled=st.session_state.stock_page <= 1):
            st.session_state.stock_page -= 1
    with pcol3:
        if st.button("Next ▶", disabled=st.session_state.stock_page >= n_pages):
            st.session_state.stock_page += 1
    with pcol2:
        st.markdown(
            f"<div style='text-align:center'>Page {st.session_state.stock_page} of {n_pages} "
            f"({len(df)} rows, {_PAGE_SIZE}/page)</div>",
            unsafe_allow_html=True,
        )

    start = (st.session_state.stock_page - 1) * _PAGE_SIZE
    page_df = df.iloc[start:start + _PAGE_SIZE]

    # Explicit 2-decimal format per column — lost when switching off
    # st.dataframe's NumberColumn(format="%.2f"); pandas' default to_html()
    # otherwise shows up to 6 decimals (e.g. "10.000000" instead of "10.00").
    # Same column set already verified against the desktop app earlier
    # (display_format._ROUND2_COLS | _PCT_COLS, translated key -> label).
    _key_to_label = dict(fmt.COLUMNS)
    _two_decimal_labels = {
        _key_to_label[k] for k in (fmt._ROUND2_COLS | fmt._PCT_COLS) if k in _key_to_label
    }
    fmt_map = {col: "{:.2f}" for col in page_df.columns if col in _two_decimal_labels}
    html_table = style_table(page_df).format(fmt_map, na_rep="").hide(axis="index").to_html()

    # Fixed pixel widths per column — content too long to fit is clipped with
    # an ellipsis instead of forcing the column (and the whole table) wider,
    # same as the desktop's Fixed column-resize mode. Built from df.columns
    # (fixed, known order), so it doesn't depend on guessing pixel position
    # by hand.
    # Every genuine number field gets the exact same width — PS/PB/EPS and
    # ROE Consistency/Insider Own. were in their own (narrower/wider) groups
    # before, which visibly broke the grid's alignment; user caught it on a
    # screenshot. Text/label columns (Cheap/Mid/Expensive, stable/improving/
    # declining) stay in their own slightly wider group since they hold words,
    # not numbers.
    _W48 = {
        "PE", "PE0", "PE1", "Rating", "Currency", "Exchange", "Div %", "Div",
        "FCF %", "D/E", "FCF", "ROE 5yr %", "EPS Growth 5yr %", "Rev Growth 5yr %",
        "Gross Margin %", "Moat Score", "Mgmt Score", "Financial Score", "Valuation Score",
        "PS", "PB", "EPS", "ROE Consistency", "Insider Own. %", "Market Cap",
    }
    _W150 = {"Name", "Industry"}
    _W57 = {"Margin Trend", "PE Val", "PE Val 1", "Buyback Trend", "Debt Trend"}
    _DEFAULT_W = 48  # fall back to the same number-field width, not a guess
    _W_UPDATED = 170  # timestamp needs more room than the default

    def _col_width(label: str) -> int:
        if label in _W48: return 48
        if label in _W150: return 150
        if label in _W57: return 57
        if label == "Updated": return _W_UPDATED
        return _DEFAULT_W

    # <colgroup> is the authoritative, browser-consistent way to fix column
    # widths on a table-layout:fixed table — nth-child width rules on th/td
    # measured 2-9px off their intended value (pandas Styler's own inline
    # per-cell styles were interfering); colgroup isn't subject to that.
    colgroup = "<colgroup>" + "".join(
        f'<col style="width:{_col_width(col)}px">' for col in page_df.columns
    ) + "</colgroup>"
    _table_open_end = html_table.index(">", html_table.index("<table")) + 1
    html_table = html_table[:_table_open_end] + colgroup + html_table[_table_open_end:]

    # table-layout:fixed only honors colgroup widths exactly when the table's
    # own width is an explicit total, not "auto" — with "auto" the browser let
    # columns balloon far past their <col> widths (measured 46-258px against
    # a 48/57/150px spec). box-sizing:border-box is needed too, otherwise
    # th/td padding (4px 8px) adds on top of the specified width instead of
    # being included in it.
    _table_total_w = sum(_col_width(col) for col in page_df.columns)

    st.markdown(
        f"""
        <style>
        .stock-table-wrap {{
            overflow-x: scroll;
            overflow-y: auto;
            max-height: 700px;
            border: 1px solid #ddd;
        }}
        .stock-table-wrap::-webkit-scrollbar {{ height: 16px; }}
        .stock-table-wrap::-webkit-scrollbar-track {{ background: #f0f0f0; }}
        .stock-table-wrap::-webkit-scrollbar-thumb {{
            background: #888; border-radius: 8px; border: 3px solid #f0f0f0;
        }}
        .stock-table-wrap::-webkit-scrollbar-thumb:hover {{ background: #555; }}
        .stock-table-wrap table {{
            border-collapse: collapse; font-size: 13px; table-layout: fixed; width: {_table_total_w}px;
        }}
        .stock-table-wrap th, .stock-table-wrap td {{
            padding: 4px 8px; white-space: nowrap; border-bottom: 1px solid #eee; text-align: left;
            overflow: hidden; text-overflow: ellipsis; box-sizing: border-box;
        }}
        .stock-table-wrap th {{
            position: sticky; top: 0; background: #fff; z-index: 1; border-bottom: 2px solid #ccc;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(f'<div class="stock-table-wrap">{html_table}</div>', unsafe_allow_html=True)

    st.download_button(
        "Export to Excel (CSV)",
        data=df.to_csv(index=False).encode("utf-8"),
        file_name="stock_analysis.csv",
        mime="text/csv",
    )


if __name__ == "__main__":
    main()
