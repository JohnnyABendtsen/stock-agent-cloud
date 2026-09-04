"""Fetch index constituent tickers from APIs, Wikipedia, and static lists."""
import json
import logging
import re
from collections.abc import Callable
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf
from yfinance import EquityQuery

log = logging.getLogger(__name__)

_CACHE_DIR = Path(__file__).parent.parent / "assets" / "ticker_cache"


def _load_cache(key: str) -> list[str]:
    path = _CACHE_DIR / f"{key}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_cache(key: str, tickers: list[str]) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (_CACHE_DIR / f"{key}.json").write_text(
            json.dumps(tickers, ensure_ascii=False), encoding="utf-8"
        )
    except Exception:
        pass

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}

_SSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "http://www.sse.com.cn/",
    "Accept": "application/json",
}


# ── Nasdaq screener API helper ───────────────────────────────────────────────

def _nasdaq_api_tickers(exchange: str) -> list[str]:
    tickers: list[str] = []
    offset = 0
    limit = 1000
    while True:
        try:
            url = (
                f"https://api.nasdaq.com/api/screener/stocks"
                f"?tableonly=true&exchange={exchange}&offset={offset}&limit={limit}"
            )
            resp = requests.get(url, headers=_HEADERS, timeout=20)
            resp.raise_for_status()
            data = resp.json()
            rows = data.get("data", {}).get("table", {}).get("rows") or []
            if not rows:
                break
            for r in rows:
                sym = r.get("symbol", "").strip()
                if sym and "/" not in sym and "^" not in sym:
                    tickers.append(sym)
            if len(rows) < limit:
                break
            offset += limit
        except Exception as exc:
            log.warning("Nasdaq API failed exchange=%s offset=%d: %s", exchange, offset, exc)
            break
    return tickers


# ── OpenFIGI fallback for Nordic tickers ────────────────────────────────────

def _openfigi_tickers(exch_code: str, yahoo_suffix: str) -> list[str]:
    """Fetch tickers from OpenFIGI (free, no API key) for an exchange.
    Used as fallback when Yahoo screener is rate-limited.
    NOTE: OpenFIGI omits hyphens in share-class tickers (COLOB vs COLO-B).
          Yahoo screener cache is preferred when available."""
    tickers: list[str] = []
    payload: dict = {"exchCode": exch_code, "securityType": "Common Stock"}
    next_cursor = None
    for _ in range(30):  # max 30 pages
        if next_cursor:
            payload["start"] = next_cursor
        try:
            resp = requests.post(
                "https://api.openfigi.com/v3/search",
                headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
                json=payload,
                timeout=15,
            )
            if resp.status_code == 429:
                import time as _time; _time.sleep(12)
                continue
            resp.raise_for_status()
            data = resp.json()
            for item in data.get("data", []):
                sym = item.get("ticker", "").strip()
                if sym:
                    tickers.append(sym + yahoo_suffix)
            next_cursor = data.get("next")
            if not next_cursor:
                break
            import time as _time; _time.sleep(0.5)
        except Exception as exc:
            log.warning("OpenFIGI failed for %s: %s", exch_code, exc)
            break
    if tickers:
        _save_cache(f"openfigi_{exch_code}", tickers)
        log.info("OpenFIGI %s: %d tickers", exch_code, len(tickers))
    return tickers


# ── Yahoo Finance screener ───────────────────────────────────────────────────

def _yahoo_screener_tickers(exchange_code: str) -> list[str]:
    """Fetch all tickers for an exchange via Yahoo Finance screener (paginated).
    Results are cached to disk; stale cache is used if Yahoo is rate-limited."""
    tickers: list[str] = []
    try:
        q = EquityQuery('and', [
            EquityQuery('eq', ['exchange', exchange_code]),
            EquityQuery('gt', ['avgdailyvol3m', 0]),
        ])
        offset = 0
        size = 250
        total = None
        while total is None or offset < total:
            result = yf.screen(q, offset=offset, size=size, sortField='ticker')
            quotes = result.get('quotes', [])
            if total is None:
                total = result.get('total', 0)
            for quote in quotes:
                sym = quote.get('symbol', '').strip()
                if sym:
                    tickers.append(sym)
            if not quotes or (total is not None and offset + size >= total):
                break
            offset += size
        log.info("Yahoo screener %s: %d tickers (total=%s)", exchange_code, len(tickers), total)
        if tickers:
            _save_cache(f"screener_{exchange_code}", tickers)
    except Exception as exc:
        log.warning("Yahoo screener failed for %s: %s", exchange_code, exc)
    if not tickers:
        cached = _load_cache(f"screener_{exchange_code}")
        if cached:
            log.info("Yahoo screener %s: using cached list (%d tickers)", exchange_code, len(cached))
            return cached
    return tickers


# ── Nasdaq Nordic scraper ────────────────────────────────────────────────────

def _scrape_nasdaq_nordic(market: str, suffix: str) -> list[str]:
    """
    Scrape all tickers for a Nasdaq Nordic market.
    market: 'copenhagen' | 'stockholm' | 'helsinki' | 'oslo'
    suffix: '.CO' | '.ST' | '.HE' | '.OL'
    """
    # Try Nasdaq API first (works for Nordic too)
    api_results = _nasdaq_api_tickers(market)
    if api_results:
        # Nasdaq API returns bare symbols for Nordic — append exchange suffix
        result = []
        for t in api_results:
            if "." not in t:
                result.append(t + suffix)
            else:
                result.append(t)
        log.info("Nasdaq API (%s): %d tickers", market, len(result))
        return result

    # Fallback: scrape Nasdaq Nordic website
    try:
        url = f"https://www.nasdaqomxnordic.com/shares/listed-companies/{market}"
        resp = requests.get(url, headers=_HEADERS, timeout=15)
        pat = suffix.replace(".", r"\.")
        found = re.findall(rf'\b([A-Z0-9\-]{{2,12}}){pat}\b', resp.text)
        unique = list(dict.fromkeys(t + suffix for t in found if len(t) >= 2))
        if len(unique) > 10:
            log.info("Nasdaq Nordic page (%s): %d tickers", market, len(unique))
            return unique
    except Exception as exc:
        log.debug("Nasdaq Nordic page failed (%s): %s", market, exc)
    return []


# ── SSE / SZSE (Shanghai + Shenzhen) scrapers ────────────────────────────────

def _scrape_sse() -> list[str]:
    """Fetch all A-share tickers from Shanghai Stock Exchange API."""
    tickers: list[str] = []
    page = 1
    page_size = 100
    while True:
        try:
            resp = requests.get(
                "http://query.sse.com.cn/sseQuery/commonSoaQuery.do",
                params={
                    "sqlId": "COMMON_SSE_CP_GPJCTPZ_GPLB_GP_L",
                    "isPagination": "true",
                    "pageHelp.pageSize": str(page_size),
                    "pageHelp.pageNo": str(page),
                    "pageHelp.beginPage": str(page),
                    "pageHelp.endPage": str(page + 1),
                },
                headers=_SSE_HEADERS,
                timeout=15,
            )
            data = resp.json()
            rows = data.get("pageHelp", {}).get("data") or data.get("result") or []
            if not rows:
                break
            for r in rows:
                code = str(r.get("COMPANY_CODE") or r.get("companyCode") or "").strip()
                if re.match(r'^\d{6}$', code):
                    tickers.append(code + ".SS")
            total = int(data.get("pageHelp", {}).get("total") or len(tickers))
            if len(tickers) >= total or len(rows) < page_size:
                break
            page += 1
        except Exception as exc:
            log.debug("SSE API page %d failed: %s", page, exc)
            break
    log.info("SSE API returned %d tickers", len(tickers))
    return tickers


