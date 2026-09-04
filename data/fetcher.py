"""Fetch financial data for a list of tickers via yfinance."""
import logging
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import numpy as np
import yfinance as yf

from config import MIN_MARKET_CAP_USD, FETCH_WORKERS, FETCH_TIMEOUT_SECONDS

# Approximate local-currency → USD conversion rates (updated periodically)
_FX_TO_USD: dict[str, float] = {
    "JPY": 1 / 150, "KRW": 1 / 1_300, "HKD": 1 / 7.8,
    "INR": 1 / 83,  "AUD": 1 / 1.55,  "CAD": 1 / 1.35,
    "GBP": 1.27,    "EUR": 1.09,       "SEK": 1 / 10.5,
    "DKK": 1 / 7.0, "NOK": 1 / 10.5,  "CHF": 1.12,
    "CNY": 1 / 7.1, "SGD": 1 / 1.35,  "TWD": 1 / 32,
    "THB": 1 / 35,  "MXN": 1 / 17,    "BRL": 1 / 5,
    "ZAR": 1 / 18,  "ILS": 1 / 3.7,
}


def _to_usd(amount: float, currency: str) -> float:
    """Convert a market-cap figure from local currency to approximate USD."""
    if not currency or currency.upper() == "USD":
        return amount
    return amount * _FX_TO_USD.get(currency.upper(), 1.0)


def _normalize_dividend_yield(val: float | None) -> float | None:
    """Yahoo's dividendYield field is inconsistently scaled across API versions —
    sometimes a fraction (0.035 = 3.5%), sometimes already ×100 (3.5). A real
    dividend yield never legitimately exceeds 100% (unlike growth rates, which
    can), so >1.0 unambiguously means "already a percentage" — normalize to a
    true fraction here, once, at the source, so every consumer (display, filters)
    can trust the value without re-guessing."""
    if val is None:
        return None
    return val / 100 if val > 1.0 else val

log = logging.getLogger(__name__)

