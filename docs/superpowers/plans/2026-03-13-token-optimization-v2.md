# CacheLens v2: Token Optimization + Intelligence + Developer Tooling — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 9 token optimization and observability features to CacheLens: junk token detector, output bloat tracking, history bloat tracking, token heatmap, cost anomaly detection, model right-sizing, live terminal `cachelens top`, weekly digest, plus README refresh with architecture diagram.

**Architecture:** All features hook into the existing proxy pipeline via a shared parsed request body. New analysis modules are pure functions called from `proxy.py`. New columns are added to the `calls` table via `_migrate()`. New API endpoints are added to `server.py`. Dashboard cards are added to `index.html` + `app.js`.

**Tech Stack:** Python 3.11+, FastAPI, SQLite (via UsageStore), tiktoken (existing), rich>=13.0 (new), WebSocket live feed (existing), pytest.

**Spec:** `docs/superpowers/specs/2026-03-13-token-optimization-v2-design.md`

---

## File Map

### New Files
| File | Purpose |
|------|---------|
| `src/cachelens/waste_detector.py` | Junk token detection: whitespace, polite filler, redundant instructions, empty messages |
| `src/cachelens/heatmap.py` | Token section classification: system, tools, context, history, query |
| `src/cachelens/anomaly.py` | Cost anomaly detection with drill-down report |
| `src/cachelens/right_sizing.py` | Model complexity scoring and downgrade recommendations |
| `src/cachelens/top.py` | Live terminal UI using `rich` and WebSocket |
| `src/cachelens/digest.py` | Weekly cost digest generation |
| `tests/test_waste_detector.py` | Tests for waste_detector.py |
| `tests/test_heatmap.py` | Tests for heatmap.py |
| `tests/test_anomaly.py` | Tests for anomaly.py |
| `tests/test_right_sizing.py` | Tests for right_sizing.py |
| `tests/test_digest.py` | Tests for digest.py |

### Modified Files
| File | Changes |
|------|---------|
| `src/cachelens/store.py` | Schema: `call_waste` table + 6 new `calls` columns. `insert_call()` new kwargs. Store methods for waste, heatmap. |
| `src/cachelens/proxy.py` | Parse request body once; capture call_id; pass waste/heatmap/bloat data to store. |
| `src/cachelens/recommender.py` | Add `output_bloat`, `history_bloat`, `right_sizing` to Recommendation type Literal. Add 3 new checks. |
| `src/cachelens/aggregator.py` | Add weekly digest dispatch loop (Sunday 08:00). |
| `src/cachelens/cli.py` | Add `top` and `report` commands. |
| `src/cachelens/server.py` | Add 8 new API endpoints. |
| `src/cachelens/static/index.html` | New dashboard cards: Token Waste, Output Efficiency, Token Heatmap, Anomaly markers. |
| `src/cachelens/static/app.js` | JS to populate new cards and visualizations. |
| `pyproject.toml` | Add `rich>=13.0`. |
| `README.md` | Comprehensive rewrite with architecture diagram (Excalidraw export). |

---

## Chunk 1: Prerequisites — Schema + Proxy Pipeline

### Task 1: Schema Migration — `call_waste` table and new `calls` columns

**Files:**
- Modify: `src/cachelens/store.py:11-85` (`_SCHEMA`), `src/cachelens/store.py:99-113` (`_migrate`)
- Modify: `src/cachelens/store.py:114-134` (`insert_call`)
- Test: `tests/test_store.py`

- [ ] **Step 1: Write failing tests for new schema**

```python
# In tests/test_store.py — add at the end of the file

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

    # Opening the store should migrate the DB
    store = UsageStore(db_path)
    cols = {row[1] for row in store._con.execute("PRAGMA table_info(calls)").fetchall()}
    assert "max_tokens_requested" in cols
    assert "token_heatmap" in cols
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /Users/stephenthorn/CacheLens
python3 -m pytest tests/test_store.py::test_calls_table_has_new_columns tests/test_store.py::test_call_waste_table_exists tests/test_store.py::test_insert_call_with_new_kwargs -v
```
Expected: FAIL

- [ ] **Step 3: Add `call_waste` table to `_SCHEMA` in `store.py`**

Add after the `request_log` CREATE statement and before the INDEX statements:

```python
CREATE TABLE IF NOT EXISTS call_waste (
    id INTEGER PRIMARY KEY,
    call_id INTEGER REFERENCES calls(id),
    waste_type TEXT,
    waste_tokens INTEGER,
    savings_usd REAL,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_call_waste_call_id ON call_waste(call_id);
```

- [ ] **Step 4: Add new columns to `calls` in `_SCHEMA`**

Add these 6 columns to the `CREATE TABLE IF NOT EXISTS calls` statement (after `user_agent`):
```sql
    latency_ms REAL,
    status_code INTEGER,
    max_tokens_requested INTEGER,
    output_utilization REAL,
    message_count INTEGER,
    history_tokens INTEGER,
    history_ratio REAL,
    token_heatmap TEXT
```
(Note: `latency_ms` and `status_code` are already in `_migrate()` but not in `_SCHEMA` for new installs — add them here too.)

- [ ] **Step 5: Add new columns to `_migrate()` in `store.py`**

After the existing `status_code` check, add:
```python
if "max_tokens_requested" not in cols:
    self._con.execute("ALTER TABLE calls ADD COLUMN max_tokens_requested INTEGER")
if "output_utilization" not in cols:
    self._con.execute("ALTER TABLE calls ADD COLUMN output_utilization REAL")
if "message_count" not in cols:
    self._con.execute("ALTER TABLE calls ADD COLUMN message_count INTEGER")
if "history_tokens" not in cols:
    self._con.execute("ALTER TABLE calls ADD COLUMN history_tokens INTEGER")
if "history_ratio" not in cols:
    self._con.execute("ALTER TABLE calls ADD COLUMN history_ratio REAL")
if "token_heatmap" not in cols:
    self._con.execute("ALTER TABLE calls ADD COLUMN token_heatmap TEXT")
```

- [ ] **Step 6: Update `insert_call()` to accept new kwargs**

Change the method signature to include optional kwargs:
```python
def insert_call(self, *, ts: int, provider: str, model: str,
                source: str, source_tag: str | None,
                input_tokens: int, output_tokens: int,
                cache_read_tokens: int, cache_write_tokens: int,
                cost_usd: float, endpoint: str, request_hash: str,
                user_agent: str = "",
                latency_ms: float | None = None,
                status_code: int | None = None,
                max_tokens_requested: int | None = None,
                output_utilization: float | None = None,
                message_count: int | None = None,
                history_tokens: int | None = None,
                history_ratio: float | None = None,
                token_heatmap: str | None = None) -> int:
```

Update the INSERT statement to include the new columns:
```python
cur = self._con.execute(
    "INSERT INTO calls (ts,provider,model,source,source_tag,"
    "input_tokens,output_tokens,cache_read_tokens,cache_write_tokens,"
    "cost_usd,endpoint,request_hash,user_agent,latency_ms,status_code,"
    "max_tokens_requested,output_utilization,message_count,history_tokens,"
    "history_ratio,token_heatmap)"
    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
    (ts, provider, model, source, source_tag,
     input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
     cost_usd, endpoint, request_hash, user_agent, latency_ms, status_code,
     max_tokens_requested, output_utilization, message_count, history_tokens,
     history_ratio, token_heatmap),
)
```

- [ ] **Step 7: Run tests to confirm they pass**

```bash
python3 -m pytest tests/test_store.py -v
```
Expected: all tests pass

- [ ] **Step 8: Commit**

```bash
git add src/cachelens/store.py tests/test_store.py
git commit -m "feat: schema migration — call_waste table + 6 new calls columns"
```

---

### Task 2: `insert_waste_items` store method

**Files:**
- Modify: `src/cachelens/store.py`
- Test: `tests/test_store.py`

- [ ] **Step 1: Write failing test**

```python
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
```

- [ ] **Step 2: Run to confirm fail**

```bash
python3 -m pytest tests/test_store.py::test_insert_and_query_waste tests/test_store.py::test_waste_summary_aggregates -v
```

- [ ] **Step 3: Add `insert_waste_items`, `get_waste_for_call`, `waste_summary` to `store.py`**

```python
def insert_waste_items(self, *, call_id: int, items: list[dict]) -> None:
    with self._lock:
        self._con.executemany(
            "INSERT INTO call_waste (call_id, waste_type, waste_tokens, savings_usd, detail)"
            " VALUES (?, ?, ?, ?, ?)",
            [(call_id, item["waste_type"], item["waste_tokens"],
              item["savings_usd"], item["detail"]) for item in items],
        )
        self._con.commit()

def get_waste_for_call(self, call_id: int) -> list[dict]:
    with self._lock:
        rows = self._con.execute(
            "SELECT * FROM call_waste WHERE call_id=? ORDER BY waste_tokens DESC",
            (call_id,)
        ).fetchall()
    return [dict(r) for r in rows]

def waste_summary(self, days: int = 30) -> dict:
    cutoff = int(time.time()) - days * 86400
    with self._lock:
        rows = self._con.execute(
            "SELECT waste_type, SUM(waste_tokens) as tokens, SUM(savings_usd) as savings"
            " FROM call_waste"
            " JOIN calls ON call_waste.call_id = calls.id"
            " WHERE calls.ts >= ?"
            " GROUP BY waste_type",
            (cutoff,)
        ).fetchall()
    by_type = {r["waste_type"]: r["tokens"] for r in rows}
    total_tokens = sum(by_type.values())
    total_savings = sum(r["savings"] for r in rows)
    return {
        "total_waste_tokens": total_tokens,
        "total_savings_usd": total_savings,
        "by_type": by_type,
    }
```

- [ ] **Step 4: Run tests**

```bash
python3 -m pytest tests/test_store.py -v
```
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/cachelens/store.py tests/test_store.py
git commit -m "feat: store methods for waste items (insert, query, summary)"
```

---

### Task 3: Proxy pipeline — shared body parsing + call ID capture

**Files:**
- Modify: `src/cachelens/proxy.py`
- Test: `tests/test_proxy.py`

- [ ] **Step 1: Write failing tests**

```python
# In tests/test_proxy.py — add new tests

def test_record_call_returns_event_with_id(tmp_path):
    """_record_call must return event dict that includes 'id' field."""
    from cachelens.store import UsageStore
    from cachelens.pricing import PricingTable
    from cachelens.proxy import _record_call
    from cachelens.detector import ParsedProxy

    store = UsageStore(tmp_path / "test.db")
    pricing = PricingTable()
    parsed = ParsedProxy(provider="anthropic", upstream_path="/v1/messages",
                         source="test", source_tag=None)
    event = _record_call(
        store=store, pricing=pricing, parsed=parsed,
        endpoint="/v1/messages", request_hash="abc",
        usage={"model": "claude-sonnet-4-6", "input_tokens": 100,
               "output_tokens": 50, "cache_read_tokens": 0, "cache_write_tokens": 0},
    )
    assert "id" in event
    assert isinstance(event["id"], int)
    assert event["id"] > 0
```

- [ ] **Step 2: Run to confirm fail**

```bash
python3 -m pytest tests/test_proxy.py::test_record_call_returns_event_with_id -v
```

- [ ] **Step 3: Update `_record_call` in `proxy.py` to return `id` in event**

Change the `_record_call` function to capture the return value of `insert_call` and include it in the event dict:

```python
call_id = store.insert_call(
    ts=ts,
    provider=parsed.provider,
    model=model,
    # ... (all existing kwargs unchanged)
    latency_ms=latency_ms,
    status_code=status_code,
)

return {
    "id": call_id,   # ADD THIS LINE
    "ts": ts,
    "provider": parsed.provider,
    # ... rest unchanged
}
```

- [ ] **Step 4: Parse request body in `handle_proxy_request`**

Add body parsing near the top of `handle_proxy_request`, after the budget cap check and path parsing but before `sha256_request`:

```python
# Parse request body for analysis (shared across all v2 features)
parsed_body: dict | None = None
if method == "POST" and body:
    try:
        parsed_body = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass
```

Also update the streaming detection to use `parsed_body` when available:
```python
if parsed.provider == "google":
    streaming = "streamGenerateContent" in parsed.upstream_path
elif parsed_body is not None:
    streaming = bool(parsed_body.get("stream") is True)
else:
    streaming = is_streaming_request(body, parsed.provider)
