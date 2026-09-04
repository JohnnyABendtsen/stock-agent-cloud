"""Load and save per-breakpoint scoring weights."""
import json
from pathlib import Path

_PATH = Path(__file__).parent.parent / "assets" / "weights.json"

_DEFAULTS: dict = {
    "final": {"moat": 25, "management": 25, "financial": 25, "valuation": 25},
    "moat": {
        "gross_margin":  {"ge60": 6, "ge50": 5, "ge40": 4, "ge30": 3, "ge20": 2, "ge10": 1},
        "margin_trend":  {"improving": 1, "declining": -1},
        "roe_consistency": {"lt3": 5, "lt6": 4, "lt10": 3, "lt15": 2, "lt20": 1},
    },
    "management": {
        "insider_ownership": {"ge30": 5, "ge15": 4, "ge5": 3, "ge1": 2},
        "buyback_trend": {"decreasing": 3, "stable": 1, "increasing": 0},
        "debt_trend":    {"decreasing": 3, "stable": 2, "increasing": 0},
    },
    "financial": {
        "roe_5yr":        {"ge25": 8, "ge20": 7, "ge15": 5, "ge10": 3, "ge5": 1},
        "debt_equity":    {"lt20": 6, "lt50": 5, "lt100": 3, "lt200": 1},
        "fcf_yield":      {"ge10": 6, "ge7": 5, "ge5": 4, "ge3": 3, "ge1": 1},
        "earnings_growth":{"ge20": 6, "ge15": 5, "ge10": 4, "ge5": 2, "ge0": 1},
    },
    "valuation": {
        "pe_label": {"cheap": 8, "mid": 4, "expensive": 1},
        "pb":       {"lt1": 6, "lt2": 5, "lt3": 4, "lt5": 2},
        "fcf_yield":{"ge8": 6, "ge5": 4, "ge3": 2, "ge1": 1},
    },
}


def load() -> dict:
    try:
        with open(_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        import copy
        return copy.deepcopy(_DEFAULTS)


def save(weights: dict) -> None:
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_PATH, "w", encoding="utf-8") as f:
        json.dump(weights, f, indent=2)