# yfinance industry string → our industry_pe_benchmarks key mapping
INDUSTRY_MAP = {
    "Drug Manufacturers—General": "Pharmaceuticals",
    "Drug Manufacturers—Specialty & Generic": "Pharmaceuticals",
    "Biotechnology": "Biotechnology & Medical Research",
    "Medical Devices": "Healthcare Equipment & Supplies",
    "Medical Instruments & Supplies": "Healthcare Equipment & Supplies",
    "Health Information Services": "Software & IT Services",
    "Software—Application": "Software & IT Services",
    "Software—Infrastructure": "Software & IT Services",
    "Information Technology Services": "Software & IT Services",
    "Semiconductors": "Semiconductors & Semiconductor Equipment",
    "Semiconductor Equipment & Materials": "Semiconductors & Semiconductor Equipment",
    "Consumer Electronics": "Computers, Phones & Household Electronics",
    "Electronic Components": "Computers, Phones & Household Electronics",
    "Computer Hardware": "Computers, Phones & Household Electronics",
    "Banks—Regional": "Banking Services",
    "Banks—Diversified": "Banking Services",
    "Capital Markets": "Investment Banking & Investment Services",
    "Asset Management": "Investment Banking & Investment Services",
    "Insurance—Life": "Insurance",
    "Insurance—Property & Casualty": "Insurance",
    "Insurance—Diversified": "Insurance",
    "Oil & Gas E&P": "Oil & Gas",
    "Oil & Gas Integrated": "Oil & Gas",
    "Oil & Gas Refining & Marketing": "Oil & Gas",
    "Oil & Gas Equipment & Services": "Oil & Gas Related Equipment and Services",
    "Specialty Chemicals": "Chemicals",
    "Chemicals": "Chemicals",
    "Aerospace & Defense": "Aerospace & Defense",
    "Airlines": "Passenger Transportation Services",
    "Railroads": "Transport Infrastructure",
    "Trucking": "Freight & Logistics Services",
    "Integrated Freight & Logistics": "Freight & Logistics Services",
    "Auto Manufacturers": "Automobiles & Auto Parts",
    "Auto Parts": "Automobiles & Auto Parts",
    "Beverages—Non-Alcoholic": "Beverages",
    "Beverages—Alcoholic": "Beverages",
    "Beverages—Brewers": "Beverages",
    "Packaged Foods": "Food & Tobacco",
    "Tobacco": "Food & Tobacco",
    "Apparel Manufacturing": "Textiles & Apparel",
    "Apparel Retail": "Textiles & Apparel",
    "Luxury Goods": "Textiles & Apparel",
    "Specialty Retail": "Specialty Retailers",
    "Department Stores": "Diversified Retail",
    "Discount Stores": "Diversified Retail",
    "Internet Retail": "Diversified Retail",
    "Publishing": "Media & Publishing",
    "Broadcasting": "Media & Publishing",
    "Entertainment": "Hotels & Entertainment Services",
    "Gambling": "Hotels & Entertainment Services",
    "Lodging": "Hotels & Entertainment Services",
    "Restaurants": "Hotels & Entertainment Services",
    "REIT—Retail": "Real Estate Operations",
    "REIT—Office": "Real Estate Operations",
    "REIT—Residential": "Real Estate Operations",
    "Real Estate Services": "Real Estate Operations",
    "Steel": "Metals & Mining",
    "Copper": "Metals & Mining",
    "Gold": "Metals & Mining",
    "Silver": "Metals & Mining",
    "Industrial Machinery": "Machinery, Equipment & Components",
    "Specialty Industrial Machinery": "Machinery, Equipment & Components",
    "Farm & Heavy Construction Machinery": "Machinery, Equipment & Components",
    "Building Materials": "Construction Materials",
    "Building Products & Equipment": "Homebuilding & Construction Supplies",
    "Residential Construction": "Homebuilding & Construction Supplies",
    "Engineering & Construction": "Construction & Engineering",
    "Utilities—Regulated Electric": "Electrical Utilities & IPPs",
    "Utilities—Independent Power Producers": "Electrical Utilities & IPPs",
    "Utilities—Renewable": "Renewable Energy",
    "Solar": "Renewable Energy",
    "Conglomerates": "Holding Companies",
    "Staffing & Employment Services": "Professional & Commercial Services",
    "Consulting Services": "Professional & Commercial Services",
    "Leisure": "Leisure Products",
    "Toys & Games": "Leisure Products",
    "Household & Personal Products": "Textiles & Apparel",
    "Communication Equipment": "Computers, Phones & Household Electronics",
    "Telecom Services": "Computers, Phones & Household Electronics",
}


def _map_industry(yf_industry: str | None, yf_sector: str | None) -> str:
    if yf_industry and yf_industry in INDUSTRY_MAP:
        return INDUSTRY_MAP[yf_industry]
    if yf_sector and yf_sector in INDUSTRY_MAP:
        return INDUSTRY_MAP[yf_sector]
    return yf_industry or yf_sector or "Unknown"


def _pct_change_trend(values: list[float]) -> str:
    """Return 'improving', 'stable', or 'declining' from a list of values."""
    clean = [v for v in values if v is not None and not np.isnan(v)]
    if len(clean) < 2:
        return "stable"
    slope = np.polyfit(range(len(clean)), clean, 1)[0]
    threshold = abs(np.mean(clean)) * 0.05 if np.mean(clean) != 0 else 0.005
    if slope > threshold:
        return "improving"
    if slope < -threshold:
        return "declining"
    return "stable"


def _safe(info: dict, key: str, default=None):
    val = info.get(key, default)
    if val is None:
        return default
    try:
        f = float(val)
        return None if (np.isnan(f) or np.isinf(f)) else f
    except (TypeError, ValueError):
        return val


def _drop_non_finite(series):
    """Like .dropna() but also removes +/-inf (division-by-zero, e.g. zero equity/revenue).

    pandas .dropna() only removes NaN — a divide-by-zero produces inf/-inf, which
    silently survives and then flows into the scoring engine as a fake max/min score.
    """
    return series[np.isfinite(series.astype(float))]


