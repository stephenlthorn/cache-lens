"""Tests for right_sizing.py — model complexity analysis."""
from unittest.mock import MagicMock


def test_simple_call_classified_simple():
    from tokenlens.right_sizing import score_complexity
    call = {
        "input_tokens": 100, "output_tokens": 50,
        "message_count": 2, "token_heatmap": None,
    }
    score = score_complexity(call)
    assert score <= 2  # simple


def test_complex_call_classified_complex():
    from tokenlens.right_sizing import score_complexity
    call = {
        "input_tokens": 5000, "output_tokens": 800,
        "message_count": 8, "token_heatmap": '{"tool_definitions": 1000}',
    }
    score = score_complexity(call)
    assert score >= 5  # complex


def test_moderate_call_classification():
    from tokenlens.right_sizing import score_complexity
    call = {
        "input_tokens": 2500, "output_tokens": 400,
        "message_count": 4, "token_heatmap": None,
    }
    score = score_complexity(call)
    assert 3 <= score <= 4  # moderate


def test_right_sizing_report_structure():
    from tokenlens.right_sizing import analyze_right_sizing
    from tokenlens.pricing import PricingTable

    pricing = PricingTable()
    store = MagicMock()
    store.recent_calls_with_features.return_value = [
        {
            "source": "myapp", "model": "claude-opus-4-6", "provider": "anthropic",
            "input_tokens": 100, "output_tokens": 50, "cost_usd": 0.10,
            "message_count": 2, "token_heatmap": None,
        }
        for _ in range(10)
    ]

    report = analyze_right_sizing(store=store, pricing=pricing, days=30)
    assert isinstance(report, list)
    if report:
        item = report[0]
        assert "source" in item
        assert "model" in item
        assert "simple_pct" in item
        assert "estimated_savings_usd" in item


def test_right_sizing_no_savings_for_haiku():
    from tokenlens.right_sizing import analyze_right_sizing
    from tokenlens.pricing import PricingTable

    pricing = PricingTable()
    store = MagicMock()
    store.recent_calls_with_features.return_value = [
        {
            "source": "app", "model": "claude-haiku-4-5", "provider": "anthropic",
            "input_tokens": 100, "output_tokens": 30, "cost_usd": 0.001,
            "message_count": 2, "token_heatmap": None,
        }
        for _ in range(5)
    ]
    report = analyze_right_sizing(store=store, pricing=pricing, days=30)
    # Haiku is already cheapest — no downgrade possible
    haiku_items = [r for r in report if r["model"] == "claude-haiku-4-5"]
    assert all(item["estimated_savings_usd"] == 0 for item in haiku_items)
