"""Tests for digest.py — weekly cost digest."""
import pytest
from pathlib import Path
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from tokenlens.pricing import PricingTable
from tokenlens.server import create_app
from tokenlens.store import UsageStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def test_store(tmp_path: Path) -> UsageStore:
    return UsageStore(tmp_path / "test.db")


@pytest.fixture
def pricing() -> PricingTable:
    return PricingTable()


@pytest.fixture
def client(test_store: UsageStore, pricing: PricingTable):  # type: ignore[return]
    app = create_app(store=test_store, pricing=pricing)
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(daily_rows=None):
    store = MagicMock()
    store.query_daily_agg_since.return_value = daily_rows or []
    store.aggregate_calls_for_date.return_value = []
    store.waste_summary.return_value = {
        "total_waste_tokens": 0, "total_savings_usd": 0.0, "by_type": {}
    }
    store.get_setting.return_value = None
    return store


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_digest_empty_store():
    from tokenlens.digest import generate_digest
    from tokenlens.pricing import PricingTable
    store = _make_store()
    pricing = PricingTable()
    report = generate_digest(store=store, pricing=pricing, days=7)
    assert report["total_spend_usd"] == 0.0
    assert report["total_calls"] == 0
    assert isinstance(report["top_sources"], list)


def test_digest_aggregates_spend():
    from tokenlens.digest import generate_digest
    from tokenlens.pricing import PricingTable
    from datetime import date, timedelta

    today = date.today()
    rows = []
    for i in range(7):
        d = (today - timedelta(days=i)).isoformat()
        rows.append({
            "date": d, "provider": "anthropic", "model": "claude-opus-4-6",
            "source": "claude-code", "call_count": 10, "input_tokens": 5000,
            "output_tokens": 1000, "cache_read_tokens": 2000, "cache_write_tokens": 0,
            "cost_usd": 6.0,
        })
    store = _make_store(daily_rows=rows)
    pricing = PricingTable()
    report = generate_digest(store=store, pricing=pricing, days=7)
    assert report["total_spend_usd"] == pytest.approx(42.0, rel=0.01)
    assert report["total_calls"] == 70
    assert len(report["top_sources"]) >= 1
    assert report["top_sources"][0]["source"] == "claude-code"


def test_digest_has_required_fields():
    from tokenlens.digest import generate_digest
    from tokenlens.pricing import PricingTable
    store = _make_store()
    pricing = PricingTable()
    report = generate_digest(store=store, pricing=pricing, days=7)
    required = [
        "total_spend_usd", "total_calls", "period_days",
        "top_sources", "waste_summary", "cache_hit_rate",
    ]
    for field in required:
        assert field in report, f"Missing field: {field}"


def test_digest_formats_as_human_text():
    from tokenlens.digest import generate_digest, format_digest_human
    from tokenlens.pricing import PricingTable
    store = _make_store()
    pricing = PricingTable()
    report = generate_digest(store=store, pricing=pricing, days=7)
    text = format_digest_human(report)
    assert "TokenLens" in text
    assert "Spend" in text


def test_digest_endpoint_exists(client):
    resp = client.get("/api/usage/digest?days=7")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_spend_usd" in data
