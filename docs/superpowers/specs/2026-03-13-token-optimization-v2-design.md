# CacheLens v2: Token Optimization + Intelligence + Developer Tooling

**Date:** 2026-03-13
**Status:** Design approved
**Approach:** Full Stack (Approach 3) — highest-impact feature from each category

## Philosophy

CacheLens v2 follows an **observe + suggest** model. All features passively detect waste and surface insights. Active proxy-layer modifications (stripping whitespace, compressing prompts) are available as opt-in toggles per feature. Nothing changes the request by default.

---

## Feature 1: Junk Token Detector

### Purpose
Scan every proxied request for token waste patterns and quantify savings.

### Waste Types Detected

| Type | Detection Method | Risk |
|------|-----------------|------|
| Whitespace bloat | Regex: excessive newlines, trailing spaces, redundant indentation | Zero false positives |
| Polite filler | Pattern dictionary: ~50 phrases ("Certainly!", "I'd be happy to help!", "Sure thing!", "Great question!") in system prompts | Low FP — only flags in system role |
| Redundant instructions | Same instruction block appearing 2+ times in a single request | Zero FP — exact match |
| Empty messages | Messages with <5 tokens | Zero FP |

### Data Model

New `call_waste` table:

```sql
CREATE TABLE call_waste (
    id INTEGER PRIMARY KEY,
    call_id INTEGER REFERENCES calls(id),
    waste_type TEXT,          -- 'whitespace' | 'polite_filler' | 'redundant_instruction' | 'empty_message'
    waste_tokens INTEGER,
    savings_usd REAL,
    detail TEXT,              -- JSON: matched pattern, location, snippet
    FOREIGN KEY (call_id) REFERENCES calls(id)
);
CREATE INDEX idx_call_waste_call_id ON call_waste(call_id);
```

### API

- `GET /api/usage/waste-summary?days=30` — aggregated waste by type, total tokens, total savings
- `GET /api/usage/waste/{call_id}` — waste breakdown for a specific call
- Waste data also included in WebSocket live feed events (new `waste` field)

### Dashboard

New "Token Waste" card on the dashboard:
- Hero number: total junk tokens found (with USD savings)
- Breakdown bar: whitespace vs filler vs redundant vs empty
- Trend line: waste tokens per day over time

### Opt-in Active Mode

Settings toggles (via API and dashboard Settings panel):
- `optimization.strip_whitespace` — remove excess whitespace before forwarding
- `optimization.strip_filler` — remove polite filler from system prompts
- `optimization.dedup_instructions` — collapse duplicate instruction blocks

Each toggle independently controllable. All default to `false`.

### Implementation

New module: `src/cachelens/waste_detector.py`

- `detect_waste(request_body: dict, provider: str) -> list[WasteItem]`
- Called from `proxy.py` after parsing the request body, before forwarding
- Returns waste items; proxy stores them in `call_waste` table
- If active mode enabled for a waste type, proxy also modifies the request body before forwarding
- Token counting uses the existing `tiktoken` tokenizer (cl100k_base)

---

## Feature 2: Output Bloat Detector

### Purpose
Track `max_tokens` requested vs actual output tokens returned. Flag over-provisioned requests and suggest tighter limits.

### Detection Logic

For each call, compare:
- `max_tokens` (from request body) vs `output_tokens` (from response)
- Track `utilization = output_tokens / max_tokens`
- Flag when utilization < 25% over 10+ calls for a source+model combo

Also detect verbose preambles in responses (when request logging is enabled):
- Pattern match first 50 chars of response for filler phrases
- Track frequency per model

### Data Model

New columns on `calls` table:

```sql
ALTER TABLE calls ADD COLUMN max_tokens_requested INTEGER;
ALTER TABLE calls ADD COLUMN output_utilization REAL;
```

`max_tokens_requested` extracted from request body during proxy handling.
`output_utilization` computed as `output_tokens / max_tokens_requested` (null if max_tokens not set).

### API

- `GET /api/usage/output-efficiency?days=30` — per source+model: avg utilization, suggested max_tokens (p95 of actual output), estimated savings
- Included in recommendations engine as new check type: `output_bloat`

### Dashboard

New "Output Efficiency" card:
- Avg utilization % (gauge visualization)
- Suggested max_tokens per source+model
- Estimated savings if tightened

### Implementation

- Extract `max_tokens` in `proxy.py` during request parsing (provider-specific field names: `max_tokens` for Anthropic/OpenAI, `maxOutputTokens` for Google)
- Compute utilization after response received
- Store on call record
- New recommendation check in `recommender.py`: `output_bloat`

---