def _scrape_szse() -> list[str]:
    """Fetch all A-share tickers from Shenzhen Stock Exchange API."""
    tickers: list[str] = []
    try:
        resp = requests.get(
            "https://www.szse.cn/api/report/ShowReport/data",
            params={
                "SHOWTYPE": "JSON",
                "CATALOGID": "1110",
                "TABKEY": "tab1",
                "random": "0.1",
            },
            headers={**_HEADERS, "Referer": "https://www.szse.cn/"},
            timeout=20,
        )
        data = resp.json()
        rows = data[0].get("data") if isinstance(data, list) else []
        for r in rows:
            code = str(r.get("zqdm") or r.get("ZQDM") or "").strip()
            if re.match(r'^\d{6}$', code):
                tickers.append(code + ".SZ")
        log.info("SZSE API returned %d tickers", len(tickers))
    except Exception as exc:
        log.debug("SZSE API failed: %s", exc)
    return tickers



_TOKYO_STATIC = [
    # Auto
    "7203.T", "7267.T", "7201.T", "7269.T", "7270.T", "7272.T", "7261.T",
    "7309.T", "7011.T", "7012.T", "7013.T",
    # Electronics / Tech
    "6758.T", "6861.T", "7741.T", "6857.T", "8035.T", "6954.T", "6594.T",
    "6702.T", "6701.T", "6723.T", "6645.T", "6479.T", "6506.T", "6586.T",
    "6952.T", "6762.T", "6971.T", "6902.T", "7751.T", "7735.T", "7733.T",
    "4543.T", "4661.T", "3659.T", "2432.T", "2413.T",
    # Financial
    "8306.T", "8316.T", "8411.T", "8766.T", "8725.T", "8001.T", "8002.T",
    "8015.T", "8031.T", "8053.T", "8058.T", "8309.T", "8604.T", "8630.T",
    "8802.T", "8750.T",
    # Telecom
    "9432.T", "9433.T", "9434.T", "9984.T", "9613.T", "9719.T",
    # Transport / Logistics
    "9020.T", "9021.T", "9022.T", "9024.T", "9064.T", "9101.T", "9104.T",
    "9107.T", "9201.T",
    # Energy / Utilities
    "9501.T", "9531.T", "5401.T", "5020.T",
    # Chemicals / Materials
    "4063.T", "4005.T", "4183.T", "4188.T", "3407.T", "3405.T", "3436.T",
    "5706.T", "5713.T", "5802.T", "5108.T",
    # Pharma / Medical
    "4502.T", "4519.T", "4523.T", "4507.T", "4528.T", "4151.T", "4568.T",
    "4578.T",
    # Retail / Consumer
    "3382.T", "3086.T", "3088.T", "3289.T", "2914.T", "2802.T", "2503.T",
    "2502.T", "2531.T", "2269.T", "2282.T", "2502.T",
    # Construction / Real Estate
    "6098.T", "8802.T", "1925.T", "1928.T", "1963.T",
    # Industrial machinery
    "6367.T", "6301.T", "6326.T", "6361.T", "6503.T", "6501.T",
    # Services / Entertainment
    "6954.T", "4704.T", "4755.T", "7832.T", "9766.T", "4452.T",
    "4324.T", "2413.T", "3659.T",
    # Additional TSE Prime blue chips
    "6501.T", "6723.T", "7912.T", "7186.T", "8750.T", "6479.T",
    "1332.T", "1333.T", "2001.T", "2002.T", "2003.T", "2004.T",
    "2108.T", "2120.T", "2201.T", "2204.T", "2206.T", "2207.T",
    "2212.T", "2221.T", "2229.T", "2264.T", "2267.T", "2270.T",
    "2281.T", "2290.T", "2301.T", "2309.T", "2317.T", "2327.T",
    "2353.T", "2378.T", "2384.T", "2395.T", "2404.T", "2411.T",
    "2412.T", "2453.T", "2461.T", "2471.T", "2492.T", "2501.T",
    "2533.T", "2607.T", "2651.T", "2670.T", "2695.T", "2702.T",
    "2726.T", "2734.T", "2750.T", "2764.T", "2784.T", "2790.T",
    "2801.T", "2809.T", "2810.T", "2811.T", "2819.T", "2875.T",
    "2882.T", "2897.T", "2901.T", "2907.T", "2910.T", "2922.T",
    "2930.T", "2980.T", "3001.T", "3004.T", "3010.T", "3016.T",
    "3023.T", "3028.T", "3031.T", "3038.T", "3053.T", "3064.T",
    "3092.T", "3099.T", "3101.T", "3103.T", "3104.T", "3105.T",
    "3106.T", "3107.T", "3110.T", "3116.T", "3141.T", "3143.T",
    "3159.T", "3161.T", "3167.T", "3169.T", "3171.T", "3176.T",
    "3182.T", "3184.T", "3189.T", "3193.T", "3196.T", "3197.T",
    "3198.T", "3199.T", "3201.T", "3202.T", "3204.T", "3205.T",
    "3206.T", "3207.T", "3208.T", "3209.T", "3210.T", "3211.T",
]

_SMI20_STATIC = [
    "NESN.SW", "ROG.SW", "NOVN.SW", "ALC.SW", "CFR.SW", "ZURN.SW",
    "ABBN.SW", "UBSG.SW", "CSGN.SW", "SREN.SW", "GIVN.SW", "LONN.SW",
    "SIKA.SW", "SCHP.SW", "KNIN.SW", "BAER.SW", "GEBN.SW", "SLHN.SW",
    "HOLN.SW", "LHN.SW",
]

_DAX40_STATIC = [
    "ADS.DE", "AIR.DE", "ALV.DE", "BAS.DE", "BAYN.DE", "BMW.DE", "BNR.DE",
    "CON.DE", "1COV.DE", "DHER.DE", "DB1.DE", "DBK.DE", "DHL.DE", "DTE.DE",
    "EOAN.DE", "FRE.DE", "HNR1.DE", "HEI.DE", "HEN3.DE", "IFX.DE", "KWS.DE",
    "LIN.DE", "MBG.DE", "MRK.DE", "MTX.DE", "MUV2.DE", "PAH3.DE", "PUM.DE",
    "QGEN.DE", "RHM.DE", "RWE.DE", "SAP.DE", "SHL.DE", "SIE.DE", "SRT3.DE",
    "SY1.DE", "VNA.DE", "VOW3.DE", "VWS.DE", "ZAL.DE",
]