def _calc_roe_consistency(bs, inc) -> float | None:
    """Std dev of annual ROE over available years — lower = more consistent."""
    try:
        if bs is None or inc is None or bs.empty or inc.empty:
            return None
        equity = bs.loc["Stockholders Equity"] if "Stockholders Equity" in bs.index else None
        net_income = inc.loc["Net Income"] if "Net Income" in inc.index else None
        if equity is None or net_income is None:
            return None
        roe_series = net_income / equity
        clean = _drop_non_finite(roe_series.dropna()).values.astype(float)
        return float(np.std(clean)) if len(clean) >= 2 else None
    except Exception:
        return None


def _calc_gross_margin_history(inc) -> list[float]:
    try:
        if inc is None or inc.empty:
            return []
        if "Gross Profit" in inc.index and "Total Revenue" in inc.index:
            gp = inc.loc["Gross Profit"]
            rev = inc.loc["Total Revenue"]
            margins = _drop_non_finite((gp / rev).dropna()).values.astype(float).tolist()
            return margins
    except Exception:
        pass
    return []


def _calc_buyback_trend(bs) -> str:
    try:
        if bs is None or bs.empty:
            return "stable"
        if "Ordinary Shares Number" in bs.index:
            shares = bs.loc["Ordinary Shares Number"].dropna().values.astype(float)
        elif "Share Issued" in bs.index:
            shares = bs.loc["Share Issued"].dropna().values.astype(float)
        else:
            return "stable"
        if len(shares) < 2:
            return "stable"
        # columns are newest first
        if shares[0] < shares[-1] * 0.97:
            return "decreasing"  # buybacks happening (good)
        if shares[0] > shares[-1] * 1.03:
            return "increasing"  # dilution (bad)
        return "stable"
    except Exception:
        return "stable"


def _calc_debt_trend(bs) -> str:
    try:
        if bs is None or bs.empty:
            return "stable"
        key = None
        for k in ("Total Debt", "Long Term Debt", "Net Debt"):
            if k in bs.index:
                key = k
                break
        if key is None:
            return "stable"
        debt = bs.loc[key].dropna().values.astype(float)
        if len(debt) < 2:
            return "stable"
        if debt[0] < debt[-1] * 0.95:
            return "decreasing"
        if debt[0] > debt[-1] * 1.05:
            return "increasing"
        return "stable"
    except Exception:
        return "stable"


def _calc_roe_5yr(bs, inc) -> float | None:
    try:
        if bs is None or inc is None or bs.empty or inc.empty:
            return None
        equity = bs.loc["Stockholders Equity"] if "Stockholders Equity" in bs.index else None
        net_income = inc.loc["Net Income"] if "Net Income" in inc.index else None
        if equity is None or net_income is None:
            return None
        roe = _drop_non_finite((net_income / equity).dropna())
        return float(roe.mean()) if len(roe) > 0 else None
    except Exception:
        return None


def _calc_earnings_growth_5yr(inc) -> float | None:
    try:
        if inc is None or inc.empty:
            return None
        if "Net Income" not in inc.index:
            return None
        ni = inc.loc["Net Income"].dropna().values.astype(float)
        if len(ni) < 2:
            return None
        # columns newest first; oldest is ni[-1]
        if ni[-1] <= 0:
            return None
        cagr = (ni[0] / ni[-1]) ** (1 / (len(ni) - 1)) - 1
        return float(cagr)
    except Exception:
        return None


def _calc_revenue_growth_5yr(inc) -> float | None:
    try:
        if inc is None or inc.empty:
            return None
        if "Total Revenue" not in inc.index:
            return None
        rev = inc.loc["Total Revenue"].dropna().values.astype(float)
        if len(rev) < 2 or rev[-1] <= 0:
            return None
        cagr = (rev[0] / rev[-1]) ** (1 / (len(rev) - 1)) - 1
        return float(cagr)
    except Exception:
        return None


def _recent_dividend(info: dict) -> float | None:
    """Return lastDividendValue only if paid within the last 18 months, else None."""
    val = info.get("lastDividendValue")
    date_ts = info.get("lastDividendDate")
    if not val or not date_ts:
        return None
    from datetime import datetime, timezone, timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(days=548)  # 18 months
    try:
        paid = datetime.fromtimestamp(date_ts, tz=timezone.utc)
        return float(val) if paid > cutoff else None
    except Exception:
        return None


