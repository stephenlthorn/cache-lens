import time
from datetime import date, datetime, timedelta, timezone

import pytest
from cachelens.store import UsageStore


@pytest.fixture
def store(tmp_path):
    return UsageStore(db_path=tmp_path / "test.db")


def _insert_call(store, *, ts=None, provider="anthropic", model="claude-sonnet-4-6",
                 source="claude-code", source_tag=None, input_tokens=100, output_tokens=50,
                 cache_read_tokens=0, cache_write_tokens=0, cost_usd=0.001,
                 endpoint="/v1/messages", request_hash="sha256:abc123"):
    store.insert_call(
        ts=ts if ts is not None else int(time.time()),
        provider=provider, model=model, source=source, source_tag=source_tag,
        input_tokens=input_tokens, output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens, cache_write_tokens=cache_write_tokens,
        cost_usd=cost_usd, endpoint=endpoint, request_hash=request_hash,
    )


def test_insert_call_and_retrieve_last_24h(store):
    _insert_call(store)
    count = store.raw_calls_last_24h()
    assert count == 1


def test_raw_calls_last_24h_returns_int(store):
    assert isinstance(store.raw_calls_last_24h(), int)
    _insert_call(store, request_hash="sha256:a")
    _insert_call(store, request_hash="sha256:b")
    assert store.raw_calls_last_24h() == 2


def test_raw_calls_last_24h_excludes_old(store):
    old_ts = int(time.time()) - 2 * 86400
    _insert_call(store, ts=old_ts, request_hash="sha256:old")
    assert store.raw_calls_last_24h() == 0


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
    _insert_call(store, ts=old_ts, request_hash="sha256:old")
    store.purge_raw_calls_older_than_days(1)
    assert store.raw_calls_last_24h() == 0


def test_rollup_bookkeeping(store):
    assert not store.rollup_done("nightly", "2026-03-10")
    store.mark_rollup_done("nightly", "2026-03-10")
    assert store.rollup_done("nightly", "2026-03-10")


def test_kpi_rolling_keys(store):
    now = int(time.time())
    _insert_call(store, ts=now, input_tokens=100, output_tokens=50,
                 cache_read_tokens=20, cache_write_tokens=10, cost_usd=0.001,
                 request_hash="sha256:x")
    kpi = store.kpi_rolling(days=1)
    assert kpi["total_cost_usd"] == pytest.approx(0.001)
    assert kpi["call_count"] == 1
    assert kpi["input_tokens"] == 100
    assert kpi["output_tokens"] == 50
    assert kpi["cache_read_tokens"] == 20
    assert kpi["cache_write_tokens"] == 10


def test_kpi_rolling_empty(store):
    kpi = store.kpi_rolling(days=1)
    assert kpi["total_cost_usd"] == 0.0
    assert kpi["call_count"] == 0
    assert kpi["cache_read_tokens"] == 0
    assert kpi["cache_write_tokens"] == 0


def test_purge_daily_agg_older_than_days(store):
    store.upsert_daily_agg(
        date="2020-01-01", provider="anthropic", model="claude-sonnet-4-6",
        source="claude-code", call_count=1, input_tokens=100,
        output_tokens=50, cache_read_tokens=0, cache_write_tokens=0,
        cost_usd=0.001,
    )
    store.upsert_daily_agg(
        date="2099-12-31", provider="anthropic", model="claude-sonnet-4-6",
        source="claude-code", call_count=1, input_tokens=100,
        output_tokens=50, cache_read_tokens=0, cache_write_tokens=0,
        cost_usd=0.001,
    )
    store.purge_daily_agg_older_than_days(1)
    assert store.daily_agg_for_date("2020-01-01") == []
    assert len(store.daily_agg_for_date("2099-12-31")) == 1


def test_aggregate_calls_for_date(store):
    day_ts = int(datetime.strptime("2025-01-01", "%Y-%m-%d").timestamp())  # 2025-01-01 00:00:00 local time
    _insert_call(store, ts=day_ts + 100, provider="anthropic", model="claude-sonnet-4-6",
                 source="app", input_tokens=200, output_tokens=100,
                 cache_read_tokens=50, cache_write_tokens=25, cost_usd=0.002,
                 request_hash="sha256:1")
    _insert_call(store, ts=day_ts + 200, provider="anthropic", model="claude-sonnet-4-6",
                 source="app", input_tokens=300, output_tokens=150,
                 cache_read_tokens=0, cache_write_tokens=0, cost_usd=0.003,
                 request_hash="sha256:2")
    rows = store.aggregate_calls_for_date("2025-01-01")
    assert len(rows) == 1
    row = rows[0]
    assert row["call_count"] == 2
    assert row["input_tokens"] == 500
    assert row["output_tokens"] == 250
    assert row["cache_read_tokens"] == 50
    assert row["cache_write_tokens"] == 25
    assert row["cost_usd"] == pytest.approx(0.005)


