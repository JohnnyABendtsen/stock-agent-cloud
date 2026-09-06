"""Headless PE0/PE1 enrichment — mirrors the desktop app's PeEnrichWorker, minus Qt.

Fills in missing pe_2026/pe_2027 for stocks that already have base data, without
re-running the full (~3h) fetch. Meant to be triggered on demand via GitHub
Actions workflow_dispatch — much cheaper than a full update_data.py run.
"""
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from config import FETCH_WORKERS
from data import database
from data.pe_enricher import enrich

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("enrich_pe")


def _fetch_one(stock: dict) -> tuple[str, float | None, float | None]:
    ticker = stock["ticker"]
    _pe = stock.get("pe")
    _eps = stock.get("eps")
    price = round(_pe * _eps, 4) if (_pe and _eps and _pe > 0 and _eps > 0) else None
    need0 = stock.get("pe_2026") is None
    need1 = stock.get("pe_2027") is None
    pe0, pe1 = enrich(ticker, price, need0, need1)
    return ticker, pe0, pe1


def main() -> int:
    started = time.monotonic()
    database.initialize_db()

    stocks = database.fetch_all_stocks()
    candidates = [s for s in stocks if s.get("pe_2026") is None or s.get("pe_2027") is None]
    total = len(candidates)
    log.info("Checking %d of %d stocks for missing PE0/PE1", total, len(stocks))

    if total == 0:
        log.info("Nothing to enrich.")
        return 0

    enriched = 0
    done = 0
    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        futures = {pool.submit(_fetch_one, s): s for s in candidates}
        for future in as_completed(futures):
            done += 1
            try:
                ticker, pe0, pe1 = future.result()
                if pe0 is not None or pe1 is not None:
                    with database.get_connection() as conn:
                        if pe0 is not None:
                            conn.execute("UPDATE stocks SET pe_2026=? WHERE ticker=?", (pe0, ticker))
                        if pe1 is not None:
                            conn.execute("UPDATE stocks SET pe_2027=? WHERE ticker=?", (pe1, ticker))
                    enriched += 1
            except Exception as exc:
                log.debug("Enrich failed for %s: %s", futures[future]["ticker"], exc)
            if done % 200 == 0 or done == total:
                log.info("Progress %d/%d — %d enriched so far", done, total, enriched)

    elapsed = time.monotonic() - started
    log.info("Done in %.1f min — %d of %d stocks got new PE0/PE1 values.", elapsed / 60, enriched, total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
