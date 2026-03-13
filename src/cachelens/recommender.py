from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from cachelens.store import UsageStore


_IMPACT_ORDER = {"high": 0, "medium": 1, "low": 2}

_DOWNSELL_MAP: dict[str, str] = {
    "gpt-4o": "gpt-4o-mini",
    "gpt-4.1": "gpt-4.1-nano",
    "claude-opus-4-6": "claude-sonnet-4-6",
    "claude-sonnet-4-6": "claude-haiku-4-5",
}


@dataclass
class Recommendation:
    id: str
    type: Literal[
        "low_cache_hit_rate", "downsell_opportunity", "cache_write_waste",
        "spend_spike", "bloated_prompts", "caching_opportunity",
        "efficiency_regression", "source_consolidation",
        "output_bloat", "history_bloat", "right_sizing",
    ]
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
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "cost_usd": 0.0,
            }
        agg[key]["call_count"] += row["call_count"] or 0
        agg[key]["input_tokens"] += row["input_tokens"] or 0
        agg[key]["output_tokens"] += row["output_tokens"] or 0
        agg[key]["cache_read_tokens"] += row["cache_read_tokens"] or 0
        agg[key]["cache_write_tokens"] += row["cache_write_tokens"] or 0
        agg[key]["cost_usd"] += row["cost_usd"] or 0.0

    rows = list(agg.values())

    recommendations: list[Recommendation] = []

    recommendations.extend(_check_low_cache_hit_rate(rows))
    recommendations.extend(_check_cache_write_waste(rows))
    recommendations.extend(_check_downsell_opportunity(rows))
    recommendations.extend(_check_spend_spike(raw_rows))
    recommendations.extend(_check_bloated_prompts(rows))
    recommendations.extend(_check_caching_opportunity(rows))
    recommendations.extend(_check_efficiency_regression(raw_rows))
    recommendations.extend(_check_source_consolidation(rows))

    # Check: output bloat — sources using < 25% of their max_tokens budget
    try:
        eff_rows = store.output_efficiency(days=30)
        for row in eff_rows:
            if row.get("avg_utilization", 1.0) < 0.25 and row.get("call_count", 0) >= 10:
                import hashlib
                rec_id = hashlib.md5(
                    f"output_bloat:{row['source']}:{row['model']}".encode()
                ).hexdigest()[:12]
                recommendations.append(Recommendation(
                    id=rec_id,
                    type="output_bloat",
                    title=f"Oversized max_tokens for {row['source']}",
                    description=(
                        f"Source '{row['source']}' ({row['model']}) uses only "
                        f"{row['avg_utilization']*100:.0f}% of its max_tokens budget on average "
                        f"across {row['call_count']} calls. Reducing max_tokens can cut costs."
                    ),
                    estimated_impact="medium",
                    deep_dive_link="/api/usage/output-efficiency",
                    metrics={
                        "avg_utilization": round(row["avg_utilization"], 3),
                        "call_count": row["call_count"],
                        "model": row["model"],
                    },
                ))
    except Exception:
        pass  # output_efficiency may not exist on old DBs; skip gracefully

    # Check: history bloat — sources with high history token ratio
    try:
        import hashlib
        conv_rows = store.conversation_efficiency(days=30)
        for row in conv_rows:
            if row["avg_history_ratio"] > 0.6 and row["call_count"] >= 5:
                rec_id = hashlib.md5(
                    f"history_bloat:{row['source']}".encode()
                ).hexdigest()[:12]
                recommendations.append(Recommendation(
                    id=rec_id,
                    type="history_bloat",
                    title="History Bloat Detected",
                    description=(
                        f"Source '{row['source']}' has {row['avg_history_ratio']:.0%} of tokens "
                        f"in historical context"
                    ),
                    estimated_impact="medium",
                    deep_dive_link="/api/usage/conversation-efficiency",
                    metrics={
                        "source": row["source"],
                        "avg_history_ratio": round(row["avg_history_ratio"], 3),
                        "avg_message_count": row["avg_message_count"],
                        "call_count": row["call_count"],
                    },
                ))
                break  # one recommendation per analysis cycle
    except Exception:
        pass  # conversation_efficiency may not exist on old DBs; skip gracefully

    return _rank(recommendations)


