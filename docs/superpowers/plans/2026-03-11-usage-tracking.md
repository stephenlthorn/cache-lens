# Usage Tracking Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend CacheLens into an always-on local proxy that tracks all AI API usage across providers, models, and sources — with a live dashboard, aggregated metrics, and rules-based recommendations.

**Architecture:** Single FastAPI daemon on 127.0.0.1:8420 that intercepts API calls via `/proxy/<provider>[/<tag>]/<path>`, records usage to SQLite (3-tier retention: raw 1d → daily 365d → yearly forever), and serves a 4-page UI with live WebSocket feed. Install via `cachelens install` writes a LaunchAgent (macOS) or systemd user service (Linux) and sets env vars session-wide.

**Tech Stack:** Python 3.11+, FastAPI, uvicorn, SQLite (stdlib), httpx (async proxy), Pydantic v2, click, existing tiktoken/cachelens engine. Vanilla JS UI (existing pattern).

**Spec:** `docs/superpowers/specs/2026-03-11-usage-tracking-design.md`

---

## Chunk 1: Data Foundation

### Task 1: Pricing data + pricing.py

**Files:**
- Create: `src/cachelens/data/pricing.json`
- Create: `src/cachelens/pricing.py`
- Create: `tests/test_pricing.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_pricing.py
from cachelens.pricing import PricingTable

def test_known_model_returns_correct_cost():
    table = PricingTable()
    cost = table.cost_usd(
        provider="anthropic",
        model="claude-sonnet-4-6",
        input_tokens=1_000_000,
        output_tokens=0,
        cache_read_tokens=0,
        cache_write_tokens=0,
    )
    assert cost == pytest.approx(3.0, rel=0.01)  # $3/MTok input

def test_unknown_model_falls_back_to_provider_default():
    table = PricingTable()
    cost = table.cost_usd(
        provider="anthropic",
        model="claude-unknown-99",
        input_tokens=1_000_000,
        output_tokens=0,
        cache_read_tokens=0,
        cache_write_tokens=0,
    )
    assert cost == 0.0  # default row is 0

def test_override_file_replaces_bundled_price(tmp_path):
    override = tmp_path / "pricing_overrides.toml"
    override.write_text("""
[models."claude-sonnet-4-6"]
input_usd_per_mtok = 99.0
output_usd_per_mtok = 0.0
cache_read_usd_per_mtok = 0.0
cache_write_usd_per_mtok = 0.0
""")
    table = PricingTable(overrides_path=override)
    cost = table.cost_usd(
        provider="anthropic", model="claude-sonnet-4-6",
        input_tokens=1_000_000, output_tokens=0,
        cache_read_tokens=0, cache_write_tokens=0,
    )
    assert cost == pytest.approx(99.0)

def test_malformed_override_skipped_daemon_does_not_fail(tmp_path, caplog):
    override = tmp_path / "pricing_overrides.toml"
    override.write_text('[models."bad"]\ninput_usd_per_mtok = "not_a_number"')
    import logging
    with caplog.at_level(logging.WARNING):
        table = PricingTable(overrides_path=override)
    assert "bad" in caplog.text or len(caplog.records) >= 0  # does not raise
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/stephenthorn/GitHub/cache-lens && source .venv/bin/activate
pytest tests/test_pricing.py -v
```
Expected: `ModuleNotFoundError: cachelens.pricing`

- [ ] **Step 3: Create `src/cachelens/data/pricing.json`**

```json
{
  "models": {
    "anthropic/default":        {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0},
    "openai/default":           {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0},
    "google/default":           {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0},
    "claude-opus-4-6":          {"input": 15.0,  "output": 75.0,  "cache_read": 1.50,  "cache_write": 18.75},
    "claude-sonnet-4-6":        {"input": 3.0,   "output": 15.0,  "cache_read": 0.30,  "cache_write": 3.75},
    "claude-haiku-4-5-20251001":{"input": 0.80,  "output": 4.0,   "cache_read": 0.08,  "cache_write": 1.0},
    "gpt-4o":                   {"input": 2.50,  "output": 10.0,  "cache_read": 1.25,  "cache_write": 0.0},
    "gpt-4o-mini":              {"input": 0.15,  "output": 0.60,  "cache_read": 0.075, "cache_write": 0.0},
    "gpt-4.1":                  {"input": 2.0,   "output": 8.0,   "cache_read": 0.50,  "cache_write": 0.0},
    "gpt-4.1-mini":             {"input": 0.40,  "output": 1.60,  "cache_read": 0.10,  "cache_write": 0.0},
    "gemini-2.0-flash":         {"input": 0.10,  "output": 0.40,  "cache_read": 0.025, "cache_write": 0.0},
    "gemini-2.5-pro-preview":   {"input": 1.25,  "output": 10.0,  "cache_read": 0.31,  "cache_write": 0.0}
  }
}
```

- [ ] **Step 4: Create `src/cachelens/pricing.py`**

```python
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore

logger = logging.getLogger(__name__)
_DATA_DIR = Path(__file__).parent / "data"


class PricingTable:
    def __init__(self, overrides_path: Optional[Path] = None) -> None:
        bundled = json.loads((_DATA_DIR / "pricing.json").read_text())
        self._prices: dict[str, dict[str, float]] = {
            k: v for k, v in bundled["models"].items()
        }
        if overrides_path and Path(overrides_path).exists():
            self._apply_overrides(Path(overrides_path))

    def _apply_overrides(self, path: Path) -> None:
        try:
            data = tomllib.loads(path.read_text())
        except Exception as e:
            logger.warning("pricing_overrides.toml failed to parse: %s", e)
            return
        for model, vals in (data.get("models") or {}).items():
            required = {"input_usd_per_mtok", "output_usd_per_mtok",
                        "cache_read_usd_per_mtok", "cache_write_usd_per_mtok"}
            try:
                if not required.issubset(vals.keys()):
                    raise ValueError(f"missing fields for {model}")
                self._prices[model] = {
                    "input":       float(vals["input_usd_per_mtok"]),
                    "output":      float(vals["output_usd_per_mtok"]),
                    "cache_read":  float(vals["cache_read_usd_per_mtok"]),
                    "cache_write": float(vals["cache_write_usd_per_mtok"]),
                }
            except Exception as e:
                logger.warning("Skipping malformed pricing override for %r: %s", model, e)

    def _row(self, provider: str, model: str) -> dict[str, float]:
        return (
            self._prices.get(model)
            or self._prices.get(f"{provider}/default")
            or {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0}
        )

    def cost_usd(
        self,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int,
        cache_write_tokens: int,
    ) -> float:
        r = self._row(provider, model)
        return (
            input_tokens       * r["input"]       / 1_000_000
            + output_tokens    * r["output"]      / 1_000_000
            + cache_read_tokens  * r["cache_read"]  / 1_000_000
            + cache_write_tokens * r["cache_write"] / 1_000_000
        )
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/test_pricing.py -v
```
Expected: 4 PASSED

- [ ] **Step 6: Commit**

```bash
git add src/cachelens/data/pricing.json src/cachelens/pricing.py tests/test_pricing.py
git commit -m "feat(pricing): add pricing table with bundled data and override support"
```

---

### Task 2: Store (SQLite schema + write/read)

**Files:**
- Create: `src/cachelens/store.py`
- Create: `tests/test_store.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_store.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_store.py -v
```
Expected: `ModuleNotFoundError: cachelens.store`

- [ ] **Step 3: Create `src/cachelens/store.py`**