_CAC40_STATIC = [
    "AC.PA", "ACA.PA", "AI.PA", "AIR.PA", "ALO.PA", "AM.PA", "ATO.PA",
    "BN.PA", "BNP.PA", "CA.PA", "CAP.PA", "CS.PA", "DG.PA", "DSY.PA",
    "ENGI.PA", "EL.PA", "ERF.PA", "FR.PA", "GLE.PA", "HO.PA", "KER.PA",
    "LHN.PA", "LR.PA", "MC.PA", "ML.PA", "MT.PA", "OR.PA", "ORA.PA",
    "PUB.PA", "RI.PA", "RMS.PA", "RNO.PA", "SAF.PA", "SAN.PA", "SGO.PA",
    "STLAP.PA", "STM.PA", "SU.PA", "TTE.PA", "VIE.PA",
]

_FTSE100_STATIC = [
    "AAF.L", "AAL.L", "ABF.L", "ADM.L", "AHT.L", "ANTO.L", "AUTO.L",
    "AV.L", "AZN.L", "BA.L", "BARC.L", "BATS.L", "BDEV.L", "BKG.L",
    "BP.L", "BRBY.L", "BT-A.L", "CCH.L", "CEG.L", "CNA.L", "CPG.L",
    "CRDA.L", "CRH.L", "DCC.L", "DGE.L", "DPLM.L", "EDV.L", "ENT.L",
    "EXPN.L", "EZJ.L", "FCIT.L", "FERG.L", "FLTR.L", "FRES.L", "GKN.L",
    "GSK.L", "HIK.L", "HL.L", "HLMA.L", "HLN.L", "HSBA.L", "HSX.L",
    "IAG.L", "ICG.L", "IHG.L", "III.L", "IMB.L", "INF.L", "ITRK.L",
    "JD.L", "KGF.L", "LAND.L", "LGEN.L", "LLOY.L", "LMP.L", "LSEG.L",
    "MKS.L", "MNDI.L", "MNG.L", "MRO.L", "NG.L", "NWG.L", "NXT.L",
    "OCDO.L", "PHNX.L", "PRU.L", "PSH.L", "PSN.L", "PSON.L", "RB.L",
    "REC.L", "REL.L", "RIO.L", "RKT.L", "RMV.L", "RR.L", "RS1.L",
    "RSA.L", "SDR.L", "SGE.L", "SHEL.L", "SMDS.L", "SMIN.L", "SMT.L",
    "SN.L", "SPX.L", "SSE.L", "STAN.L", "SVT.L", "TSCO.L", "TW.L",
    "ULVR.L", "UU.L", "VOD.L", "VTY.L", "WEIR.L", "WG.L", "WPP.L",
    "WTB.L",
]

_SEOUL_STATIC = [
    # KOSPI large cap
    "005930.KS", "000660.KS", "373220.KS", "207940.KS", "005380.KS",
    "006400.KS", "051910.KS", "035420.KS", "000270.KS", "068270.KS",
    "035720.KS", "028260.KS", "105560.KS", "055550.KS", "032830.KS",
    "003550.KS", "012330.KS", "066570.KS", "017670.KS", "030200.KS",
    "086790.KS", "010130.KS", "096770.KS", "009830.KS", "018260.KS",
    "034730.KS", "003490.KS", "011070.KS", "047050.KS", "005490.KS",
    "000810.KS", "139480.KS", "015760.KS", "009150.KS", "034020.KS",
    "000100.KS", "011200.KS", "097950.KS", "004020.KS", "079550.KS",
    "010950.KS", "024110.KS", "032640.KS", "033780.KS", "036570.KS",
    "042660.KS", "051900.KS", "064350.KS", "078930.KS", "090430.KS",
    "267250.KS", "316140.KS", "323410.KS", "352820.KS", "377300.KS",
    "000720.KS", "001040.KS", "001450.KS", "001740.KS", "002380.KS",
    "002790.KS", "003230.KS", "003670.KS", "003830.KS", "004000.KS",
    "004170.KS", "004990.KS", "005070.KS", "005830.KS", "005940.KS",
    "006260.KS", "006800.KS", "007070.KS", "007310.KS", "008560.KS",
    "009540.KS", "010140.KS", "010620.KS", "011170.KS", "011790.KS",
    "012450.KS", "014680.KS", "015020.KS", "016360.KS", "017800.KS",
    "018880.KS", "020150.KS", "021240.KS", "023530.KS", "024900.KS",
    "025840.KS", "026960.KS", "028050.KS", "029780.KS", "030000.KS",
    "033240.KS", "034730.KS", "035000.KS", "036460.KS", "037270.KS",
    "039490.KS", "041650.KS", "042670.KS", "044490.KS", "047040.KS",
    "051600.KS", "055490.KS", "057050.KS", "060000.KS", "069960.KS",
    "071050.KS", "082740.KS", "085660.KS", "086280.KS", "088350.KS",
    "093050.KS", "097950.KS", "101670.KS", "102940.KS", "108670.KS",
    # KOSDAQ
    "035900.KQ", "086900.KQ", "091990.KQ", "112040.KQ", "145020.KQ",
    "196170.KQ", "214150.KQ", "247540.KQ", "263750.KQ", "357780.KQ",
    "039030.KQ", "041510.KQ", "048410.KQ", "053800.KQ", "060370.KQ",
    "064760.KQ", "078600.KQ", "086520.KQ", "095660.KQ", "141080.KQ",
]

# Hong Kong (HKEX) — .HK suffix; codes are zero-padded to 4 digits
_HONG_KONG_STATIC = [
    # Mega cap / Hang Seng constituents
    "0700.HK", "9988.HK", "0005.HK", "1299.HK", "3690.HK", "0883.HK",
    "0941.HK", "1211.HK", "2388.HK", "2318.HK", "0267.HK", "1810.HK",
    "2628.HK", "3988.HK", "0011.HK", "0001.HK", "0002.HK", "0016.HK",
    "0066.HK", "0388.HK", "1109.HK", "1177.HK", "0027.HK", "1876.HK",
    "0288.HK", "0012.HK", "0019.HK", "0293.HK", "9999.HK", "9618.HK",
    "9888.HK", "9961.HK", "9866.HK", "1024.HK", "9626.HK", "2007.HK",
    "0003.HK", "0006.HK", "0017.HK", "0083.HK", "0101.HK", "0135.HK",
    "0151.HK", "0175.HK", "0241.HK", "0291.HK", "0316.HK", "0322.HK",
    "0358.HK", "0386.HK", "0392.HK", "0489.HK", "0494.HK", "0522.HK",
    "0548.HK", "0551.HK", "0669.HK", "0688.HK", "0762.HK", "0788.HK",
    "0836.HK", "0857.HK", "0868.HK", "0881.HK", "0960.HK", "0968.HK",
    "0992.HK", "1038.HK", "1044.HK", "1066.HK", "1071.HK", "1088.HK",
    "1093.HK", "1099.HK", "1113.HK", "1128.HK", "1137.HK", "1169.HK",
    "1171.HK", "1193.HK", "1199.HK", "1209.HK", "1214.HK", "1288.HK",
    "1336.HK", "1339.HK", "1347.HK", "1359.HK", "1378.HK", "1398.HK",
    "1448.HK", "1458.HK", "1530.HK", "1579.HK", "1618.HK", "1658.HK",
    "1666.HK", "1688.HK", "1699.HK", "1772.HK", "1776.HK", "1787.HK",
    "1797.HK", "1801.HK", "1821.HK", "1830.HK", "1833.HK", "1858.HK",
    "1860.HK", "1880.HK", "1882.HK", "1883.HK", "1898.HK", "1910.HK",
    "1919.HK", "1928.HK", "1929.HK", "1958.HK", "1963.HK", "1972.HK",
    "1988.HK", "2007.HK", "2018.HK", "2038.HK", "2066.HK", "2128.HK",
    "2168.HK", "2196.HK", "2202.HK", "2238.HK", "2269.HK", "2313.HK",
    "2331.HK", "2333.HK", "2338.HK", "2343.HK", "2356.HK", "2378.HK",
    "2382.HK", "2386.HK", "2400.HK", "2488.HK", "2518.HK", "2600.HK",
    "2601.HK", "2607.HK", "2611.HK", "2628.HK", "2638.HK", "2678.HK",
    "3328.HK", "3333.HK", "3360.HK", "3380.HK", "3618.HK", "3633.HK",
    "3668.HK", "3690.HK", "3800.HK", "3888.HK", "3908.HK", "3968.HK",
    "3988.HK", "4338.HK", "6030.HK", "6060.HK", "6078.HK", "6098.HK",
    "6110.HK", "6160.HK", "6168.HK", "6178.HK", "6185.HK", "6186.HK",
    "6618.HK", "6690.HK", "6862.HK", "6969.HK", "6993.HK",
]

