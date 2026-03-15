"""Routing engine for multi-provider request distribution.

Config structure (stored in settings table as JSON under key 'routing.config'):
{
    "aliases": {
        "<requested_model>": "<actual_model>"
    },
    "fallback_chains": {
        "<provider>": ["<provider1>", "<provider2>", ...]
    },
    "weights": {
        "<source>": {
            "<model1>": <weight_int>,
            "<model2>": <weight_int>
        }
    }
}
"""
from __future__ import annotations

import random as _random
from collections.abc import Callable
from typing import NamedTuple


class RoutingConfig(NamedTuple):
    aliases: dict[str, str]
    fallback_chains: dict[str, list[str]]
    weights: dict[str, dict[str, int]]


def parse_routing_config(raw: dict | None) -> RoutingConfig:
    """Parse a raw config dict into a RoutingConfig."""
    if not raw:
        return RoutingConfig(aliases={}, fallback_chains={}, weights={})
    return RoutingConfig(
        aliases=raw.get("aliases") or {},
        fallback_chains=raw.get("fallback_chains") or {},
        weights=raw.get("weights") or {},
    )


def resolve_model_alias(model: str, config: RoutingConfig) -> str:
    """Replace a model name with its alias if configured, otherwise passthrough."""
    return config.aliases.get(model, model)


def select_fallback_provider(
    primary: str,
    config: RoutingConfig,
    *,
    provider_healthy: Callable[[str], bool],
) -> str | None:
    """Select the first healthy provider from the fallback chain.

    Returns the primary provider if healthy. If not, tries each fallback
    in order. Returns None only if a chain is configured and ALL providers
    (primary + fallbacks) are unhealthy.

    If no chain is configured for this provider, always returns the primary
    (no fallback behavior).
    """
    chain = config.fallback_chains.get(primary)
    if chain is None:
        return primary  # No chain = always use primary

    # Try primary first
    if provider_healthy(primary):
        return primary

    # Try each fallback in order
    for fallback in chain:
        if provider_healthy(fallback):
            return fallback

    return None


def select_weighted_model(
    source: str,
    config: RoutingConfig,
    *,
    rng: _random.Random | None = None,
) -> str | None:
    """Select a model based on per-source weight distribution.

    Returns None if no weights are configured for this source.
    Uses the provided RNG for deterministic testing.
    """
    source_weights = config.weights.get(source)
    if not source_weights:
        return None

    models = list(source_weights.keys())
    weights = [source_weights[m] for m in models]

    if not models:
        return None

    r = rng if rng is not None else _random.Random()
    return r.choices(models, weights=weights, k=1)[0]


def select_lowest_latency_provider(
    *,
    candidates: list[str],
    provider_health: list[dict],
    max_error_rate: float = 0.10,
) -> str:
    """Select the provider with the lowest p50 latency from candidates.

    Excludes providers with error_rate above max_error_rate.
    Falls back to the first candidate if no health data or all are unhealthy.
    """
    if not candidates:
        return "anthropic"  # shouldn't happen, but safe default

    health_by_provider = {h["provider"]: h for h in provider_health}

    # Filter to healthy candidates with latency data
    healthy = []
    for c in candidates:
        h = health_by_provider.get(c)
        if h and h.get("error_rate", 0) <= max_error_rate:
            healthy.append((c, h["p50_ms"]))

    if not healthy:
        return candidates[0]

    # Sort by p50 latency, pick lowest
    healthy.sort(key=lambda x: x[1])
    return healthy[0][0]
