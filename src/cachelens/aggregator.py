from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta

from .store import UsageStore


def _do_nightly_rollup(store: UsageStore, target_date: date, raw_days: int = 1) -> None:
    """
    Synchronous: aggregate target_date's calls into daily_agg, then purge old raw calls.
    Idempotent via rollup_done() check + mark_rollup_done().
    """
    date_str = target_date.isoformat()
    if store.rollup_done("nightly", date_str):
        return

    rows = store.aggregate_calls_for_date(date_str)
    for row in rows:
        store.upsert_daily_agg(
            date=date_str,
            provider=row["provider"],
            model=row["model"],
            source=row["source"],
            call_count=row["call_count"],
            input_tokens=row["input_tokens"],
            output_tokens=row["output_tokens"],
            cache_read_tokens=row["cache_read_tokens"],
            cache_write_tokens=row["cache_write_tokens"],
            cost_usd=row["cost_usd"],
        )
    store.purge_raw_calls_older_than_days(raw_days)
    store.mark_rollup_done("nightly", date_str)


def _do_yearly_rollup(store: UsageStore, target_year: int, daily_days: int = 365) -> None:
    """
    Synchronous: aggregate target_year's daily_agg rows into yearly_agg, then purge old daily rows.
    Idempotent via rollup_done() check + mark_rollup_done().
    """
    year_str = str(target_year)
    if store.rollup_done("yearly", year_str):
        return

    rows = store.aggregate_daily_for_year(target_year)
    for row in rows:
        store.upsert_yearly_agg(
            year=target_year,
            provider=row["provider"],
            model=row["model"],
            source=row["source"],
            call_count=row["call_count"],
            input_tokens=row["input_tokens"],
            output_tokens=row["output_tokens"],
            cache_read_tokens=row["cache_read_tokens"],
            cache_write_tokens=row["cache_write_tokens"],
            cost_usd=row["cost_usd"],
        )
    store.purge_daily_agg_older_than_days(daily_days)
    store.mark_rollup_done("yearly", year_str)


def _seconds_until(hour: int, minute: int) -> float:
    """Return seconds until the next occurrence of HH:MM in local time."""
    now = datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target = target + timedelta(days=1)
    return (target - now).total_seconds()


async def _nightly_rollup_loop(store: UsageStore, raw_days: int = 1) -> None:
    """Asyncio task: runs nightly at 00:05 local time."""
    while True:
        sleep_secs = _seconds_until(0, 5)
        await asyncio.sleep(sleep_secs)
        yesterday = date.today() - timedelta(days=1)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _do_nightly_rollup, store, yesterday, raw_days)


async def _yearly_rollup_loop(store: UsageStore, daily_days: int = 365) -> None:
    """Asyncio task: on Jan 1 at 00:10 local time, roll up prior year."""
    while True:
        sleep_secs = _seconds_until(0, 10)
        await asyncio.sleep(sleep_secs)
        today = date.today()
        if today.month == 1 and today.day == 1:
            prior_year = today.year - 1
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, _do_yearly_rollup, store, prior_year, daily_days)


def run_startup_recovery(store: UsageStore, raw_days: int = 1, daily_days: int = 365) -> None:
    """
    Check for missed rollups and run them synchronously on startup.
    - Nightly: check past 7 days (aggregate all days first, then purge once)
    - Yearly: check prior year if date is Jan 2+

    Aggregates all missed days before purging so that recovery of older days
    is not destroyed by the purge triggered by a more-recent day's rollup.
    """
    today = date.today()

    # Use a large sentinel to skip per-rollup purging during recovery loop.
    # We do a single purge at the end.
    _NO_PURGE = 36500  # 100 years — effectively never purges

    for i in range(7, 0, -1):  # 7 days ago through yesterday (oldest first)
        target = today - timedelta(days=i)
        _do_nightly_rollup(store, target, _NO_PURGE)

    # Single purge after all aggregations are done
    store.purge_raw_calls_older_than_days(raw_days)

    if today.month == 1 and today.day >= 2:
        prior_year = today.year - 1
        _do_yearly_rollup(store, prior_year, daily_days)


def schedule_rollups(
    store: UsageStore,
    raw_days: int = 1,
    daily_days: int = 365,
) -> list[asyncio.Task]:
    """
    Create and return asyncio background tasks for nightly and yearly rollups.
    Call this inside an async context (e.g. FastAPI lifespan).
    Also runs startup recovery.
    """
    run_startup_recovery(store, raw_days, daily_days)
    tasks = [
        asyncio.create_task(_nightly_rollup_loop(store, raw_days)),
        asyncio.create_task(_yearly_rollup_loop(store, daily_days)),
    ]
    return tasks
