import time

import pytest
from datetime import date, timedelta
from cachelens.store import UsageStore
from cachelens.recommender import generate_recommendations, Recommendation


@pytest.fixture
def store(tmp_path):
    return UsageStore(tmp_path / "test.db")


def _insert_daily(store, *, date_str, provider, model, source,
                  call_count=1, input_tokens=1000, output_tokens=500,
                  cache_read_tokens=0, cache_write_tokens=0, cost_usd=1.0):
    store.upsert_daily_agg(
        date=date_str, provider=provider, model=model, source=source,
        call_count=call_count, input_tokens=input_tokens, output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens, cache_write_tokens=cache_write_tokens,
        cost_usd=cost_usd,
    )


def today_minus(days): return (date.today() - timedelta(days=days)).isoformat()


def test_no_data_returns_empty(store):
    assert generate_recommendations(store) == []


def test_low_cache_hit_rate_detected(store):
    # 200 anthropic calls with 0 cache reads
    _insert_daily(store, date_str=today_minus(1), provider="anthropic",
                  model="claude-sonnet-4-6", source="myapp",
                  call_count=200, cache_read_tokens=0, cache_write_tokens=0)
    recs = generate_recommendations(store)
    types = [r.type for r in recs]
    assert "low_cache_hit_rate" in types


def test_low_cache_hit_rate_requires_100_calls(store):
    # Only 50 calls — below threshold
    _insert_daily(store, date_str=today_minus(1), provider="anthropic",
                  model="claude-sonnet-4-6", source="myapp",
                  call_count=50, cache_read_tokens=0)
    recs = generate_recommendations(store)
    assert not any(r.type == "low_cache_hit_rate" for r in recs)


def test_low_cache_hit_rate_works_for_openai(store):
    # Now works for all providers (not just Anthropic)
    _insert_daily(store, date_str=today_minus(1), provider="openai",
                  model="gpt-4o", source="myapp",
                  call_count=200, cache_read_tokens=0)
    recs = generate_recommendations(store)
    assert any(r.type == "low_cache_hit_rate" for r in recs)


def test_cache_write_waste_detected(store):
    _insert_daily(store, date_str=today_minus(1), provider="anthropic",
                  model="claude-sonnet-4-6", source="wasteful-app",
                  cache_write_tokens=50000, cache_read_tokens=0)
    recs = generate_recommendations(store)
    assert any(r.type == "cache_write_waste" for r in recs)


def test_cache_write_waste_not_triggered_if_reads_present(store):
    _insert_daily(store, date_str=today_minus(1), provider="anthropic",
                  model="claude-sonnet-4-6", source="good-app",
                  cache_write_tokens=50000, cache_read_tokens=10000)
    recs = generate_recommendations(store)
    assert not any(r.type == "cache_write_waste" for r in recs)


def test_downsell_gpt4o_to_mini(store):
    _insert_daily(store, date_str=today_minus(1), provider="openai",
                  model="gpt-4o", source="myapp",
                  call_count=100, cost_usd=5.0)
    recs = generate_recommendations(store)
    assert any(r.type == "downsell_opportunity" for r in recs)


def test_downsell_requires_min_spend(store):
    # Only $0.40 spend — below $0.50 threshold
    _insert_daily(store, date_str=today_minus(1), provider="openai",
                  model="gpt-4o", source="myapp",
                  call_count=10, cost_usd=0.40)
    recs = generate_recommendations(store)
    assert not any(r.type == "downsell_opportunity" for r in recs)


def test_recommendations_ranked_high_before_medium(store):
    # Insert data for both high and medium impact findings
    _insert_daily(store, date_str=today_minus(1), provider="anthropic",
                  model="claude-sonnet-4-6", source="app1",
                  call_count=2000, cache_read_tokens=0)  # high impact (>1000 calls)
    _insert_daily(store, date_str=today_minus(1), provider="anthropic",
                  model="claude-sonnet-4-6", source="app2",
                  call_count=150, cache_read_tokens=0)   # medium impact
    recs = generate_recommendations(store)
    assert len(recs) >= 2
    assert recs[0].estimated_impact == "high"
    assert recs[1].estimated_impact == "medium"


def test_recommendation_has_deep_dive_link(store):
    _insert_daily(store, date_str=today_minus(1), provider="anthropic",
                  model="claude-sonnet-4-6", source="myapp",
                  call_count=200, cache_read_tokens=0)
    recs = generate_recommendations(store)
    for r in recs:
        assert r.deep_dive_link  # non-empty
        assert "?" in r.deep_dive_link  # has query params