```

Pass `parsed_body` through to both handlers with these concrete signature changes:

For `_handle_non_streaming`, add `parsed_body: dict | None = None` parameter:
```python
async def _handle_non_streaming(
    *, store, pricing, parsed, path, headers, body, send,
    parsed_body: dict | None = None,
    _waste_items=None,
    _max_tokens_requested=None,
    _message_count=None,
    _history_tokens=None,
    _history_ratio=None,
    _token_heatmap: str | None = None,
):
```

For `_UpstreamStreamResponse.__init__`, add the same new kwargs:
```python
def __init__(
    self, store, pricing, parsed, path, headers, body, callbacks,
    parsed_body: dict | None = None,
    _waste_items=None,
    _max_tokens_requested=None,
    _message_count=None,
    _history_tokens=None,
    _history_ratio=None,
    _token_heatmap: str | None = None,
):
    self._parsed_body = parsed_body
    self._waste_items = _waste_items or []
    self._max_tokens_requested = _max_tokens_requested
    self._message_count = _message_count
    self._history_tokens = _history_tokens
    self._history_ratio = _history_ratio
    self._token_heatmap = _token_heatmap
    # ... rest of existing __init__ unchanged ...
```

In `handle_proxy_request`, update both call sites:
```python
# Non-streaming:
await _handle_non_streaming(
    store=store, pricing=pricing, parsed=parsed, path=path,
    headers=headers, body=body, send=send,
    parsed_body=parsed_body,
    _waste_items=_waste_items,
    _max_tokens_requested=_max_tokens_requested,
    # ... other new kwargs ...
)

# Streaming:
await _UpstreamStreamResponse(
    store=store, pricing=pricing, parsed=parsed, path=path,
    headers=headers, body=body, callbacks=callbacks,
    parsed_body=parsed_body,
    _waste_items=_waste_items,
    _max_tokens_requested=_max_tokens_requested,
    # ... other new kwargs ...
)(scope, receive, send)
```

- [ ] **Step 5: Run all proxy tests**

```bash
python3 -m pytest tests/test_proxy.py -v
```
Expected: all pass

- [ ] **Step 6: Run full test suite**

```bash
python3 -m pytest tests/ -v --tb=short
```
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add src/cachelens/proxy.py tests/test_proxy.py
git commit -m "feat: proxy — shared body parsing, call ID propagation in event"
```

---

## Chunk 2: Feature 1 — Junk Token Detector

### Task 4: `waste_detector.py` — pure detection functions

**Files:**
- Create: `src/cachelens/waste_detector.py`
- Create: `tests/test_waste_detector.py`

- [ ] **Step 1: Create `tests/test_waste_detector.py` with failing tests**

```python
"""Tests for waste_detector.py — junk token detection."""
import pytest
from cachelens.waste_detector import detect_waste, WasteItem


def _make_request(messages, max_tokens=None, tools=None):
    body = {"messages": messages}
    if max_tokens is not None:
        body["max_tokens"] = max_tokens
    if tools is not None:
        body["tools"] = tools
    return body


def test_detects_whitespace_bloat():
    body = _make_request([
        {"role": "user", "content": "Hello\n\n\n\n\nworld    \n\n\n   "}
    ])
    items = detect_waste(body, provider="anthropic")
    whitespace_items = [i for i in items if i.waste_type == "whitespace"]
    assert len(whitespace_items) > 0
    assert whitespace_items[0].waste_tokens > 0


def test_no_whitespace_in_clean_message():
    body = _make_request([
        {"role": "user", "content": "Hello world. How are you?"}
    ])
    items = detect_waste(body, provider="anthropic")
    whitespace_items = [i for i in items if i.waste_type == "whitespace"]
    assert len(whitespace_items) == 0


def test_detects_polite_filler_in_system():
    body = _make_request([
        {"role": "system", "content": "Certainly! I'd be happy to help you with that. Sure thing!"},
        {"role": "user", "content": "What is 2+2?"},
    ])
    items = detect_waste(body, provider="anthropic")
    filler_items = [i for i in items if i.waste_type == "polite_filler"]
    assert len(filler_items) > 0


def test_no_polite_filler_in_user_messages():
    """Polite filler only detected in system role."""
    body = _make_request([
        {"role": "user", "content": "Certainly! I'd be happy to help!"},
    ])
    items = detect_waste(body, provider="anthropic")
    filler_items = [i for i in items if i.waste_type == "polite_filler"]
    assert len(filler_items) == 0


def test_detects_redundant_instructions():
    instruction = "Always respond in JSON format. Include a 'status' field."
    body = _make_request([
        {"role": "system", "content": f"Be helpful. {instruction}"},
        {"role": "user", "content": f"Do something. {instruction}"},
    ])
    items = detect_waste(body, provider="anthropic")
    redundant = [i for i in items if i.waste_type == "redundant_instruction"]
    assert len(redundant) > 0


def test_no_redundant_without_repetition():
    body = _make_request([
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "What is the weather?"},
    ])
    items = detect_waste(body, provider="anthropic")
    redundant = [i for i in items if i.waste_type == "redundant_instruction"]
    assert len(redundant) == 0


def test_detects_empty_messages():
    body = _make_request([
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": ""},
        {"role": "user", "content": "Go"},
    ])
    items = detect_waste(body, provider="anthropic")
    empty = [i for i in items if i.waste_type == "empty_message"]
    assert len(empty) > 0


def test_no_empty_for_normal_messages():
    body = _make_request([
        {"role": "user", "content": "This is a normal message with content."},
    ])
    items = detect_waste(body, provider="anthropic")
    empty = [i for i in items if i.waste_type == "empty_message"]
    assert len(empty) == 0


def test_waste_item_has_savings_usd():
    """Every WasteItem must have a non-negative savings_usd."""
    body = _make_request([
        {"role": "user", "content": "Hello\n\n\n\n\n\n\n\n\nworld"},
    ])
    items = detect_waste(body, provider="anthropic")
    for item in items:
        assert item.savings_usd >= 0.0
        assert isinstance(item.detail, str)


def test_empty_body_returns_no_waste():
    items = detect_waste({}, provider="anthropic")
    assert items == []


def test_waste_item_dataclass():
    item = WasteItem(
        waste_type="whitespace",
        waste_tokens=10,
        savings_usd=0.001,
        detail='{"location": "message[0]"}',
    )
    assert item.waste_type == "whitespace"
    assert item.waste_tokens == 10
```

- [ ] **Step 2: Run to confirm fail**

```bash
python3 -m pytest tests/test_waste_detector.py -v
```
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: Create `src/cachelens/waste_detector.py`**

```python
"""Junk token detector for CacheLens v2.

Detects four waste types in AI API request bodies:
- whitespace: excessive newlines/spaces
- polite_filler: social niceties in system prompts
- redundant_instruction: same block appearing 2+ times
- empty_message: messages with <5 tokens

Token counts use tiktoken (cl100k_base) as cross-provider approximation.
Accuracy: 5-15% drift for non-OpenAI providers vs. provider-reported counts.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import tiktoken

_TOKENIZER = tiktoken.get_encoding("cl100k_base")

_FILLER_PHRASES = [
    "certainly!", "certainly,", "i'd be happy to", "i would be happy to",
    "sure thing!", "sure,", "of course!", "of course,", "great question!",
    "absolutely!", "absolutely,", "definitely!", "definitely,",
    "i'm here to help", "i am here to help", "feel free to",
    "i'd love to", "i would love to", "no problem!", "no problem,",
    "happy to help", "glad to help", "by all means", "with pleasure",
    "without a doubt", "indeed,", "to clarify,", "as i mentioned",
    "as mentioned", "as previously stated", "let me help you with that",
]

_EXCESS_NEWLINES = re.compile(r"\n{3,}")
_TRAILING_SPACES = re.compile(r"[ \t]{3,}")


@dataclass
class WasteItem:
    waste_type: str  # 'whitespace' | 'polite_filler' | 'redundant_instruction' | 'empty_message'
    waste_tokens: int
    savings_usd: float
    detail: str  # JSON string with match context


def _count_tokens(text: str) -> int:
    return len(_TOKENIZER.encode(text))


def _estimate_savings(waste_tokens: int, provider: str) -> float:
    """Rough USD savings estimate: uses Sonnet-level input pricing as baseline."""
    # ~$3 per million input tokens (sonnet pricing)
    return waste_tokens * 3.0 / 1_000_000


def _detect_whitespace(messages: list[dict], provider: str) -> list[WasteItem]:
    items: list[WasteItem] = []
    for i, msg in enumerate(messages):
        content = msg.get("content") or ""
        if not isinstance(content, str):
            continue
        # Count excess newlines
        excess_nl = sum(len(m.group()) - 2 for m in _EXCESS_NEWLINES.finditer(content))
        # Count trailing spaces
        excess_sp = sum(len(m.group()) - 2 for m in _TRAILING_SPACES.finditer(content))
        total_excess_chars = excess_nl + excess_sp
        if total_excess_chars < 10:
            continue
        # Rough token estimate: ~4 chars/token
        waste_tokens = max(1, total_excess_chars // 4)
        if waste_tokens < 2:
            continue
        import json
        items.append(WasteItem(
            waste_type="whitespace",
            waste_tokens=waste_tokens,
            savings_usd=_estimate_savings(waste_tokens, provider),
            detail=json.dumps({"location": f"message[{i}]", "excess_chars": total_excess_chars}),
        ))
    return items


def _detect_polite_filler(messages: list[dict], provider: str) -> list[WasteItem]:
    """Detect polite filler ONLY in system-role messages."""
    import json
    items: list[WasteItem] = []
    for i, msg in enumerate(messages):
        if msg.get("role") != "system":
            continue
        content = msg.get("content") or ""
        if not isinstance(content, str):
            continue
        content_lower = content.lower()
        matched = [p for p in _FILLER_PHRASES if p in content_lower]
        if not matched:
            continue
        # Estimate tokens: sum of matched phrase token counts
        waste_tokens = sum(_count_tokens(p) for p in matched)
        if waste_tokens < 2:
            continue
        items.append(WasteItem(
            waste_type="polite_filler",
            waste_tokens=waste_tokens,
            savings_usd=_estimate_savings(waste_tokens, provider),
            detail=json.dumps({"location": f"message[{i}]", "matched": matched[:5]}),
        ))
    return items


def _detect_redundant_instructions(messages: list[dict], provider: str) -> list[WasteItem]:
    """Detect identical instruction blocks (50+ chars) appearing in 2+ messages."""
    import json
    # Extract all text blocks of 50+ chars from all messages
    all_content = [
        (i, msg.get("content") or "")
        for i, msg in enumerate(messages)
        if isinstance(msg.get("content"), str)
    ]
    # Split into sentence-like chunks
    blocks: dict[str, list[int]] = {}
    for i, content in all_content:
        # Split on periods/newlines to get sentence chunks
        sentences = re.split(r"[.\n]+", content)
        for s in sentences:
            s = s.strip()
            if len(s) >= 50:
                blocks.setdefault(s, []).append(i)

    items: list[WasteItem] = []
    seen: set[str] = set()
    for block, locations in blocks.items():
        if len(locations) < 2 or block in seen:
            continue
        seen.add(block)
        waste_tokens = _count_tokens(block) * (len(locations) - 1)
        items.append(WasteItem(
            waste_type="redundant_instruction",
            waste_tokens=waste_tokens,
            savings_usd=_estimate_savings(waste_tokens, provider),
            detail=json.dumps({"locations": locations, "snippet": block[:100]}),
        ))
    return items


def _detect_empty_messages(messages: list[dict], provider: str) -> list[WasteItem]:
    """Detect messages with < 5 tokens of content."""
    import json
    items: list[WasteItem] = []
    for i, msg in enumerate(messages):
        content = msg.get("content") or ""
        if not isinstance(content, str):
            continue
        tok_count = _count_tokens(content)
        if tok_count < 5 and tok_count >= 0:
            items.append(WasteItem(
                waste_type="empty_message",
                waste_tokens=tok_count,
                savings_usd=_estimate_savings(tok_count, provider),
                detail=json.dumps({"location": f"message[{i}]", "tokens": tok_count}),
            ))
    return items


def detect_waste(request_body: dict, provider: str) -> list[WasteItem]:
    """Detect waste in an AI API request body.

    Args:
        request_body: Parsed JSON request body dict.
        provider: Provider name ('anthropic', 'openai', 'google').

    Returns:
        List of WasteItem instances. Empty list if no waste detected.
    """
    messages = request_body.get("messages")
    if not messages or not isinstance(messages, list):
        return []

    items: list[WasteItem] = []
    items.extend(_detect_whitespace(messages, provider))
    items.extend(_detect_polite_filler(messages, provider))
    items.extend(_detect_redundant_instructions(messages, provider))
    items.extend(_detect_empty_messages(messages, provider))
    return items
```

