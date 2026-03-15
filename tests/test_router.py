"""Tests for the routing engine."""
from tokenlens.router import resolve_model_alias, RoutingConfig


def test_alias_resolves():
    config = RoutingConfig(
        aliases={"gpt-4": "claude-sonnet-4-6"},
        fallback_chains={},
        weights={},
    )
    result = resolve_model_alias("gpt-4", config)
    assert result == "claude-sonnet-4-6"


def test_alias_passthrough_when_not_configured():
    config = RoutingConfig(aliases={}, fallback_chains={}, weights={})
    result = resolve_model_alias("claude-sonnet-4-6", config)
    assert result == "claude-sonnet-4-6"


def test_alias_passthrough_when_no_match():
    config = RoutingConfig(
        aliases={"gpt-4": "claude-sonnet-4-6"},
        fallback_chains={},
        weights={},
    )
    result = resolve_model_alias("gpt-4o-mini", config)
    assert result == "gpt-4o-mini"


from tokenlens.router import select_fallback_provider


def test_fallback_returns_primary_first():
    config = RoutingConfig(
        aliases={},
        fallback_chains={"anthropic": ["openai", "google"]},
        weights={},
    )
    result = select_fallback_provider("anthropic", config, provider_healthy=lambda p: True)
    assert result == "anthropic"


def test_fallback_skips_unhealthy_primary():
    config = RoutingConfig(
        aliases={},
        fallback_chains={"anthropic": ["openai", "google"]},
        weights={},
    )
    healthy = {"openai", "google"}
    result = select_fallback_provider("anthropic", config, provider_healthy=lambda p: p in healthy)
    assert result == "openai"


def test_fallback_returns_none_when_all_down():
    config = RoutingConfig(
        aliases={},
        fallback_chains={"anthropic": ["openai"]},
        weights={},
    )
    result = select_fallback_provider("anthropic", config, provider_healthy=lambda p: False)
    assert result is None


def test_fallback_no_chain_configured():
    config = RoutingConfig(aliases={}, fallback_chains={}, weights={})
    result = select_fallback_provider("anthropic", config, provider_healthy=lambda p: True)
    assert result == "anthropic"


def test_fallback_no_chain_primary_unhealthy():
    config = RoutingConfig(aliases={}, fallback_chains={}, weights={})
    result = select_fallback_provider("anthropic", config, provider_healthy=lambda p: False)
    assert result == "anthropic"  # No chain = always try primary


import random
from tokenlens.router import select_weighted_model


def test_weighted_returns_only_model_when_single():
    config = RoutingConfig(
        aliases={},
        fallback_chains={},
        weights={"my-agent": {"claude-haiku-4-5-20251001": 100}},
    )
    result = select_weighted_model("my-agent", config, rng=random.Random(42))
    assert result == "claude-haiku-4-5-20251001"


def test_weighted_returns_none_for_unconfigured_source():
    config = RoutingConfig(aliases={}, fallback_chains={}, weights={})
    result = select_weighted_model("my-agent", config, rng=random.Random(42))
    assert result is None


def test_weighted_distribution_roughly_correct():
    config = RoutingConfig(
        aliases={},
        fallback_chains={},
        weights={"my-agent": {
            "claude-haiku-4-5-20251001": 70,
            "claude-sonnet-4-6": 30,
        }},
    )
    rng = random.Random(42)
    counts = {"claude-haiku-4-5-20251001": 0, "claude-sonnet-4-6": 0}
    for _ in range(1000):
        model = select_weighted_model("my-agent", config, rng=rng)
        counts[model] += 1
    assert 600 < counts["claude-haiku-4-5-20251001"] < 800
    assert 200 < counts["claude-sonnet-4-6"] < 400


from tokenlens.router import select_lowest_latency_provider


def test_latency_selects_fastest():
    health = [
        {"provider": "anthropic", "p50_ms": 800, "error_rate": 0.01},
        {"provider": "openai", "p50_ms": 200, "error_rate": 0.01},
        {"provider": "google", "p50_ms": 500, "error_rate": 0.01},
    ]
    result = select_lowest_latency_provider(
        candidates=["anthropic", "openai", "google"],
        provider_health=health,
    )
    assert result == "openai"


def test_latency_skips_high_error_rate():
    health = [
        {"provider": "anthropic", "p50_ms": 800, "error_rate": 0.01},
        {"provider": "openai", "p50_ms": 200, "error_rate": 0.50},
    ]
    result = select_lowest_latency_provider(
        candidates=["anthropic", "openai"],
        provider_health=health,
        max_error_rate=0.10,
    )
    assert result == "anthropic"


def test_latency_returns_first_candidate_when_no_health_data():
    result = select_lowest_latency_provider(
        candidates=["anthropic", "openai"],
        provider_health=[],
    )
    assert result == "anthropic"


def test_latency_returns_first_candidate_when_all_unhealthy():
    health = [
        {"provider": "anthropic", "p50_ms": 800, "error_rate": 0.60},
        {"provider": "openai", "p50_ms": 200, "error_rate": 0.90},
    ]
    result = select_lowest_latency_provider(
        candidates=["anthropic", "openai"],
        provider_health=health,
        max_error_rate=0.10,
    )
    assert result == "anthropic"
