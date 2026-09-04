from pathlib import Path

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "data" / "stocks.db"
ASSETS_DIR = BASE_DIR / "assets"
INDUSTRY_PE_PATH = ASSETS_DIR / "industry_pe.json"

SNAPSHOT_HOUR_CET = 23
SNAPSHOT_MINUTE_CET = 0

MIN_MARKET_CAP_USD = 0  # no cap filter — load all stocks

FETCH_WORKERS = 3
FETCH_TIMEOUT_SECONDS = 60  # per-ticker timeout for the thread pool future


APP_NAME = "Stock Analyzer"
APP_VERSION = "1.0.0"
