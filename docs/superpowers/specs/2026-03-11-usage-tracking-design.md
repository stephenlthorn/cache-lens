# CacheLens Usage Tracking — Design Spec

**Date:** 2026-03-11
**Status:** Approved

---

## 1. Overview

Extend CacheLens from a passive analysis tool into an always-on local proxy that tracks all AI API usage across every provider, model, and source on the machine — with a live dashboard, aggregated metrics, and rules-based optimization recommendations.

---

## 2. Architecture

Single FastAPI daemon extending the existing server, always running on `localhost:8420` (127.0.0.1 only — never 0.0.0.0).

```
cachelens daemon (127.0.0.1:8420)
├── /proxy/anthropic[/<tag>]     → forward to api.anthropic.com
├── /proxy/openai[/<tag>]        → forward to api.openai.com
├── /proxy/google[/<tag>]        → forward to generativelanguage.googleapis.com
├── /api/usage/*                 → aggregation queries
├── /api/live                    → WebSocket live feed
└── /                            → UI (existing + new pages)
```

### New components

| Component | Purpose |
|---|---|
| `proxy.py` | Intercept calls, detect source, forward to real API, record to DB |
| `store.py` | SQLite wrapper — writes calls, reads aggregations |
| `detector.py` | Identify source from User-Agent + URL tag + self-identification header |
| `aggregator.py` | Nightly and yearly rollup jobs (asyncio background tasks) |
| `recommender.py` | Rules-based recommendation engine over aggregated data |
| `pricing.py` | Token cost lookup + bundled pricing data |
| `server.py` | Extended with proxy routes, WebSocket, new API endpoints |
| `cli.py` | Extended with `install`, `uninstall`, `daemon`, `status` commands |
| `static/` | New UI pages: dashboard, deep-dive, recommendations |

---

## 3. Proxy & Source Detection

### URL structure

```
/proxy/<provider>[/<tag>]/<upstream-path>
```

- `<provider>`: `anthropic`, `openai`, `google`
- `<tag>`: optional source label — alphanumeric + hyphens only, max 64 chars (e.g. `claude-code`, `my-app`). Invalid tags (containing other characters) are sanitized by stripping disallowed characters and truncating; if nothing valid remains, the tag is discarded and source detection falls through to User-Agent/header/unknown.
- `<upstream-path>`: forwarded verbatim to the provider (e.g. `v1/messages`, `v1/chat/completions`)

**Examples:**
```
/proxy/anthropic/v1/messages
/proxy/anthropic/claude-code/v1/messages
/proxy/openai/my-app/v1/chat/completions
```

### Upstream URL mapping

| Provider | Base URL |
|---|---|
| `anthropic` | `https://api.anthropic.com` |
| `openai` | `https://api.openai.com` |
| `google` | `https://generativelanguage.googleapis.com` |

The proxy strips `/proxy/<provider>[/<tag>]` and forwards the remainder of the path plus all query parameters to the provider base URL. All original headers (including `Authorization`) are forwarded unchanged. The proxy never reads, stores, or logs API keys.

### Source detection priority

1. URL tag (explicit, always wins) — e.g. `/proxy/anthropic/claude-code/...` → source = `claude-code`
2. `User-Agent` header pattern matching — e.g. `claude-code/x.y.z` → source = `claude-code`
3. `X-CacheLens-Source` request header — apps can self-identify without URL changes
4. Falls back to `unknown`

### Source canonicalization

Detected sources are normalized to canonical names via a pattern table in `detector.py`. User-Agent patterns map to a fixed canonical string regardless of version — e.g. any `claude-code/*` → `claude-code`, any `python-httpx/*` → `python-httpx`. This prevents fragmentation in aggregation tables when tool versions change. The pattern table is bundled and extensible via config.

### `source` vs `source_tag` columns

- `source_tag`: the raw URL tag string as provided in the path (nullable — null if no tag was in the URL, or if the tag was fully sanitized away with no valid characters remaining). The original invalid string is not stored.
- `source`: the resolved canonical source name used for all grouping, aggregation, and display. Populated by detector priority order above.

