import time
import pytest
from cachelens.store import UsageStore

@pytest.fixture
def store(tmp_path):
    return UsageStore(db_path=tmp_path / "test.db")

def test_insert_call_and_retrieve_today(store):
    store.insert_call(
        ts=int(time.time()),
        provider="anthropic", model="claude-sonnet-4-6",
        source="claude-code", source_tag=None,
        input_tokens=100, output_tokens=50,
        cache_read_tokens=0, cache_write_tokens=0,
        cost_usd=0.001, endpoint="/v1/messages",
        request_hash="sha256:abc123",
    )
    rows = store.raw_calls_today()
    assert len(rows) == 1
    assert rows[0]["provider"] == "anthropic"
    assert rows[0]["source"] == "claude-code"

def test_daily_agg_upsert(store):
    store.upsert_daily_agg(
        date="2026-03-10", provider="anthropic", model="claude-sonnet-4-6",
        source="claude-code", call_count=5, input_tokens=5000,
        output_tokens=1000, cache_read_tokens=2000, cache_write_tokens=0,
        cost_usd=0.05,
    )
    # Re-upsert same key should replace
    store.upsert_daily_agg(
        date="2026-03-10", provider="anthropic", model="claude-sonnet-4-6",
        source="claude-code", call_count=10, input_tokens=10000,
        output_tokens=2000, cache_read_tokens=4000, cache_write_tokens=0,
        cost_usd=0.10,
    )
    rows = store.daily_agg_for_date("2026-03-10")
    assert len(rows) == 1
    assert rows[0]["call_count"] == 10

def test_yearly_agg_upsert(store):
    store.upsert_yearly_agg(
        year=2025, provider="openai", model="gpt-4o",
        source="myapp", call_count=100, input_tokens=100000,
        output_tokens=20000, cache_read_tokens=0, cache_write_tokens=0,
        cost_usd=1.0,
    )
    rows = store.yearly_agg_for_year(2025)
    assert len(rows) == 1
    assert rows[0]["model"] == "gpt-4o"

def test_purge_raw_calls_older_than(store):
    old_ts = int(time.time()) - 2 * 86400  # 2 days ago
    store.insert_call(ts=old_ts, provider="anthropic", model="x",
        source="y", source_tag=None, input_tokens=1, output_tokens=1,
        cache_read_tokens=0, cache_write_tokens=0, cost_usd=0.0,
        endpoint="/v1/messages", request_hash="sha256:old")
    store.purge_raw_calls_older_than_days(1)
    assert store.raw_calls_today() == []

def test_rollup_bookkeeping(store):
    assert not store.rollup_done("nightly", "2026-03-10")
    store.mark_rollup_done("nightly", "2026-03-10")
    assert store.rollup_done("nightly", "2026-03-10")

def test_kpi_rolling(store):
    now = int(time.time())
    store.insert_call(ts=now, provider="anthropic", model="claude-sonnet-4-6",
        source="claude-code", source_tag=None, input_tokens=100, output_tokens=50,
        cache_read_tokens=0, cache_write_tokens=0, cost_usd=0.001,
        endpoint="/v1/messages", request_hash="sha256:x")
    kpi = store.kpi_rolling(days=1)
    assert kpi["cost_usd"] == pytest.approx(0.001)
    assert kpi["call_count"] == 1