# Australia (ASX) — .AX suffix
_AUSTRALIA_STATIC = [
    # ASX 200 major constituents
    "BHP.AX", "CBA.AX", "CSL.AX", "NAB.AX", "WBC.AX", "ANZ.AX",
    "RIO.AX", "WES.AX", "WOW.AX", "MQG.AX", "FMG.AX", "TLS.AX",
    "REA.AX", "ALL.AX", "GMG.AX", "QBE.AX", "SUN.AX", "IAG.AX",
    "ORG.AX", "WDS.AX", "STO.AX", "TWE.AX", "TCL.AX", "APA.AX",
    "SGP.AX", "GPT.AX", "ASX.AX", "AMP.AX", "PPT.AX", "CPU.AX",
    "NCM.AX", "NST.AX", "EVN.AX", "NEM.AX", "OZL.AX", "IGO.AX",
    "S32.AX", "MIN.AX", "AWC.AX", "ILU.AX", "WHC.AX", "NHC.AX",
    "VCX.AX", "DXS.AX", "MGR.AX", "CHC.AX", "CLW.AX", "ABP.AX",
    "COH.AX", "RMD.AX", "SHL.AX", "PME.AX", "MSB.AX", "IDX.AX",
    "ANN.AX", "EDV.AX", "THL.AX", "EBO.AX", "CAR.AX", "SEK.AX",
    "WTC.AX", "XRO.AX", "APT.AX", "AFT.AX", "NXT.AX", "CTD.AX",
    "ELO.AX", "SPK.AX", "REH.AX", "JBH.AX", "HVN.AX", "MYR.AX",
    "LOV.AX", "BKL.AX", "ARB.AX", "HLO.AX", "PTM.AX", "MFG.AX",
    "NWL.AX", "AGL.AX", "MEZ.AX", "BOQ.AX", "BEN.AX", "MYS.AX",
    "ALD.AX", "VEA.AX", "AMC.AX", "ORI.AX", "IPL.AX", "DGL.AX",
    "CWN.AX", "SKC.AX", "TAH.AX", "ING.AX", "SKT.AX", "NWS.AX",
    "FLT.AX", "WEB.AX", "QAN.AX", "SYD.AX", "ALX.AX", "AIA.AX",
    "TCG.AX", "MCY.AX", "DOR.AX", "A2M.AX", "BGA.AX", "GNC.AX",
    "OSH.AX", "KAR.AX", "COE.AX", "BPT.AX", "VEA.AX", "CVN.AX",
    "ALK.AX", "GOR.AX", "PNR.AX", "CMM.AX", "PDN.AX", "BOE.AX",
    "PLS.AX", "AKE.AX", "PAN.AX", "SFR.AX", "OGC.AX", "RSG.AX",
    "WGX.AX", "RRL.AX", "MGX.AX", "AIS.AX", "BSL.AX", "GRR.AX",
    "CRN.AX", "YAL.AX", "CIA.AX", "FBU.AX", "CIM.AX", "ALQ.AX",
    "NWH.AX", "MND.AX", "DOW.AX", "MMA.AX", "SVW.AX", "ACF.AX",
    "SOL.AX", "MLT.AX", "ARG.AX", "AFI.AX", "WHF.AX", "DJW.AX",
]

# India (NSE) — .NS suffix
_INDIA_STATIC = [
    # Nifty 50 + large caps
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "HINDUNILVR.NS",
    "ICICIBANK.NS", "SBIN.NS", "BAJFINANCE.NS", "KOTAKBANK.NS", "WIPRO.NS",
    "HCLTECH.NS", "MARUTI.NS", "NTPC.NS", "SUNPHARMA.NS", "ONGC.NS",
    "TATAMOTORS.NS", "COALINDIA.NS", "POWERGRID.NS", "ULTRACEMCO.NS",
    "GRASIM.NS", "DIVISLAB.NS", "DRREDDY.NS", "M&M.NS", "TITAN.NS",
    "ADANIPORTS.NS", "APOLLOHOSP.NS", "BAJAJFINSV.NS", "CIPLA.NS",
    "BHARTIARTL.NS", "NESTLEIND.NS", "TECHM.NS", "ASIANPAINT.NS",
    "LTIM.NS", "INDUSINDBK.NS", "TATACONSUM.NS", "HEROMOTOCO.NS",
    "ADANIENT.NS", "JSWSTEEL.NS", "TATASTEEL.NS", "HINDALCO.NS",
    "BPCL.NS", "IOCL.NS", "VEDL.NS", "GAIL.NS", "SAIL.NS",
    "BANKBARODA.NS", "CANBK.NS", "PNB.NS", "UNIONBANK.NS", "IDFCFIRSTB.NS",
    "AXISBANK.NS", "FEDERALBNK.NS", "BANDHANBNK.NS", "RBLBANK.NS",
    "HDFCLIFE.NS", "SBILIFE.NS", "ICICIPRULI.NS", "BAJAJHLDNG.NS",
    "LT.NS", "SIEMENS.NS", "ABB.NS", "HAVELLS.NS", "VOLTAS.NS",
    "WHIRLPOOL.NS", "BLUESTARCO.NS", "CROMPTON.NS", "POLYCAB.NS",
    "LALPATHLAB.NS", "METROPOLIS.NS", "THYROCARE.NS", "DRHOMEO.NS",
    "TORNTPHARM.NS", "LUPIN.NS", "AUROPHARMA.NS", "NATCOPHARM.NS",
    "BIOCON.NS", "CADILAHC.NS", "ABBOTINDIA.NS", "PFIZER.NS",
    "ITC.NS", "HINDPETRO.NS", "PETRONET.NS", "CONCOR.NS",
    "IRCTC.NS", "INDIGO.NS", "SPICEJET.NS", "GMRINFRA.NS",
    "DLF.NS", "GODREJPROP.NS", "OBEROIRLTY.NS", "PRESTIGE.NS",
    "MUTHOOTFIN.NS", "CHOLAFIN.NS", "SHRIRAMFIN.NS", "LICHOUSFIN.NS",
    "PIDILITIND.NS", "BERGEPAINT.NS", "KANSAINER.NS", "AKZONOBEL.NS",
    "PAGEIND.NS", "MCDOWELL-N.NS", "RADICO.NS", "UNITEDSPIRITS.NS",
    "JUBLFOOD.NS", "DEVYANI.NS", "WESTLIFE.NS", "SAPPHIRE.NS",
    "NAUKRI.NS", "ZOMATO.NS", "PAYTM.NS", "POLICYBZR.NS", "DMART.NS",
    "TRENT.NS", "ABFRL.NS", "SHOPERSTOP.NS", "VMART.NS",
    "HDFCAMC.NS", "NIPPONLIFE.NS", "ICICIGI.NS", "NIACL.NS",
    "RECLTD.NS", "PFC.NS", "IRFC.NS", "HUDCO.NS",
    "TATAPOWER.NS", "ADANIGREEN.NS", "ADANITRANS.NS", "CESC.NS",
    "IDEA.NS", "MTNL.NS", "TATACOMM.NS", "ROUTE.NS",
    "INFOEDGE.NS", "JUSTDIAL.NS", "INDIAMART.NS", "CARTRADE.NS",
]