def test_aggregate_daily_for_year(store):
    store.upsert_daily_agg(
        date="2025-01-15", provider="openai", model="gpt-4o",
        source="app", call_count=3, input_tokens=300,
        output_tokens=150, cache_read_tokens=0, cache_write_tokens=0,
        cost_usd=0.03,
    )
    store.upsert_daily_agg(
        date="2025-06-20", provider="openai", model="gpt-4o",
        source="app", call_count=7, input_tokens=700,
        output_tokens=350, cache_read_tokens=100, cache_write_tokens=50,
        cost_usd=0.07,
    )
    rows = store.aggregate_daily_for_year(2025)
    assert len(rows) == 1
    row = rows[0]
    assert row["call_count"] == 10
    assert row["input_tokens"] == 1000
    assert row["cache_read_tokens"] == 100
    assert row["cache_write_tokens"] == 50


def test_last_rollup_time_none_when_no_rollup(store):
    result = store.last_rollup_time("nightly")
    assert result is None


def test_last_rollup_time_returns_datetime(store):
    before = datetime.now(tz=timezone.utc).replace(microsecond=0)
    store.mark_rollup_done("nightly", "2026-03-10")
    result = store.last_rollup_time("nightly")
    assert isinstance(result, datetime)
    assert result >= before
    assert result.tzinfo is not None


def test_db_size_bytes(store):
    size = store.db_size_bytes()
    assert isinstance(size, int)
    assert size > 0


def test_db_size_bytes_fresh_store_is_positive(tmp_path):
    s = UsageStore(db_path=tmp_path / "fresh.db")
    assert s.db_size_bytes() > 0


# ---------------------------------------------------------------------------
# kpi_rolling — historical daily_agg + today's live calls
# ---------------------------------------------------------------------------


def test_kpi_rolling_includes_yesterday_daily_agg(store):
    """kpi_rolling must count data from daily_agg, not just raw calls."""
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    store.upsert_daily_agg(
        date=yesterday, provider="anthropic", model="claude-sonnet-4-6",
        source="app", call_count=10, input_tokens=1000, output_tokens=500,
        cache_read_tokens=0, cache_write_tokens=0, cost_usd=0.10,
    )
    kpi = store.kpi_rolling(days=7)
    assert kpi["call_count"] == 10
    assert kpi["input_tokens"] == 1000
    assert kpi["total_cost_usd"] == pytest.approx(0.10)


def test_kpi_rolling_combines_daily_agg_and_live_calls(store):
    """kpi_rolling must combine yesterday's daily_agg with today's raw calls."""
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    store.upsert_daily_agg(
        date=yesterday, provider="anthropic", model="claude-sonnet-4-6",
        source="app", call_count=5, input_tokens=500, output_tokens=250,
        cache_read_tokens=0, cache_write_tokens=0, cost_usd=0.05,
    )
    # Today's raw call
    _insert_call(store, input_tokens=200, output_tokens=100, cost_usd=0.02,
                 request_hash="sha256:live1")
    kpi = store.kpi_rolling(days=7)
    assert kpi["call_count"] == 6
    assert kpi["input_tokens"] == 700
    assert kpi["total_cost_usd"] == pytest.approx(0.07)


# ---------------------------------------------------------------------------
# Settings (Phase 0)
# ---------------------------------------------------------------------------


def test_settings_get_returns_none_for_missing_key(store):
    assert store.get_setting("nonexistent") is None


def test_settings_set_and_get(store):
    store.set_setting("alerts.enabled", "true")
    assert store.get_setting("alerts.enabled") == "true"


def test_settings_upsert_overwrites(store):
    store.set_setting("alerts.threshold", "5.0")
    store.set_setting("alerts.threshold", "10.0")
    assert store.get_setting("alerts.threshold") == "10.0"


def test_settings_delete(store):
    store.set_setting("temp.key", "value")
    assert store.get_setting("temp.key") == "value"
    store.delete_setting("temp.key")
    assert store.get_setting("temp.key") is None


# ---------------------------------------------------------------------------
# Spend helpers (Phase 6-7)
# ---------------------------------------------------------------------------


def test_daily_spend_usd_includes_today(store):
    _insert_call(store, cost_usd=1.50, request_hash="sha256:d1")
    _insert_call(store, cost_usd=0.75, request_hash="sha256:d2")
    spend = store.daily_spend_usd()
    assert spend == pytest.approx(2.25)


def test_monthly_spend_usd_includes_today(store):
    _insert_call(store, cost_usd=3.00, request_hash="sha256:m1")
    spend = store.monthly_spend_usd()
    assert spend >= 3.0


# ---------------------------------------------------------------------------
# raw_calls_for_period (Phase 5)
# ---------------------------------------------------------------------------


def test_raw_calls_for_period_returns_recent(store):
    _insert_call(store, request_hash="sha256:r1")
    calls = store.raw_calls_for_period(1)
    assert len(calls) == 1


def test_raw_calls_for_period_filters_by_source(store):
    _insert_call(store, source="app-a", request_hash="sha256:s1")
    _insert_call(store, source="app-b", request_hash="sha256:s2")
    calls = store.raw_calls_for_period(1, source="app-a")
    assert len(calls) == 1
    assert calls[0]["source"] == "app-a"