All dashboard queries, aggregations, and the WebSocket live feed group by `source`. `source_tag` is stored for debugging and raw export only.

### Google SDK note

Google's Generative AI SDK (`google-generativeai`, `google-genai`) does not support a standard base URL environment variable across all versions. `cachelens install` sets `GOOGLE_AI_BASE_URL` as a best-effort attempt, but Google traffic may require manual SDK configuration (e.g. passing `transport="rest"` and a custom `client_options`). The install summary explicitly prints a note about this. The proxy endpoint is fully functional — only the auto-routing env var is unreliable for Google.

### Security

- Daemon binds to `127.0.0.1` only — not accessible from other machines
- No authentication on proxy endpoints — localhost-only binding is the security boundary
- API keys are forwarded transparently and never stored or logged
- Port conflict: if 8420 is in use, daemon exits with a clear error message. Use `--port` to override.

---

## 4. Data Model

SQLite at `~/.cachelens/usage.db`.

### `calls` (hot — 1 day retention)

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | auto-increment |
| `ts` | INTEGER | unix timestamp (seconds) |
| `provider` | TEXT | `anthropic`, `openai`, `google` |
| `model` | TEXT | as reported in response body |
| `source` | TEXT | detected or tagged |
| `source_tag` | TEXT | explicit URL tag (nullable) |
| `input_tokens` | INTEGER | from response body |
| `output_tokens` | INTEGER | from response body |
| `cache_read_tokens` | INTEGER | Anthropic prompt cache read tokens (0 for others) |
| `cache_write_tokens` | INTEGER | Anthropic prompt cache write tokens (0 for others) |
| `cost_usd` | REAL | computed at record time from `pricing` table |
| `endpoint` | TEXT | upstream path (e.g. `/v1/messages`) |
| `request_hash` | TEXT | SHA-256 of the raw request body bytes — used by the recommendations engine to detect when the same prompt body is sent repeatedly across calls (repeat detection). Hashes the raw body without normalization. Not used for deduplication — duplicate rows with the same hash are valid distinct calls. |

### `daily_agg` (365 day retention)

One row per `(date, provider, model, source)`. Unique constraint on `(date, provider, model, source)`.

| Column | Type |
|---|---|
| `date` | TEXT (`YYYY-MM-DD`) |
| `provider` | TEXT |
| `model` | TEXT |
| `source` | TEXT |
| `call_count` | INTEGER |
| `input_tokens` | INTEGER |
| `output_tokens` | INTEGER |
| `cache_read_tokens` | INTEGER |
| `cache_write_tokens` | INTEGER |
| `cost_usd` | REAL |

### `yearly_agg` (forever)

Mirrors `daily_agg` with `year` (INTEGER) instead of `date`. Unique constraint on `(year, provider, model, source)`.

### `pricing`

| Column | Type | Notes |
|---|---|---|
| `model` | TEXT PK | e.g. `claude-sonnet-4-6` |
| `input_usd_per_mtok` | REAL | cost per million input tokens |
| `output_usd_per_mtok` | REAL | cost per million output tokens |
| `cache_read_usd_per_mtok` | REAL | cost per million cache read tokens |
| `cache_write_usd_per_mtok` | REAL | cost per million cache write tokens |

Populated from a bundled `pricing.json` file (updated with releases). User overrides go in `~/.cachelens/pricing_overrides.toml` (see format in Cost Calculation section). Unknown models fall back to a `default` row per provider keyed as `anthropic/default`, `openai/default`, `google/default` — these have values of 0.0 in the bundled file (free/unknown). Malformed entries in `pricing_overrides.toml` are skipped with a logged warning; the daemon does not fail to start.

### Cost calculation

`cost_usd` in `calls` is computed at record time using the `pricing` table. `daily_agg.cost_usd` and `yearly_agg.cost_usd` are computed as `SUM(cost_usd)` from the source rows — never recomputed from token counts. Historical costs reflect the pricing data at the time each call was recorded. Pricing changes are not retroactively applied.

### `pricing_overrides.toml` format

