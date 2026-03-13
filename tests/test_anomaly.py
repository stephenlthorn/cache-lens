"""Tests for anomaly.py — cost anomaly detection."""
from unittest.mock import MagicMock


def _make_store_with_agg(rows):
    """Create a mock store with daily_agg rows."""
    store = MagicMock()
    store.query_daily_agg_since.return_value = rows
    store.aggregate_calls_for_date.return_value = []
    return store


def test_no_anomalies_with_stable_spend():
    from cachelens.anomaly import detect_anomalies
    from datetime import date, timedelta

    today = date.today()
    rows = []
    for i in range(14):
        d = (today - timedelta(days=i + 1)).isoformat()
        rows.append({
            "date": d, "provider": "anthropic", "model": "claude-sonnet-4-6",
            "source": "test", "call_count": 10, "input_tokens": 1000,
            "output_tokens": 200, "cache_read_tokens": 0, "cache_write_tokens": 0,
            "cost_usd": 1.0,
        })
    store = _make_store_with_agg(rows)
    anomalies = detect_anomalies(store=store, days=14)
    assert anomalies == []


def test_detects_spend_spike():
    from cachelens.anomaly import detect_anomalies
    from datetime import date, timedelta

    today = date.today()
    rows = []
    # 13 normal days
    for i in range(13):
        d = (today - timedelta(days=i + 1)).isoformat()
        rows.append({
            "date": d, "provider": "anthropic", "model": "claude-sonnet-4-6",
            "source": "test", "call_count": 10, "input_tokens": 1000,
            "output_tokens": 200, "cache_read_tokens": 0, "cache_write_tokens": 0,
            "cost_usd": 1.0,
        })
    # 1 spike day
    spike_date = (today - timedelta(days=1)).isoformat()
    rows[0] = {
        "date": spike_date, "provider": "anthropic", "model": "claude-sonnet-4-6",
        "source": "test", "call_count": 10, "input_tokens": 1000,
        "output_tokens": 200, "cache_read_tokens": 0, "cache_write_tokens": 0,
        "cost_usd": 20.0,  # way above normal
    }
    store = _make_store_with_agg(rows)
    anomalies = detect_anomalies(store=store, days=14)
    assert len(anomalies) >= 1
    assert any(a["date"] == spike_date for a in anomalies)


def test_detects_call_count_spike():
    """Call count spike (> 2x normal) should also be flagged."""
    from cachelens.anomaly import detect_anomalies
    from datetime import date, timedelta

    today = date.today()
    rows = []
    for i in range(13):
        d = (today - timedelta(days=i + 1)).isoformat()
        rows.append({
            "date": d, "provider": "anthropic", "model": "claude-sonnet-4-6",
            "source": "test", "call_count": 10, "input_tokens": 1000,
            "output_tokens": 200, "cache_read_tokens": 0, "cache_write_tokens": 0,
            "cost_usd": 1.0,
        })
    spike_date = (today - timedelta(days=1)).isoformat()
    rows[0] = {
        "date": spike_date, "provider": "anthropic", "model": "claude-sonnet-4-6",
        "source": "test", "call_count": 80, "input_tokens": 8000,  # 8x normal calls
        "output_tokens": 1600, "cache_read_tokens": 0, "cache_write_tokens": 0,
        "cost_usd": 1.5,  # spend barely changed (cheap burst)
    }
    store = _make_store_with_agg(rows)
    anomalies = detect_anomalies(store=store, days=14)
    assert any(a["date"] == spike_date and a.get("anomaly_type") == "call_count_spike"
               for a in anomalies)


def test_detects_token_spike():
    """Avg token spike (> 2x normal input_tokens/call) should be flagged."""
    from cachelens.anomaly import detect_anomalies
    from datetime import date, timedelta

    today = date.today()
    rows = []
    for i in range(13):
        d = (today - timedelta(days=i + 1)).isoformat()
        rows.append({
            "date": d, "provider": "anthropic", "model": "claude-sonnet-4-6",
            "source": "test", "call_count": 10, "input_tokens": 1000,
            "output_tokens": 200, "cache_read_tokens": 0, "cache_write_tokens": 0,
            "cost_usd": 1.0,
        })
    spike_date = (today - timedelta(days=1)).isoformat()
    rows[0] = {
        "date": spike_date, "provider": "anthropic", "model": "claude-sonnet-4-6",
        "source": "test", "call_count": 10, "input_tokens": 50000,  # 50x tokens/call
        "output_tokens": 200, "cache_read_tokens": 0, "cache_write_tokens": 0,
        "cost_usd": 2.0,
    }
    store = _make_store_with_agg(rows)
    anomalies = detect_anomalies(store=store, days=14)
    assert any(a["date"] == spike_date and a.get("anomaly_type") == "token_spike"
               for a in anomalies)


def test_anomaly_has_required_fields():
    from cachelens.anomaly import detect_anomalies
    from datetime import date, timedelta

    today = date.today()
    rows = []
    for i in range(13):
        d = (today - timedelta(days=i + 1)).isoformat()
        rows.append({
            "date": d, "provider": "anthropic", "model": "claude-sonnet-4-6",
            "source": "test", "call_count": 10, "input_tokens": 1000,
            "output_tokens": 200, "cache_read_tokens": 0, "cache_write_tokens": 0,
            "cost_usd": 1.0,
        })
    rows[0]["cost_usd"] = 15.0

    store = _make_store_with_agg(rows)
    anomalies = detect_anomalies(store=store, days=14)
    assert len(anomalies) >= 1, "Expected at least one anomaly for field check"
    a = anomalies[0]
    assert "date" in a
    assert "source" in a
    assert "spend_usd" in a
    assert "expected_usd" in a
    assert "stddev" in a
    assert "anomaly_type" in a
    assert "top_models" in a  # drill-down


def test_anomaly_drill_down_fields():
    """Anomaly result must include drill-down: top_models and call_count."""
    from cachelens.anomaly import detect_anomalies
    from datetime import date, timedelta

    today = date.today()
    rows = []
    for i in range(13):
        d = (today - timedelta(days=i + 1)).isoformat()
        rows.append({
            "date": d, "provider": "anthropic", "model": "claude-sonnet-4-6",
            "source": "test", "call_count": 10, "input_tokens": 1000,
            "output_tokens": 200, "cache_read_tokens": 0, "cache_write_tokens": 0,
            "cost_usd": 1.0,
        })
    rows[0]["cost_usd"] = 15.0
    store = _make_store_with_agg(rows)
    anomalies = detect_anomalies(store=store, days=14)
    assert len(anomalies) >= 1, "Expected at least one anomaly for drill-down field check"
    a = anomalies[0]
    assert isinstance(a.get("top_models"), list)
    assert "call_count" in a


def test_insufficient_data_returns_empty():
    from cachelens.anomaly import detect_anomalies
    # Need at least 7 days of data to detect anomalies
    store = _make_store_with_agg([])
    anomalies = detect_anomalies(store=store, days=14)
    assert anomalies == []