def test_recommendation_has_id(store):
    _insert_daily(store, date_str=today_minus(1), provider="anthropic",
                  model="claude-sonnet-4-6", source="myapp",
                  call_count=200, cache_read_tokens=0)
    recs = generate_recommendations(store)
    for r in recs:
        assert r.id  # non-empty


def test_only_checks_last_30_days(store):
    # Insert data from 31 days ago — should NOT trigger recommendations
    _insert_daily(store, date_str=today_minus(31), provider="anthropic",
                  model="claude-sonnet-4-6", source="old-app",
                  call_count=500, cache_read_tokens=0)
    recs = generate_recommendations(store)
    assert not any(r.type == "low_cache_hit_rate" for r in recs)


def test_recommendations_include_todays_live_calls(store):
    """generate_recommendations must include today's raw calls before nightly rollup."""
    # Insert 150 raw calls today (no rollup, so nothing in daily_agg yet)
    for i in range(150):
        store.insert_call(
            ts=int(time.time()),
            provider="anthropic",
            model="claude-sonnet-4-6",
            source="live-app",
            source_tag=None,
            input_tokens=100,
            output_tokens=50,
            cache_read_tokens=0,
            cache_write_tokens=0,
            cost_usd=0.001,
            endpoint="/v1/messages",
            request_hash=f"sha256:live-{i}",
        )
    recs = generate_recommendations(store)
    assert any(r.type == "low_cache_hit_rate" for r in recs)


def test_includes_data_from_exactly_30_days_ago(store):
    _insert_daily(store, date_str=today_minus(30), provider="anthropic",
                  model="claude-sonnet-4-6", source="boundary-app",
                  call_count=200, cache_read_tokens=0)
    recs = generate_recommendations(store)
    assert any(r.type == "low_cache_hit_rate" for r in recs)


# ---------------------------------------------------------------------------
# New recommendation checks (Phase 1)
# ---------------------------------------------------------------------------


def test_low_cache_hit_rate_below_50pct(store):
    """Hit rate below 50% triggers recommendation."""
    _insert_daily(store, date_str=today_minus(1), provider="anthropic",
                  model="claude-sonnet-4-6", source="app",
                  call_count=200, input_tokens=10000, cache_read_tokens=4000)
    recs = generate_recommendations(store)
    # 4000 / (4000+10000) = 28.6% — should trigger
    assert any(r.type == "low_cache_hit_rate" for r in recs)


def test_low_cache_hit_rate_above_50pct_no_trigger(store):
    """Hit rate above 50% does NOT trigger."""
    _insert_daily(store, date_str=today_minus(1), provider="anthropic",
                  model="claude-sonnet-4-6", source="app",
                  call_count=200, input_tokens=5000, cache_read_tokens=6000)
    recs = generate_recommendations(store)
    # 6000 / (6000+5000) = 54.5% — should NOT trigger
    assert not any(r.type == "low_cache_hit_rate" for r in recs)


def test_bloated_prompts_detected(store):
    """High input/output ratio triggers bloated_prompts."""
    _insert_daily(store, date_str=today_minus(1), provider="anthropic",
                  model="claude-sonnet-4-6", source="verbose-app",
                  call_count=100, input_tokens=100000, output_tokens=2000)
    recs = generate_recommendations(store)
    assert any(r.type == "bloated_prompts" for r in recs)


def test_bloated_prompts_normal_ratio(store):
    """Normal input/output ratio does NOT trigger."""
    _insert_daily(store, date_str=today_minus(1), provider="anthropic",
                  model="claude-sonnet-4-6", source="normal-app",
                  call_count=100, input_tokens=10000, output_tokens=5000)
    recs = generate_recommendations(store)
    assert not any(r.type == "bloated_prompts" for r in recs)


def test_caching_opportunity_openai(store):
    """OpenAI with high calls and zero cache reads triggers caching_opportunity."""
    _insert_daily(store, date_str=today_minus(1), provider="openai",
                  model="gpt-4o", source="myapp",
                  call_count=300, cache_read_tokens=0)
    recs = generate_recommendations(store)
    assert any(r.type == "caching_opportunity" for r in recs)