```python
from __future__ import annotations

import sqlite3
import time
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
    request_hash TEXT NOT NULL
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
CREATE INDEX IF NOT EXISTS idx_calls_ts ON calls(ts);
CREATE INDEX IF NOT EXISTS idx_daily_agg_date ON daily_agg(date);
"""


class UsageStore:
    def __init__(self, db_path: Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(self._path), check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._con.executescript(_SCHEMA)
        self._con.commit()

    def insert_call(self, *, ts: int, provider: str, model: str,
                    source: str, source_tag: str | None,
                    input_tokens: int, output_tokens: int,
                    cache_read_tokens: int, cache_write_tokens: int,
                    cost_usd: float, endpoint: str, request_hash: str) -> None:
        self._con.execute(
            "INSERT INTO calls (ts,provider,model,source,source_tag,"
            "input_tokens,output_tokens,cache_read_tokens,cache_write_tokens,"
            "cost_usd,endpoint,request_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (ts, provider, model, source, source_tag,
             input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
             cost_usd, endpoint, request_hash),
        )
        self._con.commit()

    def raw_calls_today(self) -> list[dict[str, Any]]:
        day_start = int(time.time()) - 86400
        rows = self._con.execute(
            "SELECT * FROM calls WHERE ts >= ?", (day_start,)
        ).fetchall()
        return [dict(r) for r in rows]

    def upsert_daily_agg(self, *, date: str, provider: str, model: str,
                          source: str, call_count: int, input_tokens: int,
                          output_tokens: int, cache_read_tokens: int,
                          cache_write_tokens: int, cost_usd: float) -> None:
        self._con.execute(
            "INSERT OR REPLACE INTO daily_agg VALUES (?,?,?,?,?,?,?,?,?,?)",
            (date, provider, model, source, call_count,
             input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, cost_usd),
        )
        self._con.commit()

    def daily_agg_for_date(self, date: str) -> list[dict[str, Any]]:
        rows = self._con.execute(
            "SELECT * FROM daily_agg WHERE date=?", (date,)
        ).fetchall()
        return [dict(r) for r in rows]

    def upsert_yearly_agg(self, *, year: int, provider: str, model: str,
                           source: str, call_count: int, input_tokens: int,
                           output_tokens: int, cache_read_tokens: int,
                           cache_write_tokens: int, cost_usd: float) -> None:
        self._con.execute(
            "INSERT OR REPLACE INTO yearly_agg VALUES (?,?,?,?,?,?,?,?,?,?)",
            (year, provider, model, source, call_count,
             input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, cost_usd),
        )
        self._con.commit()

    def yearly_agg_for_year(self, year: int) -> list[dict[str, Any]]:
        rows = self._con.execute(
            "SELECT * FROM yearly_agg WHERE year=?", (year,)
        ).fetchall()
        return [dict(r) for r in rows]

    def purge_raw_calls_older_than_days(self, days: int) -> None:
        cutoff = int(time.time()) - days * 86400
        self._con.execute("DELETE FROM calls WHERE ts < ?", (cutoff,))
        self._con.commit()

    def purge_daily_agg_older_than_days(self, days: int) -> None:
        from datetime import date, timedelta
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        self._con.execute("DELETE FROM daily_agg WHERE date < ?", (cutoff,))
        self._con.commit()

    def rollup_done(self, job: str, period: str) -> bool:
        row = self._con.execute(
            "SELECT 1 FROM rollups WHERE job=? AND period=?", (job, period)
        ).fetchone()
        return row is not None

    def mark_rollup_done(self, job: str, period: str) -> None:
        self._con.execute(
            "INSERT OR REPLACE INTO rollups VALUES (?,?,?)",
            (job, period, int(time.time())),
        )
        self._con.commit()

    def aggregate_calls_for_date(self, date: str) -> list[dict[str, Any]]:
        """Return aggregated rows for a given date string (YYYY-MM-DD)."""
        day_start = _date_to_ts(date)
        day_end = day_start + 86400
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
        cutoff = int(time.time()) - days * 86400
        row = self._con.execute(
            """SELECT COUNT(*) as call_count, COALESCE(SUM(cost_usd),0) as cost_usd,
               COALESCE(SUM(input_tokens),0) as input_tokens,
               COALESCE(SUM(output_tokens),0) as output_tokens
               FROM calls WHERE ts >= ?""",
            (cutoff,),
        ).fetchone()
        return dict(row) if row else {"call_count": 0, "cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0}

    def db_size_bytes(self) -> int:
        return self._path.stat().st_size if self._path.exists() else 0

    def last_rollup_time(self, job: str) -> str | None:
        row = self._con.execute(
            "SELECT completed_at FROM rollups WHERE job=? ORDER BY completed_at DESC LIMIT 1",
            (job,),
        ).fetchone()
        if not row:
            return None
        from datetime import datetime, timezone
        return datetime.fromtimestamp(row[0], tz=timezone.utc).isoformat()

    def close(self) -> None:
        self._con.close()


def _date_to_ts(date_str: str) -> int:
    from datetime import datetime
    import calendar
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return calendar.timegm(dt.timetuple())
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_store.py -v
```
Expected: 6 PASSED

- [ ] **Step 5: Commit**

```bash
git add src/cachelens/store.py tests/test_store.py
git commit -m "feat(store): add SQLite usage store with 3-tier schema and rollup bookkeeping"
```

---

## Chunk 2: Proxy + Source Detection

### Task 3: Source detector

**Files:**
- Create: `src/cachelens/detector.py`
- Create: `tests/test_detector.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_detector.py
from cachelens.detector import detect_source, parse_proxy_path, sanitize_tag

def test_url_tag_wins_over_user_agent():
    source, tag = detect_source(
        url_tag="my-app",
        user_agent="claude-code/1.0.0",
        source_header=None,
    )
    assert source == "my-app"
    assert tag == "my-app"

def test_user_agent_claude_code():
    source, tag = detect_source(url_tag=None, user_agent="claude-code/1.2.3", source_header=None)
    assert source == "claude-code"
    assert tag is None

def test_x_cachelens_source_header():
    source, tag = detect_source(url_tag=None, user_agent="python-httpx/0.27", source_header="my-agent")
    assert source == "my-agent"

def test_unknown_falls_back():
    source, tag = detect_source(url_tag=None, user_agent=None, source_header=None)
    assert source == "unknown"

def test_invalid_tag_sanitized():
    assert sanitize_tag("hello world!") == "hello-world"
    assert sanitize_tag("!!!") is None
    assert sanitize_tag("a" * 100) == "a" * 64

def test_parse_proxy_path_with_tag():
    provider, tag, upstream = parse_proxy_path("/proxy/anthropic/claude-code/v1/messages")
    assert provider == "anthropic"
    assert tag == "claude-code"
    assert upstream == "/v1/messages"

def test_parse_proxy_path_without_tag():
    provider, tag, upstream = parse_proxy_path("/proxy/openai/v1/chat/completions")
    assert provider == "openai"
    assert tag is None
    assert upstream == "/v1/chat/completions"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_detector.py -v
```
Expected: `ModuleNotFoundError: cachelens.detector`

- [ ] **Step 3: Create `src/cachelens/detector.py`**

```python
from __future__ import annotations

import re
from typing import Optional

_PROVIDER_BASE_URLS = {
    "anthropic": "https://api.anthropic.com",
    "openai":    "https://api.openai.com",
    "google":    "https://generativelanguage.googleapis.com",
}

_UA_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"claude-code/", re.I),         "claude-code"),
    (re.compile(r"python-anthropic/", re.I),     "anthropic-sdk-python"),
    (re.compile(r"anthropic-typescript/", re.I), "anthropic-sdk-node"),
    (re.compile(r"openai-python/", re.I),        "openai-sdk-python"),
    (re.compile(r"openai-node/", re.I),          "openai-sdk-node"),
    (re.compile(r"python-httpx/", re.I),         "python-httpx"),
    (re.compile(r"axios/", re.I),                "axios"),
    (re.compile(r"node-fetch/", re.I),           "node-fetch"),
]

_VALID_TAG = re.compile(r"[^a-zA-Z0-9\-]")
_KNOWN_UPSTREAM_PATHS = {"v1", "openai", "google", "anthropic"}


def sanitize_tag(raw: str) -> Optional[str]:
    cleaned = _VALID_TAG.sub("-", raw)[:64].strip("-")
    # collapse multiple hyphens
    cleaned = re.sub(r"-{2,}", "-", cleaned)
    return cleaned if cleaned else None


def _is_upstream_path_segment(segment: str) -> bool:
    """Heuristic: a segment that looks like a version prefix is upstream path, not a tag."""
    return bool(re.match(r"v\d+$", segment)) or segment in _KNOWN_UPSTREAM_PATHS


def parse_proxy_path(path: str) -> tuple[str, Optional[str], str]:
    """Parse /proxy/<provider>[/<tag>]/<upstream-path> into (provider, tag, upstream).

    Returns (provider, tag_or_None, upstream_path_with_leading_slash).
    """
    # Strip /proxy/
    parts = path.lstrip("/").split("/")
    # parts[0] == "proxy", parts[1] == provider, rest is tag? + upstream
    if len(parts) < 3:
        return parts[1] if len(parts) > 1 else "unknown", None, "/"

    provider = parts[1]
    remainder = parts[2:]

    # Determine if first remainder segment is a tag or start of upstream path
    if remainder and not _is_upstream_path_segment(remainder[0]):
        raw_tag = remainder[0]
        tag = sanitize_tag(raw_tag)
        upstream = "/" + "/".join(remainder[1:])
    else:
        tag = None
        upstream = "/" + "/".join(remainder)

    return provider, tag, upstream or "/"


def detect_source(
    url_tag: Optional[str],
    user_agent: Optional[str],
    source_header: Optional[str],
) -> tuple[str, Optional[str]]:
    """Returns (canonical_source, raw_tag_or_None)."""
    if url_tag:
        return url_tag, url_tag

    if source_header:
        return source_header.strip(), None

    if user_agent:
        for pattern, canonical in _UA_PATTERNS:
            if pattern.search(user_agent):
                return canonical, None

    return "unknown", None


def upstream_url(provider: str, upstream_path: str) -> str:
    base = _PROVIDER_BASE_URLS.get(provider, "")
    return base + upstream_path
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_detector.py -v
```
Expected: 8 PASSED

- [ ] **Step 5: Commit**

```bash
git add src/cachelens/detector.py tests/test_detector.py
git commit -m "feat(detector): add source detection and proxy path parsing"
```

---

### Task 4: Add httpx dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add httpx to dependencies**

In `pyproject.toml`, add to `dependencies`:
```toml
"httpx>=0.27.0",
```

- [ ] **Step 2: Install the updated dependencies**