## Feature 3: Conversation History Bloat Tracker

### Purpose
For multi-turn conversations, measure how much of the input is stale history vs the actual new content.

### Detection Logic

Multi-turn detection: request has >4 messages in the messages array.

For detected multi-turn requests:
- **History tokens**: sum of all message tokens except system prompt and last user message
- **New content tokens**: last user message tokens
- **History ratio**: `history_tokens / total_input_tokens`
- Flag when history ratio > 70% (most of the input is old conversation)

Also cross-reference with the existing repeated block detection to identify system prompts sent identically every turn.

### Data Model

New columns on `calls` table:

```sql
ALTER TABLE calls ADD COLUMN message_count INTEGER;
ALTER TABLE calls ADD COLUMN history_tokens INTEGER;
ALTER TABLE calls ADD COLUMN history_ratio REAL;
```

### API

- `GET /api/usage/conversation-efficiency?days=30` — per source: avg message count, avg history ratio, estimated savings from trimming
- Included in recommendations engine as new check type: `history_bloat`

### Dashboard

New widget in the Insights tab:
- "Conversation Efficiency" — avg history ratio per source
- Suggested trim point: "Keep last N turns + summary"
- Estimated savings

### Implementation

- Count messages and compute history tokens in `proxy.py` during request parsing
- Store on call record
- New recommendation check in `recommender.py`: `history_bloat`
- Suggestion: "Your source X sends avg 12 turns per request. 73% of input tokens are history. Trimming to last 4 turns would save ~3,800 tokens/call ($X.XX/day)."

---

## Feature 4: Prompt Compression Preview

### Purpose
Show what *could* be saved if lightweight compression were applied, without actually modifying anything.

### Compression Estimate

For each logged request, compute a "compressible tokens" estimate by summing:
1. Whitespace waste (from junk token detector)
2. Redundant instruction waste (from junk token detector)
3. Polite filler waste (from junk token detector)
4. History bloat above 50% threshold (from conversation tracker)

This is a composite score derived from features 1-3 — no new detection logic needed.

### API

- Compression estimate included in `GET /api/usage/waste-summary` as `total_compressible_tokens` and `compression_ratio`
- Per-call compression badge in `GET /api/usage/recent` response (new `compressible_tokens` field)

### Dashboard

- "Compressible" badge on each call in the live feed (shows estimated saveable tokens)
- Summary card: "X% of your input tokens are compressible. Potential savings: $Y.XX/month"

### Implementation

No new module — this is a view layer that aggregates waste data from features 1-3.
- `waste_detector.py` already produces per-call waste items
- A utility function `compression_estimate(waste_items, history_tokens, history_ratio)` computes the composite
- Dashboard renders the badge from the existing waste data

---

## Feature 5: Token Heatmap

### Purpose
Break down every request's input tokens into labeled sections so users can see *where* their tokens go.

### Section Classification

For each request's messages array, classify each message into a section:

| Section | Detection Rule |
|---------|---------------|
| `system_prompt` | role = "system" (first occurrence) |
| `tool_definitions` | role = "system" containing JSON schema patterns, OR content matching `{"type": "function"` / `tools` array in request body |
| `context` | Large blocks (>500 tokens) injected mid-conversation, OR content between `<context>`/`<documents>`/`<retrieved>` markers |
| `conversation_history` | All user/assistant messages except the last user message |
| `user_query` | Last user-role message |
| `other` | Anything that doesn't match above |

### Data Model

New column on `calls` table:

```sql
ALTER TABLE calls ADD COLUMN token_heatmap TEXT;  -- JSON
```

Example value:
```json
{
    "system_prompt": 1200,
    "tool_definitions": 3400,
    "context": 800,
    "conversation_history": 2100,
    "user_query": 150,
    "other": 0,
    "total": 7650
}
```

### API

- `GET /api/usage/token-heatmap?days=30` — aggregated heatmap averages per source+model
- Per-call heatmap available in `GET /api/usage/recent` (new `token_heatmap` field)

### Dashboard

New "Token Heatmap" visualization:
- Stacked horizontal bar per source showing section proportions
- Color-coded: system=violet, tools=amber, context=blue, history=slate, query=green
- Click a source to see per-call breakdown over time
- Average breakdown percentages displayed

### Implementation

New module: `src/cachelens/heatmap.py`

- `compute_heatmap(messages: list[dict], tools: list | None, provider: str) -> dict`
- Called from `proxy.py` after parsing request body
- Uses pattern matching on message content + role to classify
- Token counting via tiktoken
- Stored as JSON string on call record

---

## Feature 6: Cost Anomaly Detection

