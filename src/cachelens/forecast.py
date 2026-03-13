"""Spend forecasting via weighted linear regression.

No numpy required -- pure Python manual calculation.
"""
from __future__ import annotations

import calendar
from datetime import date


def compute_forecast(daily_costs: list[tuple[str, float]]) -> dict:
    """Compute a month-end spend forecast from daily cost data.

    Parameters
    ----------
    daily_costs:
        List of (date_string, cost_usd) tuples, sorted by date ascending.

    Returns
    -------
    dict with keys: projected_monthly_usd, confidence, daily_avg_usd, trend, days_remaining
    """
    today = date.today()
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    days_remaining = days_in_month - today.day

    if not daily_costs:
        return {
            "projected_monthly_usd": 0,
            "confidence": "low",
            "daily_avg_usd": 0,
            "trend": "stable",
            "days_remaining": days_remaining,
        }

    n = len(daily_costs)
    costs = [c for _, c in daily_costs]

    confidence = _confidence_level(n)
    daily_avg = sum(costs) / n

    slope = _weighted_slope(costs)
    trend = _classify_trend(slope)

    projected_monthly = round(daily_avg * days_in_month, 4)

    return {
        "projected_monthly_usd": projected_monthly,
        "confidence": confidence,
        "daily_avg_usd": round(daily_avg, 4),
        "trend": trend,
        "days_remaining": days_remaining,
    }


def _confidence_level(n: int) -> str:
    if n >= 14:
        return "high"
    if n >= 7:
        return "medium"
    return "low"


def _classify_trend(slope: float) -> str:
    if slope > 0.01:
        return "increasing"
    if slope < -0.01:
        return "decreasing"
    return "stable"


def _weighted_slope(costs: list[float]) -> float:
    """Weighted linear regression slope. Recent days weighted 2x.

    Weights: first half of data points get weight 1, second half get weight 2.
    """
    n = len(costs)
    if n < 2:
        return 0.0

    midpoint = n // 2
    weights = [1.0 if i < midpoint else 2.0 for i in range(n)]

    xs = list(range(n))
    w_sum = sum(weights)
    wx_mean = sum(w * x for w, x in zip(weights, xs)) / w_sum
    wy_mean = sum(w * y for w, y in zip(weights, costs)) / w_sum

    numerator = sum(
        w * (x - wx_mean) * (y - wy_mean)
        for w, x, y in zip(weights, xs, costs)
    )
    denominator = sum(
        w * (x - wx_mean) ** 2
        for w, x in zip(weights, xs)
    )

    if denominator == 0:
        return 0.0

    return numerator / denominator
