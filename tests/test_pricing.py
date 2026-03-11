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