# Canada (TSX) — .TO suffix
_CANADA_STATIC = [
    # TSX 60 + large caps
    "RY.TO", "TD.TO", "CNR.TO", "BN.TO", "BMO.TO", "CP.TO",
    "ENB.TO", "BCE.TO", "TRP.TO", "SU.TO", "CNQ.TO", "ABX.TO",
    "NTR.TO", "AEM.TO", "SHOP.TO", "MFC.TO", "SLF.TO", "GWO.TO",
    "WN.TO", "L.TO", "POW.TO", "FFH.TO", "EMA.TO", "FTS.TO",
    "H.TO", "CTC-A.TO", "MRU.TO", "T.TO", "QSR.TO", "BAM.TO",
    "WPM.TO", "K.TO", "FM.TO", "CCO.TO", "TOU.TO", "ARX.TO",
    "CVE.TO", "IMO.TO", "MEG.TO", "POU.TO", "BTE.TO", "ERF.TO",
    "WCP.TO", "TVE.TO", "BIR.TO", "HWX.TO", "PSK.TO", "CJ.TO",
    "ATD.TO", "DOO.TO", "MG.TO", "NFI.TO", "CAE.TO", "HXL.TO",
    "TIH.TO", "GFL.TO", "TFII.TO", "SNC.TO", "STN.TO", "WSP.TO",
    "ATA.TO", "BYD.TO", "CHP-UN.TO", "CRR-UN.TO", "DIR-UN.TO",
    "HR-UN.TO", "REI-UN.TO", "SRU-UN.TO", "AP-UN.TO", "CAR-UN.TO",
    "BEI-UN.TO", "IIP-UN.TO", "GRT-UN.TO", "COLD.TO", "NWH-UN.TO",
    "IAG.TO", "IFC.TO", "ONEX.TO", "X.TO", "RCI-B.TO", "QBR-B.TO",
    "CGX.TO", "CIX.TO", "FSZ.TO", "IGM.TO", "GS.TO", "EQB.TO",
    "CWB.TO", "LB.TO", "HCG.TO", "NA.TO", "CM.TO",
    "AC.TO", "WJA.TO", "BB.TO", "LSPD.TO", "DSGN.TO", "KXS.TO",
    "OTEX.TO", "CSU.TO", "DSG.TO", "TIXT.TO", "REAL.TO",
    "TECK-B.TO", "CS.TO", "WPK.TO", "CFP.TO", "IFP.TO", "RFP.TO",
    "ACO-X.TO", "ELD.TO", "PVG.TO", "DPM.TO", "OGC.TO", "LUG.TO",
]

