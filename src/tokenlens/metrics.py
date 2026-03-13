from __future__ import annotations

from tokenlens.store import UsageStore


def render_prometheus_metrics(store: UsageStore) -> str:
    """Return Prometheus exposition format text."""
    kpi = store.kpi_rolling(days=1)
    kpi_30d = store.kpi_rolling(days=30)
    daily_spend = store.daily_spend_usd()
    monthly_spend = store.monthly_spend_usd()
    db_size = store.db_size_bytes()

    lines: list[str] = []

    lines.append("# HELP tokenlens_daily_spend_usd Total USD spent today")
    lines.append("# TYPE tokenlens_daily_spend_usd gauge")
    lines.append(f"tokenlens_daily_spend_usd {daily_spend:.6f}")

    lines.append("# HELP tokenlens_monthly_spend_usd Total USD spent this month")
    lines.append("# TYPE tokenlens_monthly_spend_usd gauge")
    lines.append(f"tokenlens_monthly_spend_usd {monthly_spend:.6f}")

    lines.append("# HELP tokenlens_calls_today Total API calls today")
    lines.append("# TYPE tokenlens_calls_today gauge")
    lines.append(f"tokenlens_calls_today {kpi['call_count']}")

    lines.append("# HELP tokenlens_calls_30d Total API calls in last 30 days")
    lines.append("# TYPE tokenlens_calls_30d gauge")
    lines.append(f"tokenlens_calls_30d {kpi_30d['call_count']}")

    total = kpi["cache_read_tokens"] + kpi["input_tokens"]
    hit_rate = (kpi["cache_read_tokens"] / total * 100) if total > 0 else 0.0
    lines.append("# HELP tokenlens_cache_hit_rate_pct Cache hit rate percentage today")
    lines.append("# TYPE tokenlens_cache_hit_rate_pct gauge")
    lines.append(f"tokenlens_cache_hit_rate_pct {hit_rate:.2f}")

    for key in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"):
        label = key.replace("_", " ")
        lines.append(f"# HELP tokenlens_{key}_today {label} today")
        lines.append(f"# TYPE tokenlens_{key}_today gauge")
        lines.append(f"tokenlens_{key}_today {kpi[key]}")

    lines.append("# HELP tokenlens_db_size_bytes SQLite database file size")
    lines.append("# TYPE tokenlens_db_size_bytes gauge")
    lines.append(f"tokenlens_db_size_bytes {db_size}")

    return "\n".join(lines) + "\n"