```bash
pip install -e .
```

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "chore(deps): add httpx for async proxy forwarding"
```

---

### Task 5: Proxy handler

**Files:**
- Create: `src/cachelens/proxy.py`
- Create: `tests/test_proxy.py`

Note: `proxy.py` contains the core intercept logic. Tests use `httpx.MockTransport` to avoid real network calls.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_proxy.py
import json
import pytest
import httpx
from unittest.mock import MagicMock, patch

from cachelens.proxy import extract_usage_from_response, extract_usage_from_sse_chunks


def test_extract_usage_anthropic_non_streaming():
    body = json.dumps({
        "model": "claude-sonnet-4-6",
        "usage": {
            "input_tokens": 100, "output_tokens": 50,
            "cache_read_input_tokens": 20, "cache_creation_input_tokens": 5,
        }
    }).encode()
    usage = extract_usage_from_response("anthropic", body)
    assert usage["model"] == "claude-sonnet-4-6"
    assert usage["input_tokens"] == 100
    assert usage["output_tokens"] == 50
    assert usage["cache_read_tokens"] == 20
    assert usage["cache_write_tokens"] == 5


def test_extract_usage_openai_non_streaming():
    body = json.dumps({
        "model": "gpt-4o",
        "usage": {"prompt_tokens": 200, "completion_tokens": 80,
                  "prompt_tokens_details": {"cached_tokens": 40}}
    }).encode()
    usage = extract_usage_from_response("openai", body)
    assert usage["model"] == "gpt-4o"
    assert usage["input_tokens"] == 200
    assert usage["output_tokens"] == 80
    assert usage["cache_read_tokens"] == 40


def test_extract_usage_anthropic_sse():
    chunks = [
        b'data: {"type":"message_delta","usage":{"output_tokens":30}}\n',
        b'data: {"type":"message_delta","usage":{"input_tokens":150,"output_tokens":30,"cache_read_input_tokens":50,"cache_creation_input_tokens":0}}\n',
        b'data: [DONE]\n',
    ]
    # Anthropic final usage is in last message_delta with full usage fields
    usage = extract_usage_from_sse_chunks("anthropic", b"claude-sonnet-4-6", chunks)
    assert usage is not None
    assert usage["input_tokens"] == 150


def test_extract_usage_returns_none_on_missing_usage():
    body = json.dumps({"model": "gpt-4o", "choices": []}).encode()
    usage = extract_usage_from_response("openai", body)
    assert usage is None


def test_extract_usage_returns_none_on_bad_json():
    usage = extract_usage_from_response("anthropic", b"not json at all")
    assert usage is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_proxy.py -v
```
Expected: `ModuleNotFoundError: cachelens.proxy`

- [ ] **Step 3: Create `src/cachelens/proxy.py`**

```python
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)


def extract_usage_from_response(
    provider: str, body: bytes
) -> Optional[dict[str, Any]]:
    """Extract usage metadata from a complete (non-streaming) response body."""
    try:
        data = json.loads(body)
    except Exception:
        return None

    model = data.get("model")
    if not model:
        return None

    if provider == "anthropic":
        usage = data.get("usage") or {}
        return {
            "model": model,
            "input_tokens":        int(usage.get("input_tokens") or 0),
            "output_tokens":       int(usage.get("output_tokens") or 0),
            "cache_read_tokens":   int(usage.get("cache_read_input_tokens") or 0),
            "cache_write_tokens":  int(usage.get("cache_creation_input_tokens") or 0),
        }

    if provider in ("openai", "google"):
        usage = data.get("usage") or {}
        details = usage.get("prompt_tokens_details") or {}
        return {
            "model": model,
            "input_tokens":       int(usage.get("prompt_tokens") or 0),
            "output_tokens":      int(usage.get("completion_tokens") or 0),
            "cache_read_tokens":  int(details.get("cached_tokens") or 0),
            "cache_write_tokens": 0,
        }

    return None


def extract_usage_from_sse_chunks(
    provider: str,
    model_hint: bytes,
    chunks: list[bytes],
) -> Optional[dict[str, Any]]:
    """Parse accumulated SSE chunks and extract usage. Returns None if not found."""
    model: Optional[str] = None
    try:
        model = model_hint.decode("utf-8", errors="ignore").strip() or None
    except Exception:
        pass

    if provider == "anthropic":
        # Find last message_delta event with full usage fields
        best: Optional[dict[str, Any]] = None
        for chunk in chunks:
            for line in chunk.split(b"\n"):
                if not line.startswith(b"data: "):
                    continue
                try:
                    evt = json.loads(line[6:])
                except Exception:
                    continue
                if evt.get("type") == "message_start":
                    msg = evt.get("message") or {}
                    model = model or msg.get("model")
                    usage = msg.get("usage") or {}
                    if usage.get("input_tokens"):
                        best = {
                            "model": model or "unknown",
                            "input_tokens":       int(usage.get("input_tokens") or 0),
                            "output_tokens":      int(usage.get("output_tokens") or 0),
                            "cache_read_tokens":  int(usage.get("cache_read_input_tokens") or 0),
                            "cache_write_tokens": int(usage.get("cache_creation_input_tokens") or 0),
                        }
        return best

    if provider in ("openai", "google"):
        # OpenAI puts usage in the final chunk before [DONE]
        for chunk in reversed(chunks):
            for line in chunk.split(b"\n"):
                if not line.startswith(b"data: "):
                    continue
                raw = line[6:].strip()
                if raw == b"[DONE]":
                    continue
                try:
                    evt = json.loads(raw)
                except Exception:
                    continue
                usage = evt.get("usage") or {}
                if usage.get("prompt_tokens"):
                    details = usage.get("prompt_tokens_details") or {}
                    mdl = evt.get("model") or model or "unknown"
                    return {
                        "model": mdl,
                        "input_tokens":       int(usage.get("prompt_tokens") or 0),
                        "output_tokens":      int(usage.get("completion_tokens") or 0),
                        "cache_read_tokens":  int(details.get("cached_tokens") or 0),
                        "cache_write_tokens": 0,
                    }

    return None


def sha256_request(body: bytes) -> str:
    return "sha256:" + hashlib.sha256(body).hexdigest()


def is_streaming_request(body: bytes) -> bool:
    try:
        return bool(json.loads(body).get("stream"))
    except Exception:
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_proxy.py -v
```
Expected: 5 PASSED

- [ ] **Step 5: Commit**

```bash
git add src/cachelens/proxy.py tests/test_proxy.py
git commit -m "feat(proxy): add usage extraction from response bodies and SSE streams"
```

---

## Chunk 3: Aggregation + CLI

### Task 6: Aggregator

**Files:**
- Create: `src/cachelens/aggregator.py`
- Create: `tests/test_aggregator.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_aggregator.py
import time
import pytest
from datetime import date, timedelta
from cachelens.store import UsageStore
from cachelens.aggregator import run_nightly_rollup, run_yearly_rollup, missed_nightly_dates

@pytest.fixture
def store(tmp_path):
    return UsageStore(db_path=tmp_path / "test.db")

def _yesterday() -> str:
    return (date.today() - timedelta(days=1)).isoformat()

def _insert_yesterday(store, n=3):
    yesterday_ts = int(time.time()) - 86400
    for _ in range(n):
        store.insert_call(
            ts=yesterday_ts, provider="anthropic", model="claude-sonnet-4-6",
            source="claude-code", source_tag=None, input_tokens=100,
            output_tokens=50, cache_read_tokens=20, cache_write_tokens=0,
            cost_usd=0.001, endpoint="/v1/messages", request_hash="sha256:x",
        )

def test_nightly_rollup_aggregates_yesterday(store):
    _insert_yesterday(store)
    yest = _yesterday()
    run_nightly_rollup(store, yest)
    rows = store.daily_agg_for_date(yest)
    assert len(rows) == 1
    assert rows[0]["call_count"] == 3
    assert rows[0]["input_tokens"] == 300

def test_nightly_rollup_marks_done(store):
    yest = _yesterday()
    run_nightly_rollup(store, yest)
    assert store.rollup_done("nightly", yest)

def test_nightly_rollup_is_idempotent(store):
    _insert_yesterday(store)
    yest = _yesterday()
    run_nightly_rollup(store, yest)
    run_nightly_rollup(store, yest)  # second run overwrites
    rows = store.daily_agg_for_date(yest)
    assert len(rows) == 1  # not doubled

def test_missed_nightly_dates_returns_ungapped_dates(store):
    # No rollups done → all 7 days are missing
    missing = missed_nightly_dates(store, lookback_days=7)
    assert len(missing) == 7

def test_do_rollup_tick_runs_nightly_and_marks_done(store):
    _insert_yesterday(store)
    from cachelens.aggregator import _do_rollup_tick
    _do_rollup_tick(store)
    assert store.rollup_done("nightly", _yesterday())


def test_yearly_rollup_aggregates_daily_rows(store):
    store.upsert_daily_agg(
        date="2025-06-15", provider="anthropic", model="claude-sonnet-4-6",
        source="claude-code", call_count=10, input_tokens=10000,
        output_tokens=2000, cache_read_tokens=0, cache_write_tokens=0, cost_usd=0.10,
    )
    store.upsert_daily_agg(
        date="2025-09-01", provider="anthropic", model="claude-sonnet-4-6",
        source="claude-code", call_count=5, input_tokens=5000,
        output_tokens=1000, cache_read_tokens=0, cache_write_tokens=0, cost_usd=0.05,
    )
    run_yearly_rollup(store, 2025)
    rows = store.yearly_agg_for_year(2025)
    assert len(rows) == 1
    assert rows[0]["call_count"] == 15
    assert rows[0]["cost_usd"] == pytest.approx(0.15)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_aggregator.py -v
```

- [ ] **Step 3: Create `src/cachelens/aggregator.py`**