# Shanghai (SSE .SS) + Shenzhen (SZSE .SZ) — static fallback
_CHINA_STATIC = [
    # SSE main board + STAR market
    "600519.SS", "601318.SS", "600036.SS", "601166.SS", "600276.SS",
    "601288.SS", "601939.SS", "601398.SS", "600900.SS", "600887.SS",
    "601088.SS", "600028.SS", "601628.SS", "601601.SS", "600309.SS",
    "603259.SS", "688981.SS", "600104.SS", "601668.SS", "600030.SS",
    "601688.SS", "600050.SS", "601818.SS", "601229.SS", "600016.SS",
    "601225.SS", "600015.SS", "601333.SS", "603288.SS", "601186.SS",
    "601006.SS", "601021.SS", "601111.SS", "601390.SS", "601800.SS",
    "601857.SS", "601919.SS", "601985.SS", "601988.SS", "603899.SS",
    "688111.SS", "688256.SS", "688599.SS", "688658.SS", "688819.SS",
    "600000.SS", "600004.SS", "600009.SS", "600010.SS", "600011.SS",
    "600018.SS", "600019.SS", "600020.SS", "600025.SS", "600026.SS",
    "600029.SS", "600031.SS", "600038.SS", "600048.SS", "600060.SS",
    "600061.SS", "600068.SS", "600085.SS", "600089.SS", "600115.SS",
    "600132.SS", "600150.SS", "600153.SS", "600160.SS", "600161.SS",
    "600166.SS", "600170.SS", "600177.SS", "600183.SS", "600188.SS",
    "600196.SS", "600208.SS", "600219.SS", "600233.SS", "600252.SS",
    "600256.SS", "600261.SS", "600269.SS", "600271.SS", "600281.SS",
    "600295.SS", "600297.SS", "600298.SS", "600315.SS", "600316.SS",
    "600332.SS", "600346.SS", "600348.SS", "600350.SS", "600352.SS",
    "600360.SS", "600362.SS", "600369.SS", "600372.SS", "600373.SS",
    "600376.SS", "600380.SS", "600383.SS", "600390.SS", "600398.SS",
    "600406.SS", "600415.SS", "600426.SS", "600436.SS", "600438.SS",
    "600446.SS", "600460.SS", "600466.SS", "600482.SS", "600485.SS",
    "600489.SS", "600498.SS", "600500.SS", "600516.SS", "600518.SS",
    "600522.SS", "600535.SS", "600536.SS", "600537.SS", "600547.SS",
    "600549.SS", "600570.SS", "600572.SS", "600573.SS", "600575.SS",
    "600580.SS", "600583.SS", "600585.SS", "600588.SS", "600600.SS",
    "600601.SS", "600606.SS", "600614.SS", "600618.SS", "600619.SS",
    "600621.SS", "600633.SS", "600637.SS", "600642.SS", "600644.SS",
    "600649.SS", "600655.SS", "600660.SS", "600663.SS", "600674.SS",
    "600675.SS", "600688.SS", "600690.SS", "600703.SS", "600704.SS",
    "600705.SS", "600718.SS", "600721.SS", "600733.SS", "600737.SS",
    "600741.SS", "600745.SS", "600748.SS", "600760.SS", "600763.SS",
    "600765.SS", "600783.SS", "600795.SS", "600803.SS", "600809.SS",
    "600816.SS", "600820.SS", "600827.SS", "600835.SS", "600845.SS",
    "600848.SS", "600854.SS", "600856.SS", "600859.SS", "600862.SS",
    "600867.SS", "600873.SS", "600875.SS", "600886.SS", "600893.SS",
    "600895.SS", "600905.SS", "600908.SS", "600909.SS", "600918.SS",
    "600926.SS", "600928.SS", "600938.SS", "600941.SS", "600958.SS",
    "600959.SS", "600960.SS", "600963.SS", "600966.SS", "600968.SS",
    "600969.SS", "600970.SS", "600975.SS", "600979.SS", "600984.SS",
    "600989.SS", "600990.SS", "600992.SS", "600993.SS", "600997.SS",
    "600999.SS", "601000.SS", "601001.SS", "601002.SS", "601003.SS",
    "601009.SS", "601010.SS", "601012.SS", "601015.SS", "601016.SS",
    "601020.SS", "601028.SS", "601038.SS", "601058.SS", "601066.SS",
    "601069.SS", "601077.SS", "601086.SS", "601088.SS", "601100.SS",
    "601101.SS", "601106.SS", "601107.SS", "601108.SS", "601110.SS",
    "601113.SS", "601116.SS", "601117.SS", "601118.SS", "601127.SS",
    "601128.SS", "601129.SS", "601130.SS", "601132.SS", "601136.SS",
    "601138.SS", "601139.SS", "601143.SS", "601148.SS", "601150.SS",
    "601155.SS", "601156.SS", "601158.SS", "601162.SS", "601163.SS",
    "601168.SS", "601169.SS", "601177.SS", "601179.SS", "601187.SS",
    "601188.SS", "601189.SS", "601191.SS", "601193.SS", "601195.SS",
    "601198.SS", "601199.SS", "601200.SS", "601202.SS", "601203.SS",
    "601204.SS", "601205.SS", "601206.SS", "601207.SS", "601208.SS",
    "601212.SS", "601216.SS", "601218.SS", "601222.SS", "601226.SS",
    "601231.SS", "601233.SS", "601236.SS", "601238.SS", "601239.SS",
    "601311.SS", "601328.SS", "601336.SS", "601360.SS", "601366.SS",
    "601368.SS", "601369.SS", "601375.SS", "601377.SS", "601378.SS",
    "601379.SS", "601388.SS", "601398.SS", "601456.SS", "601500.SS",
    "601512.SS", "601515.SS", "601519.SS", "601528.SS", "601555.SS",
    "601566.SS", "601567.SS", "601568.SS", "601579.SS", "601600.SS",
    "601606.SS", "601607.SS", "601608.SS", "601611.SS", "601615.SS",
    "601618.SS", "601619.SS", "601622.SS", "601626.SS", "601633.SS",
    "601636.SS", "601648.SS", "601650.SS", "601658.SS", "601666.SS",
    "601669.SS", "601677.SS", "601678.SS", "601680.SS", "601689.SS",
    "601696.SS", "601698.SS", "601699.SS", "601700.SS", "601717.SS",
    "601718.SS", "601727.SS", "601728.SS", "601737.SS", "601738.SS",
    "601766.SS", "601777.SS", "601779.SS", "601788.SS", "601789.SS",
    "601798.SS", "601801.SS", "601808.SS", "601811.SS", "601816.SS",
    "601818.SS", "601825.SS", "601826.SS", "601828.SS", "601838.SS",
    "601866.SS", "601868.SS", "601869.SS", "601877.SS", "601878.SS",
    "601880.SS", "601881.SS", "601882.SS", "601886.SS", "601890.SS",
    "601895.SS", "601896.SS", "601898.SS", "601899.SS", "601900.SS",
    "601901.SS", "601908.SS", "601918.SS", "601929.SS", "601933.SS",
    "601952.SS", "601958.SS", "601966.SS", "601969.SS", "601975.SS",
    "601978.SS", "601979.SS", "601990.SS", "601991.SS", "601992.SS",
    "601995.SS", "601997.SS", "601998.SS", "601999.SS",
    # SZSE main board + ChiNext + SME
    "000858.SZ", "000002.SZ", "002594.SZ", "300750.SZ", "000001.SZ",
    "002415.SZ", "300059.SZ", "000333.SZ", "002714.SZ", "000568.SZ",
    "002352.SZ", "300015.SZ", "002230.SZ", "000776.SZ", "000651.SZ",
    "000725.SZ", "002027.SZ", "002142.SZ", "002304.SZ", "002460.SZ",
    "002493.SZ", "002736.SZ", "300014.SZ", "300122.SZ", "300142.SZ",
    "300347.SZ", "300408.SZ", "300413.SZ", "300433.SZ", "300498.SZ",
    "300760.SZ", "300857.SZ", "000063.SZ", "000100.SZ", "000166.SZ",
    "000538.SZ", "000596.SZ", "000625.SZ", "000661.SZ", "000708.SZ",
    "000786.SZ", "001979.SZ", "002001.SZ", "002032.SZ", "000009.SZ",
    "000012.SZ", "000016.SZ", "000019.SZ", "000020.SZ", "000021.SZ",
    "000023.SZ", "000025.SZ", "000027.SZ", "000028.SZ", "000029.SZ",
    "000030.SZ", "000031.SZ", "000032.SZ", "000034.SZ", "000036.SZ",
    "000039.SZ", "000040.SZ", "000042.SZ", "000043.SZ", "000045.SZ",
    "000046.SZ", "000048.SZ", "000049.SZ", "000050.SZ", "000055.SZ",
    "000056.SZ", "000058.SZ", "000059.SZ", "000060.SZ", "000061.SZ",
    "000062.SZ", "000065.SZ", "000066.SZ", "000068.SZ", "000069.SZ",
    "000070.SZ", "000078.SZ", "000088.SZ", "000089.SZ", "000090.SZ",
    "000096.SZ", "000099.SZ", "000103.SZ", "000106.SZ", "000107.SZ",
    "000153.SZ", "000155.SZ", "000158.SZ", "000159.SZ", "000160.SZ",
    "000161.SZ", "000162.SZ", "000166.SZ", "000167.SZ", "000168.SZ",
    "000169.SZ", "000170.SZ", "000171.SZ", "000172.SZ", "000173.SZ",
    "000174.SZ", "000175.SZ", "000176.SZ", "000177.SZ", "000178.SZ",
    "000179.SZ", "000180.SZ", "000181.SZ", "000182.SZ", "000183.SZ",
    "000185.SZ", "000186.SZ", "000188.SZ", "000189.SZ", "000190.SZ",
    "000192.SZ", "000193.SZ", "000195.SZ", "000197.SZ", "000198.SZ",
    "000199.SZ", "000200.SZ", "000201.SZ", "000202.SZ", "000203.SZ",
    "000205.SZ", "000206.SZ", "000207.SZ", "000209.SZ", "000210.SZ",
    "000211.SZ", "000212.SZ", "000213.SZ", "000215.SZ", "000216.SZ",
    "000217.SZ", "000218.SZ", "000219.SZ", "000220.SZ", "000221.SZ",
    "000222.SZ", "000223.SZ", "000224.SZ", "000225.SZ", "000226.SZ",
    "000227.SZ", "000228.SZ", "000229.SZ", "000230.SZ", "000231.SZ",
    "000232.SZ", "000233.SZ", "000234.SZ", "000235.SZ", "000236.SZ",
    "000237.SZ", "000238.SZ", "000239.SZ", "000240.SZ", "000241.SZ",
    "000242.SZ", "000243.SZ", "000244.SZ", "000245.SZ", "000246.SZ",
    "000248.SZ", "000249.SZ", "000250.SZ", "000251.SZ", "000252.SZ",
    "000253.SZ", "000255.SZ", "000256.SZ", "000257.SZ", "000258.SZ",
    "000259.SZ", "000260.SZ", "000261.SZ", "000262.SZ", "000263.SZ",
    "000265.SZ", "000266.SZ", "000267.SZ", "000268.SZ", "000269.SZ",
]