### Purpose
Automatically detect unusual spend patterns and explain what caused them.

### Detection Algorithm

- Compute 14-day rolling mean and standard deviation of daily spend per source
- Flag any day where `spend > mean + 2 * stddev` as an anomaly
- Also flag: call count spikes (>2x normal), avg token spikes (>2x normal)

### Drill-down Report

When an anomaly is detected, auto-generate a report:
- Top 5 most expensive calls that day
- Model distribution comparison (today vs 14-day avg)
- Call count comparison
- Avg input/output tokens comparison
- Source responsible for the spike

### Events

- WebSocket event: `{"type": "cost_anomaly", "source": "...", "spend_usd": ..., "expected_usd": ..., "drill_down": {...}}`
- Webhook event: `cost_anomaly` (opt-in via webhook settings)

### API

- `GET /api/usage/anomalies?days=30` — list of detected anomalies with drill-down data

### Dashboard

- Anomaly markers (red dots) on the cost trend chart
- Click to expand drill-down: "What happened?" panel showing the top cost drivers
- Alert banner for active anomalies (same pattern as existing cost alert banner)

### Implementation

New module: `src/cachelens/anomaly.py`

- `detect_anomalies(store: UsageStore, days: int) -> list[Anomaly]`
- Queries `daily_agg` for rolling stats
- Generates drill-down by querying top calls for flagged dates
- Called from a new endpoint and optionally from the aggregator on each rollup

---

## Feature 7: Model Right-Sizing Report

### Purpose
Identify calls that use expensive models for simple tasks, and quantify the savings from downgrading.

### Complexity Heuristic

For each call, compute a complexity score based on observable features:

