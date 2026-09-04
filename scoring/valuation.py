"""Criterion #5 — Valuation / Margin of Safety scoring (0–10)."""
from data.database import fetch_industry_benchmarks

_benchmarks: dict = {}


def _load_benchmarks() -> None:
    global _benchmarks
    if _benchmarks:
        return
    rows = fetch_industry_benchmarks()
    _benchmarks = {r["industry"]: r for r in rows}


def reload_benchmarks() -> None:
    global _benchmarks
    _benchmarks = {}
    _load_benchmarks()


def _pe_label(pe: float | None, industry: str | None) -> str:
    if pe is None or not industry:
        return "Unknown"
    _load_benchmarks()
    bench = _benchmarks.get(industry)
    if bench is None:
        return "Unknown"
    billig_max = bench["billig_max"]
    middel_max = bench["middel_max"]
    if billig_max is None or middel_max is None:
        return "Unknown"
    if pe <= billig_max:
        return "Cheap"
    if pe <= middel_max:
        return "Mid"
    return "Expensive"


def label_for_pe(pe: float | None, industry: str | None) -> str:
    return _pe_label(pe, industry)


def score(row: dict, weights: dict | None = None) -> tuple[float, str]:
    from scoring.weights import _DEFAULTS
    w = weights or _DEFAULTS["valuation"]
    _load_benchmarks()

    pe_w  = w.get("pe_label", {"cheap": 8, "mid": 4, "expensive": 1})
    pb_w  = w.get("pb",       {"lt1": 6, "lt2": 5, "lt3": 4, "lt5": 2})
    fcf_w = w.get("fcf_yield",{"ge8": 6, "ge5": 4, "ge3": 2, "ge1": 1})

    max_pts = (
        max(pe_w.values(),  default=0)
        + max(pb_w.values(),  default=0)
        + max(fcf_w.values(), default=0)
    )
    if max_pts == 0:
        return 0.0, "Unknown"

    points = 0.0

    pe       = row.get("pe")
    industry = row.get("industry")
    label    = _pe_label(pe, industry)

    points += pe_w.get(label.lower(), 0)

    pb = row.get("pb")
    if pb is not None:
        if   pb < 1.0: points += pb_w.get("lt1", 0)
        elif pb < 2.0: points += pb_w.get("lt2", 0)
        elif pb < 3.0: points += pb_w.get("lt3", 0)
        elif pb < 5.0: points += pb_w.get("lt5", 0)

    fcf = row.get("fcf_yield")
    if fcf is not None and fcf >= 0:
        if   fcf >= 0.08: points += fcf_w.get("ge8", 0)
        elif fcf >= 0.05: points += fcf_w.get("ge5", 0)
        elif fcf >= 0.03: points += fcf_w.get("ge3", 0)
        elif fcf >= 0.01: points += fcf_w.get("ge1", 0)

    final = round(max(0.0, min(10.0, (points / max_pts) * 10)), 2)
    return final, label