# ── Dynamic scrapers ─────────────────────────────────────────────────────────

def _scrape_nasdaq_copenhagen() -> list[str]:
    live = _yahoo_screener_tickers("CPH")
    if live:
        return live
    live = _scrape_nasdaq_nordic("copenhagen", ".CO")
    if live:
        return live
    live = _load_cache("openfigi_DC") or _openfigi_tickers("DC", ".CO")
    if live:
        log.info("Copenhagen: using OpenFIGI fallback (%d tickers)", len(live))
        return live
    log.warning("Copenhagen: all sources failed — returning empty list")
    return []


def _scrape_stockholm_all() -> list[str]:
    live = _yahoo_screener_tickers("STO")
    if live:
        return live
    live = _scrape_nasdaq_nordic("stockholm", ".ST")
    if live:
        return live
    live = _load_cache("openfigi_SS") or _openfigi_tickers("SS", ".ST")
    if live:
        log.info("Stockholm: using OpenFIGI fallback (%d tickers)", len(live))
        return live
    log.warning("Stockholm: all sources failed — returning empty list")
    return []


def _scrape_helsinki_all() -> list[str]:
    live = _yahoo_screener_tickers("HEL")
    if live:
        return live
    live = _load_cache("openfigi_FH") or _openfigi_tickers("FH", ".HE")
    if live:
        log.info("Helsinki: using OpenFIGI fallback (%d tickers)", len(live))
        return live
    log.warning("Helsinki: all sources failed — returning empty list")
    return []


def _scrape_oslo_all() -> list[str]:
    live = _yahoo_screener_tickers("OSL")
    if live:
        return live
    live = _load_cache("openfigi_NO") or _openfigi_tickers("NO", ".OL")
    if live:
        log.info("Oslo: using OpenFIGI fallback (%d tickers)", len(live))
        return live
    log.warning("Oslo: all sources failed — returning empty list")
    return []


def _scrape_asx() -> list[str]:
    """Fetch all ASX-listed stocks from ASX's public CSV."""
    try:
        resp = requests.get(
            "https://www.asx.com.au/asx/research/ASXListedCompanies.csv",
            headers=_HEADERS, timeout=20,
        )
        lines = resp.text.splitlines()
        tickers: list[str] = []
        for line in lines[3:]:
            parts = line.split(",")
            if len(parts) >= 2:
                code = parts[1].strip().strip('"')
                if code and re.match(r'^[A-Z0-9]{2,6}$', code):
                    tickers.append(code + ".AX")
        log.info("ASX CSV: %d tickers", len(tickers))
        return tickers
    except Exception as exc:
        log.debug("ASX CSV failed: %s", exc)
    return []


def _scrape_nse() -> list[str]:
    """Fetch all NSE-listed stocks from NSE's public CSV."""
    try:
        resp = requests.get(
            "https://archives.nseindia.com/content/equities/EQUITY_L.csv",
            headers={**_HEADERS, "Referer": "https://www.nseindia.com/"},
            timeout=20,
        )
        lines = resp.text.splitlines()
        tickers: list[str] = []
        for line in lines[1:]:
            parts = line.split(",")
            if parts:
                symbol = parts[0].strip().strip('"')
                if symbol and re.match(r'^[A-Z0-9&\-]{2,20}$', symbol):
                    tickers.append(symbol + ".NS")
        log.info("NSE CSV: %d tickers", len(tickers))
        return tickers
    except Exception as exc:
        log.debug("NSE CSV failed: %s", exc)
    return []


def _scrape_tokyo() -> list[str]:
    try:
        url = (
            "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved"
            "?formatted=false&scrIds=most_actives&start=0&count=100&region=JP&lang=ja-JP"
        )
        resp = requests.get(url, headers=_HEADERS, timeout=15)
        quotes = resp.json().get("finance", {}).get("result", [{}])[0].get("quotes", [])
        tickers = [q["symbol"] for q in quotes if q.get("symbol", "").endswith(".T")]
        if tickers:
            return tickers
    except Exception:
        pass
    return []


def _safe_wikipedia_tickers(url: str, ticker_col: str, suffix: str = "") -> list[str]:
    try:
        tables = pd.read_html(url, flavor="lxml")
        for table in tables:
            cols = [str(c).strip() for c in table.columns]
            for col in cols:
                if ticker_col.lower() in col.lower():
                    tickers = table[table.columns[cols.index(col)]].dropna().tolist()
                    result = []
                    for t in tickers:
                        t = str(t).strip()
                        if not t:
                            continue
                        if not suffix:
                            t = t.replace(".", "-")
                        result.append(t + suffix)
                    return result
    except Exception as exc:
        log.warning("Wikipedia scrape failed for %s: %s", url, exc)
    return []


def _merge(live: list[str], static: list[str]) -> list[str]:
    seen = set(live)
    result = list(live)
    for t in static:
        if t not in seen:
            result.append(t)
            seen.add(t)
    return result


# ── Public getters ────────────────────────────────────────────────────────────

def get_sp500() -> list[str]:
    return _safe_wikipedia_tickers(
        "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", "Symbol"
    ) or []


def get_nasdaq_full() -> list[str]:
    tickers = _nasdaq_api_tickers("nasdaq")
    if tickers:
        log.info("NASDAQ full: %d tickers", len(tickers))
        return tickers
    log.warning("NASDAQ API failed — falling back to NASDAQ-100")
    return _safe_wikipedia_tickers("https://en.wikipedia.org/wiki/Nasdaq-100", "Ticker") or []


def get_nyse_full() -> list[str]:
    tickers = _nasdaq_api_tickers("nyse")
    if tickers:
        log.info("NYSE full: %d tickers", len(tickers))
        return tickers
    log.warning("NYSE API failed")
    return []


def get_amsterdam_all() -> list[str]:
    live = _yahoo_screener_tickers("AMS")
    if live:
        return live
    log.warning("Euronext Amsterdam screener failed — returning empty list")
    return []


def get_brussels_all() -> list[str]:
    live = _yahoo_screener_tickers("BRU")
    if live:
        return live
    log.warning("Euronext Brussels screener failed — returning empty list")
    return []


def get_luxembourg_all() -> list[str]:
    live = _yahoo_screener_tickers("LUX")
    if live:
        return live
    log.warning("Luxembourg screener failed — returning empty list")
    return []