def test_caching_opportunity_not_for_anthropic(store):
    """Anthropic does NOT trigger caching_opportunity (only openai/google)."""
    _insert_daily(store, date_str=today_minus(1), provider="anthropic",
                  model="claude-sonnet-4-6", source="myapp",
                  call_count=300, cache_read_tokens=0)
    recs = generate_recommendations(store)
    assert not any(r.type == "caching_opportunity" for r in recs)


def test_source_consolidation(store):
    """3+ sources on same provider/model triggers source_consolidation."""
    for src in ["app1", "app2", "app3"]:
        _insert_daily(store, date_str=today_minus(1), provider="anthropic",
                      model="claude-sonnet-4-6", source=src,
                      call_count=10, cost_usd=0.10)
    recs = generate_recommendations(store)
    assert any(r.type == "source_consolidation" for r in recs)


def test_source_consolidation_not_with_2(store):
    """Only 2 sources does NOT trigger."""
    for src in ["app1", "app2"]:
        _insert_daily(store, date_str=today_minus(1), provider="anthropic",
                      model="claude-sonnet-4-6", source=src,
                      call_count=10, cost_usd=0.10)
    recs = generate_recommendations(store)
    assert not any(r.type == "source_consolidation" for r in recs)


def test_cache_write_waste_low_ratio(store):
    """Read/write ratio < 10% triggers cache_write_waste."""
    _insert_daily(store, date_str=today_minus(1), provider="anthropic",
                  model="claude-sonnet-4-6", source="waste-app",
                  cache_write_tokens=100000, cache_read_tokens=5000)
    recs = generate_recommendations(store)
    assert any(r.type == "cache_write_waste" for r in recs)


def test_downsell_at_threshold(store):
    """$0.50 spend meets the $0.50 threshold for downsell."""
    _insert_daily(store, date_str=today_minus(1), provider="openai",
                  model="gpt-4o", source="myapp",
                  call_count=10, cost_usd=0.50)
    recs = generate_recommendations(store)
    assert any(r.type == "downsell_opportunity" for r in recs)


# ---------------------------------------------------------------------------
# history_bloat recommendation tests
# ---------------------------------------------------------------------------


def test_history_bloat_recommendation_triggers(store: UsageStore):
    """history_bloat triggers when avg_history_ratio > 0.6 and call_count >= 5."""
    now = int(time.time())
    for i in range(5):
        store.insert_call(
            ts=now - i * 60, provider="anthropic", model="claude-sonnet-4-6",
            source="chatbot", source_tag=None,
            input_tokens=1000, output_tokens=200,
            cache_read_tokens=0, cache_write_tokens=0,
            cost_usd=0.01, endpoint="/v1/messages",
            request_hash=f"hb_trigger{i}",
            message_count=10,
            history_tokens=700,
            history_ratio=0.7,
        )
    recs = generate_recommendations(store)
    history_recs = [r for r in recs if r.type == "history_bloat"]
    assert len(history_recs) >= 1
    assert history_recs[0].metrics["source"] == "chatbot"


def test_history_bloat_recommendation_below_threshold(store: UsageStore):
    """history_bloat does not trigger when avg_history_ratio <= 0.6."""
    now = int(time.time())
    for i in range(5):
        store.insert_call(
            ts=now - i * 60, provider="anthropic", model="claude-sonnet-4-6",
            source="chatbot", source_tag=None,
            input_tokens=1000, output_tokens=200,
            cache_read_tokens=0, cache_write_tokens=0,
            cost_usd=0.01, endpoint="/v1/messages",
            request_hash=f"hb_low{i}",
            message_count=10,
            history_tokens=400,
            history_ratio=0.4,
        )
    recs = generate_recommendations(store)
    history_recs = [r for r in recs if r.type == "history_bloat"]
    assert len(history_recs) == 0


def test_history_bloat_recommendation_insufficient_calls(store: UsageStore):
    """history_bloat does not trigger when call_count < 5."""
    now = int(time.time())
    for i in range(4):  # only 4 calls, below the 5-call minimum
        store.insert_call(
            ts=now - i * 60, provider="anthropic", model="claude-sonnet-4-6",
            source="chatbot", source_tag=None,
            input_tokens=1000, output_tokens=200,
            cache_read_tokens=0, cache_write_tokens=0,
            cost_usd=0.01, endpoint="/v1/messages",
            request_hash=f"hb_few{i}",
            message_count=10,
            history_tokens=700,
            history_ratio=0.7,
        )
    recs = generate_recommendations(store)
    history_recs = [r for r in recs if r.type == "history_bloat"]
    assert len(history_recs) == 0
