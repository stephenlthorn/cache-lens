import time
from datetime import date, datetime, timedelta, timezone

import pytest
from tokenlens.store import UsageStore


@pytest.fixture
def store(tmp_path):
    return UsageStore(db_path=tmp_path / "test.db")


def _insert_call(store, *, ts=None, provider="anthropic", model="claude-sonnet-4-6",
                 source="claude-code", source_tag=None, input_tokens=100, output_tokens=50,
                 cache_read_tokens=0, cache_write_tokens=0, cost_usd=0.001,
                 endpoint="/v1/messages", request_hash="sha256:abc123",
                 latency_ms=None, status_code=None):
    return store.insert_call(
        ts=ts if ts is not None else int(time.time()),
        provider=provider, model=model, source=source, source_tag=source_tag,
        input_tokens=input_tokens, output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens, cache_write_tokens=cache_write_tokens,
        cost_usd=cost_usd, endpoint=endpoint, request_hash=request_hash,
        latency_ms=latency_ms, status_code=status_code,
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


# ---------------------------------------------------------------------------
# Schema migration: latency_ms + status_code (P0)
# ---------------------------------------------------------------------------


def test_insert_call_returns_row_id(store):
    row_id = _insert_call(store, request_hash="sha256:rid1")
    assert isinstance(row_id, int)
    assert row_id >= 1


def test_insert_call_with_latency_and_status(store):
    store.insert_call(
        ts=int(time.time()), provider="anthropic", model="claude-sonnet-4-6",
        source="test", source_tag=None, input_tokens=100, output_tokens=50,
        cache_read_tokens=0, cache_write_tokens=0, cost_usd=0.001,
        endpoint="/v1/messages", request_hash="sha256:ls1",
        latency_ms=142.5, status_code=200,
    )
    calls = store.raw_calls_for_period(1)
    assert len(calls) == 1
    assert calls[0]["latency_ms"] == pytest.approx(142.5)
    assert calls[0]["status_code"] == 200


def test_insert_call_without_latency_defaults_none(store):
    _insert_call(store, request_hash="sha256:nolat")
    calls = store.raw_calls_for_period(1)
    assert calls[0]["latency_ms"] is None
    assert calls[0]["status_code"] is None


# ---------------------------------------------------------------------------
# Cost Allocation Tags (Feature 2)
# ---------------------------------------------------------------------------


def test_query_by_tag_empty(store):
    """query_by_tag on empty store returns empty list."""
    result = store.query_by_tag(days=30)
    assert result == []


def test_query_by_tag_groups_by_source(store):
    """query_by_tag groups daily_agg rows by source and sums metrics."""
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    store.upsert_daily_agg(
        date=yesterday, provider="anthropic", model="claude-sonnet-4-6",
        source="team-alpha", call_count=10, input_tokens=1000,
        output_tokens=500, cache_read_tokens=200, cache_write_tokens=50,
        cost_usd=0.10,
    )
    store.upsert_daily_agg(
        date=yesterday, provider="anthropic", model="claude-sonnet-4-6",
        source="team-beta", call_count=5, input_tokens=600,
        output_tokens=300, cache_read_tokens=100, cache_write_tokens=25,
        cost_usd=0.06,
    )
    result = store.query_by_tag(days=7)
    assert len(result) == 2
    by_source = {r["source"]: r for r in result}
    assert "team-alpha" in by_source
    assert "team-beta" in by_source
    assert by_source["team-alpha"]["call_count"] == 10
    assert by_source["team-alpha"]["input_tokens"] == 1000
    assert by_source["team-alpha"]["cost_usd"] == pytest.approx(0.10)
    assert by_source["team-beta"]["call_count"] == 5
    assert by_source["team-beta"]["cost_usd"] == pytest.approx(0.06)


def test_query_by_tag_includes_today(store):
    """query_by_tag includes today's live raw calls in the totals."""
    _insert_call(store, source="team-gamma", input_tokens=200,
                 output_tokens=100, cache_read_tokens=50,
                 cache_write_tokens=10, cost_usd=0.02,
                 request_hash="sha256:tag1")
    _insert_call(store, source="team-gamma", input_tokens=300,
                 output_tokens=150, cache_read_tokens=75,
                 cache_write_tokens=20, cost_usd=0.03,
                 request_hash="sha256:tag2")
    result = store.query_by_tag(days=1)
    assert len(result) == 1
    row = result[0]
    assert row["source"] == "team-gamma"
    assert row["call_count"] == 2
    assert row["input_tokens"] == 500
    assert row["output_tokens"] == 250
    assert row["cache_read_tokens"] == 125
    assert row["cache_write_tokens"] == 30
    assert row["cost_usd"] == pytest.approx(0.05)


# ---------------------------------------------------------------------------
# Provider Health (Feature 5)
# ---------------------------------------------------------------------------


def test_provider_health_empty(store):
    result = store.provider_health(days=1)
    assert result == []


def test_provider_health_with_data(store):
    now = int(time.time())
    latencies = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]
    for i, lat in enumerate(latencies):
        _insert_call(
            store, ts=now - i, provider="anthropic",
            request_hash=f"sha256:health-{i}",
            latency_ms=lat, status_code=200,
        )
    result = store.provider_health(days=1)
    assert len(result) == 1
    row = result[0]
    assert row["provider"] == "anthropic"
    assert row["total_calls"] == 10
    assert row["error_count"] == 0
    assert row["error_rate"] == 0.0
    assert row["p50_ms"] == pytest.approx(55.0)
    assert row["p95_ms"] == pytest.approx(95.5)
    assert row["p99_ms"] == pytest.approx(99.1)


