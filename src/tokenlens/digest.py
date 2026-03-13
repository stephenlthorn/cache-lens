"""Weekly cost digest for TokenLens."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from tokenlens.pricing import PricingTable
from tokenlens.store import UsageStore


def generate_digest(store: UsageStore, pricing: PricingTable, days: int = 7) -> dict[str, Any]:
    """Generate a cost digest for the past `days` days."""
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    raw_rows = list(store.query_daily_agg_since(cutoff))

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

    total_spend = sum(r["cost_usd"] for r in raw_rows)
    total_calls = sum(r["call_count"] for r in raw_rows)
    total_input = sum(r["input_tokens"] for r in raw_rows)
    total_cache_read = sum(r["cache_read_tokens"] for r in raw_rows)
    cache_hit_rate = (total_cache_read / total_input) if total_input > 0 else 0.0

    # Top sources by spend
    source_spend: dict[str, dict] = {}
    for row in raw_rows:
        src = row["source"]
        if src not in source_spend:
            source_spend[src] = {"source": src, "cost_usd": 0.0, "call_count": 0}
        source_spend[src]["cost_usd"] += row["cost_usd"]
        source_spend[src]["call_count"] += row["call_count"]

    top_sources = sorted(source_spend.values(), key=lambda x: -x["cost_usd"])[:5]
    for src in top_sources:
        src["pct"] = round(src["cost_usd"] / total_spend * 100, 1) if total_spend > 0 else 0.0
        src["cost_usd"] = round(src["cost_usd"], 2)

    # Waste summary
    waste_summary = store.waste_summary(days=days)

    # Budget status
    monthly_limit_str = store.get_setting("budget.monthly_limit_usd")
    budget_info = None
    if monthly_limit_str:
        try:
            monthly_limit = float(monthly_limit_str)
            monthly_projected = total_spend * (30 / days)
            budget_info = {
                "monthly_limit_usd": monthly_limit,
                "projected_monthly_usd": round(monthly_projected, 2),
                "pct_used": round(monthly_projected / monthly_limit * 100, 1),
            }
        except (TypeError, ValueError):
            pass

    return {
        "period_days": days,
        "period_start": cutoff,
        "period_end": today,
        "total_spend_usd": round(total_spend, 2),
        "total_calls": total_calls,
        "cache_hit_rate": round(cache_hit_rate, 3),
        "top_sources": top_sources,
        "waste_summary": waste_summary,
        "budget": budget_info,
    }


def format_digest_human(report: dict) -> str:
    """Format a digest report as human-readable text."""
    lines = []
    start = report.get("period_start", "")
    end = report.get("period_end", "")
    lines.append(f"TokenLens Digest ({start} — {end})")
    lines.append("═" * 50)
    lines.append("")
    lines.append(f"Spend:     ${report['total_spend_usd']:.2f}")
    lines.append(f"Calls:     {report['total_calls']:,}")
    lines.append(f"Cache Hit: {report['cache_hit_rate']*100:.0f}%")
    lines.append("")
    if report.get("top_sources"):
        lines.append("Top Cost Drivers:")
        for i, src in enumerate(report["top_sources"], 1):
            lines.append(
                f"  {i}. {src['source']:<20} ${src['cost_usd']:.2f}  ({src.get('pct', 0):.0f}%)"
            )
    lines.append("")
    waste = report.get("waste_summary", {})
    if waste.get("total_waste_tokens"):
        lines.append("Waste Detected:")
        for wtype, tokens in (waste.get("by_type") or {}).items():
            lines.append(f"  {wtype:<20} {tokens:,} tokens")
    if report.get("budget"):
        b = report["budget"]
        lines.append("")
        lines.append(f"Budget: {b['pct_used']}% of monthly limit projected")
    return "\n".join(lines)
