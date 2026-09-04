"""Headless full data update — mirrors the desktop app's RefreshWorker, minus Qt.

Runs the exact same fetch/score/save pipeline as the desktop app's "Update data"
button, so GitHub Actions (or any server/cron) can refresh the shared database
without any user's PC being on. Intended to be run on a schedule, then have the
resulting data/stocks.db committed back to the repo by the workflow.
"""
import logging
import sys
import time
from datetime import datetime

from config import BASE_DIR
from data import database, fetcher, markets_config
from data.indices import get_all_tickers
from scoring import engine
from scoring.weights import load as load_weights

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("update_data")


def main() -> int:
    started = time.monotonic()
    run_start = datetime.now().astimezone()

    database.initialize_db()

    enabled = markets_config.load()
    if not enabled:
        log.error("No markets selected in assets/selected_markets.json — nothing to fetch.")
        return 1
    log.info("Enabled markets: %s", sorted(enabled))

    run_id = database.run_log_start(enabled)

    weights = load_weights()
    all_tickers = get_all_tickers(enabled)
    log.info("Discovered %d tickers across %d markets.", len(all_tickers), len(enabled))

    # Same safety net as the desktop app: only merge with the existing DB roster
    # when the selection is BROAD (many markets) and suspiciously low — a narrow,
    # deliberate selection legitimately returning few tickers is not a failure.
    _BROAD_SELECTION_MARKETS = 5
    is_broad = len(enabled) >= _BROAD_SELECTION_MARKETS
    if is_broad and len(all_tickers) < 300:
        log.warning(
            "Only %d tickers across %d markets — likely rate-limited. Merging with DB tickers.",
            len(all_tickers), len(enabled),
        )
        db_tickers = database.fetch_all_tickers()
        all_tickers = list(set(all_tickers) | db_tickers)
        log.info("After DB merge: %d tickers total", len(all_tickers))

    def on_progress(done: int, total: int) -> None:
        if done % 200 == 0 or done == total:
            log.info("Fetch progress: %d/%d", done, total)

    rows = fetcher.fetch_all(all_tickers, progress_callback=on_progress)
    scored = engine.score_all(rows, weights)
    for row in scored:
        database.upsert_stock(row)

    count = len(scored)
    database.run_log_finish(run_id, count)

    if count > 0:
        database.clear_stale_stocks(enabled, run_start)
        markets_config.save_last_downloaded(enabled)

    new_industries = database.sync_industry_benchmarks()
    database.write_all_snapshots()

    elapsed = time.monotonic() - started
    log.info(
        "Done in %.1f min — %d stocks updated%s.",
        elapsed / 60, count,
        f" ({new_industries} new industries — set PE on Industry PE benchmarks)" if new_industries else "",
    )
    return 0 if count > 0 else 2


if __name__ == "__main__":
    sys.exit(main())
