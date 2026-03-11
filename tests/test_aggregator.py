from __future__ import annotations

import pytest
from datetime import date, datetime, timedelta

from cachelens.store import UsageStore
from cachelens.aggregator import (
    _do_nightly_rollup,
    _do_yearly_rollup,
    _seconds_until,
    run_startup_recovery,
)


@pytest.fixture
def store(tmp_path):
    return UsageStore(tmp_path / "test.db")


def _insert(
    store: UsageStore,
    *,
    ts: int,
    provider: str = "anthropic",
    model: str = "claude-sonnet-4-6",
    source: str = "test",
    input_tokens: int = 100,
    output_tokens: int = 50,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    cost_usd: float = 0.01,
) -> None:
    store.insert_call(
        ts=ts,
        provider=provider,
        model=model,
        source=source,
        source_tag=None,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        cost_usd=cost_usd,
        endpoint="/v1/messages",
        request_hash="sha256:abc",
    )


def _day_ts(d: date) -> int:
    """Return unix timestamp for midnight local time of the given date."""
    return int(datetime.strptime(d.isoformat(), "%Y-%m-%d").timestamp())


# ---------------------------------------------------------------------------
# _do_nightly_rollup
# ---------------------------------------------------------------------------


def test_nightly_rollup_aggregates_to_daily_agg(store: UsageStore) -> None:
    yesterday = date.today() - timedelta(days=1)
    day_ts = _day_ts(yesterday)

    _insert(store, ts=day_ts + 100, input_tokens=100, output_tokens=50, cost_usd=0.01)
    _insert(store, ts=day_ts + 200, input_tokens=200, output_tokens=100, cost_usd=0.02)

    _do_nightly_rollup(store, yesterday)

    rows = store.daily_agg_for_date(yesterday.isoformat())
    assert len(rows) == 1
    row = rows[0]
    assert row["call_count"] == 2
    assert row["input_tokens"] == 300
    assert row["output_tokens"] == 150
    assert row["cost_usd"] == pytest.approx(0.03)


def test_nightly_rollup_is_idempotent(store: UsageStore) -> None:
    yesterday = date.today() - timedelta(days=1)
    day_ts = _day_ts(yesterday)
    _insert(store, ts=day_ts + 100, input_tokens=100, output_tokens=50, cost_usd=0.01)

    _do_nightly_rollup(store, yesterday)
    # Second run should not double-count
    _do_nightly_rollup(store, yesterday)

    rows = store.daily_agg_for_date(yesterday.isoformat())
    assert len(rows) == 1
    assert rows[0]["call_count"] == 1
    assert rows[0]["input_tokens"] == 100


def test_nightly_rollup_marks_rollup_done(store: UsageStore) -> None:
    yesterday = date.today() - timedelta(days=1)
    day_ts = _day_ts(yesterday)
    _insert(store, ts=day_ts + 100)

    assert not store.rollup_done("nightly", yesterday.isoformat())
    _do_nightly_rollup(store, yesterday)
    assert store.rollup_done("nightly", yesterday.isoformat())


def test_nightly_rollup_purges_old_raw_calls(store: UsageStore) -> None:
    yesterday = date.today() - timedelta(days=1)
    day_ts = _day_ts(yesterday)

    # An old call (3 days ago, well beyond raw_days=1)
    old_ts = _day_ts(date.today() - timedelta(days=3))
    _insert(store, ts=old_ts + 100, input_tokens=999)

    # A recent call (yesterday)
    _insert(store, ts=day_ts + 100, input_tokens=50)

    _do_nightly_rollup(store, yesterday, raw_days=1)

    # After purge, only the recent call should remain
    rows = store.daily_agg_for_date(yesterday.isoformat())
    assert len(rows) == 1
    assert rows[0]["input_tokens"] == 50

    # The old call should be gone from the raw calls table
    from cachelens.store import UsageStore as _S
    remaining = store._con.execute(
        "SELECT COUNT(*) FROM calls WHERE input_tokens = 999"
    ).fetchone()[0]
    assert remaining == 0


def test_nightly_rollup_no_calls_does_not_crash(store: UsageStore) -> None:
    yesterday = date.today() - timedelta(days=1)
    _do_nightly_rollup(store, yesterday)
    rows = store.daily_agg_for_date(yesterday.isoformat())
    assert rows == []
    assert store.rollup_done("nightly", yesterday.isoformat())


# ---------------------------------------------------------------------------
# _do_yearly_rollup
# ---------------------------------------------------------------------------


def _insert_daily_agg(
    store: UsageStore,
    *,
    date_str: str,
    provider: str = "anthropic",
    model: str = "claude-sonnet-4-6",
    source: str = "test",
    call_count: int = 1,
    input_tokens: int = 100,
    output_tokens: int = 50,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    cost_usd: float = 0.01,
) -> None:
    store.upsert_daily_agg(
        date=date_str,
        provider=provider,
        model=model,
        source=source,
        call_count=call_count,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        cost_usd=cost_usd,
    )


