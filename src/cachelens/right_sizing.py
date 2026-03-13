"""Model right-sizing analysis for CacheLens v2.

Scores each call's complexity based on observable features,
then recommends downgrade for simple/moderate calls on expensive models.
"""
from __future__ import annotations

import json
from typing import Any

from cachelens.pricing import PricingTable
from cachelens.store import UsageStore

# Downgrade map: (current_model) -> {simple: cheaper, moderate: keep_or_cheaper}
_DOWNGRADE_MAP: dict[str, dict[str, str | None]] = {
    "claude-opus-4-6":           {"simple": "claude-haiku-4-5-20251001", "moderate": "claude-sonnet-4-6"},
    "claude-sonnet-4-6":         {"simple": "claude-haiku-4-5-20251001", "moderate": None},
    "gpt-4o":                    {"simple": "gpt-4o-mini",                "moderate": None},
    "gpt-4.1":                   {"simple": "gpt-4.1-mini",               "moderate": None},
    "gemini-2.5-pro-preview":    {"simple": "gemini-2.0-flash",           "moderate": None},
}


def score_complexity(call: dict) -> int:
    """Compute complexity score 0-9 for a single call.

    Score 0-2 = simple, 3-4 = moderate, 5+ = complex.
    """
    score = 0
    input_tokens = call.get("input_tokens") or 0
    output_tokens = call.get("output_tokens") or 0
    message_count = call.get("message_count") or 0

    if input_tokens > 4000:
        score += 2
    elif input_tokens > 1500:
        score += 1
    if output_tokens > 500:
        score += 2
    elif output_tokens > 200:
        score += 1
    if message_count > 6:
        score += 2
    elif message_count > 3:
        score += 1

    # Check for tool definitions in heatmap
    heatmap_raw = call.get("token_heatmap")
    if heatmap_raw:
        try:
            hm = json.loads(heatmap_raw)
            if hm.get("tool_definitions", 0) > 0:
                score += 2
        except Exception:
            pass

    # Placeholder: flag if output is long (suggests complex generation)
    if output_tokens > 1000:
        score += 1  # additional signal

    return score


def _complexity_label(score: int) -> str:
    if score <= 2:
        return "simple"
    if score <= 4:
        return "moderate"
    return "complex"


def analyze_right_sizing(
    store: UsageStore,
    pricing: PricingTable,
    days: int = 30,
) -> list[dict[str, Any]]:
    """Analyze calls for model right-sizing opportunities.

    Returns list of dicts per source+model with:
        source, model, call_count, simple_pct, moderate_pct, complex_pct,
        suggested_model, estimated_savings_usd, weekly_savings_usd
    """
    calls = store.recent_calls_with_features(days=days)

    # Group by source+model
    groups: dict[tuple, list[dict]] = {}
    for call in calls:
        key = (call["source"], call["model"], call["provider"])
        groups.setdefault(key, []).append(call)

    results = []
    for (source, model, provider), group_calls in groups.items():
        if len(group_calls) < 5:
            continue

        scored_calls = [(c, _complexity_label(score_complexity(c))) for c in group_calls]
        complexity_counts = {"simple": 0, "moderate": 0, "complex": 0}
        for _, label in scored_calls:
            complexity_counts[label] += 1

        n = len(group_calls)
        simple_pct = complexity_counts["simple"] / n
        moderate_pct = complexity_counts["moderate"] / n
        complex_pct = complexity_counts["complex"] / n

        downgrade = _DOWNGRADE_MAP.get(model, {})
        suggested_simple = downgrade.get("simple")
        suggested_moderate = downgrade.get("moderate")

        # Estimate savings: simple calls moved to cheapest suggestion
        savings = 0.0
        if suggested_simple:
            simple_calls = [c for c, label in scored_calls if label == "simple"]
            for call in simple_calls:
                original_cost = call.get("cost_usd") or 0.0
                cheaper_cost = pricing.cost_usd(
                    provider=provider,
                    model=suggested_simple,
                    input_tokens=call.get("input_tokens", 0),
                    output_tokens=call.get("output_tokens", 0),
                    cache_read_tokens=0,
                    cache_write_tokens=0,
                )
                savings += max(0.0, original_cost - cheaper_cost)

        results.append({
            "source": source,
            "model": model,
            "provider": provider,
            "call_count": n,
            "simple_pct": round(simple_pct, 3),
            "moderate_pct": round(moderate_pct, 3),
            "complex_pct": round(complex_pct, 3),
            "suggested_model_simple": suggested_simple,
            "suggested_model_moderate": suggested_moderate,
            "estimated_savings_usd": round(savings, 4),
            "weekly_savings_usd": round(savings * 7 / max(1, days), 4),
        })

    return sorted(results, key=lambda x: -x["estimated_savings_usd"])