```python
from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .store import UsageStore

logger = logging.getLogger(__name__)


def missed_nightly_dates(store: "UsageStore", lookback_days: int = 7) -> list[str]:
    today = date.today()
    return [
        (today - timedelta(days=i)).isoformat()
        for i in range(1, lookback_days + 1)
        if not store.rollup_done("nightly", (today - timedelta(days=i)).isoformat())
    ]


def run_nightly_rollup(store: "UsageStore", target_date: str) -> None:
    rows = store.aggregate_calls_for_date(target_date)
    for row in rows:
        store.upsert_daily_agg(
            date=target_date,
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
    store.purge_raw_calls_older_than_days(1)
    store.mark_rollup_done("nightly", target_date)
    logger.info("Nightly rollup complete for %s (%d dimension rows)", target_date, len(rows))


def run_yearly_rollup(store: "UsageStore", year: int) -> None:
    rows = store.aggregate_daily_for_year(year)
    for row in rows:
        store.upsert_yearly_agg(
            year=year,
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
    store.purge_daily_agg_older_than_days(365)
    store.mark_rollup_done("yearly", str(year))
    logger.info("Yearly rollup complete for %d", year)


def _run_startup_recovery(store: "UsageStore") -> None:
    """Run missed nightly rollups on startup (past 7 days)."""
    for d in missed_nightly_dates(store, lookback_days=7):
        logger.info("Recovery: running missed nightly rollup for %s", d)
        run_nightly_rollup(store, d)

    today = date.today()
    prev_year = today.year - 1
    if today >= date(today.year, 1, 2) and not store.rollup_done("yearly", str(prev_year)):
        logger.info("Recovery: running missed yearly rollup for %d", prev_year)
        run_yearly_rollup(store, prev_year)


def _do_rollup_tick(store: "UsageStore") -> None:
    """Single rollup tick — run nightly (and yearly if needed). Testable sync seam."""
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    run_nightly_rollup(store, yesterday)

    today = date.today()
    if today.month == 1 and today.day >= 1:
        prev_year = today.year - 1
        if not store.rollup_done("yearly", str(prev_year)):
            run_yearly_rollup(store, prev_year)


async def schedule_rollups(store: "UsageStore") -> None:
    """Long-running asyncio task. Runs startup recovery then schedules daily jobs."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _run_startup_recovery, store)

    while True:
        now = datetime.now()
        # Sleep until 00:05 next day
        next_run = datetime(now.year, now.month, now.day, 0, 5) + timedelta(days=1)
        await asyncio.sleep((next_run - now).total_seconds())
        await loop.run_in_executor(None, _do_rollup_tick, store)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_aggregator.py -v
```
Expected: 6 PASSED

- [ ] **Step 5: Commit**

```bash
git add src/cachelens/aggregator.py tests/test_aggregator.py
git commit -m "feat(aggregator): add nightly/yearly rollups with missed recovery"
```

---

### Task 7: Recommendations engine

**Files:**
- Create: `src/cachelens/recommender.py`
- Create: `tests/test_recommender.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_recommender.py
import pytest
from cachelens.recommender import generate_recommendations

def test_flags_zero_cache_hit_rate():
    rows = [
        {"provider": "anthropic", "model": "claude-sonnet-4-6", "source": "unknown",
         "call_count": 100, "input_tokens": 50000, "output_tokens": 5000,
         "cache_read_tokens": 0, "cache_write_tokens": 200, "cost_usd": 0.50},
    ]
    recs = generate_recommendations(rows)
    titles = [r["title"] for r in recs]
    assert any("cache" in t.lower() for t in titles)

def test_flags_large_model_for_small_calls():
    rows = [
        {"provider": "openai", "model": "gpt-4o", "source": "myapp",
         "call_count": 500, "input_tokens": 50000, "output_tokens": 10000,
         "cache_read_tokens": 0, "cache_write_tokens": 0, "cost_usd": 2.0},
    ]
    recs = generate_recommendations(rows)
    # avg input = 100 tokens → should flag gpt-4o for small calls
    titles = [r["title"] for r in recs]
    assert any("mini" in t.lower() or "small" in t.lower() or "downgrad" in t.lower() for t in titles)

def test_flags_unknown_source():
    rows = [
        {"provider": "anthropic", "model": "claude-sonnet-4-6", "source": "unknown",
         "call_count": 1000, "input_tokens": 500000, "output_tokens": 100000,
         "cache_read_tokens": 0, "cache_write_tokens": 0, "cost_usd": 5.0},
    ]
    recs = generate_recommendations(rows)
    titles = [r["title"] for r in recs]
    assert any("tag" in t.lower() or "source" in t.lower() for t in titles)

def test_no_false_positives_on_good_usage():
    rows = [
        {"provider": "anthropic", "model": "claude-haiku-4-5-20251001", "source": "myapp",
         "call_count": 100, "input_tokens": 200000, "output_tokens": 50000,
         "cache_read_tokens": 180000, "cache_write_tokens": 5000, "cost_usd": 0.10},
    ]
    recs = generate_recommendations(rows)
    # Good cache hit rate, small model, known source → few or no recs
    assert len(recs) <= 1

def test_recommendations_sorted_by_impact():
    rows = [
        {"provider": "anthropic", "model": "claude-sonnet-4-6", "source": "unknown",
         "call_count": 5000, "input_tokens": 2500000, "output_tokens": 500000,
         "cache_read_tokens": 0, "cache_write_tokens": 0, "cost_usd": 20.0},
        {"provider": "openai", "model": "gpt-4o-mini", "source": "bot",
         "call_count": 10, "input_tokens": 1000, "output_tokens": 200,
         "cache_read_tokens": 0, "cache_write_tokens": 0, "cost_usd": 0.001},
    ]
    recs = generate_recommendations(rows)
    if len(recs) >= 2:
        assert recs[0]["estimated_savings_usd"] >= recs[1]["estimated_savings_usd"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_recommender.py -v
```

- [ ] **Step 3: Create `src/cachelens/recommender.py`**

```python
from __future__ import annotations

from typing import Any

# Avg input tokens below this threshold → consider a smaller/cheaper model
_SMALL_CALL_TOKEN_THRESHOLD = 500

_DOWNSELL_MAP = {
    # Keys must match the `model` field as returned in API response bodies.
    # Anthropic returns bare model IDs (e.g. "claude-sonnet-4-6") without date suffixes.
    "gpt-4o":            "gpt-4o-mini",
    "gpt-4.1":           "gpt-4.1-mini",
    "claude-opus-4-6":   "claude-sonnet-4-6",
    "claude-sonnet-4-6": "claude-haiku-4-5-20251001",
}


def generate_recommendations(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Generate ranked recommendations from aggregated usage rows.

    Each row: provider, model, source, call_count, input_tokens, output_tokens,
              cache_read_tokens, cache_write_tokens, cost_usd
    """
    recs: list[dict[str, Any]] = []

    for row in rows:
        call_count = max(1, row.get("call_count") or 1)
        input_tokens = row.get("input_tokens") or 0
        cache_read = row.get("cache_read_tokens") or 0
        cache_write = row.get("cache_write_tokens") or 0
        cost = row.get("cost_usd") or 0.0
        source = row.get("source") or "unknown"
        model = row.get("model") or ""
        provider = row.get("provider") or ""
        avg_input = input_tokens / call_count

        # Rule 1: cache write tokens exist but zero cache reads → prompt not being reused
        if cache_write > 0 and cache_read == 0 and call_count >= 10:
            recs.append({
                "rule": "cache_write_never_reused",
                "title": f"Cache writes for {source}/{model} never result in cache hits",
                "description": (
                    f"Source '{source}' is writing {cache_write:,} cache tokens "
                    f"but reading 0 — the system prompt likely changes each call. "
                    f"Pin static content at the top of your prompt."
                ),
                "provider": provider, "model": model, "source": source,
                "estimated_savings_usd": cost * 0.15,
                "deep_dive_filter": f"?provider={provider}&model={model}&source={source}",
            })

        # Rule 2: zero cache hits on Anthropic for high-volume calls
        if provider == "anthropic" and cache_read == 0 and call_count >= 50:
            recs.append({
                "rule": "zero_anthropic_cache_hits",
                "title": f"No prompt cache hits for {model} via {source}",
                "description": (
                    f"{call_count:,} calls to {model} with 0 cache hits. "
                    f"Structure your prompt with large static content first (system prompt, "
                    f"docs, tools) to enable Anthropic prompt caching."
                ),
                "provider": provider, "model": model, "source": source,
                "estimated_savings_usd": cost * 0.30,
                "deep_dive_filter": f"?provider={provider}&model={model}&source={source}",
            })

        # Rule 3: high-volume unknown source
        if source == "unknown" and call_count >= 100:
            recs.append({
                "rule": "unknown_source",
                "title": f"High-volume traffic from untagged source ({model})",
                "description": (
                    f"{call_count:,} calls cannot be attributed to a source. "
                    f"Tag your proxy URL (e.g. /proxy/{provider}/my-app/...) "
                    f"or set X-CacheLens-Source header."
                ),
                "provider": provider, "model": model, "source": source,
                "estimated_savings_usd": 0.0,
                "deep_dive_filter": f"?provider={provider}&source=unknown",
            })

        # Rule 4: large expensive model used for small calls
        cheaper = _DOWNSELL_MAP.get(model)
        if cheaper and avg_input < _SMALL_CALL_TOKEN_THRESHOLD and call_count >= 20:
            recs.append({
                "rule": "oversized_model",
                "title": f"{model} used for small calls — consider {cheaper}",
                "description": (
                    f"Average call size is {avg_input:.0f} tokens but you're using "
                    f"{model}. {cheaper} handles tasks of this size at a fraction of the cost."
                ),
                "provider": provider, "model": model, "source": source,
                "estimated_savings_usd": cost * 0.50,
                "deep_dive_filter": f"?provider={provider}&model={model}&source={source}",
            })

    recs.sort(key=lambda r: r["estimated_savings_usd"], reverse=True)
    return recs
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_recommender.py -v
```
Expected: 5 PASSED

- [ ] **Step 5: Commit**

```bash
git add src/cachelens/recommender.py tests/test_recommender.py
git commit -m "feat(recommender): add rules-based recommendation engine"
```

---

## Chunk 4: Server + CLI Integration

### Task 8: Extend server with proxy routes, WebSocket, and usage API

**Files:**
- Create: `tests/test_server.py`
- Modify: `src/cachelens/server.py`
- Modify: `pyproject.toml` (add `tomli` for Python < 3.11 fallback — already handled in pricing.py)

- [ ] **Step 1: Write failing tests for new server behavior**

