from __future__ import annotations

import sqlite3
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


_SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
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
CREATE TABLE IF NOT EXISTS daily_agg (
    date TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    source TEXT NOT NULL,
    call_count INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0.0,
    UNIQUE(date, provider, model, source)
);
CREATE TABLE IF NOT EXISTS yearly_agg (
    year INTEGER NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    source TEXT NOT NULL,
    call_count INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0.0,
    UNIQUE(year, provider, model, source)
);
CREATE TABLE IF NOT EXISTS rollups (
    job TEXT NOT NULL,
    period TEXT NOT NULL,
    completed_at INTEGER NOT NULL,
    PRIMARY KEY (job, period)
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS response_cache (
    request_hash TEXT PRIMARY KEY,
    response_body BLOB NOT NULL,
    response_status INTEGER NOT NULL,
    response_headers TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    cached_at INTEGER NOT NULL,
    ttl_seconds INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS request_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id INTEGER NOT NULL,
    ts INTEGER NOT NULL,
    request_body TEXT,
    response_body TEXT
);
CREATE INDEX IF NOT EXISTS idx_calls_ts ON calls(ts);
CREATE INDEX IF NOT EXISTS idx_daily_agg_date ON daily_agg(date);
CREATE INDEX IF NOT EXISTS idx_request_log_ts ON request_log(ts);
"""


class UsageStore:
    def __init__(self, db_path: Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._con = sqlite3.connect(str(self._path), check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._con.executescript(_SCHEMA)
        self._migrate()
        self._con.commit()

    def _migrate(self) -> None:
        """Non-destructive schema migrations for existing databases."""
        cols = {
            row[1]
            for row in self._con.execute("PRAGMA table_info(calls)").fetchall()
        }
        if "user_agent" not in cols:
            self._con.execute(
                "ALTER TABLE calls ADD COLUMN user_agent TEXT NOT NULL DEFAULT ''"
            )
        if "latency_ms" not in cols:
            self._con.execute("ALTER TABLE calls ADD COLUMN latency_ms REAL")
        if "status_code" not in cols:
            self._con.execute("ALTER TABLE calls ADD COLUMN status_code INTEGER")

    def insert_call(self, *, ts: int, provider: str, model: str,
                    source: str, source_tag: str | None,
                    input_tokens: int, output_tokens: int,
                    cache_read_tokens: int, cache_write_tokens: int,
                    cost_usd: float, endpoint: str, request_hash: str,
                    user_agent: str = "",
                    latency_ms: float | None = None,
                    status_code: int | None = None) -> int:
        with self._lock:
            cur = self._con.execute(
                "INSERT INTO calls (ts,provider,model,source,source_tag,"
                "input_tokens,output_tokens,cache_read_tokens,cache_write_tokens,"
                "cost_usd,endpoint,request_hash,user_agent,latency_ms,status_code)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (ts, provider, model, source, source_tag,
                 input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
                 cost_usd, endpoint, request_hash, user_agent, latency_ms, status_code),
            )
            row_id = cur.lastrowid
            self._con.commit()
        return row_id

    def raw_calls_last_24h(self) -> int:
        day_start = int(time.time()) - 86400
        with self._lock:
            row = self._con.execute(
                "SELECT COUNT(*) FROM calls WHERE ts >= ?", (day_start,)
            ).fetchone()
        return row[0] if row else 0

    def upsert_daily_agg(self, *, date: str, provider: str, model: str,
                          source: str, call_count: int, input_tokens: int,
                          output_tokens: int, cache_read_tokens: int,
                          cache_write_tokens: int, cost_usd: float) -> None:
        with self._lock:
            self._con.execute(
                "INSERT OR REPLACE INTO daily_agg VALUES (?,?,?,?,?,?,?,?,?,?)",
                (date, provider, model, source, call_count,
                 input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, cost_usd),
            )
            self._con.commit()

    def daily_agg_for_date(self, date: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._con.execute(
                "SELECT * FROM daily_agg WHERE date=?", (date,)
            ).fetchall()
        return [dict(r) for r in rows]

    def upsert_yearly_agg(self, *, year: int, provider: str, model: str,
                           source: str, call_count: int, input_tokens: int,
                           output_tokens: int, cache_read_tokens: int,
                           cache_write_tokens: int, cost_usd: float) -> None:
        with self._lock:
            self._con.execute(
                "INSERT OR REPLACE INTO yearly_agg VALUES (?,?,?,?,?,?,?,?,?,?)",
                (year, provider, model, source, call_count,
                 input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, cost_usd),
            )
            self._con.commit()

    def yearly_agg_for_year(self, year: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._con.execute(
                "SELECT * FROM yearly_agg WHERE year=?", (year,)
            ).fetchall()
        return [dict(r) for r in rows]

    def purge_raw_calls_older_than_days(self, days: int) -> None:
        cutoff = int(time.time()) - days * 86400
        with self._lock:
            self._con.execute("DELETE FROM calls WHERE ts < ?", (cutoff,))
            self._con.commit()

    def purge_daily_agg_older_than_days(self, days: int) -> None:
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        with self._lock:
            self._con.execute("DELETE FROM daily_agg WHERE date < ?", (cutoff,))
            self._con.commit()

    def rollup_done(self, job: str, period: str) -> bool:
        with self._lock:
            row = self._con.execute(
                "SELECT 1 FROM rollups WHERE job=? AND period=?", (job, period)
            ).fetchone()
        return row is not None

    def mark_rollup_done(self, job: str, period: str) -> None:
        with self._lock:
            self._con.execute(
                "INSERT OR REPLACE INTO rollups VALUES (?,?,?)",
                (job, period, int(time.time())),
            )
            self._con.commit()

    def aggregate_calls_for_date(self, date: str) -> list[dict[str, Any]]:
        """Return aggregated rows for a given date string (YYYY-MM-DD)."""
        day_start = _date_to_ts(date)
        day_end = day_start + 86400
        with self._lock:
            rows = self._con.execute(
                """SELECT provider, model, source,
                   COUNT(*) as call_count,
                   SUM(input_tokens) as input_tokens,
                   SUM(output_tokens) as output_tokens,
                   SUM(cache_read_tokens) as cache_read_tokens,
                   SUM(cache_write_tokens) as cache_write_tokens,
                   SUM(cost_usd) as cost_usd
                   FROM calls WHERE ts >= ? AND ts < ?
                   GROUP BY provider, model, source""",
                (day_start, day_end),
            ).fetchall()
        return [dict(r) for r in rows]

    def aggregate_daily_for_year(self, year: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._con.execute(
                """SELECT provider, model, source,
                   SUM(call_count) as call_count,
                   SUM(input_tokens) as input_tokens,
                   SUM(output_tokens) as output_tokens,
                   SUM(cache_read_tokens) as cache_read_tokens,
                   SUM(cache_write_tokens) as cache_write_tokens,
                   SUM(cost_usd) as cost_usd
                   FROM daily_agg WHERE date >= ? AND date < ?
                   GROUP BY provider, model, source""",
                (f"{year}-01-01", f"{year+1}-01-01"),
            ).fetchall()
        return [dict(r) for r in rows]

    def kpi_rolling(self, days: int) -> dict[str, Any]:
        """Return KPI totals for the rolling window.

        Uses daily_agg for historical dates (retained beyond raw call purge window)
        and raw calls for today, then combines both to give accurate totals
        regardless of when the nightly rollup last ran.
        """
        today = date.today().isoformat()
        since = (date.today() - timedelta(days=days)).isoformat()

        with self._lock:
            # Historical totals from daily_agg (excludes today; handled separately)
            agg = self._con.execute(
                """SELECT COALESCE(SUM(call_count),0) as call_count,
                   COALESCE(SUM(cost_usd),0) as total_cost_usd,
                   COALESCE(SUM(input_tokens),0) as input_tokens,
                   COALESCE(SUM(output_tokens),0) as output_tokens,
                   COALESCE(SUM(cache_read_tokens),0) as cache_read_tokens,
                   COALESCE(SUM(cache_write_tokens),0) as cache_write_tokens
                   FROM daily_agg WHERE date >= ? AND date < ?""",
                (since, today),
            ).fetchone()

            # Today's live totals from raw calls
            day_start = _date_to_ts(today)
            live = self._con.execute(
                """SELECT COUNT(*) as call_count,
                   COALESCE(SUM(cost_usd),0) as total_cost_usd,
                   COALESCE(SUM(input_tokens),0) as input_tokens,
                   COALESCE(SUM(output_tokens),0) as output_tokens,
                   COALESCE(SUM(cache_read_tokens),0) as cache_read_tokens,
                   COALESCE(SUM(cache_write_tokens),0) as cache_write_tokens
                   FROM calls WHERE ts >= ?""",
                (day_start,),
            ).fetchone()

        return {
            "call_count": agg["call_count"] + live["call_count"],
            "total_cost_usd": agg["total_cost_usd"] + live["total_cost_usd"],
            "input_tokens": agg["input_tokens"] + live["input_tokens"],
            "output_tokens": agg["output_tokens"] + live["output_tokens"],
            "cache_read_tokens": agg["cache_read_tokens"] + live["cache_read_tokens"],
            "cache_write_tokens": agg["cache_write_tokens"] + live["cache_write_tokens"],
        }

    def db_size_bytes(self) -> int:
        return self._path.stat().st_size if self._path.exists() else 0

    def last_rollup_time(self, job: str) -> datetime | None:
        with self._lock:
            row = self._con.execute(
                "SELECT completed_at FROM rollups WHERE job=? ORDER BY completed_at DESC LIMIT 1",
                (job,),
            ).fetchone()
        if not row:
            return None
        return datetime.fromtimestamp(row[0], tz=timezone.utc)

    def query_daily_agg_since(self, since_date: str) -> list[dict]:
        """Return all daily_agg rows where date >= since_date."""
        with self._lock:
            rows = self._con.execute(
                "SELECT * FROM daily_agg WHERE date >= ? ORDER BY date",
                (since_date,),
            ).fetchall()
        return [dict(r) for r in rows]

    def recent_calls(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return the most recent raw calls, newest first."""
        with self._lock:
            rows = self._con.execute(
                "SELECT * FROM calls ORDER BY ts DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Settings (key-value store)
    # ------------------------------------------------------------------

    def get_setting(self, key: str) -> str | None:
        with self._lock:
            row = self._con.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row else None

    def set_setting(self, key: str, value: str) -> None:
        with self._lock:
            self._con.execute(
                "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                (key, value, int(time.time())),
            )
            self._con.commit()

    def delete_setting(self, key: str) -> None:
        with self._lock:
            self._con.execute("DELETE FROM settings WHERE key = ?", (key,))
            self._con.commit()

    def get_settings_by_prefix(self, prefix: str) -> list[dict]:
        """Return all settings whose key starts with the given prefix."""
        with self._lock:
            rows = self._con.execute(
                "SELECT key, value, updated_at FROM settings WHERE key LIKE ?",
                (prefix + "%",),
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Spend helpers (for alerts + budget caps)
    # ------------------------------------------------------------------

    def daily_spend_usd(self) -> float:
        """Total cost_usd for today (daily_agg + live calls, deduplicated)."""
        today = date.today().isoformat()
        with self._lock:
            agg = self._con.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM daily_agg WHERE date = ?",
                (today,),
            ).fetchone()
            day_start = _date_to_ts(today)
            live = self._con.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM calls WHERE ts >= ?",
                (day_start,),
            ).fetchone()
        return (agg[0] or 0.0) + (live[0] or 0.0)

    def monthly_spend_usd(self) -> float:
        """Total cost_usd for the current calendar month."""
        today = date.today()
        month_start = today.replace(day=1).isoformat()
        today_str = today.isoformat()
        with self._lock:
            agg = self._con.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM daily_agg WHERE date >= ? AND date < ?",
                (month_start, today_str),
            ).fetchone()
            day_start = _date_to_ts(today_str)
            live = self._con.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM calls WHERE ts >= ?",
                (day_start,),
            ).fetchone()
        return (agg[0] or 0.0) + (live[0] or 0.0)

    def daily_spend_by_source(self, source: str) -> float:
        """Total cost_usd for today filtered by source."""
        today = date.today().isoformat()
        with self._lock:
            agg = self._con.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM daily_agg WHERE date = ? AND source = ?",
                (today, source),
            ).fetchone()
            day_start = _date_to_ts(today)
            live = self._con.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM calls WHERE ts >= ? AND source = ?",
                (day_start, source),
            ).fetchone()
        return (agg[0] or 0.0) + (live[0] or 0.0)

    def monthly_spend_by_source(self, source: str) -> float:
        """Total cost_usd for the current calendar month filtered by source."""
        today = date.today()
        month_start = today.replace(day=1).isoformat()
        today_str = today.isoformat()
        with self._lock:
            agg = self._con.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM daily_agg WHERE date >= ? AND date < ? AND source = ?",
                (month_start, today_str, source),
            ).fetchone()
            day_start = _date_to_ts(today_str)
            live = self._con.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM calls WHERE ts >= ? AND source = ?",
                (day_start, source),
            ).fetchone()
        return (agg[0] or 0.0) + (live[0] or 0.0)

    # ------------------------------------------------------------------
    # Cache hit rate trend (Phase 3)
    # ------------------------------------------------------------------

    def daily_cache_hit_trend(self, days: int) -> list[dict[str, Any]]:
        """Per-day cache hit data for the last N days."""
        since = (date.today() - timedelta(days=days)).isoformat()
        today = date.today().isoformat()
        with self._lock:
            rows = self._con.execute(
                """SELECT date,
                   SUM(input_tokens) as input_tokens,
                   SUM(cache_read_tokens) as cache_read_tokens
                   FROM daily_agg WHERE date >= ? AND date < ?
                   GROUP BY date ORDER BY date""",
                (since, today),
            ).fetchall()
        result = [dict(r) for r in rows]

        today_rows = self.aggregate_calls_for_date(today)
        if today_rows:
            total_input = sum(r["input_tokens"] for r in today_rows)
            total_cache_read = sum(r["cache_read_tokens"] for r in today_rows)
            if total_input + total_cache_read > 0:
                result.append({
                    "date": today,
                    "input_tokens": total_input,
                    "cache_read_tokens": total_cache_read,
                })
        return result

    # ------------------------------------------------------------------
    # Spend Forecasting (daily cost series)
    # ------------------------------------------------------------------

    def daily_cost_series(self, days: int) -> list[tuple[str, float]]:
        """Return daily total cost for the last N days, sorted by date ASC.

        Uses daily_agg for historical dates and raw calls for today's live data.
        """
        today = date.today()
        since = (today - timedelta(days=days)).isoformat()
        today_str = today.isoformat()

        with self._lock:
            rows = self._con.execute(
                """SELECT date, SUM(cost_usd) as total_cost
                   FROM daily_agg
                   WHERE date >= ? AND date < ?
                   GROUP BY date
                   ORDER BY date""",
                (since, today_str),
            ).fetchall()

            day_start = _date_to_ts(today_str)
            live = self._con.execute(
                """SELECT COALESCE(SUM(cost_usd), 0) as total_cost
                   FROM calls WHERE ts >= ?""",
                (day_start,),
            ).fetchone()

        result = [(row["date"], row["total_cost"]) for row in rows]

        live_cost = live["total_cost"] if live else 0.0
        if live_cost > 0:
            result.append((today_str, live_cost))

        result.sort(key=lambda x: x[0])
        return result

    # ------------------------------------------------------------------
    # Cost Allocation Tags
    # ------------------------------------------------------------------

    def query_by_tag(self, days: int) -> list[dict]:
        """Return usage grouped by source (tag) for the last N days.

        Combines historical daily_agg with today's live raw calls,
        then groups everything by source.
        """
        today = date.today().isoformat()
        since = (date.today() - timedelta(days=days)).isoformat()

        with self._lock:
            agg_rows = self._con.execute(
                """SELECT source,
                   SUM(call_count) as call_count,
                   SUM(input_tokens) as input_tokens,
                   SUM(output_tokens) as output_tokens,
                   SUM(cache_read_tokens) as cache_read_tokens,
                   SUM(cache_write_tokens) as cache_write_tokens,
                   SUM(cost_usd) as cost_usd
                   FROM daily_agg WHERE date >= ? AND date < ?
                   GROUP BY source""",
                (since, today),
            ).fetchall()

            day_start = _date_to_ts(today)
            live_rows = self._con.execute(
                """SELECT source,
                   COUNT(*) as call_count,
                   COALESCE(SUM(input_tokens), 0) as input_tokens,
                   COALESCE(SUM(output_tokens), 0) as output_tokens,
                   COALESCE(SUM(cache_read_tokens), 0) as cache_read_tokens,
                   COALESCE(SUM(cache_write_tokens), 0) as cache_write_tokens,
                   COALESCE(SUM(cost_usd), 0) as cost_usd
                   FROM calls WHERE ts >= ?
                   GROUP BY source""",
                (day_start,),
            ).fetchall()

        by_source: dict[str, dict] = {}
        for row in agg_rows:
            src = row["source"]
            by_source[src] = {
                "source": src,
                "call_count": row["call_count"] or 0,
                "input_tokens": row["input_tokens"] or 0,
                "output_tokens": row["output_tokens"] or 0,
                "cache_read_tokens": row["cache_read_tokens"] or 0,
                "cache_write_tokens": row["cache_write_tokens"] or 0,
                "cost_usd": row["cost_usd"] or 0.0,
            }
        for row in live_rows:
            src = row["source"]
            if src in by_source:
                by_source[src]["call_count"] += row["call_count"] or 0
                by_source[src]["input_tokens"] += row["input_tokens"] or 0
                by_source[src]["output_tokens"] += row["output_tokens"] or 0
                by_source[src]["cache_read_tokens"] += row["cache_read_tokens"] or 0
                by_source[src]["cache_write_tokens"] += row["cache_write_tokens"] or 0
                by_source[src]["cost_usd"] += row["cost_usd"] or 0.0
            else:
                by_source[src] = {
                    "source": src,
                    "call_count": row["call_count"] or 0,
                    "input_tokens": row["input_tokens"] or 0,
                    "output_tokens": row["output_tokens"] or 0,
                    "cache_read_tokens": row["cache_read_tokens"] or 0,
                    "cache_write_tokens": row["cache_write_tokens"] or 0,
                    "cost_usd": row["cost_usd"] or 0.0,
                }

        return sorted(by_source.values(), key=lambda r: r["cost_usd"], reverse=True)

    # ------------------------------------------------------------------
    # Raw calls for sessions (Phase 5)
    # ------------------------------------------------------------------

    def raw_calls_for_period(self, days: int, source: str | None = None) -> list[dict[str, Any]]:
        """Return raw calls for the last N days, sorted by ts ASC."""
        cutoff = int(time.time()) - days * 86400
        with self._lock:
            if source:
                rows = self._con.execute(
                    "SELECT * FROM calls WHERE ts >= ? AND source = ? ORDER BY ts ASC",
                    (cutoff, source),
                ).fetchall()
            else:
                rows = self._con.execute(
                    "SELECT * FROM calls WHERE ts >= ? ORDER BY ts ASC",
                    (cutoff,),
                ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Provider Health (Latency/Status Tracking)
    # ------------------------------------------------------------------

    def provider_health(self, days: int = 1) -> list[dict[str, Any]]:
        cutoff = int(time.time()) - days * 86400
        with self._lock:
            rows = self._con.execute(
                "SELECT provider, latency_ms, status_code FROM calls"
                " WHERE ts >= ? AND latency_ms IS NOT NULL",
                (cutoff,),
            ).fetchall()
        if not rows:
            return []

        by_provider: dict[str, list[dict]] = {}
        for row in rows:
            provider = row["provider"]
            if provider not in by_provider:
                by_provider[provider] = []
            by_provider[provider].append(dict(row))

        result: list[dict[str, Any]] = []
        for provider, calls in sorted(by_provider.items()):
            total = len(calls)
            error_count = sum(
                1 for c in calls
                if c["status_code"] is not None and c["status_code"] >= 400
            )
            error_rate = error_count / total if total > 0 else 0.0
            latencies = sorted(c["latency_ms"] for c in calls)
            result.append({
                "provider": provider,
                "total_calls": total,
                "error_count": error_count,
                "error_rate": round(error_rate, 4),
                "p50_ms": round(_percentile(latencies, 50), 1),
                "p95_ms": round(_percentile(latencies, 95), 1),
                "p99_ms": round(_percentile(latencies, 99), 1),
            })
        return result

    # ------------------------------------------------------------------
    # Rate Limit Tracking
    # ------------------------------------------------------------------

    def rate_limit_events(self, days: int = 1) -> list[dict]:
        """Return 429 events grouped by provider and hour for the last N days."""
        cutoff = int(time.time()) - days * 86400
        with self._lock:
            rows = self._con.execute(
                """SELECT provider,
                   (ts / 3600) * 3600 as hour_ts,
                   COUNT(*) as count
                   FROM calls
                   WHERE status_code = 429 AND ts >= ?
                   GROUP BY provider, hour_ts
                   ORDER BY hour_ts ASC""",
                (cutoff,),
            ).fetchall()
        return [dict(r) for r in rows]

    def rate_limit_summary(self, days: int = 1) -> list[dict]:
        """Return per-provider 429 count and total calls for rate calculation."""
        cutoff = int(time.time()) - days * 86400
        with self._lock:
            rows = self._con.execute(
                """SELECT provider,
                   SUM(CASE WHEN status_code = 429 THEN 1 ELSE 0 END) as rate_limit_count,
                   COUNT(*) as total_calls
                   FROM calls
                   WHERE ts >= ?
                   GROUP BY provider
                   HAVING rate_limit_count > 0""",
                (cutoff,),
            ).fetchall()
        return [
            {
                "provider": r["provider"],
                "rate_limit_count": r["rate_limit_count"],
                "total_calls": r["total_calls"],
                "rate_limit_pct": (
                    r["rate_limit_count"] / r["total_calls"] * 100
                    if r["total_calls"] > 0
                    else 0.0
                ),
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Request Deduplication (Response Cache)
    # ------------------------------------------------------------------

    def get_cached_response(self, request_hash: str) -> dict[str, Any] | None:
        """Return cached response if it exists and has not expired, else None."""
        now = int(time.time())
        with self._lock:
            row = self._con.execute(
                "SELECT * FROM response_cache WHERE request_hash = ?",
                (request_hash,),
            ).fetchone()
        if row is None:
            return None
        if row["cached_at"] + row["ttl_seconds"] <= now:
            return None
        return dict(row)

    def set_cached_response(
        self,
        *,
        request_hash: str,
        response_body: bytes,
        response_status: int,
        response_headers: str,
        provider: str,
        model: str,
        ttl_seconds: int,
    ) -> None:
        with self._lock:
            self._con.execute(
                "INSERT OR REPLACE INTO response_cache "
                "(request_hash, response_body, response_status, response_headers, "
                "provider, model, cached_at, ttl_seconds) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    request_hash,
                    response_body,
                    response_status,
                    response_headers,
                    provider,
                    model,
                    int(time.time()),
                    ttl_seconds,
                ),
            )
            self._con.commit()

    def purge_expired_cache(self) -> int:
        """Delete expired response_cache entries, return count deleted."""
        now = int(time.time())
        with self._lock:
            cursor = self._con.execute(
                "DELETE FROM response_cache WHERE cached_at + ttl_seconds <= ?",
                (now,),
            )
            self._con.commit()
        return cursor.rowcount

    # ------------------------------------------------------------------
    # Request/Response Logging
    # ------------------------------------------------------------------

    def insert_request_log(
        self,
        *,
        call_id: int,
        ts: int,
        request_body: str | None,
        response_body: str | None,
    ) -> None:
        with self._lock:
            self._con.execute(
                "INSERT INTO request_log (call_id, ts, request_body, response_body) "
                "VALUES (?, ?, ?, ?)",
                (call_id, ts, request_body, response_body),
            )
            self._con.commit()

    def get_request_logs(self, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self._con.execute(
                "SELECT r.id, r.call_id, r.ts, r.request_body, r.response_body, "
                "c.provider, c.model, c.source, c.endpoint "
                "FROM request_log r "
                "LEFT JOIN calls c ON c.id = r.call_id "
                "ORDER BY r.ts DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_request_log(self, log_id: int) -> dict | None:
        with self._lock:
            row = self._con.execute(
                "SELECT r.id, r.call_id, r.ts, r.request_body, r.response_body, "
                "c.provider, c.model, c.source, c.endpoint "
                "FROM request_log r "
                "LEFT JOIN calls c ON c.id = r.call_id "
                "WHERE r.id = ?",
                (log_id,),
            ).fetchone()
        return dict(row) if row else None

    def close(self) -> None:
        self._con.close()


def _date_to_ts(date_str: str) -> int:
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return int(dt.timestamp())


def _percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    n = len(sorted_values)
    if n == 1:
        return sorted_values[0]
    k = (pct / 100) * (n - 1)
    f = int(k)
    c = f + 1
    if c >= n:
        return sorted_values[-1]
    d = k - f
    return sorted_values[f] + d * (sorted_values[c] - sorted_values[f])
