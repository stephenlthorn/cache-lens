from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta

from .store import UsageStore

_log = logging.getLogger(__name__)


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
        try:
            await asyncio.get_running_loop().run_in_executor(None, _do_nightly_rollup, store, yesterday, raw_days)
        except Exception:
            # Log but continue — don't let one failed rollup kill the background task
            _log.exception("Nightly rollup failed for %s", yesterday)


async def _yearly_rollup_loop(store: UsageStore, daily_days: int = 365) -> None:
    """Asyncio task: on Jan 1 at 00:10 local time, roll up prior year."""
    while True:
        sleep_secs = _seconds_until(0, 10)
        await asyncio.sleep(sleep_secs)
        today = date.today()
        if today.month == 1 and today.day == 1:
            prior_year = today.year - 1
            try:
                await asyncio.get_running_loop().run_in_executor(None, _do_yearly_rollup, store, prior_year, daily_days)
            except Exception:
                # Log but continue — don't let one failed rollup kill the background task
                _log.exception("Yearly rollup failed for %s", prior_year)


def run_startup_recovery(store: UsageStore, raw_days: int = 1, daily_days: int = 365) -> None:
    """
    Check for missed rollups and run them synchronously on startup.
    - Nightly: check past 7 days (aggregate all days first, then purge once)
    - Yearly: check prior year if date is Jan 2+

    Aggregates all missed days before purging so that recovery of older days
    is not destroyed by the purge triggered by a more-recent day's rollup.

    Yearly rollup: only checked if today is Jan 2 or later (Jan 1 itself is handled
    by the scheduled _yearly_rollup_loop task at 00:10).
    """
    today = date.today()

    # Use a large sentinel to skip per-rollup purging during recovery loop.
    # We do a single purge at the end (only if at least one rollup actually ran).
    _NO_PURGE = 36500  # 100 years — effectively never purges

    any_rollup_ran = False
    for i in range(7, 0, -1):  # 7 days ago through yesterday (oldest first)
        target = today - timedelta(days=i)
        date_str = target.isoformat()
        if not store.rollup_done("nightly", date_str):
            any_rollup_ran = True
        _do_nightly_rollup(store, target, _NO_PURGE)

    # Single purge after all aggregations, but only if at least one rollup ran
    if any_rollup_ran:
        store.purge_raw_calls_older_than_days(raw_days)

    if today.month == 1 and today.day >= 2:
        prior_year = today.year - 1
        _do_yearly_rollup(store, prior_year, daily_days)


async def _weekly_digest_loop(store: UsageStore) -> None:
    """Fires Sunday at 08:00 local time, dispatches weekly_digest webhook."""
    from datetime import datetime, timedelta

    while True:
        now = datetime.now()
        # Find next Sunday 08:00
        # weekday(): Monday=0, Sunday=6
        days_until_sunday = (6 - now.weekday()) % 7
        if days_until_sunday == 0 and now.hour >= 8:
            days_until_sunday = 7
        target = now.replace(hour=8, minute=0, second=0, microsecond=0) + timedelta(days=days_until_sunday)
        sleep_secs = (target - now).total_seconds()
        await asyncio.sleep(sleep_secs)
        try:
            from tokenlens.digest import generate_digest
            from tokenlens.pricing import PricingTable
            from tokenlens.webhooks import dispatch_webhook
            pricing = PricingTable()
            report = generate_digest(store=store, pricing=pricing, days=7)
            webhook_url = store.get_setting("webhook.url")
            webhook_enabled = store.get_setting("webhook.enabled") == "true"
            webhook_events = store.get_setting("webhook.events") or ""
            if webhook_enabled and webhook_url and "weekly_digest" in webhook_events:
                await dispatch_webhook(url=webhook_url, event={"type": "weekly_digest", "data": report})
        except Exception:
            _log.exception("Weekly digest dispatch failed")


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
        asyncio.create_task(_weekly_digest_loop(store)),
    ]
    return tasks