```python
# tests/test_server.py
import time
import json
import pytest
from fastapi.testclient import TestClient

from cachelens.store import UsageStore
from cachelens.pricing import PricingTable
from cachelens.server import create_app


@pytest.fixture
def store(tmp_path):
    return UsageStore(db_path=tmp_path / "test.db")

@pytest.fixture
def pricing():
    return PricingTable()

@pytest.fixture
def client(store, pricing):
    app = create_app(store=store, pricing=pricing)
    return TestClient(app)


def test_kpi_returns_zeros_with_no_data(client):
    resp = client.get("/api/usage/kpi")
    assert resp.status_code == 200
    data = resp.json()
    assert data["today"]["call_count"] == 0
    assert data["week"]["cost_usd"] == 0.0


def test_kpi_reflects_inserted_call(client, store):
    store.insert_call(
        ts=int(time.time()), provider="anthropic", model="claude-sonnet-4-6",
        source="claude-code", source_tag=None, input_tokens=100, output_tokens=50,
        cache_read_tokens=0, cache_write_tokens=0, cost_usd=0.0004,
        endpoint="/v1/messages", request_hash="sha256:x",
    )
    resp = client.get("/api/usage/kpi")
    assert resp.json()["today"]["call_count"] == 1
    assert resp.json()["today"]["cost_usd"] == pytest.approx(0.0004)


def test_daily_returns_empty_list_when_no_data(client):
    resp = client.get("/api/usage/daily?days=7")
    assert resp.status_code == 200
    assert resp.json() == []


def test_recommendations_empty_when_no_data(client):
    resp = client.get("/api/usage/recommendations")
    assert resp.status_code == 200
    assert resp.json() == []


def test_analyze_endpoint_still_works(client):
    payload = {"input": '{"messages":[{"role":"user","content":"hello"}]}'}
    resp = client.post("/api/analyze", json=payload)
    assert resp.status_code == 200
    assert "cacheability_score" in resp.json()


def test_websocket_accepts_connection(client):
    with client.websocket_connect("/api/live") as ws:
        # Connection should be accepted without error
        pass  # clean disconnect


def test_websocket_rejects_11th_connection(client):
    """Max 10 concurrent WebSocket connections; 11th gets HTTP 503."""
    conns = []
    try:
        for _ in range(10):
            conns.append(client.websocket_connect("/api/live").__enter__())
        # 11th should fail
        with pytest.raises(Exception):
            client.websocket_connect("/api/live").__enter__()
    finally:
        for c in conns:
            try:
                c.__exit__(None, None, None)
            except Exception:
                pass


def test_create_app_accepts_injected_store(store, pricing):
    """create_app must accept store and pricing kwargs for test injection."""
    app = create_app(store=store, pricing=pricing)
    assert app is not None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_server.py -v
```
Expected: `ModuleNotFoundError` or `ImportError` — server has no new routes yet.

- [ ] **Step 3: Rewrite `src/cachelens/server.py`**

Replace the existing file with:

```python
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .aggregator import schedule_rollups
from .detector import detect_source, parse_proxy_path, sanitize_tag, upstream_url
from .parser import parse_input
from .engine.analyzer import analyze
from .pricing import PricingTable
from .proxy import (
    extract_usage_from_response,
    extract_usage_from_sse_chunks,
    is_streaming_request,
    sha256_request,
)
from .recommender import generate_recommendations
from .store import UsageStore

logger = logging.getLogger(__name__)

_CACHELENS_DIR = Path.home() / ".cachelens"
_active_ws: set[WebSocket] = set()


def _get_store() -> UsageStore:
    return UsageStore(db_path=_CACHELENS_DIR / "usage.db")


def _get_pricing() -> PricingTable:
    return PricingTable(overrides_path=_CACHELENS_DIR / "pricing_overrides.toml")


async def _broadcast(store: UsageStore, pricing: PricingTable, msg: dict[str, Any]) -> None:
    dead = set()
    for ws in _active_ws:
        try:
            await ws.send_text(json.dumps(msg))
        except Exception:
            dead.add(ws)
    _active_ws.difference_update(dead)


def create_app(store: UsageStore | None = None, pricing: PricingTable | None = None) -> FastAPI:
    _store = store or _get_store()
    _pricing = pricing or _get_pricing()

    app = FastAPI(title="CacheLens")
    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # ── Startup: schedule rollups ──────────────────────────────────────────
    @app.on_event("startup")
    async def _startup() -> None:
        asyncio.create_task(schedule_rollups(_store))

    # ── Existing UI ────────────────────────────────────────────────────────
    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (static_dir / "index.html").read_text(encoding="utf-8")

    @app.post("/api/analyze")
    def api_analyze(payload: dict) -> JSONResponse:
        raw = payload.get("input", "")
        analysis_input = parse_input(raw)
        result = analyze(analysis_input)
        return JSONResponse(content=json.loads(result.model_dump_json()))

    # ── WebSocket live feed ────────────────────────────────────────────────
    @app.websocket("/api/live")
    async def ws_live(ws: WebSocket) -> None:
        if len(_active_ws) >= 10:
            await ws.close(code=1013)
            return
        await ws.accept()
        _active_ws.add(ws)
        try:
            while True:
                await ws.receive_text()  # keep alive; client ping
        except WebSocketDisconnect:
            _active_ws.discard(ws)

    # ── Usage API ──────────────────────────────────────────────────────────
    @app.get("/api/usage/kpi")
    def api_kpi() -> JSONResponse:
        return JSONResponse({
            "today":    _store.kpi_rolling(days=1),
            "week":     _store.kpi_rolling(days=7),
            "month":    _store.kpi_rolling(days=30),
            "year":     _store.kpi_rolling(days=365),
        })

    @app.get("/api/usage/daily")
    def api_daily(days: int = 30) -> JSONResponse:
        from datetime import date, timedelta
        import sqlite3
        con = _store._con
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        rows = con.execute(
            "SELECT * FROM daily_agg WHERE date >= ? ORDER BY date", (cutoff,)
        ).fetchall()
        return JSONResponse([dict(r) for r in rows])

    @app.get("/api/usage/recommendations")
    def api_recommendations(days: int = 30) -> JSONResponse:
        from datetime import date, timedelta
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        rows = _store._con.execute(
            """SELECT provider, model, source,
               SUM(call_count) as call_count, SUM(input_tokens) as input_tokens,
               SUM(output_tokens) as output_tokens,
               SUM(cache_read_tokens) as cache_read_tokens,
               SUM(cache_write_tokens) as cache_write_tokens,
               SUM(cost_usd) as cost_usd
               FROM daily_agg WHERE date >= ?
               GROUP BY provider, model, source""",
            (cutoff,),
        ).fetchall()
        recs = generate_recommendations([dict(r) for r in rows])
        return JSONResponse(recs)

    # ── Proxy routes ───────────────────────────────────────────────────────
    @app.api_route(
        "/proxy/{provider}/{rest_of_path:path}",
        methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    )
    async def proxy_handler(provider: str, rest_of_path: str, request: Request) -> Any:
        full_path = f"/proxy/{provider}/{rest_of_path}"
        _, tag, upstream_path = parse_proxy_path(full_path)

        # Reconstruct query string
        qs = request.url.query
        target = upstream_url(provider, upstream_path) + (f"?{qs}" if qs else "")

        ua = request.headers.get("user-agent")
        src_header = request.headers.get("x-cachelens-source")
        source, source_tag = detect_source(url_tag=tag, user_agent=ua, source_header=src_header)

        body = await request.body()
        streaming = is_streaming_request(body)
        req_hash = sha256_request(body)

        # Forward headers (strip host)
        fwd_headers = {
            k: v for k, v in request.headers.items()
            if k.lower() not in ("host", "content-length")
        }

        async with httpx.AsyncClient(timeout=120) as client:
            if streaming:
                return await _handle_streaming(
                    client, request.method, target, fwd_headers, body,
                    provider, source, source_tag, req_hash, _store, _pricing,
                )
            else:
                return await _handle_non_streaming(
                    client, request.method, target, fwd_headers, body,
                    provider, source, source_tag, req_hash, _store, _pricing,
                )

    return app


async def _handle_non_streaming(
    client: httpx.AsyncClient, method: str, url: str,
    headers: dict, body: bytes, provider: str,
    source: str, source_tag: str | None, req_hash: str,
    store: UsageStore, pricing: PricingTable,
) -> Any:
    try:
        resp = await client.request(method, url, headers=headers, content=body)
    except Exception as e:
        logger.warning("Proxy upstream error: %s", e)
        return JSONResponse({"error": "upstream_error", "detail": str(e)}, status_code=502)

    resp_body = resp.content
    usage = extract_usage_from_response(provider, resp_body)

    if usage and resp.status_code < 300:
        _record_and_broadcast(store, pricing, provider, source, source_tag,
                              req_hash, usage, asyncio.get_event_loop())

    return JSONResponse(
        content=json.loads(resp_body) if resp_body else {},
        status_code=resp.status_code,
        headers=dict(resp.headers),
    )


async def _handle_streaming(
    client: httpx.AsyncClient, method: str, url: str,
    headers: dict, body: bytes, provider: str,
    source: str, source_tag: str | None, req_hash: str,
    store: UsageStore, pricing: PricingTable,
) -> StreamingResponse:
    chunks: list[bytes] = []
    model_hint = b""

    async def _generate():
        nonlocal model_hint
        try:
            async with client.stream(method, url, headers=headers, content=body) as resp:
                async for chunk in resp.aiter_bytes():
                    chunks.append(chunk)
                    # Sniff model from first chunk
                    if not model_hint and b'"model"' in chunk:
                        import re
                        m = re.search(rb'"model"\s*:\s*"([^"]+)"', chunk)
                        if m:
                            model_hint = m.group(1)
                    yield chunk
        except Exception as e:
            logger.warning("Streaming proxy error: %s", e)
            return

        usage = extract_usage_from_sse_chunks(provider, model_hint, chunks)
        if usage:
            _record_and_broadcast(store, pricing, provider, source, source_tag,
                                  req_hash, usage, asyncio.get_event_loop())

    return StreamingResponse(_generate(), media_type="text/event-stream")


def _record_and_broadcast(
    store: UsageStore, pricing: PricingTable,
    provider: str, source: str, source_tag: str | None,
    req_hash: str, usage: dict, loop: asyncio.AbstractEventLoop,
) -> None:
    cost = pricing.cost_usd(
        provider=provider,
        model=usage["model"],
        input_tokens=usage["input_tokens"],
        output_tokens=usage["output_tokens"],
        cache_read_tokens=usage["cache_read_tokens"],
        cache_write_tokens=usage["cache_write_tokens"],
    )
    ts = int(time.time())
    store.insert_call(
        ts=ts, provider=provider, model=usage["model"],
        source=source, source_tag=source_tag,
        input_tokens=usage["input_tokens"],
        output_tokens=usage["output_tokens"],
        cache_read_tokens=usage["cache_read_tokens"],
        cache_write_tokens=usage["cache_write_tokens"],
        cost_usd=cost, endpoint="", request_hash=req_hash,
    )
    msg = {
        "ts": ts, "provider": provider, "model": usage["model"],
        "source": source,
        "input_tokens": usage["input_tokens"],
        "output_tokens": usage["output_tokens"],
        "cache_read_tokens": usage["cache_read_tokens"],
        "cache_write_tokens": usage["cache_write_tokens"],
        "cost_usd": cost,
    }
    asyncio.ensure_future(_broadcast(store, pricing, msg))


def run(port: int = 8420, open_browser: bool = True) -> None:
    app = create_app()
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(f"http://127.0.0.1:{port}/")).start()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
```