def test_provider_health_error_rate(store):
    now = int(time.time())
    for i in range(8):
        _insert_call(
            store, ts=now - i, provider="openai",
            request_hash=f"sha256:ok-{i}",
            latency_ms=50.0, status_code=200,
        )
    for i in range(2):
        _insert_call(
            store, ts=now - 10 - i, provider="openai",
            request_hash=f"sha256:err-{i}",
            latency_ms=500.0, status_code=500,
        )
    result = store.provider_health(days=1)
    assert len(result) == 1
    row = result[0]
    assert row["provider"] == "openai"
    assert row["total_calls"] == 10
    assert row["error_count"] == 2
    assert row["error_rate"] == pytest.approx(0.2)


def test_provider_health_multiple_providers(store):
    now = int(time.time())
    _insert_call(store, ts=now, provider="anthropic", request_hash="sha256:a1",
                 latency_ms=25.0, status_code=200)
    _insert_call(store, ts=now, provider="openai", request_hash="sha256:o1",
                 latency_ms=50.0, status_code=200)
    result = store.provider_health(days=1)
    assert len(result) == 2
    providers = {r["provider"] for r in result}
    assert providers == {"anthropic", "openai"}


def test_provider_health_excludes_null_latency(store):
    now = int(time.time())
    _insert_call(store, ts=now, provider="anthropic", request_hash="sha256:nolat2")
    result = store.provider_health(days=1)
    assert result == []


# ---------------------------------------------------------------------------
# Rate Limit Tracking (Feature 7)
# ---------------------------------------------------------------------------


def test_rate_limit_events_empty(store):
    result = store.rate_limit_events(days=1)
    assert result == []


