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
CREATE INDEX IF NOT EXISTS idx_calls_ts ON calls(ts);
CREATE INDEX IF NOT EXISTS idx_daily_agg_date ON daily_agg(date);
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

    def insert_call(self, *, ts: int, provider: str, model: str,
                    source: str, source_tag: str | None,
                    input_tokens: int, output_tokens: int,
                    cache_read_tokens: int, cache_write_tokens: int,
                    cost_usd: float, endpoint: str, request_hash: str,
                    user_agent: str = "") -> None:
        with self._lock:
            self._con.execute(
                "INSERT INTO calls (ts,provider,model,source,source_tag,"
                "input_tokens,output_tokens,cache_read_tokens,cache_write_tokens,"
                "cost_usd,endpoint,request_hash,user_agent) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (ts, provider, model, source, source_tag,
                 input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
                 cost_usd, endpoint, request_hash, user_agent),
            )
            self._con.commit()

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

    def close(self) -> None:
        self._con.close()


def _date_to_ts(date_str: str) -> int:
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return int(dt.timestamp())
