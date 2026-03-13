"""Cost anomaly detection for CacheLens v2.

Algorithm:
  - Compute 14-day rolling mean and stddev of daily spend per source
  - Flag days where spend > mean + 2 * stddev
  - Require at least 7 data points to avoid false positives
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any

from cachelens.store import UsageStore


def _mean_stddev(values: list[float]) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    n = len(values)
    mean = sum(values) / n
    if n < 2:
        return mean, 0.0
    variance = sum((x - mean) ** 2 for x in values) / (n - 1)
    return mean, math.sqrt(variance)


def detect_anomalies(store: UsageStore, days: int = 30) -> list[dict[str, Any]]:
    """Detect cost, call count, and token anomalies in daily aggregated data.

    Returns list of anomaly dicts with: date, source, anomaly_type, spend_usd,
    expected_usd, stddev, threshold_usd, multiplier, call_count, top_models.
    """
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    raw_rows = store.query_daily_agg_since(cutoff)

    # Supplement with today's live data
    today = date.today().isoformat()
    today_in_agg = {(r["provider"], r["model"], r["source"])
                    for r in raw_rows if r["date"] == today}
    for r in store.aggregate_calls_for_date(today):
        if (r["provider"], r["model"], r["source"]) not in today_in_agg:
            raw_rows.append({
                "date": today,
                "provider": r["provider"],
                "model": r["model"],
                "source": r["source"],
                "call_count": r["call_count"],
                "input_tokens": r["input_tokens"],
                "output_tokens": r["output_tokens"],
                "cache_read_tokens": r["cache_read_tokens"],
                "cache_write_tokens": r["cache_write_tokens"],
                "cost_usd": r["cost_usd"],
            })

    # Aggregate per (source, date): spend, call_count, input_tokens, models used
    daily_by_source: dict[str, dict[str, dict]] = {}
    for row in raw_rows:
        source = row["source"]
        d = row["date"]
        daily_by_source.setdefault(source, {})
        if d not in daily_by_source[source]:
            daily_by_source[source][d] = {
                "cost_usd": 0.0, "call_count": 0, "input_tokens": 0,
                "models": [],
            }
        daily_by_source[source][d]["cost_usd"] += row["cost_usd"]
        daily_by_source[source][d]["call_count"] += row["call_count"] or 0
        daily_by_source[source][d]["input_tokens"] += row["input_tokens"] or 0
        daily_by_source[source][d]["models"].append(row["model"])

    anomalies: list[dict] = []
    threshold_multiplier = 2.0

    for source, date_data in daily_by_source.items():
        sorted_dates = sorted(date_data.keys())
        if len(sorted_dates) < 7:
            continue

        for i, check_date in enumerate(sorted_dates):
            baseline_dates = sorted_dates[max(0, i - 14):i]
            if len(baseline_dates) < 7:
                continue

            day = date_data[check_date]
            top_models = list(dict.fromkeys(day["models"]))[:3]  # unique, preserve order

            # --- Spend spike ---
            baseline_spend = [date_data[d]["cost_usd"] for d in baseline_dates]
            mean_spend, stddev_spend = _mean_stddev(baseline_spend)
            threshold_spend = mean_spend + threshold_multiplier * stddev_spend
            actual_spend = day["cost_usd"]
            if actual_spend > threshold_spend and actual_spend > mean_spend * 1.5:
                anomalies.append({
                    "date": check_date,
                    "source": source,
                    "anomaly_type": "spend_spike",
                    "spend_usd": round(actual_spend, 4),
                    "expected_usd": round(mean_spend, 4),
                    "stddev": round(stddev_spend, 4),
                    "threshold_usd": round(threshold_spend, 4),
                    "multiplier": round(actual_spend / mean_spend, 2) if mean_spend > 0 else None,
                    "call_count": day["call_count"],
                    "top_models": top_models,
                })

            # --- Call count spike (> 2x rolling mean) ---
            baseline_calls = [date_data[d]["call_count"] for d in baseline_dates]
            mean_calls, _ = _mean_stddev(baseline_calls)
            actual_calls = day["call_count"]
            if mean_calls > 0 and actual_calls > mean_calls * 2:
                anomalies.append({
                    "date": check_date,
                    "source": source,
                    "anomaly_type": "call_count_spike",
                    "spend_usd": round(actual_spend, 4),
                    "expected_usd": round(mean_spend, 4),
                    "stddev": round(stddev_spend, 4),
                    "threshold_usd": round(threshold_spend, 4),
                    "multiplier": round(actual_calls / mean_calls, 2),
                    "call_count": actual_calls,
                    "top_models": top_models,
                })

            # --- Token spike (avg tokens/call > 2x rolling mean) ---
            actual_calls_nonzero = max(1, actual_calls)
            baseline_tok_per_call = [
                date_data[d]["input_tokens"] / max(1, date_data[d]["call_count"])
                for d in baseline_dates
            ]
            mean_tok, _ = _mean_stddev(baseline_tok_per_call)
            actual_tok_per_call = day["input_tokens"] / actual_calls_nonzero
            if mean_tok > 0 and actual_tok_per_call > mean_tok * 2:
                anomalies.append({
                    "date": check_date,
                    "source": source,
                    "anomaly_type": "token_spike",
                    "spend_usd": round(actual_spend, 4),
                    "expected_usd": round(mean_spend, 4),
                    "stddev": round(stddev_spend, 4),
                    "threshold_usd": round(threshold_spend, 4),
                    "multiplier": round(actual_tok_per_call / mean_tok, 2),
                    "call_count": actual_calls,
                    "top_models": top_models,
                })

    return sorted(anomalies, key=lambda x: x["date"], reverse=True)