def test_rate_limit_events_counts_429s(store):
    now = int(time.time())
    hour_start = (now // 3600) * 3600
    _insert_call(store, ts=hour_start + 10, provider="anthropic",
                 request_hash="sha256:rl1", status_code=429)
    _insert_call(store, ts=hour_start + 20, provider="anthropic",
                 request_hash="sha256:rl2", status_code=429)
    _insert_call(store, ts=hour_start + 30, provider="openai",
                 request_hash="sha256:rl3", status_code=429)
    result = store.rate_limit_events(days=1)
    assert len(result) == 2
    anthropic_row = next(r for r in result if r["provider"] == "anthropic")
    openai_row = next(r for r in result if r["provider"] == "openai")
    assert anthropic_row["count"] == 2
    assert anthropic_row["hour_ts"] == hour_start
    assert openai_row["count"] == 1
    assert openai_row["hour_ts"] == hour_start


def test_rate_limit_events_ignores_200s(store):
    now = int(time.time())
    _insert_call(store, ts=now, provider="anthropic",
                 request_hash="sha256:ok1", status_code=200)
    _insert_call(store, ts=now + 1, provider="anthropic",
                 request_hash="sha256:ok2", status_code=200)
    result = store.rate_limit_events(days=1)
    assert result == []


def test_rate_limit_summary(store):
    now = int(time.time())
    _insert_call(store, ts=now, provider="anthropic",
                 request_hash="sha256:s429a", status_code=429)
    _insert_call(store, ts=now + 1, provider="anthropic",
                 request_hash="sha256:s200a", status_code=200)
    _insert_call(store, ts=now + 2, provider="anthropic",
                 request_hash="sha256:s200b", status_code=200)
    _insert_call(store, ts=now + 3, provider="anthropic",
                 request_hash="sha256:s429b", status_code=429)
    result = store.rate_limit_summary(days=1)
    assert len(result) == 1
    row = result[0]
    assert row["provider"] == "anthropic"
    assert row["rate_limit_count"] == 2
    assert row["total_calls"] == 4
    assert row["rate_limit_pct"] == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# Per-source budget (Feature 15)
# ---------------------------------------------------------------------------


def test_daily_spend_by_source(store):
    _insert_call(store, source="app-a", cost_usd=1.50, request_hash="sha256:sa1")
    _insert_call(store, source="app-a", cost_usd=0.75, request_hash="sha256:sa2")
    _insert_call(store, source="app-b", cost_usd=2.00, request_hash="sha256:sb1")
    assert store.daily_spend_by_source("app-a") == pytest.approx(2.25)
    assert store.daily_spend_by_source("app-b") == pytest.approx(2.00)


def test_monthly_spend_by_source(store):
    _insert_call(store, source="app-a", cost_usd=3.00, request_hash="sha256:ma1")
    _insert_call(store, source="app-b", cost_usd=5.00, request_hash="sha256:mb1")
    assert store.monthly_spend_by_source("app-a") >= 3.0
    assert store.monthly_spend_by_source("app-b") >= 5.0


def test_daily_spend_by_source_empty(store):
    assert store.daily_spend_by_source("nonexistent") == 0.0


def test_get_settings_by_prefix(store):
    store.set_setting("budget.sources.app-a.daily_limit_usd", "10.0")
    store.set_setting("budget.sources.app-a.monthly_limit_usd", "100.0")
    store.set_setting("budget.sources.app-b.daily_limit_usd", "5.0")
    store.set_setting("unrelated.key", "value")
    results = store.get_settings_by_prefix("budget.sources.")
    assert len(results) == 3
    keys = [r["key"] for r in results]
    assert "budget.sources.app-a.daily_limit_usd" in keys
    assert "budget.sources.app-a.monthly_limit_usd" in keys
    assert "budget.sources.app-b.daily_limit_usd" in keys
    assert "unrelated.key" not in keys


# ---------------------------------------------------------------------------
# Schema migration: call_waste table + 6 new calls columns
# ---------------------------------------------------------------------------


def test_calls_table_has_new_columns(tmp_path):
    store = UsageStore(tmp_path / "test.db")
    conn = store._con
    cols = {row[1] for row in conn.execute("PRAGMA table_info(calls)").fetchall()}
    assert "max_tokens_requested" in cols
    assert "output_utilization" in cols
    assert "message_count" in cols
    assert "history_tokens" in cols
    assert "history_ratio" in cols
    assert "token_heatmap" in cols


def test_call_waste_table_exists(tmp_path):
    store = UsageStore(tmp_path / "test.db")
    conn = store._con
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert "call_waste" in tables


def test_insert_call_returns_id(tmp_path):
    store = UsageStore(tmp_path / "test.db")
    row_id = store.insert_call(
        ts=1000, provider="anthropic", model="claude-sonnet-4-6",
        source="test", source_tag=None,
        input_tokens=100, output_tokens=50,
        cache_read_tokens=0, cache_write_tokens=0,
        cost_usd=0.001, endpoint="/v1/messages",
        request_hash="abc123",
    )
    assert isinstance(row_id, int)
    assert row_id > 0


def test_insert_call_with_new_kwargs(tmp_path):
    store = UsageStore(tmp_path / "test.db")
    row_id = store.insert_call(
        ts=1000, provider="anthropic", model="claude-sonnet-4-6",
        source="test", source_tag=None,
        input_tokens=1000, output_tokens=200,
        cache_read_tokens=0, cache_write_tokens=0,
        cost_usd=0.01, endpoint="/v1/messages",
        request_hash="def456",
        max_tokens_requested=800,
        output_utilization=0.25,
        message_count=8,
        history_tokens=600,
        history_ratio=0.6,
        token_heatmap='{"system_prompt": 200, "user_query": 100}',
    )
    row = store._con.execute("SELECT * FROM calls WHERE id=?", (row_id,)).fetchone()
    assert dict(row)["max_tokens_requested"] == 800
    assert abs(dict(row)["output_utilization"] - 0.25) < 0.001
    assert dict(row)["message_count"] == 8


def test_migrate_existing_db_adds_columns(tmp_path):
    """Simulate an old DB (without new columns) being migrated."""
    import sqlite3
    db_path = tmp_path / "old.db"
    con = sqlite3.connect(str(db_path))
    con.executescript("""
        CREATE TABLE calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts INTEGER NOT NULL,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            source TEXT NOT NULL,
            source_tag TEXT,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            cache_read_tokens INTEGER NOT NULL DEFAULT 0,
            cache_write_tokens INTEGER NOT NULL DEFAULT 0,
            cost_usd REAL NOT NULL DEFAULT 0.0,
            endpoint TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            user_agent TEXT NOT NULL DEFAULT ''
        );
    """)
    con.commit()
    con.close()
    store = UsageStore(db_path)
    cols = {row[1] for row in store._con.execute("PRAGMA table_info(calls)").fetchall()}
    assert "max_tokens_requested" in cols
    assert "token_heatmap" in cols


def test_insert_and_query_waste(tmp_path):
    store = UsageStore(tmp_path / "test.db")
    call_id = store.insert_call(
        ts=1000, provider="anthropic", model="claude-sonnet-4-6",
        source="test", source_tag=None,
        input_tokens=100, output_tokens=50,
        cache_read_tokens=0, cache_write_tokens=0,
        cost_usd=0.001, endpoint="/v1/messages",
        request_hash="abc",
    )
    store.insert_waste_items(call_id=call_id, items=[
        {"waste_type": "whitespace", "waste_tokens": 50, "savings_usd": 0.0001, "detail": "{}"},
        {"waste_type": "polite_filler", "waste_tokens": 20, "savings_usd": 0.00004, "detail": "{}"},
    ])
    rows = store.get_waste_for_call(call_id)
    assert len(rows) == 2
    assert rows[0]["waste_type"] == "whitespace"
    assert rows[0]["waste_tokens"] == 50


def test_waste_summary_aggregates(tmp_path):
    store = UsageStore(tmp_path / "test.db")
    now = int(__import__("time").time())
    for i in range(3):
        cid = store.insert_call(
            ts=now - i * 3600, provider="anthropic", model="claude-sonnet-4-6",
            source="test", source_tag=None,
            input_tokens=100, output_tokens=50,
            cache_read_tokens=0, cache_write_tokens=0,
            cost_usd=0.001, endpoint="/v1/messages",
            request_hash=f"hash{i}",
        )
        store.insert_waste_items(call_id=cid, items=[
            {"waste_type": "whitespace", "waste_tokens": 10 * (i + 1), "savings_usd": 0.001 * (i + 1), "detail": "{}"},
        ])
    summary = store.waste_summary(days=1)
    assert summary["total_waste_tokens"] == 60
    assert summary["by_type"]["whitespace"] == 60


def test_waste_summary_excludes_old_records(tmp_path):
    """Records older than the cutoff are excluded from waste_summary."""
    import time as _time
    store = UsageStore(tmp_path / "test.db")
    now = int(_time.time())
    # Old call (2 days ago) — should be excluded with days=1
    old_cid = store.insert_call(
        ts=now - 2 * 86400, provider="anthropic", model="claude-sonnet-4-6",
        source="test", source_tag=None,
        input_tokens=100, output_tokens=50,
        cache_read_tokens=0, cache_write_tokens=0,
        cost_usd=0.001, endpoint="/v1/messages",
        request_hash="old",
    )
    store.insert_waste_items(call_id=old_cid, items=[
        {"waste_type": "whitespace", "waste_tokens": 999, "savings_usd": 0.1, "detail": "{}"},
    ])
    summary = store.waste_summary(days=1)
    assert summary["total_waste_tokens"] == 0
    assert summary["by_type"] == {}


def test_insert_waste_items_empty_list(tmp_path):
    """insert_waste_items with an empty list is a no-op."""
    store = UsageStore(tmp_path / "test.db")
    call_id = store.insert_call(
        ts=1000, provider="anthropic", model="claude-sonnet-4-6",
        source="test", source_tag=None,
        input_tokens=100, output_tokens=50,
        cache_read_tokens=0, cache_write_tokens=0,
        cost_usd=0.001, endpoint="/v1/messages",
        request_hash="abc2",
    )
    store.insert_waste_items(call_id=call_id, items=[])  # should not raise
    rows = store.get_waste_for_call(call_id)
    assert rows == []
