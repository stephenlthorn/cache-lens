# TokenLens

**AI cost intelligence — local-first.**

TokenLens is a transparent proxy + dashboard that tracks every AI API call, shows you exactly where your money goes, and finds ways to spend less.

- **Zero config** — install, point your SDK, done
- **100% local** — your data never leaves your machine
- **Real-time** — live WebSocket feed of every API call
- **Works with** Anthropic, OpenAI, and Google AI

![TokenLens Dashboard](docs/dashboard.png)

---

## Quick Start

```bash
# 1. Install
pip install tokenlens        # or: pipx install tokenlens

# 2. Set up as background service (auto-starts on boot)
tokenlens install

# 3. Open dashboard
tokenlens ui
```

That's it. Your shell now has `ANTHROPIC_BASE_URL`, `OPENAI_BASE_URL`, and `GOOGLE_AI_BASE_URL` pointed at the TokenLens proxy. Every SDK call flows through TokenLens automatically.

### Manual setup (without daemon)

```bash
# Start the server
tokenlens ui --port 8420

# Point your SDK at the proxy
export ANTHROPIC_BASE_URL="http://localhost:8420/proxy/anthropic"
export OPENAI_BASE_URL="http://localhost:8420/proxy/openai"
```

---

## Features

### Cost Intelligence

| Feature | Description |
|---------|-------------|
| **Real-time KPIs** | Total spend, savings, call count, and token breakdown at a glance |
| **Spend Forecasting** | Projected monthly cost with trend analysis and confidence scoring |
| **Token Cost Breakdown** | Daily cost split by input, output, cache read, and cache write tokens |
| **Cost Allocation Tags** | Per-source cost aggregation — see which tool or agent spends the most |
| **Model Comparison** | "What if I switched from Opus to Sonnet?" — instant cost comparison |
| **Budget Caps** | Daily and monthly spend limits with automatic request blocking |
| **Per-source Budgets** | Set spend limits per tool/agent/source independently |
| **Custom Pricing** | Override default per-token rates for any model |

### Observability

| Feature | Description |
|---------|-------------|
| **Live Feed** | Real-time WebSocket stream of every API call with cost |
| **Cache Hit Tracking** | Daily cache hit rate trend with improvement/degradation detection |
| **Provider Health** | P50/P95/P99 latency and error rates per provider |
| **Rate Limit Tracking** | Hourly 429 error counts and timeline per provider |
| **Request Logging** | Optional full request/response body capture for debugging |
| **Session Detection** | Groups API calls into logical sessions by timing and source |
| **Prometheus Metrics** | `GET /metrics` endpoint for Grafana/Prometheus integration |

### Recommendations

| Feature | Description |
|---------|-------------|
| **8-Check Engine** | Automated analysis: cache utilization, model costs, prompt sizes, waste detection, call frequency, and more |
| **Actionable Insights** | Each recommendation includes estimated savings impact |

### Integrations

| Feature | Description |
|---------|-------------|
| **CSV Export** | Download usage data for spreadsheets or BI tools |
| **Webhook Notifications** | HTTP callbacks on `call_recorded` and `cost_alert` events |
| **Prometheus /metrics** | Standard exposition format for monitoring stacks |

### Proxy Features

| Feature | Description |
|---------|-------------|
| **Request Deduplication** | Cache identical requests within a TTL window (opt-in) |
| **Cost Alerts** | Real-time WebSocket alerts when daily spend exceeds threshold |

---

## CLI Reference

```
tokenlens install             # Install as background service
tokenlens uninstall [--purge] # Remove service (--purge deletes data)
tokenlens ui [--port 8420]    # Open dashboard in browser
tokenlens daemon              # Run in foreground (for debugging)
tokenlens status              # Show daemon status
tokenlens analyze <file|->    # Analyze a prompt trace for waste
```

### Analyze options

```
--format human|json     Output format (default: human)
--suggestions           Show full suggestion details
--score-only            Print only the cacheability score
--min-tokens N          Minimum tokens to flag (default: 50)
```

---

## API Reference

All endpoints are served at `http://localhost:8420` (default port).