```toml
# Override or add a model's pricing (all four fields required per entry)
[models."gpt-4o-mini"]
input_usd_per_mtok = 0.15
output_usd_per_mtok = 0.60
cache_read_usd_per_mtok = 0.075
cache_write_usd_per_mtok = 0.0

[models."my-local-model"]
input_usd_per_mtok = 0.0
output_usd_per_mtok = 0.0
cache_read_usd_per_mtok = 0.0
cache_write_usd_per_mtok = 0.0
```

Overrides are loaded at daemon startup and merged over the bundled `pricing.json`. Partial overrides (only some fields) are not supported — all four fields are required per model entry.

---

## 5. Data Retention & Rollups

```
raw calls (1 day)
      ↓ nightly rollup
daily_agg (365 days)
      ↓ yearly rollup (Jan 1)
yearly_agg (forever)
```

### Rollup trigger

Both rollup jobs run as **asyncio background tasks** inside the daemon. All times are in the **local system timezone**.

| Job | Scheduled time | Action |
|---|---|---|
| Nightly | 00:05 local time daily | Aggregate yesterday → `daily_agg`, purge raw calls > 1 day old |
| Yearly | 00:10 local time on Jan 1 | Aggregate prior year's `daily_agg` → `yearly_agg`, purge `daily_agg` rows > 365 days old |

**Missed rollup recovery:** On startup, the daemon checks the `rollups` bookkeeping table. If a nightly rollup for any date in the past 7 days is absent, it runs immediately. If the daemon has been offline for more than 7 days, raw calls older than 1 day are already purged — those dates gap silently in `daily_agg`. This is accepted data loss. If the yearly rollup for the prior year is absent and the current date is Jan 2 or later, it runs immediately.

**Yearly rollup re-aggregation:** The yearly rollup always re-aggregates from scratch — it queries `SUM` over all `daily_agg` rows for the target year and does `INSERT OR REPLACE`. It does not accumulate. This means re-running it after additional daily rows are added produces the correct total.

### Idempotency

Both jobs use `INSERT OR REPLACE` on the unique constraint of the target table. A `rollups` bookkeeping table records completed rollups by `(job, period)`. On startup, the daemon queries this table to determine which rollups are needed — duplicate runs are safe and produce the same result.

### Storage estimate

- Raw calls: ~1 day, then gone
- Daily: max ~13MB (365 days × ~37KB), then resets each year after yearly rollup
- Yearly: negligible forever (~few KB/year)

### Config (`~/.cachelens/config.toml`)

```toml
[retention]
raw_days = 1
daily_days = 365
aggregate = true
```

---

## 6. WebSocket Live Feed

**Endpoint:** `GET /api/live` (WebSocket upgrade)

**Message format** (JSON, one object per call):

```json
{
  "ts": 1741694400,
  "provider": "anthropic",
  "model": "claude-sonnet-4-6",
  "source": "claude-code",
  "input_tokens": 1240,
  "output_tokens": 312,
  "cache_read_tokens": 800,
  "cache_write_tokens": 0,
  "cost_usd": 0.00421,
  "endpoint": "/v1/messages"
}
```

- A call is **recorded and emitted** only when the upstream returns a successful (2xx) response containing usage metadata. Failed upstream responses (4xx/5xx) and interrupted calls are silently discarded — not recorded.
- For streaming responses (Anthropic SSE, OpenAI `stream=true`): the proxy passes chunks through to the client immediately (no buffering). Usage metadata is parsed from the final chunk in-flight. Anthropic: usage is in the `message_delta` event's `usage` field. OpenAI: usage is in the final `data:` object before `[DONE]`. If the stream is interrupted before usage metadata arrives, the call is discarded.
- WebSocket message is emitted after usage metadata is parsed — this may be slightly after the last byte is delivered to the client. Acceptable latency.
- No subscription model — all connected clients receive all events
- Maximum 10 concurrent WebSocket connections. Additional connections beyond 10 are rejected with HTTP 503.
- Clients should implement exponential backoff reconnection (documented in UI JS)
- No throttle — at typical developer usage volumes, rate is not a concern

