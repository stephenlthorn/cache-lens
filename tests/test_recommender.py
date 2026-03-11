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


def test_low_cache_hit_rate_not_for_openai(store):
    # OpenAI doesn't support prompt caching the same way
    _insert_daily(store, date_str=today_minus(1), provider="openai",
                  model="gpt-4o", source="myapp",
                  call_count=200, cache_read_tokens=0)
    recs = generate_recommendations(store)
    assert not any(r.type == "low_cache_hit_rate" for r in recs)


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
    # Only $0.50 spend — below $1.00 threshold
    _insert_daily(store, date_str=today_minus(1), provider="openai",
                  model="gpt-4o", source="myapp",
                  call_count=10, cost_usd=0.50)
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