### Usage

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/usage/kpi?days=30` | KPI summary (cost, savings, tokens) |
| `GET` | `/api/usage/daily?days=30` | Daily aggregated usage |
| `GET` | `/api/usage/recent?limit=50` | Recent raw API calls |
| `GET` | `/api/usage/sources` | Cost breakdown by source |
| `GET` | `/api/usage/forecast` | Projected monthly spend |
| `GET` | `/api/usage/by-tag?days=30` | Cost allocation by tag/source |
| `GET` | `/api/usage/token-breakdown?days=30` | Cost split by token type |
| `GET` | `/api/usage/cache-trend?days=30` | Cache hit rate over time |
| `GET` | `/api/usage/compare?from_model=X&to_model=Y` | Model cost comparison |
| `GET` | `/api/usage/sessions?days=1` | Detected sessions |
| `GET` | `/api/usage/budget-status` | Current spend vs limits |
| `GET` | `/api/usage/provider-health?days=1` | Latency and error rates |
| `GET` | `/api/usage/rate-limits?days=1` | Rate limit (429) events |
| `GET` | `/api/usage/recommendations` | AI-generated cost recommendations |

### Settings

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET/PUT` | `/api/settings/alerts` | Cost alert thresholds |
| `GET/PUT` | `/api/settings/budget` | Budget cap configuration |
| `GET/PUT` | `/api/settings/pricing` | Custom pricing overrides |
| `GET/PUT` | `/api/settings/webhooks` | Webhook notification config |

### Other

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/status` | Daemon health and DB stats |
| `GET` | `/api/export/csv?days=30` | Download usage as CSV |
| `GET` | `/api/logs?limit=20` | Request/response logs |
| `GET` | `/api/logs/{id}` | Single log entry detail |
| `GET` | `/metrics` | Prometheus metrics |
| `POST` | `/api/analyze` | Analyze prompt for cacheability |
| `WS` | `/api/live` | Real-time call stream |
| `ANY` | `/proxy/{provider}/{path}` | Transparent API proxy |

---

## Configuration

TokenLens stores its data in `~/.tokenlens/`:

```
~/.tokenlens/
  usage.db        # SQLite database (all usage data)
  config.toml     # Retention settings
  logs/           # Daemon logs
```

### Settings (via API or Dashboard)

| Setting | Default | Description |
|---------|---------|-------------|
| `budget.daily_limit_usd` | — | Daily spend cap |
| `budget.monthly_limit_usd` | — | Monthly spend cap |
| `budget.enabled` | `false` | Enable budget enforcement |
| `alerts.daily_cost_threshold` | — | Alert when daily spend exceeds this |
| `alerts.enabled` | `false` | Enable cost alerts |
| `webhook.url` | — | Webhook endpoint URL |
| `webhook.events` | — | Comma-separated event types |
| `webhook.enabled` | `false` | Enable webhooks |
| `logging.enabled` | `false` | Capture request/response bodies |
| `dedup.enabled` | `false` | Enable request deduplication |
| `dedup.ttl_seconds` | `60` | Dedup cache TTL |

---

## How It Works

```
Your App → SDK → TokenLens Proxy (localhost:8420) → AI Provider
                      ↓
              Records every call
              Calculates cost
              Checks budget caps
              Broadcasts via WebSocket
                      ↓
              Dashboard + API + Alerts
```

TokenLens acts as a transparent HTTP proxy. It intercepts API calls, records token usage and cost, then forwards them to the real provider. Responses stream back untouched. The dashboard reads from a local SQLite database — nothing leaves your machine.

---

## Development

```bash
git clone https://github.com/stephenlthorn/cache-lens.git
cd cache-lens
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python3 -m pytest tests/ -v
```

### Requirements

- Python 3.11+
- No external services required

---

## Sponsorship

If TokenLens helps you ship faster or cut token spend, consider sponsoring:

https://github.com/sponsors/stephenlthorn

| Tier | Price |
|------|-------|
| Supporter | $5/month |
| Power User | $25/month |
| Company Sponsor | $200/month |

---

## License

See [LICENSE](./LICENSE) for details.
