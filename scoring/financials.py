"""Criterion #4 — Financial Strength scoring (0–10)."""


def score(row: dict, weights: dict | None = None) -> float:
    from scoring.weights import _DEFAULTS
    w = weights or _DEFAULTS["financial"]

    roe_w = w.get("roe_5yr",        {"ge25": 8, "ge20": 7, "ge15": 5, "ge10": 3, "ge5": 1})
    de_w  = w.get("debt_equity",    {"lt20": 6, "lt50": 5, "lt100": 3, "lt200": 1})
    fcf_w = w.get("fcf_yield",      {"ge10": 6, "ge7": 5, "ge5": 4, "ge3": 3, "ge1": 1})
    eg_w  = w.get("earnings_growth",{"ge20": 6, "ge15": 5, "ge10": 4, "ge5": 2, "ge0": 1})

    max_pts = (
        max(roe_w.values(), default=0)
        + max(de_w.values(),  default=0)
        + max(fcf_w.values(), default=0)
        + max(eg_w.values(),  default=0)
    )
    if max_pts == 0:
        return 0.0

    points = 0.0

    roe = row.get("roe_5yr")
    if roe is not None:
        if   roe >= 0.25: points += roe_w.get("ge25", 0)
        elif roe >= 0.20: points += roe_w.get("ge20", 0)
        elif roe >= 0.15: points += roe_w.get("ge15", 0)
        elif roe >= 0.10: points += roe_w.get("ge10", 0)
        elif roe >= 0.05: points += roe_w.get("ge5",  0)

    de = row.get("debt_equity")
    if de is not None:
        r = de / 100
        if   r < 0.20: points += de_w.get("lt20",  0)
        elif r < 0.50: points += de_w.get("lt50",  0)
        elif r < 1.00: points += de_w.get("lt100", 0)
        elif r < 2.00: points += de_w.get("lt200", 0)

    fcf = row.get("fcf_yield")
    if fcf is not None:
        if   fcf >= 0.10: points += fcf_w.get("ge10", 0)
        elif fcf >= 0.07: points += fcf_w.get("ge7",  0)
        elif fcf >= 0.05: points += fcf_w.get("ge5",  0)
        elif fcf >= 0.03: points += fcf_w.get("ge3",  0)
        elif fcf >= 0.01: points += fcf_w.get("ge1",  0)

    eg = row.get("earnings_growth_5yr")
    if eg is not None:
        if   eg >= 0.20: points += eg_w.get("ge20", 0)
        elif eg >= 0.15: points += eg_w.get("ge15", 0)
        elif eg >= 0.10: points += eg_w.get("ge10", 0)
        elif eg >= 0.05: points += eg_w.get("ge5",  0)
        elif eg >= 0.00: points += eg_w.get("ge0",  0)

    return round(max(0.0, min(10.0, (points / max_pts) * 10)), 2)
