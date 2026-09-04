"""Criterion #2 — Economic Moat scoring (0–10)."""


def score(row: dict, weights: dict | None = None) -> float:
    from scoring.weights import _DEFAULTS
    w = weights or _DEFAULTS["moat"]

    gm_w    = w.get("gross_margin",   {"ge60": 6, "ge50": 5, "ge40": 4, "ge30": 3, "ge20": 2, "ge10": 1})
    trend_w = w.get("margin_trend",   {"improving": 1, "declining": -1})
    roe_w   = w.get("roe_consistency",{"lt3": 5, "lt6": 4, "lt10": 3, "lt15": 2, "lt20": 1})

    # max possible points = top breakpoint of each factor
    max_pts = (
        max(gm_w.values(), default=0)
        + abs(trend_w.get("improving", 1))
        + max(roe_w.values(), default=0)
    )
    if max_pts == 0:
        return 0.0

    points = 0.0

    gm = row.get("gross_margin")
    if gm is not None:
        if   gm >= 0.60: points += gm_w.get("ge60", 0)
        elif gm >= 0.50: points += gm_w.get("ge50", 0)
        elif gm >= 0.40: points += gm_w.get("ge40", 0)
        elif gm >= 0.30: points += gm_w.get("ge30", 0)
        elif gm >= 0.20: points += gm_w.get("ge20", 0)
        elif gm >= 0.10: points += gm_w.get("ge10", 0)

    trend = row.get("gross_margin_trend", "stable")
    if   trend == "improving": points += trend_w.get("improving", 0)
    elif trend == "declining": points += trend_w.get("declining", 0)

    c = row.get("roe_consistency")
    if c is not None:
        if   c < 0.03: points += roe_w.get("lt3",  0)
        elif c < 0.06: points += roe_w.get("lt6",  0)
        elif c < 0.10: points += roe_w.get("lt10", 0)
        elif c < 0.15: points += roe_w.get("lt15", 0)
        elif c < 0.20: points += roe_w.get("lt20", 0)

    return round(max(0.0, min(10.0, (points / max_pts) * 10)), 2)