def _price_to_financial_currency(price: float | None, currency: str, financial_currency: str) -> float | None:
    """Convert a price quote to the currency used by EPS/earnings-estimate figures.

    Some exchanges quote price in a currency's minor unit while financial-statement
    figures (EPS, analyst estimates) are in the major unit — e.g. UK LSE stocks:
    price currency "GBp" (pence) vs financialCurrency "GBP" (pounds), a 100x gap.
    Detected generically: same 3-letter code, different case, price currency is
    lowercase-second-letter (the minor-unit convention) -> divide by 100.
    """
    if price is None:
        return None
    if (
        currency and financial_currency
        and currency != financial_currency
        and currency.upper() == financial_currency.upper()
    ):
        return price / 100
    return price


def _normalize_currency(currency: str, financial_currency: str) -> str:
    """Prefer the proper ISO code (financialCurrency is always 3 uppercase letters,
    e.g. 'GBP') over the price-quote currency, which can carry a minor-unit case
    variant (e.g. 'GBp' for pence). Falls back to uppercasing whatever we have."""
    if financial_currency:
        return financial_currency.upper()
    return (currency or "").upper()


def _analyst_pe(price: float | None, ee, year_key: str) -> float | None:
    """Return PE from analyst consensus avg (yfinance earnings_estimate).

    `price` must already be converted to the same currency as the estimate
    (see _price_to_financial_currency) — this function does no unit conversion.
    Returns None if no analyst data is available — no extrapolation.
    """
    if price is None or price <= 0 or ee is None or ee.empty:
        return None
    try:
        avg_eps = float(ee.loc[year_key, "avg"])
        if avg_eps > 0:
            result = round(price / avg_eps, 2)
            return result if np.isfinite(result) else None
    except Exception:
        pass
    return None