def get_swiss_all() -> list[str]:
    live = _yahoo_screener_tickers("EBS")
    if live:
        return live
    log.warning("Swiss screener failed — falling back to SMI20 static list")
    return list(_SMI20_STATIC)


def get_xetra_all() -> list[str]:
    live = _yahoo_screener_tickers("GER")
    if live:
        return live
    log.warning("XETRA screener failed — falling back to DAX40 static list")
    return list(_DAX40_STATIC)


def get_euronext_paris_all() -> list[str]:
    live = _yahoo_screener_tickers("PAR")
    if live:
        return live
    log.warning("Euronext Paris screener failed — falling back to CAC40 static list")
    return list(_CAC40_STATIC)


def get_lse_all() -> list[str]:
    live = _yahoo_screener_tickers("LSE")
    if live:
        return live
    log.warning("LSE screener failed — falling back to FTSE100 static list")
    return list(_FTSE100_STATIC)


def get_dax40() -> list[str]:
    live = _safe_wikipedia_tickers(
        "https://en.wikipedia.org/wiki/DAX", "Ticker", suffix=".DE"
    )
    if live:
        return live
    log.info("DAX Wikipedia blocked — using static list (%d tickers)", len(_DAX40_STATIC))
    return list(_DAX40_STATIC)


def get_ftse100() -> list[str]:
    live = _safe_wikipedia_tickers(
        "https://en.wikipedia.org/wiki/FTSE_100_Index", "Ticker", suffix=".L"
    )
    if live:
        return live
    log.info("FTSE100 Wikipedia blocked — using static list (%d tickers)", len(_FTSE100_STATIC))
    return list(_FTSE100_STATIC)


def get_cac40() -> list[str]:
    live = _safe_wikipedia_tickers(
        "https://en.wikipedia.org/wiki/CAC_40", "Ticker", suffix=".PA"
    )
    if live:
        return live
    log.info("CAC40 Wikipedia blocked — using static list (%d tickers)", len(_CAC40_STATIC))
    return list(_CAC40_STATIC)


def get_nasdaq_copenhagen() -> list[str]:
    return list(dict.fromkeys(_scrape_nasdaq_copenhagen()))


def get_stockholm_all() -> list[str]:
    return list(dict.fromkeys(_scrape_stockholm_all()))


def get_helsinki_all() -> list[str]:
    return list(dict.fromkeys(_scrape_helsinki_all()))


def get_oslo_all() -> list[str]:
    return list(dict.fromkeys(_scrape_oslo_all()))


def get_omx_nordic() -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for ticker in (
        get_nasdaq_copenhagen()
        + get_stockholm_all()
        + get_helsinki_all()
        + get_oslo_all()
    ):
        if ticker not in seen:
            seen.add(ticker)
            result.append(ticker)
    return result


def get_tokyo() -> list[str]:
    live = _scrape_tokyo()
    static = list(dict.fromkeys(_TOKYO_STATIC))
    return _merge(live, static) if live else static


def get_seoul() -> list[str]:
    return list(dict.fromkeys(_SEOUL_STATIC))


def get_hong_kong() -> list[str]:
    """Hang Seng / HKEX — static list of major stocks."""
    return list(dict.fromkeys(_HONG_KONG_STATIC))


def get_australia() -> list[str]:
    """ASX — live CSV with static fallback."""
    live = _scrape_asx()
    static = list(dict.fromkeys(_AUSTRALIA_STATIC))
    return _merge(live, static) if live else static


def get_india() -> list[str]:
    """NSE India — live CSV with static fallback."""
    live = _scrape_nse()
    static = list(dict.fromkeys(_INDIA_STATIC))
    return _merge(live, static) if live else static


def get_canada() -> list[str]:
    """TSX Canada — static list of major stocks."""
    return list(dict.fromkeys(_CANADA_STATIC))


def get_shanghai_all() -> list[str]:
    """All Shanghai (SSE) + Shenzhen (SZSE) A-shares — live API with static fallback."""
    sse = _scrape_sse()
    szse = _scrape_szse()
    live = sse + szse

    static = [t for t in dict.fromkeys(_CHINA_STATIC)
              if re.match(r'^\d{6}\.(SS|SZ)$', t)]

    if live:
        log.info("China live scrape: %d SSE + %d SZSE = %d total",
                 len(sse), len(szse), len(live))
        return _merge(live, static)

    log.warning("China live scrape failed — using static list (%d tickers)", len(static))
    return static


# Registry: key → (display label, getter function)
MARKET_SOURCES: dict[str, tuple[str, Callable[[], list[str]]]] = {
    "sp500":         ("S&P 500 (USA)",           get_sp500),
    "nasdaq_full":   ("NASDAQ Full (USA)",        get_nasdaq_full),
    "nyse_full":     ("NYSE Full (USA)",          get_nyse_full),
    "swiss_full":    ("SIX Full (Switzerland)",    get_swiss_all),
    "amsterdam":     ("Euronext Amsterdam (NL)",   get_amsterdam_all),
    "brussels":      ("Euronext Brussels (BE)",    get_brussels_all),
    "luxembourg":    ("Luxembourg (LU)",           get_luxembourg_all),
    "dax40":         ("DAX 40 (Germany)",         get_dax40),
    "xetra":         ("XETRA Full (Germany)",     get_xetra_all),
    "ftse100":       ("FTSE 100 (UK)",            get_ftse100),
    "lse_full":      ("LSE Full (UK)",            get_lse_all),
    "cac40":         ("CAC 40 (France)",          get_cac40),
    "euronext_paris":("Euronext Full (France)",   get_euronext_paris_all),
    "copenhagen":    ("Copenhagen (DK)",          get_nasdaq_copenhagen),
    "stockholm":     ("Stockholm (SE)",           get_stockholm_all),
    "helsinki":      ("Helsinki (FI)",            get_helsinki_all),
    "oslo":          ("Oslo (NO)",                get_oslo_all),
    "tokyo":         ("Tokyo / Nikkei (Japan)",   get_tokyo),
    "seoul":         ("Seoul / KOSPI (Korea)",    get_seoul),
    "shanghai":      ("Shanghai + Shenzhen (China)", get_shanghai_all),
    "hong_kong":     ("Hang Seng (Hong Kong)",    get_hong_kong),
    "australia":     ("ASX (Australia)",          get_australia),
    "india":         ("NSE (India)",              get_india),
    "canada":        ("TSX (Canada)",             get_canada),
}


def get_all_tickers(enabled: set[str] | None = None) -> list[str]:
    """Return deduplicated list of tickers for the selected markets.

    enabled: set of MARKET_SOURCES keys to fetch. None means all markets.
    """
    seen: set[str] = set()
    result: list[str] = []
    for key, (label, fn) in MARKET_SOURCES.items():
        if enabled is not None and key not in enabled:
            continue
        try:
            for ticker in fn():
                if ticker and ticker not in seen:
                    seen.add(ticker)
                    result.append(ticker)
        except Exception as exc:
            log.warning("Source %s failed: %s", label, exc)
    log.info("Total unique tickers discovered: %d (markets: %s)",
             len(result), sorted(enabled) if enabled is not None else "all")
    return result
