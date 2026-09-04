"""Criterion #3 — Management Quality scoring (0–10)."""


def score(row: dict, weights: dict | None = None) -> float:
    from scoring.weights import _DEFAULTS
    w = weights or _DEFAULTS["management"]

    ins_w  = w.get("insider_ownership", {"ge30": 5, "ge15": 4, "ge5": 3, "ge1": 2})
    buy_w  = w.get("buyback_trend",     {"decreasing": 3, "stable": 1, "increasing": 0})
    debt_w = w.get("debt_trend",        {"decreasing": 3, "stable": 2, "increasing": 0})

    max_pts = (
        max(ins_w.values(),  default=0)
        + max(buy_w.values(),  default=0)
        + max(debt_w.values(), default=0)
    )
    if max_pts == 0:
        return 0.0

    points = 0.0

    insider = row.get("insider_ownership")
    if insider is not None:
        if   insider >= 0.30: points += ins_w.get("ge30", 0)
        elif insider >= 0.15: points += ins_w.get("ge15", 0)
        elif insider >= 0.05: points += ins_w.get("ge5",  0)
        elif insider >= 0.01: points += ins_w.get("ge1",  0)

    buyback = row.get("buyback_trend", "stable")
    points += buy_w.get(buyback, 0)

    debt_trend = row.get("debt_trend", "stable")
    points += debt_w.get(debt_trend, 0)

    return round(max(0.0, min(10.0, (points / max_pts) * 10)), 2)
