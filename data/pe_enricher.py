"""Fetch missing PE0/PE1 from multiple channels — first-value-wins per field."""
import logging
import requests
from bs4 import BeautifulSoup
import yfinance as yf

log = logging.getLogger(__name__)

_HDR = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "da-DK,da;q=0.9,en;q=0.8",
}


# ── Channel 1: yfinance ───────────────────────────────────────────────────────

def _yfinance(ticker: str, price: float | None) -> tuple[float | None, float | None]:
    try:
        t = yf.Ticker(ticker)
        pe0, pe1 = None, None

        # Try earnings_estimate first (most accurate — analyst avg EPS)
        try:
            ee = t.earnings_estimate
            if ee is not None and not ee.empty:
                for col in ("0y", "0"):
                    try:
                        avg = float(ee.loc[col, "avg"])
                        if avg > 0 and price and price > 0:
                            pe0 = round(price / avg, 2)
                        break
                    except Exception:
                        pass
                for col in ("+1y", "1y", "1"):
                    try:
                        avg = float(ee.loc[col, "avg"])
                        if avg > 0 and price and price > 0:
                            pe1 = round(price / avg, 2)
                        break
                    except Exception:
                        pass
        except Exception:
            pass

        # Fallback: forwardPE from info (covers pe0 when earnings_estimate is empty)
        if pe0 is None:
            info = t.info or {}
            fpe = info.get("forwardPE")
            if fpe and float(fpe) > 0:
                pe0 = round(float(fpe), 2)

        return pe0, pe1
    except Exception as exc:
        log.debug("yfinance enricher %s: %s", ticker, exc)
        return None, None


# ── Channel 2: euroinvestor.dk ────────────────────────────────────────────────

def _euroinvestor(ticker: str, price: float | None) -> tuple[float | None, float | None]:
    # Blocked — 404 for all tested tickers
    return None, None


# ── Channel 3: aktietip.dk ────────────────────────────────────────────────────

def _aktietip(ticker: str, price: float | None) -> tuple[float | None, float | None]:
    # Blocked — 454 for all tested tickers
    return None, None


# ── Channel 4: marketscreener.com ─────────────────────────────────────────────

def _marketscreener(ticker: str, price: float | None) -> tuple[float | None, float | None]:
    # Blocked — 403 for all tested tickers
    return None, None


# ── Orchestrator ──────────────────────────────────────────────────────────────

_CHANNELS = [_yfinance, _euroinvestor, _aktietip, _marketscreener]


def enrich(ticker: str, price: float | None, need_pe0: bool, need_pe1: bool) -> tuple[float | None, float | None]:
    """Try each channel in order; stop per field at first hit."""
    pe0: float | None = None
    pe1: float | None = None

    for channel in _CHANNELS:
        if (not need_pe0 or pe0 is not None) and (not need_pe1 or pe1 is not None):
            break
        try:
            c0, c1 = channel(ticker, price)
        except Exception as exc:
            log.debug("Channel %s error for %s: %s", channel.__name__, ticker, exc)
            c0, c1 = None, None
        if need_pe0 and pe0 is None and c0 is not None:
            pe0 = c0
            log.info("%s pe0=%.2f from %s", ticker, pe0, channel.__name__)
        if need_pe1 and pe1 is None and c1 is not None:
            pe1 = c1
            log.info("%s pe1=%.2f from %s", ticker, pe1, channel.__name__)

    return pe0, pe1
