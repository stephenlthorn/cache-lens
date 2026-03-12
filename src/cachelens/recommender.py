from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from cachelens.store import UsageStore


_IMPACT_ORDER = {"high": 0, "medium": 1, "low": 2}

_DOWNSELL_MAP: dict[str, str] = {
    "gpt-4o": "gpt-4o-mini",
    "gpt-4.1": "gpt-4.1-mini",
    "claude-opus-4-6": "claude-sonnet-4-6",
}


@dataclass
class Recommendation:
    id: str
    type: Literal["low_cache_hit_rate", "downsell_opportunity", "cache_write_waste"]
    title: str
    description: str
    estimated_impact: str
    deep_dive_link: str
    metrics: dict


def generate_recommendations(store: UsageStore) -> list[Recommendation]:
    """Query the store and return ranked recommendations."""
    cutoff = (date.today() - timedelta(days=30)).isoformat()

    raw_rows = store.query_daily_agg_since(cutoff)

    # Supplement with today's live data (not yet in daily_agg until nightly rollup)
    today = date.today().isoformat()
    today_in_agg = {(r["provider"], r["model"], r["source"]) for r in raw_rows if r["date"] == today}
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

    # Aggregate by (provider, model, source) across the 30-day window
    agg: dict[tuple, dict] = {}
    for row in raw_rows:
        key = (row["provider"], row["model"], row["source"])
        if key not in agg:
            agg[key] = {
                "provider": row["provider"],
                "model": row["model"],
                "source": row["source"],
                "call_count": 0,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "cost_usd": 0.0,
            }
        agg[key]["call_count"] += row["call_count"] or 0
        agg[key]["cache_read_tokens"] += row["cache_read_tokens"] or 0
        agg[key]["cache_write_tokens"] += row["cache_write_tokens"] or 0
        agg[key]["cost_usd"] += row["cost_usd"] or 0.0

    rows = list(agg.values())

    recommendations: list[Recommendation] = []

    recommendations.extend(_check_low_cache_hit_rate(rows))
    recommendations.extend(_check_cache_write_waste(rows))
    recommendations.extend(_check_downsell_opportunity(rows))

    return _rank(recommendations)


def _check_low_cache_hit_rate(rows: list) -> list[Recommendation]:
    results: list[Recommendation] = []
    for row in rows:
        provider = row["provider"]
        model = row["model"]
        source = row["source"]
        call_count = row["call_count"] or 0
        cache_read = row["cache_read_tokens"] or 0

        if provider != "anthropic":
            continue
        if cache_read != 0:
            continue
        if call_count < 100:
            continue

        impact = "high" if call_count >= 1000 else "medium"
        rec_id = f"low_cache_hit_rate:{provider}:{model}:{source}"
        link = f"?provider={provider}&model={model}&source={source}"

        results.append(Recommendation(
            id=rec_id,
            type="low_cache_hit_rate",
            title=f"{model} via `{source}` — 0% cache hits",
            description=(
                f"{model} via `{source}` — 0% cache hits across {call_count:,} calls. "
                "Tag your sources and check prompt structure."
            ),
            estimated_impact=impact,
            deep_dive_link=link,
            metrics={
                "provider": provider,
                "model": model,
                "source": source,
                "call_count": call_count,
                "cache_read_tokens": cache_read,
            },
        ))

    return results


def _check_cache_write_waste(rows: list) -> list[Recommendation]:
    # Group by source, anthropic only
    by_source: dict[str, dict] = {}
    for row in rows:
        if row["provider"] != "anthropic":
            continue
        source = row["source"]
        if source not in by_source:
            by_source[source] = {"cache_write_tokens": 0, "cache_read_tokens": 0}
        by_source[source]["cache_write_tokens"] += row["cache_write_tokens"] or 0
        by_source[source]["cache_read_tokens"] += row["cache_read_tokens"] or 0

    results: list[Recommendation] = []
    for source, totals in by_source.items():
        write_tokens = totals["cache_write_tokens"]
        read_tokens = totals["cache_read_tokens"]

        if write_tokens <= 0:
            continue
        if read_tokens != 0:
            continue

        impact = "high" if write_tokens > 100_000 else "medium"
        rec_id = f"cache_write_waste:anthropic:{source}"
        link = f"?provider=anthropic&source={source}"

        results.append(Recommendation(
            id=rec_id,
            type="cache_write_waste",
            title=f"Cache write tokens never reused for source `{source}`",
            description=(
                f"Cache write tokens never reused for source `{source}` — "
                "system prompt likely changes every call."
            ),
            estimated_impact=impact,
            deep_dive_link=link,
            metrics={
                "source": source,
                "cache_write_tokens": write_tokens,
                "cache_read_tokens": read_tokens,
            },
        ))

    return results


def _check_downsell_opportunity(rows: list) -> list[Recommendation]:
    results: list[Recommendation] = []
    for row in rows:
        provider = row["provider"]
        model = row["model"]
        source = row["source"]
        cost_usd = row["cost_usd"] or 0.0

        cheaper = _DOWNSELL_MAP.get(model)
        if cheaper is None:
            continue
        if cost_usd < 1.0:
            continue

        impact = "high" if cost_usd >= 10.0 else "medium"
        rec_id = f"downsell_opportunity:{provider}:{model}:{source}"
        link = f"?provider={provider}&model={model}&source={source}"

        results.append(Recommendation(
            id=rec_id,
            type="downsell_opportunity",
            title=f"Consider routing `{model}` calls to `{cheaper}`",
            description=(
                f"${cost_usd:.2f} spent on `{model}` via `{source}` — "
                f"consider routing to `{cheaper}` for lower cost."
            ),
            estimated_impact=impact,
            deep_dive_link=link,
            metrics={
                "provider": provider,
                "model": model,
                "source": source,
                "cost_usd": cost_usd,
                "suggested_model": cheaper,
            },
        ))

    return results


def _rank(recommendations: list[Recommendation]) -> list[Recommendation]:
    def _sort_key(r: Recommendation) -> tuple:
        impact_order = {"high": 0, "medium": 1, "low": 2}
        if r.type == "low_cache_hit_rate":
            secondary = -r.metrics.get("call_count", 0)
        elif r.type == "cache_write_waste":
            secondary = -r.metrics.get("cache_write_tokens", 0)
        else:
            secondary = -r.metrics.get("cost_usd", 0.0)
        return (impact_order.get(r.estimated_impact, 3), secondary)

    return sorted(recommendations, key=_sort_key)
