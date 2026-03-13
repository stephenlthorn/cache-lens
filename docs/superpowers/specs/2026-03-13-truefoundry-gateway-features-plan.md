# TokenLens — AI Gateway Features Plan
**Inspired by:** TrueFoundry AI Gateway
**Target start:** week of 2026-03-16
**Repo:** stephenlthorn/token-lens

---

## What TrueFoundry Does That TokenLens Doesn't

TrueFoundry AI Gateway is a proxy layer with governance, routing, and safety on top. TokenLens currently does observability only (passive proxy + dashboard). The features below would transform TokenLens from a passive monitor into an active gateway.

---

## Feature Groups (prioritized)

### Group 1 — Multi-Provider Routing (HIGH VALUE, moderate effort)
**What:** Route LLM calls to different providers/models based on rules — not just observe traffic.

- **Fallback chains**: if Anthropic returns 529, auto-retry via OpenAI
- **Latency-based routing**: send to whichever endpoint is fastest right now
- **Weighted load balancing**: split traffic 70% Haiku / 30% Sonnet for a source
- **Model aliasing**: `gpt-4` → `claude-3-5-sonnet` transparent swap

**Files affected:** `proxy.py`, new `router.py`
**API changes:** new `POST /api/config/routing` endpoint, routing config stored in DB

---

### Group 2 — Budget Enforcement & Quota Management (HIGH VALUE, low effort)
**What:** Hard-stop or throttle requests when spend limits are hit — not just alert.

- **Per-source hard caps**: block requests from `my-agent` after $50/month
- **Per-model quotas**: limit Opus calls to 100/day
- **Soft throttle**: return 429 with `Retry-After` when approaching limits
- **Emergency kill switch**: pause all traffic for a source

**Files affected:** `proxy.py`, `store.py`, settings UI in `index.html`
**API changes:** new `POST /api/config/quotas` endpoint

---

### Group 3 — Input/Output Guardrails (HIGH VALUE, high effort)
**What:** Filter requests and responses for PII, toxicity, prompt injection.

- **PII scrubbing**: detect and redact email, phone, SSN patterns before logging
- **Prompt injection detection**: flag requests containing jailbreak patterns
- **Output content filtering**: warn or block toxic/harmful responses
- **Custom regex rules**: user-defined patterns to block/redact

**Files affected:** new `guardrails.py`, `proxy.py`, settings UI
**Dependencies:** optional: `presidio` for PII, pattern matching built-in

---

### Group 4 — Prompt Caching Visibility & Control (MEDIUM VALUE, low effort)
**What:** Already partially done. Extend with active caching control.

- **Cache warming**: pre-populate cache with static system prompt prefix
- **Per-source cache enable/disable toggle**: turn off cache for sources where it hurts
- **Cache TTL visibility**: show when cached content will expire

**Files affected:** `proxy.py`, dashboard UI

---

### Group 5 — Multi-Provider Support (MEDIUM VALUE, high effort)
**What:** Pass traffic to OpenAI, Gemini, Groq, Mistral — not just Anthropic.

- **Unified request format**: translate OpenAI-format requests to each provider
- **Provider health dashboard**: latency + error rate per provider in real time
- **Cost normalization**: show comparable cost across providers for same task

**Files affected:** new `providers/` directory, `proxy.py`, `pricing.py`, dashboard

---

### Group 6 — Developer Playground UI (MEDIUM VALUE, moderate effort)
**What:** Built-in UI to test prompts against any configured model.

- **Model selector**: pick provider + model from configured list
- **Parameter sliders**: temperature, max_tokens, streaming toggle
- **Cost preview**: estimated cost before sending
- **Side-by-side comparison**: run same prompt through two models, compare cost/output

**Files affected:** new page in `index.html`, new `POST /api/playground/run` endpoint

---

### Group 7 — RBAC & API Key Management (LOW VALUE for solo use, high effort)
**What:** Multi-user access with roles — relevant if TokenLens is shared with a team.

- **API key issuance**: generate keys that proxy traffic on behalf of a team member
- **Role-based limits**: dev keys can't hit Opus, only CI keys can exceed $5/day
- **Audit log**: every key creation/rotation/deletion logged

**Files affected:** new `auth.py`, DB schema changes, settings UI

---

## Suggested Weekly Execution Plan

| Day | Work |
|-----|------|
| Mon | Group 2: Budget enforcement in proxy — hard caps + 429 throttle. Low risk, high leverage. |
| Tue | Group 1: Fallback chains. Single config struct, wired into proxy retry logic. |
| Wed | Group 1: Latency routing + weighted load balancing. |
| Thu | Group 3: PII scrubbing + prompt injection detection (regex-based, no deps). |
| Fri | Group 6: Playground UI — model selector, parameters, cost preview. |
| Week 2 | Group 5: Multi-provider support (OpenAI compat layer). |

---

## Architecture Notes

- All new features gate behind `tokenlens config` — zero behaviour change unless configured
- Routing config, quota config, and guardrail rules stored in the existing SQLite DB (new tables)
- Playground runs requests through the proxy, so they show up in the live feed and cost tracking automatically
- Multi-provider requires a `providers/` abstraction layer that normalises request/response formats

---

## Out of Scope (for now)

- SSO / OAuth
- Cloud deployment (Kubernetes, VPC)
- Agentic workflow tracing (tool call spans)
- MCP server integration