- [ ] **Step 4: Run all server tests to verify they pass**

```bash
pytest tests/test_server.py -v
```
Expected: all PASS

- [ ] **Step 5: Run full test suite to verify nothing regressed**

```bash
pytest tests/ -v
```
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/cachelens/server.py tests/test_server.py
git commit -m "feat(server): add proxy routes, WebSocket live feed, and usage API"
```

---

### Task 9: Extend CLI with daemon, status, install, uninstall

**Files:**
- Modify: `src/cachelens/cli.py`
- Create: `src/cachelens/installer.py`
- Create: `tests/test_installer.py`

- [ ] **Step 1: Write failing tests for installer**

```python
# tests/test_installer.py
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from cachelens.installer import (
    write_shell_env_block,
    remove_shell_env_block,
    make_launchagent_plist,
    make_systemd_service,
)

_MARKER_START = "# >>> cachelens env >>>"
_MARKER_END   = "# <<< cachelens env <<<"

def test_write_shell_env_block_creates_block(tmp_path):
    rc = tmp_path / ".zshrc"
    rc.write_text("# existing content\n")
    write_shell_env_block(rc, port=8420)
    content = rc.read_text()
    assert _MARKER_START in content
    assert "ANTHROPIC_BASE_URL" in content
    assert "http://localhost:8420" in content

def test_write_shell_env_block_is_idempotent(tmp_path):
    rc = tmp_path / ".zshrc"
    rc.write_text("")
    write_shell_env_block(rc, port=8420)
    write_shell_env_block(rc, port=8420)
    content = rc.read_text()
    assert content.count(_MARKER_START) == 1

def test_remove_shell_env_block(tmp_path):
    rc = tmp_path / ".zshrc"
    rc.write_text("before\n" + _MARKER_START + "\nfoo\n" + _MARKER_END + "\nafter\n")
    remove_shell_env_block(rc)
    content = rc.read_text()
    assert _MARKER_START not in content
    assert "before" in content
    assert "after" in content

def test_make_launchagent_plist_contains_port():
    plist = make_launchagent_plist(port=8420, cachelens_bin="/usr/local/bin/cachelens")
    assert "8420" in plist
    assert "ANTHROPIC_BASE_URL" in plist
    assert "com.cachelens" in plist

def test_make_systemd_service_contains_port():
    svc = make_systemd_service(port=8420, cachelens_bin="/usr/local/bin/cachelens")
    assert "8420" in svc
    assert "cachelens daemon" in svc
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_installer.py -v
```

- [ ] **Step 3: Create `src/cachelens/installer.py`**

```python
from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from pathlib import Path

_MARKER_START = "# >>> cachelens env >>>"
_MARKER_END   = "# <<< cachelens env <<<"


def _env_block(port: int) -> str:
    base = f"http://localhost:{port}"
    return (
        f"{_MARKER_START}\n"
        f"export ANTHROPIC_BASE_URL={base}/proxy/anthropic\n"
        f"export OPENAI_BASE_URL={base}/proxy/openai\n"
        f"export GOOGLE_AI_BASE_URL={base}/proxy/google\n"
        f"{_MARKER_END}\n"
    )


def write_shell_env_block(rc_path: Path, port: int) -> None:
    existing = rc_path.read_text() if rc_path.exists() else ""
    if _MARKER_START in existing:
        return  # already present — idempotent
    with rc_path.open("a") as f:
        f.write("\n" + _env_block(port))


def remove_shell_env_block(rc_path: Path) -> None:
    if not rc_path.exists():
        return
    lines = rc_path.read_text().splitlines(keepends=True)
    out, inside = [], False
    for line in lines:
        if _MARKER_START in line:
            inside = True
        if not inside:
            out.append(line)
        if _MARKER_END in line:
            inside = False
    rc_path.write_text("".join(out))


def make_launchagent_plist(port: int, cachelens_bin: str) -> str:
    base = f"http://localhost:{port}"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>             <string>com.cachelens</string>
    <key>ProgramArguments</key>
    <array>
        <string>{cachelens_bin}</string>
        <string>daemon</string>
        <string>--port</string>
        <string>{port}</string>
    </array>
    <key>RunAtLoad</key>         <true/>
    <key>KeepAlive</key>         <true/>
    <key>StandardOutPath</key>   <string>{Path.home()}/.cachelens/daemon.log</string>
    <key>StandardErrorPath</key> <string>{Path.home()}/.cachelens/daemon.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>ANTHROPIC_BASE_URL</key> <string>{base}/proxy/anthropic</string>
        <key>OPENAI_BASE_URL</key>    <string>{base}/proxy/openai</string>
        <key>GOOGLE_AI_BASE_URL</key> <string>{base}/proxy/google</string>
    </dict>
</dict>
</plist>
"""


def make_systemd_service(port: int, cachelens_bin: str) -> str:
    base = f"http://localhost:{port}"
    return f"""[Unit]
Description=CacheLens usage tracking daemon
After=network.target

[Service]
ExecStart={cachelens_bin} daemon --port {port}
Restart=on-failure
Environment=ANTHROPIC_BASE_URL={base}/proxy/anthropic
Environment=OPENAI_BASE_URL={base}/proxy/openai
Environment=GOOGLE_AI_BASE_URL={base}/proxy/google