def fetch_ticker(ticker: str, _attempt: int = 1) -> dict | None:
    """Fetch all required data for a single ticker. Returns None if below cap filter.

    Retries up to 3 times with back-off when Yahoo returns empty data (rate-limit).
    """
    # Spread concurrent requests to reduce Yahoo rate-limit hits
    if _attempt == 1:
        time.sleep(random.uniform(0.5, 1.2))
    try:
        t = yf.Ticker(ticker)

        # --- Fast-path market-cap filter using fast_info (lightweight endpoint) ---
        market_cap: float | None = None
        currency: str = ""
        try:
            fi = t.fast_info
            market_cap = fi.market_cap
            currency = (getattr(fi, "currency", "") or "").upper()
        except Exception:
            pass


        # --- Full info fetch for fundamental data ---
        try:
            info = t.info or {}
        except Exception:
            info = {}

        # Empty info = rate-limited. Retry up to 3 times with backoff.
        if not info:
            if _attempt < 3:
                time.sleep(_attempt * 2 + random.uniform(0, 1))
                return fetch_ticker(ticker, _attempt=_attempt + 1)
            return None  # give up after 3 attempts

        # Validate exchange matches ticker suffix — skip if Yahoo returns wrong market
        _SUFFIX_EXCHANGES = {
            ".CO": {"CPH", "CSE", "KOB", "COP"},
            ".ST": {"STO", "NGM"},
            ".HE": {"HEL"},
            ".OL": {"OSL"},
        }
        yahoo_exchange = (_safe(info, "exchange") or "").upper()
        if yahoo_exchange:
            for suffix, valid in _SUFFIX_EXCHANGES.items():
                if ticker.upper().endswith(suffix) and yahoo_exchange not in valid:
                    return None

        # Reconcile market cap / currency from info (more accurate than fast_info)
        info_cap = _safe(info, "marketCap")
        info_cur = (_safe(info, "currency") or "").upper()
        if info_cap:
            market_cap = info_cap
        if info_cur:
            currency = info_cur

        financial_currency = (_safe(info, "financialCurrency") or "").upper()
        stored_currency = _normalize_currency(currency, financial_currency)
        # Analyst-estimate EPS is quoted in financial_currency (e.g. GBP pounds),
        # while the raw price quote can be in a minor unit of the SAME currency
        # (e.g. GBp pence) — convert before dividing, only for pe_2026/2027/2028.
        raw_price = _safe(info, "currentPrice") or _safe(info, "regularMarketPrice")
        analyst_price = _price_to_financial_currency(raw_price, currency, financial_currency)

        # Fetch balance sheet, financials, and analyst estimates
        bs = t.balance_sheet
        inc = t.financials
        try:
            ee = t.earnings_estimate
            if ee is None or ee.empty:
                ee = None
        except Exception:
            ee = None

        fcf = _safe(info, "freeCashflow")
        fcf_yield = (fcf / market_cap) if fcf and market_cap else None

        gross_margin_hist = _calc_gross_margin_history(inc)
        gross_margin = float(np.mean(gross_margin_hist)) if gross_margin_hist else _safe(info, "grossMargins")
        gross_margin_trend = _pct_change_trend(gross_margin_hist)

        roe_5yr = _calc_roe_5yr(bs, inc) or _safe(info, "returnOnEquity")
        roe_consistency = _calc_roe_consistency(bs, inc)
        earnings_growth = _calc_earnings_growth_5yr(inc) or _safe(info, "earningsGrowth")
        revenue_growth = _calc_revenue_growth_5yr(inc) or _safe(info, "revenueGrowth")
        buyback_trend = _calc_buyback_trend(bs)
        debt_trend = _calc_debt_trend(bs)

        industry = _map_industry(_safe(info, "industry", ""), _safe(info, "sector", ""))

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")

        return {
            "ticker": ticker,
            "name": _safe(info, "longName") or _safe(info, "shortName") or ticker,
            "exchange": _safe(info, "exchange") or "",
            "currency": stored_currency,
            "pe": _safe(info, "trailingPE"),
            "ps": _safe(info, "priceToSalesTrailing12Months"),
            "pb": _safe(info, "priceToBook"),
            "eps": _safe(info, "trailingEps"),
            "market_cap": market_cap,
            "dividend_yield": _normalize_dividend_yield(_safe(info, "dividendYield")),
            "dividend_amount": _recent_dividend(info),
            "fcf": fcf,
            "fcf_yield": fcf_yield,
            "debt_equity": _safe(info, "debtToEquity"),
            "roe_5yr": roe_5yr,
            "earnings_growth_5yr": earnings_growth,
            "revenue_growth_5yr": revenue_growth,
            "gross_margin": gross_margin,
            "gross_margin_trend": gross_margin_trend,
            "roe_consistency": roe_consistency,
            "insider_ownership": _safe(info, "heldPercentInsiders"),
            "buyback_trend": buyback_trend,
            "debt_trend": debt_trend,
            "industry": industry,
            "sector": _safe(info, "sector") or "",
            "pe_label": None,  # filled by scoring engine
            "pe_2026": _analyst_pe(analyst_price, ee, "0y"),
            "pe_2027": _analyst_pe(analyst_price, ee, "+1y"),
            "pe_2028": _analyst_pe(analyst_price, ee, "+2y"),
            "score_moat": None,
            "score_management": None,
            "score_financial": None,
            "score_valuation": None,
            "stock_rating": None,
            "last_updated": now,
        }
    except Exception as exc:
        log.debug("Failed to fetch %s: %s", ticker, exc)
        return None


def fetch_all(
    tickers: list[str],
    progress_callback=None,
) -> list[dict]:
    """Fetch data for all tickers using a thread pool.

    progress_callback(done: int, total: int) is called after each ticker.
    """
    results = []
    errors = 0
    shuffled = list(tickers)
    random.shuffle(shuffled)
    total = len(shuffled)
    done = 0

    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        futures = {pool.submit(fetch_ticker, t): t for t in shuffled}
        for future in as_completed(futures):
            done += 1
            try:
                row = future.result(timeout=FETCH_TIMEOUT_SECONDS)
                if row is not None:
                    results.append(row)
            except Exception as exc:
                errors += 1
                log.debug("Future error for %s: %s", futures[future], exc)
            if progress_callback:
                progress_callback(done, total)
            if done % 500 == 0:
                log.info("Progress %d/%d — %d passed filter so far (%d errors)",
                         done, total, len(results), errors)

    log.info(
        "Done: %d/%d tickers → %d passed $1B filter, %d fetch errors, %d filtered out",
        total, total, len(results), errors, total - len(results) - errors,
    )
    return results
