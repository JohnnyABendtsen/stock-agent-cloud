"""Persist which markets are selected for data fetching."""
import json
import logging

from config import ASSETS_DIR
from data.indices import MARKET_SOURCES

log = logging.getLogger(__name__)

_PATH = ASSETS_DIR / "selected_markets.json"


def load() -> set[str]:
    """Return the set of enabled market keys. Returns None if no config saved yet."""
    if not _PATH.exists():
        return set(MARKET_SOURCES.keys())
    try:
        data = json.loads(_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            log.warning("selected_markets.json has unexpected format — using all markets")
            return set(MARKET_SOURCES.keys())
        valid = {k for k in data if k in MARKET_SOURCES}
        unknown = [k for k in data if k not in MARKET_SOURCES]
        if unknown:
            log.warning("Ignoring unknown market keys in config: %s", unknown)
        if not valid:
            log.warning("No valid market keys in config — using all markets")
            return set(MARKET_SOURCES.keys())
        return valid
    except Exception as exc:
        log.warning("Could not read %s, using all markets: %s", _PATH, exc)
        return set(MARKET_SOURCES.keys())


def exists() -> bool:
    """Return True if the user has ever saved a market selection."""
    return _PATH.exists()


def save(enabled: set[str]) -> None:
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    _PATH.write_text(
        json.dumps(sorted(enabled), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


_LAST_DL_PATH = ASSETS_DIR / "last_downloaded_markets.json"


def load_last_downloaded() -> set[str]:
    if not _LAST_DL_PATH.exists():
        return set()
    try:
        data = json.loads(_LAST_DL_PATH.read_text(encoding="utf-8"))
        return set(data) if isinstance(data, list) else set()
    except Exception:
        return set()


def save_last_downloaded(enabled: set[str]) -> None:
    _LAST_DL_PATH.parent.mkdir(parents=True, exist_ok=True)
    _LAST_DL_PATH.write_text(
        json.dumps(sorted(enabled), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