def _check_low_cache_hit_rate(rows: list) -> list[Recommendation]:
    """Flag any provider/model/source with cache hit rate < 50% and >= 100 calls."""
    results: list[Recommendation] = []
    for row in rows:
        provider = row["provider"]
        model = row["model"]
        source = row["source"]
        call_count = row["call_count"] or 0
        cache_read = row["cache_read_tokens"] or 0
        input_tokens = row["input_tokens"] or 0

        if call_count < 100:
            continue

        total = cache_read + input_tokens
        if total <= 0:
            continue
        hit_rate = cache_read / total * 100

        if hit_rate >= 50:
            continue

        impact = "high" if call_count >= 1000 else "medium"
        rec_id = f"low_cache_hit_rate:{provider}:{model}:{source}"
        link = f"?provider={provider}&model={model}&source={source}"

        results.append(Recommendation(
            id=rec_id,
            type="low_cache_hit_rate",
            title=f"{model} via `{source}` — {hit_rate:.0f}% cache hits",
            description=(
                f"{model} via `{source}` — {hit_rate:.0f}% cache hits across {call_count:,} calls. "
                "Consider structuring prompts with static prefixes for better caching."
            ),
            estimated_impact=impact,
            deep_dive_link=link,
            metrics={
                "provider": provider,
                "model": model,
                "source": source,
                "call_count": call_count,
                "cache_read_tokens": cache_read,
                "hit_rate": hit_rate,
            },
        ))

    return results