---

## 7. UI Pages

### Page 1: Dashboard (default)
- Live feed strip — WebSocket stream of calls as they arrive (provider / model / source / tokens / cost)
- KPI cards: today / last 7 days / last 30 days / last 365 days cost (rolling windows, local timezone)
- Token volume chart with day/week/month/year toggle
- Cost breakdown chart by provider
- Source breakdown table: source rows × provider columns, cells = cost

### Page 2: Deep Dive
- Filter bar: date range + provider + model + source
- Timeline chart (zooms to selected range)
- Sortable table: one row per `(provider, model, source)` with totals
- Row expand → daily breakdown for that combo
- Cache efficiency sub-section: cache hit % per model (Anthropic only)

### Page 3: Recommendations
- Rules-based, no LLM calls — consistent with existing engine philosophy
- Ranked findings by estimated impact, examples:
  - "claude-sonnet-4-6 via `unknown` — 0% cache hits across 3,200 calls. Tag your sources and check prompt structure."
  - "40% of OpenAI spend on `gpt-4o` for calls under 200 tokens — consider routing to `gpt-4o-mini`."
  - "Cache write tokens never reused for source `myapp` — system prompt likely changes every call."
- Each finding links to the relevant Deep Dive filter view

### Page 4: Analyze (existing — unchanged)
- Paste/upload trace for one-off analysis

---

## 8. Install / Uninstall

```bash
cachelens install              # one-time setup
cachelens uninstall            # clean removal (data preserved unless --purge)
cachelens daemon [--port N]    # start manually
cachelens status [--format json]  # daemon status
```

### `cachelens install` steps

1. Create `~/.cachelens/` with default `config.toml`
2. Write LaunchAgent plist at `~/Library/LaunchAgents/com.cachelens.plist` (macOS) or systemd user service at `~/.config/systemd/user/cachelens.service` (Linux)
3. Set env vars in shell config files (`~/.zshrc`, `~/.bashrc`, `~/.profile` as applicable) **and** via `launchctl setenv` on macOS (covers GUI apps and agents that don't source shell files)
4. Start daemon immediately — no reboot required
5. Print a clear summary of every file written and every env var set

**Env vars set:**
```
ANTHROPIC_BASE_URL=http://localhost:8420/proxy/anthropic
OPENAI_BASE_URL=http://localhost:8420/proxy/openai
GOOGLE_AI_BASE_URL=http://localhost:8420/proxy/google
```

Existing values for these vars are backed up as comments in the shell file before overwriting.

### `cachelens uninstall`

Removes plist/service, unsets env vars (restores backed-up values if present), stops daemon. If the daemon is already stopped, uninstall proceeds silently (idempotent). Leaves `~/.cachelens/` intact unless `--purge` is passed.

### `cachelens status` output

Human-readable by default:
```
CacheLens daemon: running (pid 12345, port 8420)
DB size: 4.2 MB
Raw calls today: 142
Retention: raw=1d, daily=365d, aggregate=true
Last rollup: 2026-03-11 00:00:01
```

With `--format json`:
```json
{
  "daemon": "running",
  "pid": 12345,
  "port": 8420,
  "db_size_bytes": 4404019,
  "raw_calls_today": 142,
  "retention": {"raw_days": 1, "daily_days": 365, "aggregate": true},
  "last_nightly_rollup": "2026-03-11T00:05:01",
  "last_yearly_rollup": "2026-01-01T00:10:02"
}
```
Fields `last_nightly_rollup` and `last_yearly_rollup` are `null` if no rollup has run yet.

### Port conflicts

If port 8420 is in use, `cachelens daemon` exits immediately with:
```
Error: port 8420 is already in use. Use --port N to specify a different port.
```

If a non-default port is used, `cachelens install` writes the correct port into the env vars and LaunchAgent config.

---

## 9. Out of Scope (this spec)

- Windows support
- Team/multi-user features
- Cloud sync
- LLM-powered recommendations
- HTTPS MITM interception (cert-based)
- Retroactive cost recalculation when pricing changes