- [ ] **Step 4: Run tests**

```bash
python3 -m pytest tests/test_waste_detector.py -v
```
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/cachelens/waste_detector.py tests/test_waste_detector.py
git commit -m "feat: waste_detector — junk token detection (whitespace, filler, redundant, empty)"
```

---

### Task 5: Wire waste detection into proxy pipeline

**Files:**
- Modify: `src/cachelens/proxy.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_proxy.py — add

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import json

@pytest.mark.asyncio
async def test_proxy_records_waste_items(tmp_path):
    """Waste items from detect_waste are stored in call_waste table after a non-streaming call."""
    from cachelens.store import UsageStore
    from cachelens.pricing import PricingTable
    from cachelens.proxy import handle_proxy_request

    store = UsageStore(tmp_path / "test.db")
    pricing = PricingTable()

    request_body = {
        "model": "claude-sonnet-4-6",
        "messages": [
            {"role": "system", "content": "Certainly! I'd be happy to help you."},
            {"role": "user", "content": "Hello\n\n\n\n\nworld"},
        ],
        "max_tokens": 100,
    }
    body = json.dumps(request_body).encode()

    fake_response_body = json.dumps({
        "usage": {"input_tokens": 30, "output_tokens": 10, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
        "model": "claude-sonnet-4-6",
    }).encode()

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.status_code = 200
        mock_response.content = fake_response_body
        mock_response.headers = {"content-type": "application/json"}
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.request = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        await handle_proxy_request(
            path="/proxy/anthropic/v1/messages",
            method="POST",
            headers={"content-type": "application/json", "x-api-key": "test"},
            body=body,
            store=store,
            pricing=pricing,
        )

    # Check waste items were stored
    waste_rows = store._con.execute("SELECT * FROM call_waste").fetchall()
    assert len(waste_rows) > 0
```

- [ ] **Step 2: Run to confirm fail**

```bash
python3 -m pytest tests/test_proxy.py::test_proxy_records_waste_items -v
```

- [ ] **Step 3: Import and wire waste detection in `proxy.py`**

At the top of `proxy.py`, add:
```python
from cachelens.waste_detector import detect_waste
```

In `handle_proxy_request`, after parsing `parsed_body`, add waste detection:
```python
# Run waste detection on parsed request body
_waste_items = []
if parsed_body is not None:
    _waste_items = detect_waste(parsed_body, parsed.provider)
```

Pass `_waste_items` and `parsed_body` to both `_handle_non_streaming` and `_UpstreamStreamResponse`.

In `_handle_non_streaming` and `_UpstreamStreamResponse.__call__`, after `_record_call` returns the event (which now includes `id`), store waste items:
```python
if _waste_items:
    store.insert_waste_items(
        call_id=event["id"],
        items=[{"waste_type": w.waste_type, "waste_tokens": w.waste_tokens,
                "savings_usd": w.savings_usd, "detail": w.detail}
               for w in _waste_items],
    )
```

Also add total waste tokens to the event dict:
```python
event["waste_tokens"] = sum(w.waste_tokens for w in _waste_items)
```

- [ ] **Step 4: Run all tests**

```bash
python3 -m pytest tests/ -v --tb=short
```
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/cachelens/proxy.py
git commit -m "feat: wire waste detection into proxy pipeline"
```

---

### Task 6: Waste API endpoints in `server.py`

**Files:**
- Modify: `src/cachelens/server.py`
- Test: `tests/test_server.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_server.py — add new tests

def test_waste_summary_endpoint_empty(client):
    resp = client.get("/api/usage/waste-summary")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_waste_tokens" in data
    assert data["total_waste_tokens"] == 0


def test_waste_summary_endpoint_with_data(client: TestClient, test_store: UsageStore):
    # Insert a call and waste
    call_id = test_store.insert_call(
        ts=int(__import__("time").time()), provider="anthropic",
        model="claude-sonnet-4-6", source="test", source_tag=None,
        input_tokens=500, output_tokens=100, cache_read_tokens=0,
        cache_write_tokens=0, cost_usd=0.01, endpoint="/v1/messages",
        request_hash="wh1",
    )
    test_store.insert_waste_items(call_id=call_id, items=[
        {"waste_type": "whitespace", "waste_tokens": 30, "savings_usd": 0.0001, "detail": "{}"},
    ])
    resp = client.get("/api/usage/waste-summary?days=1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_waste_tokens"] == 30
    assert data["by_type"]["whitespace"] == 30


def test_waste_call_detail_endpoint(client: TestClient, test_store: UsageStore):
    call_id = test_store.insert_call(
        ts=int(__import__("time").time()), provider="anthropic",
        model="claude-sonnet-4-6", source="test", source_tag=None,
        input_tokens=200, output_tokens=50, cache_read_tokens=0,
        cache_write_tokens=0, cost_usd=0.002, endpoint="/v1/messages",
        request_hash="wh2",
    )
    test_store.insert_waste_items(call_id=call_id, items=[
        {"waste_type": "polite_filler", "waste_tokens": 15, "savings_usd": 0.00005, "detail": "{}"},
    ])
    resp = client.get(f"/api/usage/waste/{call_id}")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["waste_type"] == "polite_filler"
```

- [ ] **Step 2: Run to confirm fail**

```bash
python3 -m pytest tests/test_server.py::test_waste_summary_endpoint_empty tests/test_server.py::test_waste_summary_endpoint_with_data tests/test_server.py::test_waste_call_detail_endpoint -v
```

- [ ] **Step 3: Add endpoints to `server.py`**

```python
@app.get("/api/usage/waste-summary")
def api_waste_summary(days: int = 30):
    return store.waste_summary(days=days)


@app.get("/api/usage/waste/{call_id}")
def api_waste_detail(call_id: int):
    return store.get_waste_for_call(call_id)
```

- [ ] **Step 4: Run tests**

```bash
python3 -m pytest tests/test_server.py -v
```
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/cachelens/server.py tests/test_server.py
git commit -m "feat: waste API endpoints — /api/usage/waste-summary and /waste/{call_id}"
```

---

### Task 7: Token Waste dashboard card

**Files:**
- Modify: `src/cachelens/static/index.html`
- Modify: `src/cachelens/static/app.js`

- [ ] **Step 1: Add "Token Waste" KPI card to `index.html`**

In the KPI grid section, after the existing KPI cards, add:
```html
<div class="kpi-card">
  <div class="kpi-label">Waste Detected</div>
  <div class="kpi-savings" id="kpi-waste-tokens" style="color: var(--warn, #f59e0b);">—</div>
  <div class="kpi-spent" id="kpi-waste-savings">—</div>
</div>
```

Bump the cache-bust version in the `<script src>` tag.

- [ ] **Step 2: Add waste fetch to `app.js`**

In the dashboard init section, after loading KPI data, add:
```javascript
async function loadWasteSummary() {
  try {
    const r = await fetch('/api/usage/waste-summary?days=30');
    const d = await r.json();
    const wtEl = document.getElementById('kpi-waste-tokens');
    const wsEl = document.getElementById('kpi-waste-savings');
    if (wtEl) wtEl.textContent = d.total_waste_tokens
      ? `${d.total_waste_tokens.toLocaleString()} tok`
      : '—';
    if (wsEl) wsEl.textContent = d.total_savings_usd
      ? `Saved: $${d.total_savings_usd.toFixed(2)}`
      : 'No waste found';
  } catch (e) {
    // silent fail
  }
}
loadWasteSummary();
```

- [ ] **Step 3: Start server and visually verify card appears**

```bash
cachelens ui --port 8420 --no-open &
```
Open browser to `http://localhost:8420`, verify "Waste Detected" card appears.

- [ ] **Step 4: Commit**

```bash
git add src/cachelens/static/index.html src/cachelens/static/app.js
git commit -m "feat: token waste dashboard card"
```

---

## Chunk 3: Features 2 + 3 — Output Bloat + History Bloat

### Task 8: Output Bloat — proxy extraction + store + endpoint + recommender

**Files:**
- Modify: `src/cachelens/proxy.py`
- Modify: `src/cachelens/store.py`
- Modify: `src/cachelens/recommender.py`
- Modify: `src/cachelens/server.py`
- Test: `tests/test_server.py`, `tests/test_store.py`

- [ ] **Step 1: Write failing tests for output efficiency endpoint**

```python
# tests/test_server.py — add

def test_output_efficiency_endpoint_empty(client):
    resp = client.get("/api/usage/output-efficiency")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)


def test_output_efficiency_calculates_utilization(client: TestClient, test_store: UsageStore):
    now = int(__import__("time").time())
    for i in range(12):
        test_store.insert_call(
            ts=now - i * 60, provider="anthropic", model="claude-sonnet-4-6",
            source="myapp", source_tag=None,
            input_tokens=500, output_tokens=50,
            cache_read_tokens=0, cache_write_tokens=0,
            cost_usd=0.005, endpoint="/v1/messages",
            request_hash=f"oe{i}",
            max_tokens_requested=400,
            output_utilization=0.125,
        )
    resp = client.get("/api/usage/output-efficiency?days=1")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) > 0
    row = next(r for r in data if r["source"] == "myapp")
    assert row["avg_utilization"] == pytest.approx(0.125, rel=0.01)
    assert row["call_count"] == 12
```

- [ ] **Step 2: Run to confirm fail**

```bash
python3 -m pytest tests/test_server.py::test_output_efficiency_endpoint_empty tests/test_server.py::test_output_efficiency_calculates_utilization -v
```

- [ ] **Step 3: Add `output_efficiency` store method to `store.py`**

```python
def output_efficiency(self, days: int = 30) -> list[dict]:
    """Per source+model: avg utilization, call count, p95 output, suggested max_tokens."""
    cutoff = int(time.time()) - days * 86400
    with self._lock:
        rows = self._con.execute(
            """
            SELECT source, model, provider,
                   COUNT(*) as call_count,
                   AVG(output_utilization) as avg_utilization,
                   AVG(output_tokens) as avg_output_tokens,
                   MAX(output_tokens) as max_output_tokens
            FROM calls
            WHERE ts >= ? AND output_utilization IS NOT NULL AND output_utilization < 0.5
            GROUP BY source, model, provider
            HAVING call_count >= 10
            """,
            (cutoff,)
        ).fetchall()
    return [dict(r) for r in rows]
```

- [ ] **Step 4: Add endpoint to `server.py`**

```python
@app.get("/api/usage/output-efficiency")
def api_output_efficiency(days: int = 30):
    return store.output_efficiency(days=days)
```

- [ ] **Step 4b: Update `_record_call` in `proxy.py` to accept new output bloat kwargs**

Update the `_record_call` function signature to accept and forward the new kwargs to `insert_call`:

```python
def _record_call(
    *, store, pricing, parsed, endpoint, request_hash, usage,
    user_agent="", latency_ms=None, status_code=None,
    max_tokens_requested: int | None = None,
    output_utilization: float | None = None,
    message_count: int | None = None,
    history_tokens: int | None = None,
    history_ratio: float | None = None,
    token_heatmap: str | None = None,
) -> dict:
    # ... existing body ...
    call_id = store.insert_call(
        ts=ts,
        provider=parsed.provider,
        model=model,
        # ... existing kwargs ...
        latency_ms=latency_ms,
        status_code=status_code,
        max_tokens_requested=max_tokens_requested,
        output_utilization=output_utilization,
        message_count=message_count,
        history_tokens=history_tokens,
        history_ratio=history_ratio,
        token_heatmap=token_heatmap,
    )
    return {
        "id": call_id,
        # ... rest of return dict unchanged ...
    }
```

- [ ] **Step 5: Extract `max_tokens_requested` in `proxy.py`**

In `handle_proxy_request`, after parsing `parsed_body`, extract max_tokens:
```python
_max_tokens_requested: int | None = None
if parsed_body is not None:
    # Provider-specific field names
    for field in ("max_tokens", "maxOutputTokens"):
        val = parsed_body.get(field)
        if val is not None:
            try:
                _max_tokens_requested = int(val)
            except (TypeError, ValueError):
                pass
            break
```

Pass `_max_tokens_requested` to both handlers. In `_record_call`, pass it to `insert_call` as `max_tokens_requested=...`. Compute `output_utilization` in `_record_call`:
```python
output_utilization: float | None = None
if max_tokens_requested and max_tokens_requested > 0 and output_tokens > 0:
    output_utilization = output_tokens / max_tokens_requested
```

- [ ] **Step 6: Add `output_bloat` recommendation check to `recommender.py`**

Extend the `Recommendation.type` Literal to include `'output_bloat'`:
```python
type: Literal[
    "low_cache_hit_rate", "downsell_opportunity", "cache_write_waste",
    "spend_spike", "bloated_prompts", "caching_opportunity",
    "efficiency_regression", "source_consolidation",
    "output_bloat", "history_bloat", "right_sizing",
]
```

Add a new check after the existing checks in `generate_recommendations` (which already receives `store: UsageStore`). Note: the internal list in this function is named `recommendations`, not `recs`:
```python
# Check: output bloat — sources using < 25% of their max_tokens budget
try:
    eff_rows = store.output_efficiency(days=30)
    for row in eff_rows:
        if row.get("avg_utilization", 1.0) < 0.25 and row.get("call_count", 0) >= 10:
            import hashlib
            rec_id = hashlib.md5(
                f"output_bloat:{row['source']}:{row['model']}".encode()
            ).hexdigest()[:12]
            recommendations.append(Recommendation(
                id=rec_id,
                type="output_bloat",
                title=f"Oversized max_tokens for {row['source']}",
                description=(
                    f"Source '{row['source']}' ({row['model']}) uses only "
                    f"{row['avg_utilization']*100:.0f}% of its max_tokens budget on average "
                    f"across {row['call_count']} calls. Reducing max_tokens can cut costs."
                ),
                estimated_impact="medium",
                deep_dive_link="/api/usage/output-efficiency",
                metrics={
                    "avg_utilization": round(row["avg_utilization"], 3),
                    "call_count": row["call_count"],
                    "model": row["model"],
                },
            ))
except Exception:
    pass  # output_efficiency may not exist on old DBs; skip gracefully
```

- [ ] **Step 7: Run all tests**

```bash
python3 -m pytest tests/ -v --tb=short
```

- [ ] **Step 8: Commit**

```bash
git add src/cachelens/store.py src/cachelens/proxy.py src/cachelens/server.py src/cachelens/recommender.py tests/test_server.py
git commit -m "feat: output bloat — max_tokens tracking, utilization, output-efficiency endpoint"
```

---

### Task 9: History Bloat — proxy extraction + store + endpoint + recommender

**Files:**
- Modify: `src/cachelens/proxy.py`
- Modify: `src/cachelens/store.py`
- Modify: `src/cachelens/server.py`
- Test: `tests/test_server.py`, `tests/test_store.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_server.py — add

def test_conversation_efficiency_endpoint_empty(client):
    resp = client.get("/api/usage/conversation-efficiency")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)


def test_conversation_efficiency_with_multi_turn(client: TestClient, test_store: UsageStore):
    now = int(__import__("time").time())
    for i in range(5):
        test_store.insert_call(
            ts=now - i * 120, provider="anthropic", model="claude-sonnet-4-6",
            source="chatbot", source_tag=None,
            input_tokens=1000, output_tokens=200,
            cache_read_tokens=0, cache_write_tokens=0,
            cost_usd=0.01, endpoint="/v1/messages",
            request_hash=f"hb{i}",
            message_count=10,
            history_tokens=700,
            history_ratio=0.7,
        )
    resp = client.get("/api/usage/conversation-efficiency?days=1")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) > 0
    row = next(r for r in data if r["source"] == "chatbot")
    assert row["avg_message_count"] == pytest.approx(10, rel=0.01)
    assert row["avg_history_ratio"] == pytest.approx(0.7, rel=0.01)
```

- [ ] **Step 2: Run to confirm fail**

```bash
python3 -m pytest tests/test_server.py::test_conversation_efficiency_endpoint_empty tests/test_server.py::test_conversation_efficiency_with_multi_turn -v
```

- [ ] **Step 3: Add `conversation_efficiency` to `store.py`**

```python
def conversation_efficiency(self, days: int = 30) -> list[dict]:
    """Per source: avg message count, avg history ratio, call count."""
    cutoff = int(time.time()) - days * 86400
    with self._lock:
        rows = self._con.execute(
            """
            SELECT source, COUNT(*) as call_count,
                   AVG(message_count) as avg_message_count,
                   AVG(history_ratio) as avg_history_ratio,
                   AVG(history_tokens) as avg_history_tokens
            FROM calls
            WHERE ts >= ? AND message_count > 6 AND history_ratio IS NOT NULL
            GROUP BY source
            ORDER BY avg_history_ratio DESC
            """,
            (cutoff,)
        ).fetchall()
    return [dict(r) for r in rows]
```

- [ ] **Step 4: Add endpoint to `server.py`**

```python
@app.get("/api/usage/conversation-efficiency")
def api_conversation_efficiency(days: int = 30):
    return store.conversation_efficiency(days=days)
```

- [ ] **Step 5: Compute history metrics in `proxy.py`**

In `handle_proxy_request`, after extracting `_max_tokens_requested`, compute history metrics:
```python
_message_count: int | None = None
_history_tokens: int | None = None
_history_ratio: float | None = None

if parsed_body is not None:
    messages = parsed_body.get("messages") or []
    if isinstance(messages, list):
        _message_count = len(messages)
        if _message_count > 6:
            # History = all messages except system + last user
            history_msgs = [
                m for m in messages[:-1]
                if m.get("role") in ("user", "assistant")
            ]
            if history_msgs:
                try:
                    import tiktoken
                    enc = tiktoken.get_encoding("cl100k_base")
                    def _tok(m):
                        c = m.get("content") or ""
                        return len(enc.encode(c)) if isinstance(c, str) else 0
                    _history_tokens = sum(_tok(m) for m in history_msgs)
                    total_input = sum(_tok(m) for m in messages)
                    if total_input > 0:
                        _history_ratio = _history_tokens / total_input
                except Exception:
                    pass
```

Pass these to `_record_call` → `insert_call`.

- [ ] **Step 6: Run all tests**

```bash
python3 -m pytest tests/ -v --tb=short
```

- [ ] **Step 7: Commit**

```bash
git add src/cachelens/store.py src/cachelens/proxy.py src/cachelens/server.py tests/test_server.py
git commit -m "feat: history bloat — message_count/history_ratio tracking, conversation-efficiency endpoint"
```

---

## Chunk 4: Feature 5 — Token Heatmap

### Task 10: `heatmap.py` + store + endpoint

**Files:**
- Create: `src/cachelens/heatmap.py`
- Create: `tests/test_heatmap.py`
- Modify: `src/cachelens/proxy.py`
- Modify: `src/cachelens/store.py`
- Modify: `src/cachelens/server.py`

- [ ] **Step 1: Write failing tests for `heatmap.py`**

```python
"""Tests for heatmap.py — token section classification."""
import json
import pytest
from cachelens.heatmap import compute_heatmap


def test_classifies_system_prompt():
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello"},
    ]
    result = compute_heatmap(messages=messages, tools=None, provider="anthropic")
    assert result["system_prompt"] > 0
    assert result["user_query"] > 0
    assert result["total"] > 0


def test_classifies_tool_definitions():
    tools = [{"name": "search", "description": "Search the web",
               "input_schema": {"type": "object", "properties": {}}}]
    messages = [
        {"role": "user", "content": "Search for cats"},
    ]
    result = compute_heatmap(messages=messages, tools=tools, provider="anthropic")
    assert result["tool_definitions"] > 0


def test_classifies_conversation_history():
    messages = [
        {"role": "user", "content": "What is Python?"},
        {"role": "assistant", "content": "Python is a programming language."},
        {"role": "user", "content": "Tell me more."},
        {"role": "assistant", "content": "It was created by Guido van Rossum."},
        {"role": "user", "content": "What version is current?"},
    ]
    result = compute_heatmap(messages=messages, tools=None, provider="anthropic")
    assert result["conversation_history"] > 0
    assert result["user_query"] > 0


def test_classifies_context_markers():
    messages = [
        {"role": "user", "content": "<context>\nThis is injected context about the topic.\n</context>\nNow answer my question."},
    ]
    result = compute_heatmap(messages=messages, tools=None, provider="anthropic")
    assert result["context"] > 0


def test_heatmap_total_matches_sum():
    messages = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello!"},
        {"role": "user", "content": "How are you?"},
    ]
    result = compute_heatmap(messages=messages, tools=None, provider="anthropic")
    section_sum = (
        result["system_prompt"] + result["tool_definitions"] + result["context"]
        + result["conversation_history"] + result["user_query"] + result["other"]
    )
    assert abs(result["total"] - section_sum) <= 5  # allow small rounding


def test_empty_messages_returns_zero_heatmap():
    result = compute_heatmap(messages=[], tools=None, provider="anthropic")
    assert result["total"] == 0
    assert result["user_query"] == 0
```

- [ ] **Step 2: Run to confirm fail**

```bash
python3 -m pytest tests/test_heatmap.py -v
```

- [ ] **Step 3: Create `src/cachelens/heatmap.py`**

```python
"""Token heatmap — classify input tokens into labeled sections.

Sections:
  system_prompt       role='system' (first occurrence)
  tool_definitions    tools/functions array in request body
  context             content with <context>/<documents>/<retrieved> markers, or large mid-conversation blocks
  conversation_history  all user/assistant messages except last user message
  user_query          last user-role message
  other               anything unclassified
"""
from __future__ import annotations

import re

import tiktoken

_TOKENIZER = tiktoken.get_encoding("cl100k_base")
_CONTEXT_RE = re.compile(r"<(context|documents|retrieved|doc)[^>]*>", re.IGNORECASE)


def _tok(text: str) -> int:
    if not text:
        return 0
    return len(_TOKENIZER.encode(text))


def _message_text(msg: dict) -> str:
    content = msg.get("content") or ""
    if isinstance(content, str):
        return content
    # Handle list-of-blocks format (Anthropic)
    if isinstance(content, list):
        return " ".join(
            block.get("text", "") for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


def compute_heatmap(
    messages: list[dict],
    tools: list | None,
    provider: str,
) -> dict:
    """Classify tokens in a request into labeled sections.

    Returns a dict:
        {system_prompt, tool_definitions, context, conversation_history, user_query, other, total}
    """
    counts = {
        "system_prompt": 0,
        "tool_definitions": 0,
        "context": 0,
        "conversation_history": 0,
        "user_query": 0,
        "other": 0,
        "total": 0,
    }

    if not messages:
        return counts

    # Tool definitions
    if tools:
        import json
        try:
            tools_str = json.dumps(tools)
            counts["tool_definitions"] = _tok(tools_str)
        except Exception:
            pass

    # Find last user message index
    last_user_idx = -1
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            last_user_idx = i
            break

    first_system_seen = False
    for i, msg in enumerate(messages):
        role = msg.get("role", "")
        text = _message_text(msg)
        token_count = _tok(text)

        if role == "system" and not first_system_seen:
            counts["system_prompt"] += token_count
            first_system_seen = True
        elif i == last_user_idx:
            # Check for context markers in user query
            if _CONTEXT_RE.search(text):
                # Split: context part vs query part (rough heuristic: last 100 chars are query)
                context_part = re.sub(r"\S.*", "", text[:max(0, len(text) - 100)])
                query_part = text[-100:]
                counts["context"] += _tok(context_part)
                counts["user_query"] += _tok(query_part)
            else:
                counts["user_query"] += token_count
        elif role in ("user", "assistant"):
            counts["conversation_history"] += token_count
        else:
            counts["other"] += token_count

    counts["total"] = sum(
        counts[k] for k in ("system_prompt", "tool_definitions", "context",
                            "conversation_history", "user_query", "other")
    )
    return counts
```

- [ ] **Step 4: Run tests**

```bash
python3 -m pytest tests/test_heatmap.py -v
```
Expected: all pass

- [ ] **Step 5: Add heatmap store method and endpoint**

In `store.py`, add:
```python
def token_heatmap_summary(self, days: int = 30) -> list[dict]:
    """Aggregated average heatmap per source+model."""
    import json
    cutoff = int(time.time()) - days * 86400
    with self._lock:
        rows = self._con.execute(
            "SELECT source, model, token_heatmap FROM calls"
            " WHERE ts >= ? AND token_heatmap IS NOT NULL",
            (cutoff,)
        ).fetchall()

    # Aggregate by source+model
    agg: dict[tuple, dict] = {}
    counts: dict[tuple, int] = {}
    sections = ["system_prompt", "tool_definitions", "context",
                "conversation_history", "user_query", "other"]

    for row in rows:
        key = (row["source"], row["model"])
        try:
            hm = json.loads(row["token_heatmap"])
        except Exception:
            continue
        if key not in agg:
            agg[key] = {s: 0.0 for s in sections}
            counts[key] = 0
        counts[key] += 1
        for s in sections:
            agg[key][s] += hm.get(s, 0)

    result = []
    for (source, model), totals in agg.items():
        n = counts[(source, model)]
        avg = {s: round(totals[s] / n) for s in sections}
        avg["total"] = sum(avg[s] for s in sections)
        result.append({"source": source, "model": model, "call_count": n, **avg})

    return sorted(result, key=lambda x: -x["total"])
```

In `server.py`, add:
```python
@app.get("/api/usage/token-heatmap")
def api_token_heatmap(days: int = 30):
    return store.token_heatmap_summary(days=days)
```

- [ ] **Step 6: Wire heatmap into proxy**

In `proxy.py`, import heatmap:
```python
from cachelens.heatmap import compute_heatmap
```

After computing history metrics, compute heatmap:
```python
_token_heatmap_json: str | None = None
if parsed_body is not None:
    try:
        import json
        messages = parsed_body.get("messages") or []
        tools = parsed_body.get("tools")
        hm = compute_heatmap(messages=messages, tools=tools, provider=parsed.provider)
        _token_heatmap_json = json.dumps(hm)
    except Exception:
        pass
```

Pass `_token_heatmap_json` through `_record_call` → `insert_call(token_heatmap=...)`.

- [ ] **Step 7: Run all tests**

```bash
python3 -m pytest tests/ -v --tb=short
```

- [ ] **Step 8: Commit**

```bash
git add src/cachelens/heatmap.py tests/test_heatmap.py src/cachelens/proxy.py src/cachelens/store.py src/cachelens/server.py
git commit -m "feat: token heatmap — section classification, proxy integration, API endpoint"
```

---

## Chunk 5: Feature 6 — Cost Anomaly Detection

### Task 11: `anomaly.py` + store + endpoint

**Files:**
- Create: `src/cachelens/anomaly.py`
- Create: `tests/test_anomaly.py`
- Modify: `src/cachelens/server.py`

- [ ] **Step 1: Write failing tests**

```python
"""Tests for anomaly.py — cost anomaly detection."""
import time
import pytest
from unittest.mock import MagicMock


def _make_store_with_agg(rows):
    """Create a mock store with daily_agg rows."""
    store = MagicMock()
    store.query_daily_agg_since.return_value = rows
    store.aggregate_calls_for_date.return_value = []
    return store


def test_no_anomalies_with_stable_spend():
    from cachelens.anomaly import detect_anomalies
    from datetime import date, timedelta

    today = date.today()
    rows = []
    for i in range(14):
        d = (today - timedelta(days=i + 1)).isoformat()
        rows.append({
            "date": d, "provider": "anthropic", "model": "claude-sonnet-4-6",
            "source": "test", "call_count": 10, "input_tokens": 1000,
            "output_tokens": 200, "cache_read_tokens": 0, "cache_write_tokens": 0,
            "cost_usd": 1.0,
        })
    store = _make_store_with_agg(rows)
    anomalies = detect_anomalies(store=store, days=14)
    assert anomalies == []


def test_detects_spend_spike():
    from cachelens.anomaly import detect_anomalies
    from datetime import date, timedelta

    today = date.today()
    rows = []
    # 13 normal days
    for i in range(13):
        d = (today - timedelta(days=i + 1)).isoformat()
        rows.append({
            "date": d, "provider": "anthropic", "model": "claude-sonnet-4-6",
            "source": "test", "call_count": 10, "input_tokens": 1000,
            "output_tokens": 200, "cache_read_tokens": 0, "cache_write_tokens": 0,
            "cost_usd": 1.0,
        })
    # 1 spike day
    spike_date = (today - timedelta(days=1)).isoformat()
    rows[0] = {
        "date": spike_date, "provider": "anthropic", "model": "claude-sonnet-4-6",
        "source": "test", "call_count": 10, "input_tokens": 1000,
        "output_tokens": 200, "cache_read_tokens": 0, "cache_write_tokens": 0,
        "cost_usd": 20.0,  # way above normal
    }
    store = _make_store_with_agg(rows)
    anomalies = detect_anomalies(store=store, days=14)
    assert len(anomalies) >= 1
    assert any(a["date"] == spike_date for a in anomalies)


def test_detects_call_count_spike():
    """Call count spike (> 2x normal) should also be flagged."""
    from cachelens.anomaly import detect_anomalies
    from datetime import date, timedelta

    today = date.today()
    rows = []
    for i in range(13):
        d = (today - timedelta(days=i + 1)).isoformat()
        rows.append({
            "date": d, "provider": "anthropic", "model": "claude-sonnet-4-6",
            "source": "test", "call_count": 10, "input_tokens": 1000,
            "output_tokens": 200, "cache_read_tokens": 0, "cache_write_tokens": 0,
            "cost_usd": 1.0,
        })
    spike_date = (today - timedelta(days=1)).isoformat()
    rows[0] = {
        "date": spike_date, "provider": "anthropic", "model": "claude-sonnet-4-6",
        "source": "test", "call_count": 80, "input_tokens": 8000,  # 8x normal calls
        "output_tokens": 1600, "cache_read_tokens": 0, "cache_write_tokens": 0,
        "cost_usd": 1.5,  # spend barely changed (cheap burst)
    }
    store = _make_store_with_agg(rows)
    anomalies = detect_anomalies(store=store, days=14)
    assert any(a["date"] == spike_date and a.get("anomaly_type") == "call_count_spike"
               for a in anomalies)


def test_detects_token_spike():
    """Avg token spike (> 2x normal input_tokens/call) should be flagged."""
    from cachelens.anomaly import detect_anomalies
    from datetime import date, timedelta

    today = date.today()
    rows = []
    for i in range(13):
        d = (today - timedelta(days=i + 1)).isoformat()
        rows.append({
            "date": d, "provider": "anthropic", "model": "claude-sonnet-4-6",
            "source": "test", "call_count": 10, "input_tokens": 1000,
            "output_tokens": 200, "cache_read_tokens": 0, "cache_write_tokens": 0,
            "cost_usd": 1.0,
        })
    spike_date = (today - timedelta(days=1)).isoformat()
    rows[0] = {
        "date": spike_date, "provider": "anthropic", "model": "claude-sonnet-4-6",
        "source": "test", "call_count": 10, "input_tokens": 50000,  # 50x tokens/call
        "output_tokens": 200, "cache_read_tokens": 0, "cache_write_tokens": 0,
        "cost_usd": 2.0,
    }
    store = _make_store_with_agg(rows)
    anomalies = detect_anomalies(store=store, days=14)
    assert any(a["date"] == spike_date and a.get("anomaly_type") == "token_spike"
               for a in anomalies)


def test_anomaly_has_required_fields():
    from cachelens.anomaly import detect_anomalies
    from datetime import date, timedelta

    today = date.today()
    rows = []
    for i in range(13):
        d = (today - timedelta(days=i + 1)).isoformat()
        rows.append({
            "date": d, "provider": "anthropic", "model": "claude-sonnet-4-6",
            "source": "test", "call_count": 10, "input_tokens": 1000,
            "output_tokens": 200, "cache_read_tokens": 0, "cache_write_tokens": 0,
            "cost_usd": 1.0,
        })
    rows[0]["cost_usd"] = 15.0

    store = _make_store_with_agg(rows)
    anomalies = detect_anomalies(store=store, days=14)
    if anomalies:
        a = anomalies[0]
        assert "date" in a
        assert "source" in a
        assert "spend_usd" in a
        assert "expected_usd" in a
        assert "stddev" in a
        assert "anomaly_type" in a
        assert "top_models" in a  # drill-down


def test_anomaly_drill_down_fields():
    """Anomaly result must include drill-down: top_models and call_count."""
    from cachelens.anomaly import detect_anomalies
    from datetime import date, timedelta

    today = date.today()
    rows = []
    for i in range(13):
        d = (today - timedelta(days=i + 1)).isoformat()
        rows.append({
            "date": d, "provider": "anthropic", "model": "claude-sonnet-4-6",
            "source": "test", "call_count": 10, "input_tokens": 1000,
            "output_tokens": 200, "cache_read_tokens": 0, "cache_write_tokens": 0,
            "cost_usd": 1.0,
        })
    rows[0]["cost_usd"] = 15.0
    store = _make_store_with_agg(rows)
    anomalies = detect_anomalies(store=store, days=14)
    assert len(anomalies) >= 1, "Expected at least one anomaly for drill-down field check"
    a = anomalies[0]
    assert isinstance(a.get("top_models"), list)
    assert "call_count" in a


def test_insufficient_data_returns_empty():
    from cachelens.anomaly import detect_anomalies
    # Need at least 7 days of data to detect anomalies
    store = _make_store_with_agg([])
    anomalies = detect_anomalies(store=store, days=14)
    assert anomalies == []
```

- [ ] **Step 2: Run to confirm fail**

```bash
python3 -m pytest tests/test_anomaly.py -v
```

- [ ] **Step 3: Create `src/cachelens/anomaly.py`**

```python
"""Cost anomaly detection for CacheLens v2.

Algorithm:
  - Compute 14-day rolling mean and stddev of daily spend per source
  - Flag days where spend > mean + 2 * stddev
  - Require at least 7 data points to avoid false positives
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any

from cachelens.store import UsageStore


def _mean_stddev(values: list[float]) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    n = len(values)
    mean = sum(values) / n
    if n < 2:
        return mean, 0.0
    variance = sum((x - mean) ** 2 for x in values) / (n - 1)
    return mean, math.sqrt(variance)


def detect_anomalies(store: UsageStore, days: int = 30) -> list[dict[str, Any]]:
    """Detect cost, call count, and token anomalies in daily aggregated data.

    Returns list of anomaly dicts with: date, source, anomaly_type, spend_usd,
    expected_usd, stddev, threshold_usd, multiplier, call_count, top_models.
    """
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    raw_rows = store.query_daily_agg_since(cutoff)

    # Supplement with today's live data
    today = date.today().isoformat()
    today_in_agg = {(r["provider"], r["model"], r["source"])
                    for r in raw_rows if r["date"] == today}
    for r in store.aggregate_calls_for_date(today):
        if (r["provider"], r["model"], r["source"]) not in today_in_agg:
            raw_rows.append({
                "date": today,
                "provider": r["provider"],
                "model": r["model"],
                "source": r["source"],
                "call_count": r["call_count"],
                "input_tokens": r["input_tokens"],
                "output_tokens": r["output_tokens"],
                "cache_read_tokens": r["cache_read_tokens"],
                "cache_write_tokens": r["cache_write_tokens"],
                "cost_usd": r["cost_usd"],
            })

    # Aggregate per (source, date): spend, call_count, input_tokens, models used
    daily_by_source: dict[str, dict[str, dict]] = {}
    for row in raw_rows:
        source = row["source"]
        d = row["date"]
        daily_by_source.setdefault(source, {})
        if d not in daily_by_source[source]:
            daily_by_source[source][d] = {
                "cost_usd": 0.0, "call_count": 0, "input_tokens": 0,
                "models": [],
            }
        daily_by_source[source][d]["cost_usd"] += row["cost_usd"]
        daily_by_source[source][d]["call_count"] += row["call_count"] or 0
        daily_by_source[source][d]["input_tokens"] += row["input_tokens"] or 0
        daily_by_source[source][d]["models"].append(row["model"])

    anomalies: list[dict] = []
    threshold_multiplier = 2.0

    for source, date_data in daily_by_source.items():
        sorted_dates = sorted(date_data.keys())
        if len(sorted_dates) < 7:
            continue

        for i, check_date in enumerate(sorted_dates):
            baseline_dates = sorted_dates[max(0, i - 14):i]
            if len(baseline_dates) < 7:
                continue

            day = date_data[check_date]
            top_models = list(dict.fromkeys(day["models"]))[:3]  # unique, preserve order

            # --- Spend spike ---
            baseline_spend = [date_data[d]["cost_usd"] for d in baseline_dates]
            mean_spend, stddev_spend = _mean_stddev(baseline_spend)
            threshold_spend = mean_spend + threshold_multiplier * stddev_spend
            actual_spend = day["cost_usd"]
            if actual_spend > threshold_spend and actual_spend > mean_spend * 1.5:
                anomalies.append({
                    "date": check_date,
                    "source": source,
                    "anomaly_type": "spend_spike",
                    "spend_usd": round(actual_spend, 4),
                    "expected_usd": round(mean_spend, 4),
                    "stddev": round(stddev_spend, 4),
                    "threshold_usd": round(threshold_spend, 4),
                    "multiplier": round(actual_spend / mean_spend, 2) if mean_spend > 0 else None,
                    "call_count": day["call_count"],
                    "top_models": top_models,
                })

            # --- Call count spike (> 2x rolling mean) ---
            baseline_calls = [date_data[d]["call_count"] for d in baseline_dates]
            mean_calls, _ = _mean_stddev(baseline_calls)
            actual_calls = day["call_count"]
            if mean_calls > 0 and actual_calls > mean_calls * 2:
                anomalies.append({
                    "date": check_date,
                    "source": source,
                    "anomaly_type": "call_count_spike",
                    "spend_usd": round(actual_spend, 4),
                    "expected_usd": round(mean_spend, 4),
                    "stddev": round(stddev_spend, 4),
                    "threshold_usd": round(threshold_spend, 4),
                    "multiplier": round(actual_calls / mean_calls, 2),
                    "call_count": actual_calls,
                    "top_models": top_models,
                })

            # --- Token spike (avg tokens/call > 2x rolling mean) ---
            actual_calls_nonzero = max(1, actual_calls)
            baseline_tok_per_call = [
                date_data[d]["input_tokens"] / max(1, date_data[d]["call_count"])
                for d in baseline_dates
            ]
            mean_tok, _ = _mean_stddev(baseline_tok_per_call)
            actual_tok_per_call = day["input_tokens"] / actual_calls_nonzero
            if mean_tok > 0 and actual_tok_per_call > mean_tok * 2:
                anomalies.append({
                    "date": check_date,
                    "source": source,
                    "anomaly_type": "token_spike",
                    "spend_usd": round(actual_spend, 4),
                    "expected_usd": round(mean_spend, 4),
                    "stddev": round(stddev_spend, 4),
                    "threshold_usd": round(threshold_spend, 4),
                    "multiplier": round(actual_tok_per_call / mean_tok, 2),
                    "call_count": actual_calls,
                    "top_models": top_models,
                })

    return sorted(anomalies, key=lambda x: x["date"], reverse=True)
```

- [ ] **Step 4: Run tests**

```bash
python3 -m pytest tests/test_anomaly.py -v
```
Expected: all pass

- [ ] **Step 5: Add endpoint to `server.py`**

```python
@app.get("/api/usage/anomalies")
def api_anomalies(days: int = 30):
    from cachelens.anomaly import detect_anomalies
    return detect_anomalies(store=store, days=days)
```

- [ ] **Step 6: Run all tests**

```bash
python3 -m pytest tests/ -v --tb=short
```

- [ ] **Step 7: Commit**

```bash
git add src/cachelens/anomaly.py tests/test_anomaly.py src/cachelens/server.py
git commit -m "feat: cost anomaly detection — rolling mean/stddev, API endpoint"
```

---

## Chunk 6: Features 7 + 8 + 9 — Right-Sizing, Top, Digest

### Task 12: `right_sizing.py` + endpoint + recommender

**Files:**
- Create: `src/cachelens/right_sizing.py`
- Create: `tests/test_right_sizing.py`
- Modify: `src/cachelens/store.py`
- Modify: `src/cachelens/server.py`
- Modify: `src/cachelens/recommender.py`

- [ ] **Step 1: Write failing tests**

```python
"""Tests for right_sizing.py — model complexity analysis."""
import pytest
from unittest.mock import MagicMock


def _make_store_with_calls(call_rows):
    store = MagicMock()
    store.recent_calls_with_features.return_value = call_rows
    return store


def test_simple_call_classified_simple():
    from cachelens.right_sizing import score_complexity
    call = {
        "input_tokens": 100, "output_tokens": 50,
        "message_count": 2, "token_heatmap": None,
    }
    score = score_complexity(call)
    assert score <= 2  # simple


def test_complex_call_classified_complex():
    from cachelens.right_sizing import score_complexity
    call = {
        "input_tokens": 5000, "output_tokens": 800,
        "message_count": 8, "token_heatmap": '{"tool_definitions": 1000}',
    }
    score = score_complexity(call)
    assert score >= 5  # complex


def test_moderate_call_classification():
    from cachelens.right_sizing import score_complexity
    call = {
        "input_tokens": 2500, "output_tokens": 400,
        "message_count": 4, "token_heatmap": None,
    }
    score = score_complexity(call)
    assert 3 <= score <= 4  # moderate


def test_right_sizing_report_structure():
    from cachelens.right_sizing import analyze_right_sizing
    from cachelens.pricing import PricingTable

    pricing = PricingTable()
    store = MagicMock()
    store.recent_calls_with_features.return_value = [
        {
            "source": "myapp", "model": "claude-opus-4-6", "provider": "anthropic",
            "input_tokens": 100, "output_tokens": 50, "cost_usd": 0.10,
            "message_count": 2, "token_heatmap": None,
        }
        for _ in range(10)
    ]

    report = analyze_right_sizing(store=store, pricing=pricing, days=30)
    assert isinstance(report, list)
    if report:
        item = report[0]
        assert "source" in item
        assert "model" in item
        assert "simple_pct" in item
        assert "estimated_savings_usd" in item


def test_right_sizing_no_savings_for_haiku():
    from cachelens.right_sizing import analyze_right_sizing
    from cachelens.pricing import PricingTable

    pricing = PricingTable()
    store = MagicMock()
    store.recent_calls_with_features.return_value = [
        {
            "source": "app", "model": "claude-haiku-4-5", "provider": "anthropic",
            "input_tokens": 100, "output_tokens": 30, "cost_usd": 0.001,
            "message_count": 2, "token_heatmap": None,
        }
        for _ in range(5)
    ]
    report = analyze_right_sizing(store=store, pricing=pricing, days=30)
    # Haiku is already cheapest — no downgrade possible
    haiku_items = [r for r in report if r["model"] == "claude-haiku-4-5"]
    assert all(item["estimated_savings_usd"] == 0 for item in haiku_items)
```

- [ ] **Step 2: Run to confirm fail**

```bash
python3 -m pytest tests/test_right_sizing.py -v
```

- [ ] **Step 3: Add `recent_calls_with_features` to `store.py`**

```python
def recent_calls_with_features(self, days: int = 30) -> list[dict]:
    """Return recent calls with all analysis columns for right-sizing."""
    cutoff = int(time.time()) - days * 86400
    with self._lock:
        rows = self._con.execute(
            """SELECT source, model, provider, input_tokens, output_tokens,
                      cost_usd, message_count, token_heatmap
               FROM calls WHERE ts >= ?""",
            (cutoff,)
        ).fetchall()
    return [dict(r) for r in rows]
```

- [ ] **Step 4: Create `src/cachelens/right_sizing.py`**

```python
"""Model right-sizing analysis for CacheLens v2.

Scores each call's complexity based on observable features,
then recommends downgrade for simple/moderate calls on expensive models.
"""
from __future__ import annotations

import json
from typing import Any

from cachelens.pricing import PricingTable
from cachelens.store import UsageStore

# Downgrade map: (current_model) -> {simple: cheaper, moderate: keep_or_cheaper}
_DOWNGRADE_MAP: dict[str, dict[str, str | None]] = {
    "claude-opus-4-6":  {"simple": "claude-haiku-4-5", "moderate": "claude-sonnet-4-6"},
    "claude-sonnet-4-6": {"simple": "claude-haiku-4-5", "moderate": None},
    "gpt-4o":            {"simple": "gpt-4o-mini",      "moderate": None},
    "gpt-4.1":           {"simple": "gpt-4.1-mini",     "moderate": None},
    "gemini-2.5-pro":    {"simple": "gemini-2.0-flash",  "moderate": None},
}


def score_complexity(call: dict) -> int:
    """Compute complexity score 0-8 for a single call.

    Score 0-2 = simple, 3-4 = moderate, 5+ = complex.
    """
    score = 0
    input_tokens = call.get("input_tokens") or 0
    output_tokens = call.get("output_tokens") or 0
    message_count = call.get("message_count") or 0

    if input_tokens > 2000:
        score += 2
    if output_tokens > 500:
        score += 1
    if message_count > 6:
        score += 1

    # Check for tool definitions in heatmap
    heatmap_raw = call.get("token_heatmap")
    if heatmap_raw:
        try:
            hm = json.loads(heatmap_raw)
            if hm.get("tool_definitions", 0) > 0:
                score += 2
        except Exception:
            pass

    # Code block heuristic: skipped here (requires request body not available post-call)
    # Placeholder: flag if output is long (suggests complex generation)
    if output_tokens > 1000:
        score += 1  # additional signal

    return score


def _complexity_label(score: int) -> str:
    if score <= 2:
        return "simple"
    if score <= 4:
        return "moderate"
    return "complex"


def analyze_right_sizing(
    store: UsageStore,
    pricing: PricingTable,
    days: int = 30,
) -> list[dict[str, Any]]:
    """Analyze calls for model right-sizing opportunities.

    Returns list of dicts per source+model with:
        source, model, call_count, simple_pct, moderate_pct, complex_pct,
        suggested_model, estimated_savings_usd, weekly_savings_usd
    """
    calls = store.recent_calls_with_features(days=days)

    # Group by source+model
    groups: dict[tuple, list[dict]] = {}
    for call in calls:
        key = (call["source"], call["model"], call["provider"])
        groups.setdefault(key, []).append(call)

    results = []
    for (source, model, provider), group_calls in groups.items():
        if len(group_calls) < 5:
            continue

        complexity_counts = {"simple": 0, "moderate": 0, "complex": 0}
        for call in group_calls:
            label = _complexity_label(score_complexity(call))
            complexity_counts[label] += 1

        n = len(group_calls)
        simple_pct = complexity_counts["simple"] / n
        moderate_pct = complexity_counts["moderate"] / n
        complex_pct = complexity_counts["complex"] / n

        downgrade = _DOWNGRADE_MAP.get(model, {})
        suggested_simple = downgrade.get("simple")
        suggested_moderate = downgrade.get("moderate")

        # Estimate savings: simple calls moved to cheapest suggestion
        savings = 0.0
        if suggested_simple:
            simple_calls = [c for c in group_calls
                            if _complexity_label(score_complexity(c)) == "simple"]
            for call in simple_calls:
                original_cost = call.get("cost_usd") or 0.0
                cheaper_cost = pricing.cost_usd(
                    provider=provider,
                    model=suggested_simple,
                    input_tokens=call.get("input_tokens", 0),
                    output_tokens=call.get("output_tokens", 0),
                    cache_read_tokens=0,
                    cache_write_tokens=0,
                )
                savings += max(0.0, original_cost - cheaper_cost)

        results.append({
            "source": source,
            "model": model,
            "provider": provider,
            "call_count": n,
            "simple_pct": round(simple_pct, 3),
            "moderate_pct": round(moderate_pct, 3),
            "complex_pct": round(complex_pct, 3),
            "suggested_model_simple": suggested_simple,
            "suggested_model_moderate": suggested_moderate,
            "estimated_savings_usd": round(savings, 4),
            "weekly_savings_usd": round(savings * 7 / max(1, days), 4),
        })

    return sorted(results, key=lambda x: -x["estimated_savings_usd"])
```

- [ ] **Step 5: Add endpoint to `server.py`**

```python
@app.get("/api/usage/right-sizing")
def api_right_sizing(days: int = 30):
    from cachelens.right_sizing import analyze_right_sizing
    return analyze_right_sizing(store=store, pricing=pricing, days=days)
```

- [ ] **Step 5b: Extend `Recommendation.type` Literal and add `right_sizing` check in `recommender.py`**

In `recommender.py`, the `Recommendation.type` Literal already has `output_bloat` and `history_bloat` added in Task 8. Add `right_sizing` (it should already be there from Task 8 Step 6's extension, but confirm the full Literal is):

```python
type: Literal[
    "low_cache_hit_rate", "downsell_opportunity", "cache_write_waste",
    "spend_spike", "bloated_prompts", "caching_opportunity",
    "efficiency_regression", "source_consolidation",
    "output_bloat", "history_bloat", "right_sizing",
]
```

Add a new check after the `output_bloat` check in `generate_recommendations`. Note: the internal list variable is `recommendations`, not `recs`:
```python
# Check: right-sizing — sources using expensive models for simple tasks
try:
    from cachelens.right_sizing import analyze_right_sizing
    from cachelens.pricing import PricingTable as _PT
    rs_rows = analyze_right_sizing(store=store, pricing=_PT(), days=30)
    for row in rs_rows:
        if row["simple_pct"] >= 0.5 and row["estimated_savings_usd"] > 0.10:
            import hashlib
            rec_id = hashlib.md5(
                f"right_sizing:{row['source']}:{row['model']}".encode()
            ).hexdigest()[:12]
            recommendations.append(Recommendation(
                id=rec_id,
                type="right_sizing",
                title=f"Downgrade {row['model']} for simple calls from {row['source']}",
                description=(
                    f"{row['simple_pct']*100:.0f}% of calls from '{row['source']}' "
                    f"on {row['model']} are simple. Suggested alternative: "
                    f"{row['suggested_model_simple']}. "
                    f"Est. savings: ${row['estimated_savings_usd']:.2f}."
                ),
                estimated_impact="high" if row["estimated_savings_usd"] > 1.0 else "medium",
                deep_dive_link="/api/usage/right-sizing",
                metrics={
                    "simple_pct": row["simple_pct"],
                    "suggested_model": row["suggested_model_simple"],
                    "estimated_savings_usd": row["estimated_savings_usd"],
                },
            ))
except Exception:
    pass
```

- [ ] **Step 6: Run tests**

```bash
python3 -m pytest tests/test_right_sizing.py tests/test_server.py -v
```

- [ ] **Step 7: Commit**

```bash
git add src/cachelens/right_sizing.py tests/test_right_sizing.py src/cachelens/store.py src/cachelens/server.py src/cachelens/recommender.py tests/test_right_sizing.py
git commit -m "feat: model right-sizing — complexity scoring, downgrade recommendations, recommender integration"
```

---

### Task 13: `cachelens top` CLI command

**Files:**
- Create: `src/cachelens/top.py`
- Modify: `src/cachelens/cli.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Add `rich` to `pyproject.toml`**

In `pyproject.toml`, add `rich>=13.0` to dependencies:
```toml
dependencies = [
  "click>=8.1.7",
  "pydantic>=2.6.0",
  "tiktoken>=0.7.0",
  "fastapi>=0.110.0",
  "uvicorn[standard]>=0.27.0",
  "httpx>=0.27.0",
  "rich>=13.0",
]
```

Install it:
```bash
pip3 install rich
```

- [ ] **Step 2: Create `src/cachelens/top.py`**

```python
"""Live terminal view for CacheLens — `cachelens top`.

Connects to the WebSocket live feed and renders a rich terminal table
with the latest API calls, cost, and waste metrics.

Keyboard input runs in a separate thread to avoid blocking the WebSocket
event loop. Keys communicated to the main loop via queue.Queue.
"""
from __future__ import annotations

import asyncio
import json
import queue
import threading
import time
from collections import deque
from typing import Deque

import websockets
from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.text import Text

_MAX_ROWS = 50
_ROLLING_WINDOW = 60  # seconds


def _fmt_tokens(n: int | None) -> str:
    if n is None:
        return "—"
    if n >= 1000:
        return f"{n/1000:.1f}k"
    return str(n)


def _fmt_cost(usd: float | None) -> str:
    if usd is None:
        return "—"
    if usd < 0.01:
        return f"${usd:.4f}"
    return f"${usd:.2f}"


def _build_table(calls: list[dict], stats: dict) -> Table:
    table = Table(
        title=(
            f"CacheLens top — {stats['calls_per_min']:.0f} calls/min | "
            f"${stats['cost_per_hr']:.2f}/hr | "
            f"Cache: {stats['cache_pct']:.0f}% | "
            f"Waste: {stats['waste_tok_per_min']:.0f} tok/min"
        ),
        show_header=True,
        header_style="bold cyan",
        expand=True,
    )
    table.add_column("TIME", style="dim", width=9)
    table.add_column("SOURCE", min_width=12, max_width=20)
    table.add_column("MODEL", min_width=14, max_width=22)
    table.add_column("IN", justify="right", width=6)
    table.add_column("OUT", justify="right", width=5)
    table.add_column("CACHE", justify="right", width=6)
    table.add_column("COST", justify="right", width=7)
    table.add_column("WASTE", justify="right", width=6)

    for call in calls[:_MAX_ROWS]:
        ts = call.get("ts", 0)
        t_str = time.strftime("%H:%M:%S", time.localtime(ts)) if ts else "—"
        cost = call.get("cost_usd")
        cache_read = call.get("cache_read_tokens", 0) or 0
        total_in = call.get("input_tokens", 0) or 0
        waste = call.get("waste_tokens", 0) or 0

        cost_text = Text(_fmt_cost(cost))
        if cost and cost > 0.05:
            cost_text.stylize("bold red")
        elif cost and cost > 0.01:
            cost_text.stylize("yellow")

        cache_text = Text(_fmt_tokens(cache_read))
        if total_in > 0 and cache_read / total_in > 0.5:
            cache_text.stylize("green")

        waste_text = Text(_fmt_tokens(waste) if waste else "—")
        if waste and waste > 100:
            waste_text.stylize("yellow")

        table.add_row(
            t_str,
            call.get("source", "—")[:20],
            (call.get("model", "—") or "—")[:22],
            _fmt_tokens(total_in),
            _fmt_tokens(call.get("output_tokens")),
            cache_text,
            cost_text,
            waste_text,
        )
    return table


def _compute_stats(calls: list[dict], window_secs: int = _ROLLING_WINDOW) -> dict:
    now = time.time()
    recent = [c for c in calls if now - c.get("ts", 0) <= window_secs]
    n = len(recent)
    calls_per_min = n / (window_secs / 60) if window_secs > 0 else 0
    total_cost = sum(c.get("cost_usd") or 0 for c in recent)
    cost_per_hr = total_cost * (3600 / window_secs) if window_secs > 0 else 0
    total_in = sum(c.get("input_tokens") or 0 for c in recent)
    total_cache = sum(c.get("cache_read_tokens") or 0 for c in recent)
    cache_pct = (total_cache / total_in * 100) if total_in > 0 else 0
    waste_per_min = sum(c.get("waste_tokens") or 0 for c in recent) / (window_secs / 60) if n > 0 else 0
    return {
        "calls_per_min": calls_per_min,
        "cost_per_hr": cost_per_hr,
        "cache_pct": cache_pct,
        "waste_tok_per_min": waste_per_min,
    }


async def _run_async(port: int) -> None:
    url = f"ws://localhost:{port}/api/live"
    calls: Deque[dict] = deque(maxlen=_MAX_ROWS)
    key_q: queue.Queue[str] = queue.Queue()
    paused = False
    console = Console()

    def _keyboard_reader():
        """Read single keypresses in a separate thread."""
        import sys, tty, termios
        fd = sys.stdin.fileno()
        try:
            old = termios.tcgetattr(fd)
            tty.setraw(fd)
            while True:
                ch = sys.stdin.read(1)
                key_q.put(ch)
                if ch in ("q", "Q"):
                    break
        except Exception:
            pass
        finally:
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
            except Exception:
                pass

    kb_thread = threading.Thread(target=_keyboard_reader, daemon=True)
    kb_thread.start()

    try:
        async with websockets.connect(url) as ws:
            with Live(console=console, refresh_per_second=2, screen=True) as live:
                while True:
                    # Check keyboard
                    try:
                        while True:
                            key = key_q.get_nowait()
                            if key in ("q", "Q"):
                                return
                            if key in ("p", "P"):
                                paused = not paused
                    except queue.Empty:
                        pass

                    # Receive message with timeout
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=0.5)
                        event = json.loads(msg)
                        if not paused:
                            calls.appendleft(event)
                    except asyncio.TimeoutError:
                        pass
                    except Exception:
                        break

                    stats = _compute_stats(list(calls))
                    live.update(_build_table(list(calls), stats))
    except Exception as e:
        console.print(f"[red]Could not connect to CacheLens at {url}[/red]")
        console.print(f"[dim]Is the daemon running? Try: cachelens ui --port {port}[/dim]")


def run_top(port: int = 8420) -> None:
    """Entry point for `cachelens top`."""
    asyncio.run(_run_async(port=port))
```

- [ ] **Step 3: Add `websockets` to dependencies**

```toml
"websockets>=12.0",
```
Install: `pip3 install websockets`

- [ ] **Step 4: Add `top` command to `cli.py`**

```python
@main.command()
@click.option("--port", default=8420, show_default=True, help="Daemon port")
def top(port: int) -> None:
    """Live terminal view of API traffic (htop-style)."""
    from .top import run_top
    run_top(port=port)
```

- [ ] **Step 5: Run full test suite (no test for top — it's interactive)**

```bash
python3 -m pytest tests/ -v --tb=short
```
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add src/cachelens/top.py src/cachelens/cli.py pyproject.toml
git commit -m "feat: cachelens top — live htop-style terminal view with rich"
```

---

### Task 14: `cachelens report` + Weekly Digest

**Files:**
- Create: `src/cachelens/digest.py`
- Create: `tests/test_digest.py`
- Modify: `src/cachelens/cli.py`
- Modify: `src/cachelens/server.py`
- Modify: `src/cachelens/aggregator.py`

- [ ] **Step 1: Write failing tests**

```python
"""Tests for digest.py — weekly cost digest."""
import pytest
import time
from unittest.mock import MagicMock


def _make_store(daily_rows=None, waste_summary=None):
    store = MagicMock()
    store.query_daily_agg_since.return_value = daily_rows or []
    store.aggregate_calls_for_date.return_value = []
    store.waste_summary.return_value = waste_summary or {
        "total_waste_tokens": 0, "total_savings_usd": 0.0, "by_type": {}
    }
    store.get_setting.return_value = None
    return store


def test_digest_empty_store():
    from cachelens.digest import generate_digest
    from cachelens.pricing import PricingTable
    store = _make_store()
    pricing = PricingTable()
    report = generate_digest(store=store, pricing=pricing, days=7)
    assert report["total_spend_usd"] == 0.0
    assert report["total_calls"] == 0
    assert isinstance(report["top_sources"], list)


def test_digest_aggregates_spend():
    from cachelens.digest import generate_digest
    from cachelens.pricing import PricingTable
    from datetime import date, timedelta

    today = date.today()
    rows = []
    for i in range(7):
        d = (today - timedelta(days=i)).isoformat()
        rows.append({
            "date": d, "provider": "anthropic", "model": "claude-opus-4-6",
            "source": "claude-code", "call_count": 10, "input_tokens": 5000,
            "output_tokens": 1000, "cache_read_tokens": 2000, "cache_write_tokens": 0,
            "cost_usd": 6.0,
        })
    store = _make_store(daily_rows=rows)
    pricing = PricingTable()
    report = generate_digest(store=store, pricing=pricing, days=7)
    assert report["total_spend_usd"] == pytest.approx(42.0, rel=0.01)
    assert report["total_calls"] == 70
    assert len(report["top_sources"]) >= 1
    assert report["top_sources"][0]["source"] == "claude-code"


def test_digest_has_required_fields():
    from cachelens.digest import generate_digest
    from cachelens.pricing import PricingTable
    store = _make_store()
    pricing = PricingTable()
    report = generate_digest(store=store, pricing=pricing, days=7)
    required = [
        "total_spend_usd", "total_calls", "period_days",
        "top_sources", "waste_summary", "cache_hit_rate",
    ]
    for field in required:
        assert field in report, f"Missing field: {field}"


def test_digest_formats_as_human_text():
    from cachelens.digest import format_digest_human
    from cachelens.pricing import PricingTable
    store = _make_store()
    pricing = PricingTable()
    from cachelens.digest import generate_digest
    report = generate_digest(store=store, pricing=pricing, days=7)
    text = format_digest_human(report)
    assert "CacheLens" in text
    assert "Spend" in text


def test_digest_endpoint_exists(client):
    resp = client.get("/api/usage/digest?days=7")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_spend_usd" in data
```

- [ ] **Step 2: Run to confirm fail**

```bash
python3 -m pytest tests/test_digest.py -v
```

- [ ] **Step 3: Create `src/cachelens/digest.py`**

```python
"""Weekly cost digest for CacheLens v2."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from cachelens.pricing import PricingTable
from cachelens.store import UsageStore


def generate_digest(store: UsageStore, pricing: PricingTable, days: int = 7) -> dict[str, Any]:
    """Generate a cost digest for the past `days` days."""
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    raw_rows = store.query_daily_agg_since(cutoff)

    # Supplement with today's live data
    today = date.today().isoformat()
    today_in_agg = {(r["provider"], r["model"], r["source"])
                    for r in raw_rows if r["date"] == today}
    for r in store.aggregate_calls_for_date(today):
        if (r["provider"], r["model"], r["source"]) not in today_in_agg:
            raw_rows.append({
                "date": today,
                "provider": r["provider"],
                "model": r["model"],
                "source": r["source"],
                "call_count": r["call_count"],
                "input_tokens": r["input_tokens"],
                "output_tokens": r["output_tokens"],
                "cache_read_tokens": r["cache_read_tokens"],
                "cache_write_tokens": r["cache_write_tokens"],
                "cost_usd": r["cost_usd"],
            })

    total_spend = sum(r["cost_usd"] for r in raw_rows)
    total_calls = sum(r["call_count"] for r in raw_rows)
    total_input = sum(r["input_tokens"] for r in raw_rows)
    total_cache_read = sum(r["cache_read_tokens"] for r in raw_rows)
    cache_hit_rate = (total_cache_read / total_input) if total_input > 0 else 0.0

    # Top sources by spend
    source_spend: dict[str, dict] = {}
    for row in raw_rows:
        src = row["source"]
        if src not in source_spend:
            source_spend[src] = {"source": src, "cost_usd": 0.0, "call_count": 0}
        source_spend[src]["cost_usd"] += row["cost_usd"]
        source_spend[src]["call_count"] += row["call_count"]

    top_sources = sorted(source_spend.values(), key=lambda x: -x["cost_usd"])[:5]
    for src in top_sources:
        src["pct"] = round(src["cost_usd"] / total_spend * 100, 1) if total_spend > 0 else 0.0
        src["cost_usd"] = round(src["cost_usd"], 2)

    # Waste summary
    waste_summary = store.waste_summary(days=days)

    # Budget status
    monthly_limit_str = store.get_setting("budget.monthly_limit_usd")
    budget_info = None
    if monthly_limit_str:
        try:
            monthly_limit = float(monthly_limit_str)
            # Rough: extrapolate week spend to month
            monthly_projected = total_spend * (30 / days)
            budget_info = {
                "monthly_limit_usd": monthly_limit,
                "projected_monthly_usd": round(monthly_projected, 2),
                "pct_used": round(monthly_projected / monthly_limit * 100, 1),
            }
        except (TypeError, ValueError):
            pass

    return {
        "period_days": days,
        "period_start": cutoff,
        "period_end": today,
        "total_spend_usd": round(total_spend, 2),
        "total_calls": total_calls,
        "cache_hit_rate": round(cache_hit_rate, 3),
        "top_sources": top_sources,
        "waste_summary": waste_summary,
        "budget": budget_info,
    }


def format_digest_human(report: dict) -> str:
    """Format a digest report as human-readable text."""
    lines = []
    start = report.get("period_start", "")
    end = report.get("period_end", "")
    lines.append(f"CacheLens Digest ({start} — {end})")
    lines.append("═" * 50)
    lines.append("")
    lines.append(f"Spend:     ${report['total_spend_usd']:.2f}")
    lines.append(f"Calls:     {report['total_calls']:,}")
    lines.append(f"Cache Hit: {report['cache_hit_rate']*100:.0f}%")
    lines.append("")
    if report.get("top_sources"):
        lines.append("Top Cost Drivers:")
        for i, src in enumerate(report["top_sources"], 1):
            lines.append(
                f"  {i}. {src['source']:<20} ${src['cost_usd']:.2f}  ({src.get('pct', 0):.0f}%)"
            )
    lines.append("")
    waste = report.get("waste_summary", {})
    if waste.get("total_waste_tokens"):
        lines.append("Waste Detected:")
        for wtype, tokens in (waste.get("by_type") or {}).items():
            lines.append(f"  {wtype:<20} {tokens:,} tokens")
    if report.get("budget"):
        b = report["budget"]
        lines.append("")
        lines.append(f"Budget: {b['pct_used']}% of monthly limit projected")
    return "\n".join(lines)
```

- [ ] **Step 4: Add `report` CLI command to `cli.py`**

```python
@main.command("report")
@click.option("--days", default=7, show_default=True, help="Days to include")
@click.option("--format", "fmt", default="human", type=click.Choice(["human", "json"]))
@click.option("--port", default=8420, show_default=True)
def report_cmd(days: int, fmt: str, port: int) -> None:
    """Print a cost digest report."""
    import json as _json
    import httpx
    try:
        r = httpx.get(f"http://127.0.0.1:{port}/api/usage/digest?days={days}", timeout=5.0)
        data = r.json()
        if fmt == "json":
            click.echo(_json.dumps(data, indent=2))
        else:
            from .digest import format_digest_human
            click.echo(format_digest_human(data))
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
```

- [ ] **Step 5: Add digest endpoint to `server.py`**

```python
@app.get("/api/usage/digest")
def api_digest(days: int = 7):
    from cachelens.digest import generate_digest
    return generate_digest(store=store, pricing=pricing, days=days)
```

- [ ] **Step 6: Add weekly digest loop to `aggregator.py`**

```python
async def _weekly_digest_loop(store: UsageStore) -> None:
    """Asyncio task: fires Sunday at 08:00 local time, dispatches weekly_digest webhook."""
    from datetime import datetime, timedelta

    while True:
        now = datetime.now()
        # Find next Sunday 08:00
        days_until_sunday = (6 - now.weekday()) % 7  # 0=Monday, 6=Sunday
        if days_until_sunday == 0 and now.hour >= 8:
            days_until_sunday = 7
        target = now.replace(hour=8, minute=0, second=0, microsecond=0) + timedelta(days=days_until_sunday)
        sleep_secs = (target - now).total_seconds()
        await asyncio.sleep(sleep_secs)
        try:
            from .digest import generate_digest
            from .pricing import PricingTable
            from .webhooks import dispatch_webhook
            pricing = PricingTable()
            report = generate_digest(store=store, pricing=pricing, days=7)
            webhook_url = store.get_setting("webhook.url")
            webhook_enabled = store.get_setting("webhook.enabled") == "true"
            webhook_events = store.get_setting("webhook.events") or ""
            if webhook_enabled and webhook_url and "weekly_digest" in webhook_events:
                await dispatch_webhook(url=webhook_url, event={"type": "weekly_digest", "data": report})
        except Exception:
            _log.exception("Weekly digest dispatch failed")
```

Update `schedule_rollups` to also start this task:
```python
tasks = [
    asyncio.create_task(_nightly_rollup_loop(store, raw_days)),
    asyncio.create_task(_yearly_rollup_loop(store, daily_days)),
    asyncio.create_task(_weekly_digest_loop(store)),
]
```

- [ ] **Step 7: Run all tests**

```bash
python3 -m pytest tests/ -v --tb=short
```
Expected: all pass

- [ ] **Step 8: Commit**

```bash
git add src/cachelens/digest.py tests/test_digest.py src/cachelens/cli.py src/cachelens/server.py src/cachelens/aggregator.py
git commit -m "feat: weekly cost digest — CLI report, API endpoint, Sunday webhook dispatch"
```

---

## Chunk 7: README + Excalidraw Diagram + GitHub Push

### Task 15: README rewrite

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Create Excalidraw architecture diagram**

Use the `excalidraw-diagram` skill to generate the diagram JSON. Invoke the skill with this prompt:

> "Create a CacheLens architecture diagram showing:
> - Left column: 'Your App' box with arrows labeled 'LLM SDK calls' going to 'CacheLens Proxy'
> - Middle column: 'CacheLens Proxy' box containing sub-labels: 'Budget Check', 'Waste Detector', 'Heatmap', 'History Bloat'
> - Middle-right column: 'SQLite Store' box connected to Proxy with bidirectional arrow
> - Right column: Two output boxes: 'Web Dashboard' (connected from Store) and 'cachelens top CLI' (connected from Store)
> - Bottom row: 'Anthropic / OpenAI / Google APIs' connected from Proxy with arrow labeled 'upstream'
> - Clean dark-on-white style, horizontal left-to-right flow"

The skill will output Excalidraw JSON. Save it to `docs/architecture.excalidraw`.

```bash
# Confirm the file was created
ls -la docs/architecture.excalidraw
```

- [ ] **Step 2: Export diagram as PNG to `docs/architecture.png`**

This is a one-time manual step — the PNG is committed to the repo alongside the `.excalidraw` source.

**Option A (preferred):** Open `https://excalidraw.com`, click the hamburger menu → "Open" → load `docs/architecture.excalidraw`. Then hamburger → "Export image" → PNG → save to `docs/architecture.png`.

**Option B (headless/CI):** If `node` and `npx` are available:
```bash
npx --yes @excalidraw/cli export --format png docs/architecture.excalidraw -o docs/architecture.png
```

**Option C (fallback):** Use a browser screenshot of the rendered diagram and crop to the diagram bounds. Save the result as `docs/architecture.png`.

Note: The PNG is a derived artifact committed manually. The `.excalidraw` JSON is the authoritative source. Do not add PNG regeneration to CI — commit the PNG directly after each manual edit.

- [ ] **Step 3: Rewrite `README.md`**

Write a comprehensive, easy-to-scan README with:
- **Hero**: 1-sentence description, badges (Python 3.11+, MIT)
- **Quick Start**: 3-command install → `pip install cachelens` / `cachelens install` / `cachelens ui`
- **Architecture diagram**: `![Architecture](docs/architecture.png)`
- **What it does**: bullet list of all features grouped by category
- **Feature table**: all 22+ features from v1 + v2 in a well-organized table
- **Dashboard screenshot**: `![Dashboard](docs/dashboard.png)`
- **CLI Reference**: all commands with descriptions
- **API Reference**: all endpoints in a table
- **Configuration**: all settings with defaults
- **How It Works**: the proxy pipeline with v2 additions
- **Sponsorship section**
- **Development** section

- [ ] **Step 4: Run full test suite to confirm nothing broken**

```bash
python3 -m pytest tests/ -v --tb=short
```
Expected: all pass

- [ ] **Step 5: Commit README + diagram**

```bash
git add README.md docs/architecture.excalidraw docs/architecture.png
git commit -m "docs: comprehensive README rewrite with architecture diagram"
```

---

### Task 16: Final verification and GitHub push

- [ ] **Step 1: Run full test suite**

```bash
python3 -m pytest tests/ -v
```
Expected: all tests pass (should be 350+)

- [ ] **Step 2: Verify install works**

```bash
pip3 install -e . 2>&1 | tail -5
python3 -c "import cachelens; print('OK')"
cachelens --help
```
Expected: all commands listed, no import errors

- [ ] **Step 3: Spot-check new endpoints**

```bash
cachelens ui --no-open --port 9999 &
sleep 2
curl -s http://localhost:9999/api/usage/waste-summary | python3 -m json.tool
curl -s http://localhost:9999/api/usage/token-heatmap | python3 -m json.tool
curl -s http://localhost:9999/api/usage/anomalies | python3 -m json.tool
curl -s http://localhost:9999/api/usage/digest | python3 -m json.tool
kill %1
```
Expected: all return valid JSON

- [ ] **Step 4: Commit summary**

```bash
git add -A
git status
```
Confirm only expected files are staged.

- [ ] **Step 5: Push to GitHub**

```bash
git push origin main
```

---

## Verification Checklist

After all tasks complete, verify:

- [ ] `python3 -m pytest tests/ -v` — all tests pass
- [ ] `python3 -c "from cachelens.waste_detector import detect_waste; print('ok')"` — imports clean
- [ ] `python3 -c "from cachelens.heatmap import compute_heatmap; print('ok')"` — imports clean
- [ ] `python3 -c "from cachelens.anomaly import detect_anomalies; print('ok')"` — imports clean
- [ ] `python3 -c "from cachelens.right_sizing import analyze_right_sizing; print('ok')"` — imports clean
- [ ] `python3 -c "from cachelens.digest import generate_digest; print('ok')"` — imports clean
- [ ] `cachelens --help` shows `top` and `report` commands
- [ ] Dashboard loads without console errors
- [ ] All 8 new API endpoints return 200 with valid JSON
- [ ] README renders correctly on GitHub
