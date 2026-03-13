"""Tests for per-session cost tracking (Phase 5)."""
import time

from tokenlens.sessions import detect_sessions


def _call(ts, source="claude-code", model="claude-sonnet-4-6", cost=0.01):
    return {
        "ts": ts,
        "source": source,
        "model": model,
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_read_tokens": 0,
        "cost_usd": cost,
    }


def test_empty_calls_returns_empty():
    assert detect_sessions([]) == []


def test_single_call_produces_one_session():
    sessions = detect_sessions([_call(1000)])
    assert len(sessions) == 1
    assert sessions[0]["call_count"] == 1
    assert sessions[0]["source"] == "claude-code"


def test_two_calls_within_gap_same_session():
    sessions = detect_sessions([
        _call(1000),
        _call(1500),  # 500s gap, within 1800s default
    ])
    assert len(sessions) == 1
    assert sessions[0]["call_count"] == 2


def test_two_calls_exceed_gap_two_sessions():
    sessions = detect_sessions([
        _call(1000),
        _call(3000),  # 2000s gap, exceeds 1800s default
    ])
    assert len(sessions) == 2


def test_different_sources_separate_sessions():
    sessions = detect_sessions([
        _call(1000, source="app1"),
        _call(1100, source="app2"),
    ])
    assert len(sessions) == 2
    sources = {s["source"] for s in sessions}
    assert sources == {"app1", "app2"}


def test_custom_gap_seconds():
    sessions = detect_sessions([
        _call(1000),
        _call(1500),  # 500s gap
    ], gap_seconds=300)
    # 500s > 300s gap, so two sessions
    assert len(sessions) == 2


def test_session_cost_aggregated():
    sessions = detect_sessions([
        _call(1000, cost=0.05),
        _call(1100, cost=0.10),
    ])
    assert len(sessions) == 1
    assert abs(sessions[0]["total_cost_usd"] - 0.15) < 1e-9


def test_session_models_tracked():
    sessions = detect_sessions([
        _call(1000, model="claude-sonnet-4-6"),
        _call(1100, model="claude-haiku-4-5"),
    ])
    assert len(sessions) == 1
    assert set(sessions[0]["models"]) == {"claude-sonnet-4-6", "claude-haiku-4-5"}


def test_sessions_sorted_most_recent_first():
    sessions = detect_sessions([
        _call(1000, source="app1"),
        _call(5000, source="app1"),
    ], gap_seconds=100)
    # Two sessions for app1: ts=1000 and ts=5000
    assert len(sessions) == 2
    assert sessions[0]["start_ts"] > sessions[1]["start_ts"]


def test_session_has_required_fields():
    sessions = detect_sessions([_call(1000)])
    s = sessions[0]
    assert "session_id" in s
    assert "source" in s
    assert "start_ts" in s
    assert "end_ts" in s
    assert "duration_minutes" in s
    assert "call_count" in s
    assert "total_cost_usd" in s
    assert "models" in s
    assert "input_tokens" in s
    assert "output_tokens" in s