def test_yearly_rollup_aggregates_daily_to_yearly(store: UsageStore) -> None:
    _insert_daily_agg(store, date_str="2025-01-15", call_count=5, input_tokens=500, output_tokens=250, cost_usd=0.05)
    _insert_daily_agg(store, date_str="2025-06-01", call_count=3, input_tokens=300, output_tokens=150, cost_usd=0.03)

    _do_yearly_rollup(store, 2025)

    rows = store.yearly_agg_for_year(2025)
    assert len(rows) == 1
    row = rows[0]
    assert row["call_count"] == 8
    assert row["input_tokens"] == 800
    assert row["output_tokens"] == 400
    assert row["cost_usd"] == pytest.approx(0.08)


def test_yearly_rollup_is_idempotent(store: UsageStore) -> None:
    _insert_daily_agg(store, date_str="2025-03-10", call_count=2, input_tokens=200, output_tokens=100, cost_usd=0.02)

    _do_yearly_rollup(store, 2025)
    _do_yearly_rollup(store, 2025)

    rows = store.yearly_agg_for_year(2025)
    assert len(rows) == 1
    assert rows[0]["call_count"] == 2
    assert rows[0]["input_tokens"] == 200


def test_yearly_rollup_marks_rollup_done(store: UsageStore) -> None:
    _insert_daily_agg(store, date_str="2025-05-01")

    assert not store.rollup_done("yearly", "2025")
    _do_yearly_rollup(store, 2025)
    assert store.rollup_done("yearly", "2025")


def test_yearly_rollup_purges_old_daily_agg(store: UsageStore) -> None:
    # 2025 rows — these should be purged when daily_days=365 and we're in 2026+
    _insert_daily_agg(store, date_str="2025-01-15", call_count=1, input_tokens=100, output_tokens=50, cost_usd=0.01)
    # A more recent row to survive the purge (within 365 days)
    recent_date = (date.today() - timedelta(days=30)).isoformat()
    _insert_daily_agg(store, date_str=recent_date, call_count=2, input_tokens=200, output_tokens=100, cost_usd=0.02)

    _do_yearly_rollup(store, 2025, daily_days=365)

    # Old 2025-01-15 row should be purged (it's more than 365 days old relative to today in 2026)
    old_rows = store._con.execute(
        "SELECT * FROM daily_agg WHERE date='2025-01-15'"
    ).fetchall()
    assert len(old_rows) == 0

    # Recent row should survive
    recent_rows = store._con.execute(
        "SELECT * FROM daily_agg WHERE date=?", (recent_date,)
    ).fetchall()
    assert len(recent_rows) == 1


def test_yearly_rollup_no_daily_rows_does_not_crash(store: UsageStore) -> None:
    _do_yearly_rollup(store, 2025)
    rows = store.yearly_agg_for_year(2025)
    assert rows == []
    assert store.rollup_done("yearly", "2025")


# ---------------------------------------------------------------------------
# _seconds_until
# ---------------------------------------------------------------------------


def test_seconds_until_returns_positive() -> None:
    secs = _seconds_until(0, 5)
    assert secs > 0
    assert secs <= 86400


def test_seconds_until_returns_at_most_one_day() -> None:
    for h, m in [(0, 5), (0, 10), (12, 0), (23, 59)]:
        secs = _seconds_until(h, m)
        assert 0 < secs <= 86400, f"seconds_until({h},{m}) = {secs}"


# ---------------------------------------------------------------------------
# run_startup_recovery
# ---------------------------------------------------------------------------


def test_run_startup_recovery_runs_missing_nightly_rollups(store: UsageStore) -> None:
    three_days_ago = date.today() - timedelta(days=3)
    day_ts = _day_ts(three_days_ago)
    _insert(store, ts=day_ts + 100, input_tokens=150, output_tokens=75, cost_usd=0.015)

    run_startup_recovery(store)

    rows = store.daily_agg_for_date(three_days_ago.isoformat())
    assert len(rows) == 1
    assert rows[0]["call_count"] == 1
    assert rows[0]["input_tokens"] == 150


def test_run_startup_recovery_is_idempotent(store: UsageStore) -> None:
    two_days_ago = date.today() - timedelta(days=2)
    day_ts = _day_ts(two_days_ago)
    _insert(store, ts=day_ts + 100, input_tokens=50, output_tokens=25, cost_usd=0.005)

    run_startup_recovery(store)
    run_startup_recovery(store)

    rows = store.daily_agg_for_date(two_days_ago.isoformat())
    assert len(rows) == 1
    assert rows[0]["call_count"] == 1


def test_run_startup_recovery_marks_all_nightly_days_done(store: UsageStore) -> None:
    run_startup_recovery(store)
    today = date.today()
    for i in range(1, 8):
        target = today - timedelta(days=i)
        assert store.rollup_done("nightly", target.isoformat()), (
            f"Expected nightly rollup done for {target.isoformat()}"
        )