| Signal | Points | Rationale |
|--------|--------|-----------|
| Input tokens > 2000 | +2 | Long prompts usually need strong models |
| Contains code blocks (``` or indented blocks) | +2 | Code tasks need capable models |
| Tool/function calls in request | +2 | Tool use benefits from stronger reasoning |
| Message count > 6 | +1 | Deep conversations need context tracking |
| Output tokens > 500 | +1 | Long outputs suggest complex generation |

Score 0-2 = "simple", 3-4 = "moderate", 5+ = "complex"

### Downgrade Map

| Current Model | Suggested For Simple | Suggested For Moderate |
|--------------|---------------------|----------------------|
| claude-opus-4-6 | claude-haiku-4-5 | claude-sonnet-4-6 |
| claude-sonnet-4-6 | claude-haiku-4-5 | (keep) |
| gpt-4o | gpt-4o-mini | (keep) |
| gpt-4.1 | gpt-4.1-mini | (keep) |
| gemini-2.5-pro | gemini-2.0-flash | (keep) |

### API

- `GET /api/usage/right-sizing?days=30` — per source+model: call distribution by complexity, estimated savings from downgrades, suggested model

### Dashboard

New Insights card: "Model Right-Sizing"
- Pie chart: simple vs moderate vs complex calls per source
- Headline: "42% of your Opus calls are simple queries. Switching to Haiku would save $X.XX/month"
- Drill-down: table of sources with downgrade recommendations

### Implementation

New module: `src/cachelens/right_sizing.py`

- `analyze_right_sizing(store: UsageStore, pricing: PricingTable, days: int) -> RightSizingReport`
- Queries raw calls (within retention window) for complexity signals
- Uses pricing table to compute hypothetical costs
- New recommendation check in `recommender.py`: `right_sizing`

---

## Feature 8: `cachelens top`

### Purpose
Live htop-style terminal view of API traffic.

### Display Layout

```
CacheLens top — 14 calls/min | $0.47/hr | Cache: 62% | Waste: 1,240 tok/min
───────────────────────────────────────────────────────────────────────────────
TIME       SOURCE          MODEL              IN    OUT  CACHE   COST   WASTE
12:04:01   claude-code     opus-4-6         2.4k   890   1.2k  $0.12    340
12:04:00   cursor          sonnet-4-6         800   210    600  $0.01      0
12:03:58   claude-code     opus-4-6         5.1k  1.2k   3.8k  $0.18    890
12:03:55   aider           haiku-4-5          200    45    180  $0.00      0
...
[q]uit  [p]ause  [f]ilter source  [m]odel filter  [s]ort column
```

### Implementation

New CLI command in `cli.py`:

```python
@main.command()
@click.option("--port", default=8420)
def top(port: int):
    """Live terminal view of API traffic."""
    from .top import run_top
    run_top(port=port)
```

New module: `src/cachelens/top.py`

- Connects to WebSocket `ws://localhost:{port}/api/live`
- Uses `rich` library for terminal rendering (Live display + Table)
- Header row: computed from rolling window of last 60 seconds
- Scrolling table: last 50 calls
- Keyboard handling via `rich` or `click.getchar()`
- Color coding: green for high cache hit, red for high cost, yellow for waste detected

### Dependencies

Add `rich>=13.0` to `pyproject.toml` dependencies.

---

## Feature 9: Weekly Cost Digest

### Purpose
Automated 7-day summary, available via CLI and webhook.

### Report Contents

```
CacheLens Weekly Digest (Mar 6 - Mar 13, 2026)
═══════════════════════════════════════════════

Spend:      $42.17  (+12% vs prev week)
Calls:      1,847
Cache Hit:  64% (improving)

Top Cost Drivers:
  1. claude-code / opus-4-6      $28.40  (67%)
  2. cursor / sonnet-4-6          $8.20  (19%)
  3. aider / haiku-4-5            $5.57  (13%)

Waste Detected:
  Whitespace:    12,400 tokens  ($1.24)
  Polite filler:  3,200 tokens  ($0.32)
  History bloat:  8,900 tokens  ($0.89)

Optimization Opportunities:
  1. Right-size 42% of Opus calls to Haiku → save $8.40/wk
  2. Strip whitespace from claude-code prompts → save $1.24/wk
  3. Trim conversation history in cursor → save $0.89/wk

Budget: 84% of monthly limit used (16 days remaining)
```

### CLI

```bash
cachelens report              # Last 7 days, human format
cachelens report --days 30    # Last 30 days
cachelens report --format json  # Machine-readable
```

### Webhook

New event type `weekly_digest` — fires automatically every Sunday via aggregator scheduler. Contains the full report as JSON.

### API

- `GET /api/usage/digest?days=7` — returns digest data as JSON

### Implementation

New module: `src/cachelens/digest.py`

- `generate_digest(store: UsageStore, pricing: PricingTable, days: int) -> DigestReport`
- Aggregates data from: daily_agg, call_waste, anomalies, right_sizing
- New CLI command in `cli.py`
- New endpoint in `server.py`
- Weekly scheduler job in `aggregator.py`

---

## Architecture Summary

### New Modules

| Module | Purpose |
|--------|---------|
| `waste_detector.py` | Junk token detection (whitespace, filler, redundant, empty) |
| `heatmap.py` | Token section classification (system, tools, context, history, query) |
| `anomaly.py` | Cost anomaly detection with drill-down |
| `right_sizing.py` | Model complexity analysis and downgrade recommendations |
| `top.py` | Live terminal UI |
| `digest.py` | Weekly report generation |

### Schema Changes

- New table: `call_waste` (waste items per call)
- New columns on `calls`: `max_tokens_requested`, `output_utilization`, `message_count`, `history_tokens`, `history_ratio`, `token_heatmap`

### New API Endpoints

| Endpoint | Feature |
|----------|---------|
| `GET /api/usage/waste-summary` | Junk Token Detector |
| `GET /api/usage/waste/{call_id}` | Per-call waste detail |
| `GET /api/usage/output-efficiency` | Output Bloat Detector |
| `GET /api/usage/conversation-efficiency` | History Bloat Tracker |
| `GET /api/usage/token-heatmap` | Token Heatmap |
| `GET /api/usage/anomalies` | Cost Anomaly Detection |
| `GET /api/usage/right-sizing` | Model Right-Sizing |
| `GET /api/usage/digest` | Weekly Digest |

### New CLI Commands

| Command | Feature |
|---------|---------|
| `cachelens top` | Live terminal view |
| `cachelens report` | Weekly digest |

### New Dependencies

| Package | Purpose |
|---------|---------|
| `rich>=13.0` | Terminal UI for `cachelens top` |

### Dashboard Changes

- New cards: Token Waste, Output Efficiency, Token Heatmap
- New Insights widgets: Conversation Efficiency, Model Right-Sizing, Anomaly markers
- Live feed: compressible badge, waste field per call
- Cost chart: anomaly markers with click-to-drill-down

### Integration Points

All features hook into the existing proxy pipeline:
1. Request arrives at `proxy.py`
2. Parse request body (extract messages, max_tokens, tools)
3. Run waste detection, heatmap classification, history analysis
4. Forward request to provider (optionally modified if active optimizations enabled)
5. Receive response, extract output tokens
6. Compute output utilization
7. Store all data on call record + call_waste table
8. Broadcast via WebSocket (includes waste + heatmap data)
9. Anomaly detection runs on daily rollup
10. Right-sizing and digest run on weekly rollup or on-demand via API