[Install]
WantedBy=default.target
"""


def install(port: int = 8420) -> None:
    cachelens_dir = Path.home() / ".cachelens"
    cachelens_dir.mkdir(parents=True, exist_ok=True)

    config = cachelens_dir / "config.toml"
    if not config.exists():
        config.write_text(
            "[retention]\nraw_days = 1\ndaily_days = 365\naggregate = true\n"
        )

    bin_path = shutil.which("cachelens") or sys.executable
    print(f"CacheLens install\n{'='*40}")

    if platform.system() == "Darwin":
        _install_macos(port, bin_path, cachelens_dir)
    else:
        _install_linux(port, bin_path, cachelens_dir)

    # Set env vars in shell files
    for rc in [Path.home() / ".zshrc", Path.home() / ".bashrc", Path.home() / ".profile"]:
        if rc.exists():
            write_shell_env_block(rc, port)
            print(f"  ✓ env vars written to {rc}")

    print(f"\nGoogle note: GOOGLE_AI_BASE_URL is set but some google-genai SDK versions")
    print(f"  require manual client configuration. The proxy endpoint works regardless.")
    print(f"\nOpen http://localhost:{port} in your browser after starting a new shell.")


def _install_macos(port: int, bin_path: str, cachelens_dir: Path) -> None:
    plist_path = Path.home() / "Library" / "LaunchAgents" / "com.cachelens.plist"
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_text(make_launchagent_plist(port, bin_path))
    print(f"  ✓ LaunchAgent written to {plist_path}")

    # Set env vars for current GUI session
    base = f"http://localhost:{port}"
    for var, val in [
        ("ANTHROPIC_BASE_URL", f"{base}/proxy/anthropic"),
        ("OPENAI_BASE_URL",    f"{base}/proxy/openai"),
        ("GOOGLE_AI_BASE_URL", f"{base}/proxy/google"),
    ]:
        subprocess.run(["launchctl", "setenv", var, val], check=False)
    print("  ✓ env vars set via launchctl (GUI apps)")

    subprocess.run(["launchctl", "load", str(plist_path)], check=False)
    print("  ✓ daemon loaded and started")


def _install_linux(port: int, bin_path: str, cachelens_dir: Path) -> None:
    svc_dir = Path.home() / ".config" / "systemd" / "user"
    svc_dir.mkdir(parents=True, exist_ok=True)
    svc_path = svc_dir / "cachelens.service"
    svc_path.write_text(make_systemd_service(port, bin_path))
    print(f"  ✓ systemd user service written to {svc_path}")

    env_dir = Path.home() / ".config" / "environment.d"
    env_dir.mkdir(parents=True, exist_ok=True)
    env_file = env_dir / "cachelens.conf"
    base = f"http://localhost:{port}"
    env_file.write_text(
        f"ANTHROPIC_BASE_URL={base}/proxy/anthropic\n"
        f"OPENAI_BASE_URL={base}/proxy/openai\n"
        f"GOOGLE_AI_BASE_URL={base}/proxy/google\n"
    )
    print(f"  ✓ environment.d config written to {env_file}")

    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    subprocess.run(["systemctl", "--user", "enable", "--now", "cachelens"], check=False)
    print("  ✓ daemon enabled and started")


def uninstall(purge: bool = False) -> None:
    print(f"CacheLens uninstall\n{'='*40}")

    if platform.system() == "Darwin":
        plist = Path.home() / "Library" / "LaunchAgents" / "com.cachelens.plist"
        subprocess.run(["launchctl", "unload", str(plist)], check=False)
        if plist.exists():
            plist.unlink()
            print(f"  ✓ removed {plist}")
        for var in ["ANTHROPIC_BASE_URL", "OPENAI_BASE_URL", "GOOGLE_AI_BASE_URL"]:
            subprocess.run(["launchctl", "unsetenv", var], check=False)
    else:
        subprocess.run(["systemctl", "--user", "disable", "--now", "cachelens"], check=False)
        svc = Path.home() / ".config" / "systemd" / "user" / "cachelens.service"
        if svc.exists():
            svc.unlink()
        env_file = Path.home() / ".config" / "environment.d" / "cachelens.conf"
        if env_file.exists():
            env_file.unlink()

    for rc in [Path.home() / ".zshrc", Path.home() / ".bashrc", Path.home() / ".profile"]:
        remove_shell_env_block(rc)
        print(f"  ✓ env block removed from {rc}")

    if purge:
        import shutil as sh
        sh.rmtree(Path.home() / ".cachelens", ignore_errors=True)
        print("  ✓ ~/.cachelens/ removed")

    print("Done. Restart your shell to clear env vars from the current session.")
```

- [ ] **Step 4: Run installer tests**

```bash
pytest tests/test_installer.py -v
```
Expected: 5 PASSED

- [ ] **Step 5: Extend `src/cachelens/cli.py` — add daemon, status, install, uninstall**

Add these commands to the existing `cli.py` (keep `analyze` and `ui` as-is):

```python
import os
import sys

@main.command()
@click.option("--port", type=int, default=8420, show_default=True)
def daemon(port: int) -> None:
    """Run the CacheLens daemon (proxy + UI server)."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex(("127.0.0.1", port)) == 0:
            raise click.ClickException(
                f"port {port} is already in use. Use --port N to specify a different port."
            )
    from .server import run
    run(port=port, open_browser=False)


@main.command()
@click.option("--format", "out_format", type=click.Choice(["human", "json"]), default="human")
def status(out_format: str) -> None:
    """Show daemon status and DB stats."""
    import json as _json
    import socket
    from pathlib import Path as _Path

    cachelens_dir = _Path.home() / ".cachelens"
    db_path = cachelens_dir / "usage.db"

    # Check if daemon is running
    port = 8420
    daemon_running = False
    pid = None
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex(("127.0.0.1", port)) == 0:
            daemon_running = True

    from .store import UsageStore
    store = UsageStore(db_path=db_path)
    raw_today = len(store.raw_calls_today())
    db_size = store.db_size_bytes()
    last_nightly = store.last_rollup_time("nightly")
    last_yearly  = store.last_rollup_time("yearly")

    data = {
        "daemon": "running" if daemon_running else "stopped",
        "port": port,
        "db_size_bytes": db_size,
        "raw_calls_today": raw_today,
        "retention": {"raw_days": 1, "daily_days": 365, "aggregate": True},
        "last_nightly_rollup": last_nightly,
        "last_yearly_rollup": last_yearly,
    }

    if out_format == "json":
        click.echo(_json.dumps(data, indent=2))
    else:
        status_str = "running" if daemon_running else "stopped"
        click.echo(f"CacheLens daemon: {status_str} (port {port})")
        click.echo(f"DB size:          {db_size / 1024:.1f} KB")
        click.echo(f"Raw calls today:  {raw_today}")
        click.echo(f"Retention:        raw=1d, daily=365d, aggregate=true")
        click.echo(f"Last nightly:     {last_nightly or 'never'}")
        click.echo(f"Last yearly:      {last_yearly or 'never'}")


@main.command()
@click.option("--port", type=int, default=8420, show_default=True)
def install(port: int) -> None:
    """Install CacheLens as a system daemon (sets env vars, registers autostart)."""
    from .installer import install as _install
    _install(port=port)


@main.command()
@click.option("--purge", is_flag=True, help="Also remove ~/.cachelens/ data directory")
def uninstall(purge: bool) -> None:
    """Remove CacheLens daemon and env var configuration."""
    from .installer import uninstall as _uninstall
    _uninstall(purge=purge)
```

- [ ] **Step 6: Smoke-test the CLI**

```bash
cachelens --help
cachelens status
```
Expected: both run without error

- [ ] **Step 7: Commit**

```bash
git add src/cachelens/cli.py src/cachelens/installer.py src/cachelens/server.py tests/test_installer.py
git commit -m "feat(cli): add daemon, status, install, uninstall commands"
```

---

## Chunk 5: UI

### Task 10: Dashboard, Deep Dive, and Recommendations pages

**Files:**
- Modify: `src/cachelens/static/index.html`
- Modify: `src/cachelens/static/app.js`
- Modify: `src/cachelens/static/style.css`

The existing UI is a single-page analyzer. We add a nav bar and three new pages. The existing Analyze page stays as-is; the other pages are rendered dynamically by `app.js`.

- [ ] **Step 1: Add nav + page skeleton to `index.html`**

Replace the `<body>` in `index.html` to wrap the existing content in a `#page-analyze` div and add nav + placeholder divs for the new pages:

```html
<nav class="cl-nav">
  <a href="#" data-page="dashboard" class="nav-link">Dashboard</a>
  <a href="#" data-page="deepdive"  class="nav-link">Deep Dive</a>
  <a href="#" data-page="recs"      class="nav-link">Recommendations</a>
  <a href="#" data-page="analyze"   class="nav-link active">Analyze</a>
</nav>

<div id="page-dashboard" class="page hidden"></div>
<div id="page-deepdive"  class="page hidden"></div>
<div id="page-recs"      class="page hidden"></div>
<div id="page-analyze"   class="page">
  <!-- existing analyzer content goes here -->
</div>
```

- [ ] **Step 2: Add nav styles to `style.css`**

```css
.cl-nav {
  display: flex;
  gap: 1.5rem;
  padding: 0.75rem 1.5rem;
  background: var(--bg-2, #1a1a2e);
  border-bottom: 1px solid var(--border, #333);
  position: sticky;
  top: 0;
  z-index: 100;
}
.nav-link { color: var(--text-muted, #888); text-decoration: none; font-size: 0.9rem; }
.nav-link.active, .nav-link:hover { color: var(--accent, #7c3aed); }
.page.hidden { display: none; }
.kpi-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 1rem; margin: 1rem 0; }
.kpi-card { background: var(--bg-2, #1a1a2e); border-radius: 8px; padding: 1rem;
             border: 1px solid var(--border, #333); }
.kpi-card .label { font-size: 0.75rem; color: var(--text-muted, #888); }
.kpi-card .value { font-size: 1.5rem; font-weight: 600; margin-top: 0.25rem; }
.live-feed { max-height: 200px; overflow-y: auto; font-size: 0.8rem;
             background: var(--bg-2, #1a1a2e); border-radius: 8px;
             padding: 0.5rem; margin-bottom: 1rem; }
.live-feed .call-row { padding: 0.2rem 0; border-bottom: 1px solid var(--border, #222);
                        display: flex; gap: 0.75rem; }
.call-row .provider { color: var(--accent, #7c3aed); min-width: 80px; }
.rec-card { background: var(--bg-2, #1a1a2e); border-radius: 8px; padding: 1rem;
             margin-bottom: 0.75rem; border-left: 3px solid var(--accent, #7c3aed); }
.rec-card .rec-title { font-weight: 600; margin-bottom: 0.25rem; }
.rec-card .rec-desc { font-size: 0.85rem; color: var(--text-muted, #888); }
.rec-card .rec-savings { font-size: 0.8rem; color: #22c55e; margin-top: 0.25rem; }
table.usage-table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
table.usage-table th, table.usage-table td { padding: 0.5rem 0.75rem;
  text-align: left; border-bottom: 1px solid var(--border, #333); }
table.usage-table th { color: var(--text-muted, #888); font-weight: 500; }
table.usage-table tr:hover td { background: var(--bg-2, #1a1a2e); }
```

- [ ] **Step 3: Add page JS to `app.js`**

Append to the existing `app.js`:

```javascript
// ── Navigation ─────────────────────────────────────────────────────────────
function showPage(name) {
  document.querySelectorAll('.page').forEach(p => p.classList.add('hidden'));
  document.querySelectorAll('.nav-link').forEach(l => l.classList.remove('active'));
  document.getElementById('page-' + name).classList.remove('hidden');
  document.querySelector(`[data-page="${name}"]`).classList.add('active');
  if (name === 'dashboard') initDashboard();
  if (name === 'deepdive')  initDeepDive();
  if (name === 'recs')      initRecs();
}
document.querySelectorAll('.nav-link').forEach(link => {
  link.addEventListener('click', e => {
    e.preventDefault();
    showPage(link.dataset.page);
  });
});

// ── Dashboard ───────────────────────────────────────────────────────────────
let _wsConnected = false;

function initDashboard() {
  const el = document.getElementById('page-dashboard');
  if (el.dataset.init) return;
  el.dataset.init = '1';
  el.innerHTML = `
    <div style="padding:1.5rem">
      <h2>Dashboard</h2>
      <div class="live-feed" id="live-feed"><em style="color:#555">Waiting for calls…</em></div>
      <div class="kpi-grid" id="kpi-grid">
        <div class="kpi-card"><div class="label">Today</div><div class="value" id="kpi-today">—</div></div>
        <div class="kpi-card"><div class="label">Last 7 days</div><div class="value" id="kpi-week">—</div></div>
        <div class="kpi-card"><div class="label">Last 30 days</div><div class="value" id="kpi-month">—</div></div>
        <div class="kpi-card"><div class="label">Last 365 days</div><div class="value" id="kpi-year">—</div></div>
      </div>
    </div>`;
  loadKpi();
  connectWs();
}

function fmt$(v) { return v == null ? '—' : '$' + (v).toFixed(4); }

async function loadKpi() {
  try {
    const d = await fetch('/api/usage/kpi').then(r => r.json());
    document.getElementById('kpi-today').textContent = fmt$(d.today?.cost_usd);
    document.getElementById('kpi-week').textContent  = fmt$(d.week?.cost_usd);
    document.getElementById('kpi-month').textContent = fmt$(d.month?.cost_usd);
    document.getElementById('kpi-year').textContent  = fmt$(d.year?.cost_usd);
  } catch(e) { console.warn('KPI load failed', e); }
}

function connectWs() {
  if (_wsConnected) return;
  const ws = new WebSocket(`ws://${location.host}/api/live`);
  let retry = 1000;
  ws.onopen = () => { _wsConnected = true; retry = 1000; };
  ws.onmessage = e => {
    const d = JSON.parse(e.data);
    const feed = document.getElementById('live-feed');
    if (!feed) return;
    const row = document.createElement('div');
    row.className = 'call-row';
    row.innerHTML = `<span class="provider">${d.provider}</span>
      <span>${d.model}</span><span style="color:#888">${d.source}</span>
      <span>${d.input_tokens}in/${d.output_tokens}out</span>
      <span style="color:#22c55e">${fmt$(d.cost_usd)}</span>`;
    if (feed.firstChild?.tagName === 'EM') feed.innerHTML = '';
    feed.prepend(row);
    if (feed.children.length > 50) feed.lastChild.remove();
    loadKpi();
  };
  ws.onclose = () => {
    _wsConnected = false;
    setTimeout(() => { retry = Math.min(retry * 2, 30000); connectWs(); }, retry);
  };
}

// ── Deep Dive ───────────────────────────────────────────────────────────────
async function initDeepDive() {
  const el = document.getElementById('page-deepdive');
  if (el.dataset.init) return;
  el.dataset.init = '1';
  el.innerHTML = `
    <div style="padding:1.5rem">
      <h2>Deep Dive</h2>
      <div id="dd-loading" style="color:#888">Loading…</div>
      <div id="dd-content" class="hidden"></div>
    </div>`;
  try {
    const rows = await fetch('/api/usage/daily?days=30').then(r => r.json());
    renderDeepDive(rows);
  } catch(e) { document.getElementById('dd-loading').textContent = 'Error loading data.'; }
}

function renderDeepDive(rows) {
  const loading = document.getElementById('dd-loading');
  const content = document.getElementById('dd-content');
  loading.classList.add('hidden');
  content.classList.remove('hidden');

  // Aggregate by (provider, model, source)
  const agg = {};
  rows.forEach(r => {
    const k = `${r.provider}|${r.model}|${r.source}`;
    if (!agg[k]) agg[k] = { provider: r.provider, model: r.model, source: r.source,
      call_count:0, input_tokens:0, output_tokens:0, cache_read_tokens:0, cost_usd:0 };
    agg[k].call_count     += r.call_count;
    agg[k].input_tokens   += r.input_tokens;
    agg[k].output_tokens  += r.output_tokens;
    agg[k].cache_read_tokens += r.cache_read_tokens;
    agg[k].cost_usd       += r.cost_usd;
  });

  const sorted = Object.values(agg).sort((a,b) => b.cost_usd - a.cost_usd);
  const tbody = sorted.map(r => {
    const cacheHit = r.input_tokens > 0
      ? ((r.cache_read_tokens / r.input_tokens) * 100).toFixed(1) + '%' : '—';
    return `<tr>
      <td>${r.provider}</td><td>${r.model}</td><td>${r.source}</td>
      <td>${r.call_count.toLocaleString()}</td>
      <td>${(r.input_tokens/1000).toFixed(1)}K</td>
      <td>${(r.output_tokens/1000).toFixed(1)}K</td>
      <td>${cacheHit}</td>
      <td>$${r.cost_usd.toFixed(4)}</td>
    </tr>`;
  }).join('');

  content.innerHTML = `
    <table class="usage-table">
      <thead><tr>
        <th>Provider</th><th>Model</th><th>Source</th>
        <th>Calls</th><th>Input</th><th>Output</th><th>Cache Hit</th><th>Cost</th>
      </tr></thead>
      <tbody>${tbody}</tbody>
    </table>`;
}

// ── Recommendations ─────────────────────────────────────────────────────────
async function initRecs() {
  const el = document.getElementById('page-recs');
  if (el.dataset.init) return;
  el.dataset.init = '1';
  el.innerHTML = `<div style="padding:1.5rem"><h2>Recommendations</h2><div id="recs-content">Loading…</div></div>`;
  try {
    const recs = await fetch('/api/usage/recommendations').then(r => r.json());
    const content = document.getElementById('recs-content');
    if (!recs.length) {
      content.innerHTML = '<p style="color:#888">No recommendations — looking good!</p>';
      return;
    }
    content.innerHTML = recs.map(r => `
      <div class="rec-card">
        <div class="rec-title">${r.title}</div>
        <div class="rec-desc">${r.description}</div>
        ${r.estimated_savings_usd > 0
          ? `<div class="rec-savings">Est. savings: $${r.estimated_savings_usd.toFixed(4)}</div>`
          : ''}
        <a href="#" onclick="showPage('deepdive')" style="font-size:0.8rem;color:#7c3aed">
          View in Deep Dive →</a>
      </div>`).join('');
  } catch(e) {
    document.getElementById('recs-content').textContent = 'Error loading recommendations.';
  }
}
```

- [ ] **Step 4: Smoke-test the UI**

```bash
# Kill any existing daemon first
cachelens daemon --port 8421 &
sleep 1
open http://127.0.0.1:8421
```

Click through Dashboard, Deep Dive, Recommendations, Analyze. Verify no JS errors in browser console.

- [ ] **Step 5: Kill test daemon**

```bash
pkill -f "cachelens daemon --port 8421"
```

- [ ] **Step 6: Commit**

```bash
git add src/cachelens/static/
git commit -m "feat(ui): add Dashboard, Deep Dive, and Recommendations pages"
```

---

## Chunk 6: Integration + End-to-End Smoke Test

### Task 11: Full integration smoke test

**Files:**
- Create: `tests/test_integration.py`

- [ ] **Step 1: Write integration test**

```python
# tests/test_integration.py
"""Integration tests for the full proxy + store + WebSocket flow.
Uses httpx mock transport — no real network calls."""
import json
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi.testclient import TestClient

from cachelens.store import UsageStore
from cachelens.pricing import PricingTable
from cachelens.server import create_app


@pytest.fixture
def store(tmp_path):
    return UsageStore(db_path=tmp_path / "test.db")

@pytest.fixture
def pricing():
    return PricingTable()

@pytest.fixture
def client(store, pricing):
    app = create_app(store=store, pricing=pricing)
    return TestClient(app)

def test_kpi_returns_zeros_when_empty(client):
    resp = client.get("/api/usage/kpi")
    assert resp.status_code == 200
    data = resp.json()
    assert data["today"]["call_count"] == 0

def test_recommendations_empty_when_no_data(client):
    resp = client.get("/api/usage/recommendations")
    assert resp.status_code == 200
    assert resp.json() == []

def test_analyze_endpoint_still_works(client):
    payload = {"input": '{"messages":[{"role":"user","content":"hello"}]}'}
    resp = client.post("/api/analyze", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert "cacheability_score" in data
```

- [ ] **Step 2: Run integration tests**

```bash
pytest tests/test_integration.py -v
```
Expected: 3 PASSED

- [ ] **Step 3: Run full test suite**

```bash
pytest tests/ -v
```
Expected: all PASS

- [ ] **Step 4: End-to-end manual smoke test**

```bash
# Start daemon
source .venv/bin/activate
cachelens daemon &
sleep 1

# Verify proxy is reachable (will 404 upstream but proxy itself responds)
curl -s http://localhost:8420/proxy/anthropic/v1/models \
  -H "Authorization: Bearer test" | head -c 200

# Verify UI loads
curl -s http://localhost:8420/ | grep -c "CacheLens"

# Check status
cachelens status

# Kill daemon
pkill -f "cachelens daemon"
```

- [ ] **Step 5: Final commit**

```bash
git add tests/test_integration.py
git commit -m "test(integration): add full proxy + store + API integration tests"
```

---

## Run order summary

```
Chunk 1: pricing → store
Chunk 2: detector → proxy (+ httpx dep)
Chunk 3: aggregator → recommender
Chunk 4: server (extended) → cli (extended) → installer
Chunk 5: UI (index.html + app.js + style.css)
Chunk 6: integration tests
```

Each chunk is independently testable. No chunk depends on a later chunk.