def _check_cache_write_waste(rows: list) -> list[Recommendation]:
    """Flag sources with cache writes but disproportionately low reads."""
    by_source: dict[str, dict] = {}
    for row in rows:
        source = row["source"]
        if source not in by_source:
            by_source[source] = {
                "cache_write_tokens": 0,
                "cache_read_tokens": 0,
                "providers": set(),
            }
        by_source[source]["cache_write_tokens"] += row["cache_write_tokens"] or 0
        by_source[source]["cache_read_tokens"] += row["cache_read_tokens"] or 0
        by_source[source]["providers"].add(row["provider"])

    results: list[Recommendation] = []
    for source, totals in by_source.items():
        write_tokens = totals["cache_write_tokens"]
        read_tokens = totals["cache_read_tokens"]

        if write_tokens <= 0:
            continue

        if read_tokens == 0:
            reason = "zero cache reads"
        elif read_tokens / write_tokens < 0.10:
            reason = f"read/write ratio only {read_tokens / write_tokens:.1%}"
        else:
            continue

        impact = "high" if write_tokens > 100_000 else "medium"
        providers = sorted(totals["providers"])
        rec_id = f"cache_write_waste:{providers[0]}:{source}"
        link = f"?source={source}"

        results.append(Recommendation(
            id=rec_id,
            type="cache_write_waste",
            title=f"Cache write tokens poorly utilized for `{source}`",
            description=(
                f"Cache write waste for `{source}` — {reason}. "
                "System prompt likely changes every call, preventing cache reuse."
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
    """Flag expensive models that could use cheaper alternatives. Threshold: $0.50."""
    results: list[Recommendation] = []
    for row in rows:
        provider = row["provider"]
        model = row["model"]
        source = row["source"]
        cost_usd = row["cost_usd"] or 0.0

        cheaper = _DOWNSELL_MAP.get(model)
        if cheaper is None:
            continue
        if cost_usd < 0.50:
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


def _check_spend_spike(raw_rows: list) -> list[Recommendation]:
    """Flag if today's spend rate is > 2x the 7-day rolling average."""
    today = date.today().isoformat()
    today_cost = sum(r["cost_usd"] or 0.0 for r in raw_rows if r["date"] == today)
    if today_cost <= 0:
        return []

    seven_days_ago = (date.today() - timedelta(days=7)).isoformat()
    prev_costs: dict[str, float] = {}
    for r in raw_rows:
        d = r["date"]
        if d >= seven_days_ago and d < today:
            prev_costs[d] = prev_costs.get(d, 0.0) + (r["cost_usd"] or 0.0)

    if len(prev_costs) < 3:
        return []

    avg_daily = sum(prev_costs.values()) / len(prev_costs)
    if avg_daily <= 0:
        return []

    ratio = today_cost / avg_daily
    if ratio <= 2.0:
        return []

    return [Recommendation(
        id="spend_spike:today",
        type="spend_spike",
        title=f"Spend spike: today is {ratio:.1f}x your 7-day average",
        description=(
            f"Today's cost is ${today_cost:.2f} vs 7-day avg of ${avg_daily:.2f}/day "
            f"({ratio:.1f}x higher). Check for runaway loops or unexpected traffic."
        ),
        estimated_impact="high",
        deep_dive_link=f"?from={today}&to={today}",
        metrics={
            "today_cost_usd": today_cost,
            "avg_daily_cost_usd": avg_daily,
            "ratio": ratio,
        },
    )]


def _check_bloated_prompts(rows: list) -> list[Recommendation]:
    """Flag model+source combos where input/output ratio > 20."""
    results: list[Recommendation] = []
    for row in rows:
        call_count = row["call_count"] or 0
        if call_count < 50:
            continue
        input_tokens = row["input_tokens"] or 0
        output_tokens = row["output_tokens"] or 0
        if output_tokens <= 0:
            continue

        ratio = input_tokens / output_tokens
        if ratio <= 20:
            continue

        model = row["model"]
        source = row["source"]
        provider = row["provider"]

        results.append(Recommendation(
            id=f"bloated_prompts:{provider}:{model}:{source}",
            type="bloated_prompts",
            title=f"High input/output ratio ({ratio:.0f}:1) for `{model}` via `{source}`",
            description=(
                f"Input tokens are {ratio:.0f}x output tokens across {call_count} calls. "
                "Your prompts may be oversized — consider trimming context or using summarization."
            ),
            estimated_impact="medium",
            deep_dive_link=f"?provider={provider}&model={model}&source={source}",
            metrics={
                "provider": provider,
                "model": model,
                "source": source,
                "input_output_ratio": ratio,
                "call_count": call_count,
            },
        ))

    return results


def _check_caching_opportunity(rows: list) -> list[Recommendation]:
    """Flag high-volume OpenAI/Google calls with zero cache reads."""
    results: list[Recommendation] = []
    for row in rows:
        provider = row["provider"]
        if provider not in ("openai", "google"):
            continue
        call_count = row["call_count"] or 0
        if call_count < 200:
            continue
        cache_read = row["cache_read_tokens"] or 0
        if cache_read > 0:
            continue

        model = row["model"]
        source = row["source"]

        results.append(Recommendation(
            id=f"caching_opportunity:{provider}:{model}:{source}",
            type="caching_opportunity",
            title=f"Enable prompt caching for `{model}` via `{source}`",
            description=(
                f"{call_count} calls with zero cache reads on {provider}. "
                "Prompt caching is available but not being used — enable it to reduce costs."
            ),
            estimated_impact="medium",
            deep_dive_link=f"?provider={provider}&model={model}&source={source}",
            metrics={
                "provider": provider,
                "model": model,
                "source": source,
                "call_count": call_count,
            },
        ))

    return results


def _check_efficiency_regression(raw_rows: list) -> list[Recommendation]:
    """Detect if cache hit rate dropped by >10pp over the last 14 days."""
    today = date.today()
    cutoff_14d = (today - timedelta(days=14)).isoformat()
    cutoff_7d = (today - timedelta(days=7)).isoformat()

    first_input, first_cache = 0, 0
    second_input, second_cache = 0, 0

    for r in raw_rows:
        d = r["date"]
        if d < cutoff_14d:
            continue
        inp = r["input_tokens"] or 0
        cr = r["cache_read_tokens"] or 0
        if d < cutoff_7d:
            first_input += inp
            first_cache += cr
        else:
            second_input += inp
            second_cache += cr

    total_first = first_cache + first_input
    total_second = second_cache + second_input
    if total_first <= 0 or total_second <= 0:
        return []

    rate_first = first_cache / total_first * 100
    rate_second = second_cache / total_second * 100
    drop = rate_first - rate_second

    if drop <= 10:
        return []

    return [Recommendation(
        id="efficiency_regression",
        type="efficiency_regression",
        title=f"Cache efficiency dropped {drop:.0f}pp over the last 14 days",
        description=(
            f"Cache hit rate went from {rate_first:.0f}% (weeks ago) to {rate_second:.0f}% (recent). "
            "Check if prompt structure or system messages changed recently."
        ),
        estimated_impact="high",
        deep_dive_link="",
        metrics={
            "rate_first_half": rate_first,
            "rate_second_half": rate_second,
            "drop_pp": drop,
        },
    )]


def _check_source_consolidation(rows: list) -> list[Recommendation]:
    """Flag when 3+ sources use the same model with the same provider."""
    by_model: dict[tuple[str, str], list[str]] = {}
    for row in rows:
        key = (row["provider"], row["model"])
        source = row["source"]
        if key not in by_model:
            by_model[key] = []
        if source not in by_model[key]:
            by_model[key].append(source)

    results: list[Recommendation] = []
    for (provider, model), sources in by_model.items():
        if len(sources) < 3:
            continue
        results.append(Recommendation(
            id=f"source_consolidation:{provider}:{model}",
            type="source_consolidation",
            title=f"{len(sources)} sources using `{model}` — consider consolidating",
            description=(
                f"Sources {', '.join(f'`{s}`' for s in sources[:5])} all use `{model}` on {provider}. "
                "Consolidating could improve cache reuse across sources."
            ),
            estimated_impact="low",
            deep_dive_link=f"?provider={provider}&model={model}",
            metrics={
                "provider": provider,
                "model": model,
                "sources": sources,
                "source_count": len(sources),
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
        elif r.type == "spend_spike":
            secondary = -r.metrics.get("ratio", 0)
        elif r.type == "bloated_prompts":
            secondary = -r.metrics.get("input_output_ratio", 0)
        else:
            secondary = -r.metrics.get("cost_usd", 0.0)
        return (impact_order.get(r.estimated_impact, 3), secondary)

    return sorted(recommendations, key=_sort_key)
