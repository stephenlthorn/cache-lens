# tests/test_quotas.py
"""Tests for quota enforcement logic."""
import pytest

from tokenlens.quotas import QuotaResult, check_quotas


def _make_config(source_limits=None, model_limits=None, kill_switches=None):
    """Helper to build quota config dict."""
    return {
        "source_limits": source_limits or {},
        "model_limits": model_limits or {},
        "kill_switches": kill_switches or [],
    }


# --- Kill switch ---

def test_kill_switch_blocks_source():
    config = _make_config(kill_switches=["my-agent"])
    result = check_quotas(
        config=config, source="my-agent", model="claude-sonnet-4-6",
        source_daily_spend=0.0, source_monthly_spend=0.0, model_calls_today=0,
    )
    assert not result.allowed
    assert result.reason == "source paused via kill switch"
    assert result.retry_after == 3600


def test_kill_switch_allows_other_source():
    config = _make_config(kill_switches=["blocked-agent"])
    result = check_quotas(
        config=config, source="ok-agent", model="claude-sonnet-4-6",
        source_daily_spend=0.0, source_monthly_spend=0.0, model_calls_today=0,
    )
    assert result.allowed


# --- Per-source spend caps ---

def test_source_daily_cap_blocks():
    config = _make_config(source_limits={
        "my-agent": {"daily_limit_usd": 50.0},
    })
    result = check_quotas(
        config=config, source="my-agent", model="claude-sonnet-4-6",
        source_daily_spend=50.01, source_monthly_spend=50.01, model_calls_today=0,
    )
    assert not result.allowed
    assert "daily" in result.reason


def test_source_daily_cap_allows_under():
    config = _make_config(source_limits={
        "my-agent": {"daily_limit_usd": 50.0},
    })
    result = check_quotas(
        config=config, source="my-agent", model="claude-sonnet-4-6",
        source_daily_spend=49.99, source_monthly_spend=49.99, model_calls_today=0,
    )
    assert result.allowed


def test_source_monthly_cap_blocks():
    config = _make_config(source_limits={
        "my-agent": {"monthly_limit_usd": 200.0},
    })
    result = check_quotas(
        config=config, source="my-agent", model="claude-sonnet-4-6",
        source_daily_spend=5.0, source_monthly_spend=200.50, model_calls_today=0,
    )
    assert not result.allowed
    assert "monthly" in result.reason


# --- Per-model call caps ---

def test_model_daily_call_cap_blocks():
    config = _make_config(model_limits={
        "claude-opus-4-6": {"daily_call_limit": 100},
    })
    result = check_quotas(
        config=config, source="my-agent", model="claude-opus-4-6",
        source_daily_spend=0.0, source_monthly_spend=0.0, model_calls_today=100,
    )
    assert not result.allowed
    assert "model" in result.reason


def test_model_daily_call_cap_allows_under():
    config = _make_config(model_limits={
        "claude-opus-4-6": {"daily_call_limit": 100},
    })
    result = check_quotas(
        config=config, source="my-agent", model="claude-opus-4-6",
        source_daily_spend=0.0, source_monthly_spend=0.0, model_calls_today=99,
    )
    assert result.allowed


# --- No config = allow everything ---

def test_no_config_allows():
    config = _make_config()
    result = check_quotas(
        config=config, source="anything", model="anything",
        source_daily_spend=999.0, source_monthly_spend=9999.0, model_calls_today=9999,
    )
    assert result.allowed


# --- Unconfigured source/model passthrough ---

def test_unconfigured_source_passes():
    config = _make_config(source_limits={
        "other-agent": {"daily_limit_usd": 1.0},
    })
    result = check_quotas(
        config=config, source="my-agent", model="claude-sonnet-4-6",
        source_daily_spend=999.0, source_monthly_spend=999.0, model_calls_today=0,
    )
    assert result.allowed
