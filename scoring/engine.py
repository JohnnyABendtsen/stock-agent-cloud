"""Combine all criteria scores into a single Stock Rating (1–10)."""
from scoring import moat, management, financials, valuation
from scoring.weights import load as load_weights


def score_stock(row: dict, weights: dict | None = None) -> dict:
    if weights is None:
        weights = load_weights()

    result = dict(row)
    result["score_moat"]       = moat.score(row, weights.get("moat"))
    result["score_management"] = management.score(row, weights.get("management"))
    result["score_financial"]  = financials.score(row, weights.get("financial"))
    val_score, pe_label        = valuation.score(row, weights.get("valuation"))
    result["score_valuation"]  = val_score
    result["pe_label"]         = pe_label
    result["pe_label_1"]       = valuation.label_for_pe(row.get("pe_2027"), row.get("industry"))

    f = weights.get("final", {})
    total_pct = sum(f.get(k, 25) for k in ("moat", "management", "financial", "valuation"))
    if total_pct == 0:
        total_pct = 100

    raw = (
        result["score_moat"]       * f.get("moat",       25) / total_pct
        + result["score_management"] * f.get("management", 25) / total_pct
        + result["score_financial"]  * f.get("financial",  25) / total_pct
        + result["score_valuation"]  * f.get("valuation",  25) / total_pct
    )
    result["stock_rating"] = round(max(1.0, min(10.0, raw)), 2)
    return result


def score_all(rows: list[dict], weights: dict | None = None) -> list[dict]:
    w = weights or load_weights()
    return [score_stock(r, w) for r in rows]
