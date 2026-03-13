import pytest
from cachelens.pricing import PricingTable


def test_known_model_returns_correct_cost():
    table = PricingTable()
    cost = table.cost_usd(
        provider="anthropic",
        model="claude-sonnet-4-6",
        input_tokens=1_000_000,
        output_tokens=0,
        cache_read_tokens=0,
        cache_write_tokens=0,
    )
    assert cost == pytest.approx(3.0, rel=0.01)  # $3/MTok input


def test_unknown_model_falls_back_to_provider_default():
    table = PricingTable()
    cost = table.cost_usd(
        provider="anthropic",
        model="claude-unknown-99",
        input_tokens=1_000_000,
        output_tokens=0,
        cache_read_tokens=0,
        cache_write_tokens=0,
    )
    assert cost == 0.0  # default row is 0


def test_override_file_replaces_bundled_price(tmp_path):
    override = tmp_path / "pricing_overrides.toml"
    override.write_text("""
[models."claude-sonnet-4-6"]
input_usd_per_mtok = 99.0
output_usd_per_mtok = 0.0
cache_read_usd_per_mtok = 0.0
cache_write_usd_per_mtok = 0.0
""")
    table = PricingTable(overrides_path=override)
    cost = table.cost_usd(
        provider="anthropic", model="claude-sonnet-4-6",
        input_tokens=1_000_000, output_tokens=0,
        cache_read_tokens=0, cache_write_tokens=0,
    )
    assert cost == pytest.approx(99.0)


def test_malformed_override_skipped_daemon_does_not_fail(tmp_path, caplog):
    override = tmp_path / "pricing_overrides.toml"
    override.write_text('[models."bad"]\ninput_usd_per_mtok = "not_a_number"')
    import logging
    with caplog.at_level(logging.WARNING):
        table = PricingTable(overrides_path=override)
    assert table._prices.get("bad") is None, "malformed model must not be loaded into prices"
    assert any("bad" in r.message for r in caplog.records), "warning must name the skipped model"


def test_savings_usd_returns_difference_between_input_and_cache_read_rate():
    table = PricingTable()
    # claude-sonnet-4-6: input=$3/MTok, cache_read=$0.30/MTok
    # 1M cache_read tokens → saved $3.00 - $0.30 = $2.70
    savings = table.savings_usd(
        provider="anthropic",
        model="claude-sonnet-4-6",
        cache_read_tokens=1_000_000,
    )
    assert savings == pytest.approx(2.70, rel=0.01)


def test_savings_usd_zero_when_no_cache_reads():
    table = PricingTable()
    savings = table.savings_usd(
        provider="anthropic",
        model="claude-sonnet-4-6",
        cache_read_tokens=0,
    )
    assert savings == 0.0


def test_savings_usd_unknown_model_returns_zero():
    table = PricingTable()
    savings = table.savings_usd(
        provider="anthropic",
        model="unknown-model-99",
        cache_read_tokens=1_000_000,
    )
    assert savings == 0.0


# ---------------------------------------------------------------------------
# get_all_prices / apply_overrides_from_dict (Custom Pricing Overrides)
# ---------------------------------------------------------------------------


def test_get_all_prices_returns_dict():
    """PricingTable.get_all_prices() returns a dict of model -> rate dicts."""
    table = PricingTable()
    prices = table.get_all_prices()
    assert isinstance(prices, dict)
    assert "claude-sonnet-4-6" in prices
    assert "input" in prices["claude-sonnet-4-6"]
    assert "output" in prices["claude-sonnet-4-6"]
    assert "cache_read" in prices["claude-sonnet-4-6"]
    assert "cache_write" in prices["claude-sonnet-4-6"]


def test_apply_overrides_updates_rates():
    """After apply_overrides_from_dict, cost_usd changes accordingly."""
    table = PricingTable()
    original_cost = table.cost_usd(
        provider="anthropic",
        model="claude-sonnet-4-6",
        input_tokens=1_000_000,
        output_tokens=0,
        cache_read_tokens=0,
        cache_write_tokens=0,
    )
    assert original_cost == pytest.approx(3.0, rel=0.01)

    table.apply_overrides_from_dict({
        "claude-sonnet-4-6": {"input": 10.0},
    })
    new_cost = table.cost_usd(
        provider="anthropic",
        model="claude-sonnet-4-6",
        input_tokens=1_000_000,
        output_tokens=0,
        cache_read_tokens=0,
        cache_write_tokens=0,
    )
    assert new_cost == pytest.approx(10.0, rel=0.01)


def test_apply_overrides_partial_keys_preserves_others():
    """Overriding only 'input' preserves existing output/cache_read/cache_write."""
    table = PricingTable()
    original = table.get_all_prices()["claude-sonnet-4-6"].copy()

    table.apply_overrides_from_dict({
        "claude-sonnet-4-6": {"input": 99.0},
    })
    updated = table.get_all_prices()["claude-sonnet-4-6"]
    assert updated["input"] == 99.0
    assert updated["output"] == original["output"]
    assert updated["cache_read"] == original["cache_read"]
    assert updated["cache_write"] == original["cache_write"]
