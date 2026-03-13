# Token Spend Dashboard Redesign

**Date:** 2026-03-13
**Status:** Approved
**Scope:** `src/tokenlens/static/index.html`, `app.js`, `style.css` — Dashboard page only

---

## Problem

The current dashboard is framed around cache efficiency. The cache explainer banner leads the page, "Cache Savings" is a hero KPI, and a dedicated "Cache Hit Rate" chart occupies half the chart row. Users who care primarily about total token spend — how much they're spending, on what, and where — have to hunt past cache-centric UI to find that information.

## Goal

Reframe the dashboard around **cost and spend**. Cache remains available as a useful module but is no longer the primary lens. The user should open the dashboard and immediately understand: how much did I spend, on which models, from which sources.

---

## Design Decisions

### 1. KPI Row (top of page)

Six cards in a row:

| Card | Metric | Style |
|------|--------|-------|
| Today | Actual spend | Blue, large |
| 7 Days | Actual spend | Blue, large |
| 30 Days | Actual spend | Blue, large |
| 365 Days | Actual spend | Blue, large |
| Forecast | Projected month-end spend (`projected_monthly_usd` from `/api/usage/forecast`) | Amber, medium |
| Saved (30d) | Cache savings | Green, small/secondary |

**Removed:** the existing per-period "savings" sub-label under each KPI card. Savings now lives in one dedicated small card.
**Promoted:** Month-End Forecast moves from its own card section into the KPI row. It uses the existing `loadForecast()` call to `/api/usage/forecast` and extracts `projected_monthly_usd`. The full forecast card (with trend arrow, confidence pill, daily avg) is removed from the dashboard body.

### 2. Stacked Daily Cost Chart (full width)

Replaces both the "Token Volume" and "Cache Hit Rate" charts. Has its **own independent range tabs** (7d / 30d / 90d) — changing the KPI period does not affect the chart range, and vice versa.

- Single full-width stacked bar chart
- Y-axis: dollar cost
- X-axis: days
- Toggle: **By Model** | **By Source** (switches stack color coding)
- Range tabs: 7d / 30d / 90d (independent from KPI period)
- **Model colors:** fragments matched case-insensitively: `opus`→`#7c5cff`, `sonnet`→`#64a0ff`, `haiku`→`#2ee9a6`. Unrecognized models cycle through the overflow palette (indices 3–7 of the palette below, then wrap).
- **Source colors:** each unique source key cycles through the source palette in encounter order. The source palette is the extended palette starting at index 3 to avoid visual collision with model colors: `#ffb020`, `#ff6b6b`, `#e879f9`, `#38bdf8`, `#a3e635`, then wraps from index 0.
- **Extended palette** (8 colors): `#7c5cff`, `#64a0ff`, `#2ee9a6`, `#ffb020`, `#ff6b6b`, `#e879f9`, `#38bdf8`, `#a3e635`

### 3. Spend Breakdown Section

Two side-by-side panels immediately below the cost chart:

**By Model** — horizontal bar per model, dollar amount + percentage of period total
**By Source** — horizontal bar per source, dollar amount + percentage of period total

Both panels are driven by the **same period tabs as the KPI row** (Today / 7d / 30d / 365d). No additional period selector is added. The period tabs in the KPI row control both the KPI card values and the Spend Breakdown panels; the chart range tabs are separate.

### 4. Cache & Efficiency — Collapsed Accordion

Replaces the always-visible "Token Anatomy" 4-box section and the dedicated Cache Hit Rate chart.

- Default state: **collapsed** — shows one summary line: `Cache & Efficiency  |  $X.XX saved  |  XX% hit rate  |  ▼`
- Expanded state: reveals the existing Token Anatomy boxes (Fresh Input / Cache Write / Cache Read / Output), the Cache Hit Rate trend chart, and the Waste Detected stats
- The cache explainer banner at the top of the page is **removed entirely**

### 5. Existing Provider / Source Tables

The `.dash-bottom` section (containing `#providerTable` and `#sourceTable`) is **removed**. It is fully replaced by the new Spend Breakdown section (§3).

### 6. Waste Detected KPI Card

The existing "Waste Detected" KPI card (`#kpi-waste-tokens`, `#kpi-waste-savings`) is **moved into the cache accordion** expanded section. It is not included in the main KPI row.

### 7. Live Feed + Recent Sessions

No changes. These sections remain as-is below the spend breakdown. The Live Feed table already shows per-request cost.

### 8. Removed from Dashboard

- Cache explainer banner (`div.cache-explainer`)
- "Token Anatomy" section (`div.token-anatomy`) — moved inside cache accordion
- "Cache Hit Rate" chart (`canvas#cacheTrendChart`) — moved inside cache accordion
- "Waste Detected" KPI card — moved inside cache accordion
- "Token Volume" chart (`canvas#tokenChart`) — replaced by stacked cost chart
- Full forecast card (`#forecast-card`) — `projected_monthly_usd` surfaced in KPI row instead
- `.dash-bottom` provider/source tables — replaced by Spend Breakdown section
- Per-card savings sub-label from individual KPI cards

---

## Component Map

| Component | File location | Change |
|-----------|--------------|--------|
| KPI row | `index.html` `#kpi-grid` | Replace 5 existing cards with 6 new ones |
| Token Volume + Cache Hit Rate charts | `index.html` `.chart-row` | Replace with single `.cost-chart-card` (full width) |
| Token Anatomy + Waste Detected | `index.html` `.token-anatomy` + KPI card | Move into `.cache-accordion` expanded section |
| Forecast card | `index.html` `#forecast-card` | Remove; wire `projected_monthly_usd` into KPI row |
| Cache explainer | `index.html` `.cache-explainer` | Remove |
| `.dash-bottom` | `index.html` `.dash-bottom` | Remove |
| New: Spend Breakdown | `index.html` | New `.spend-breakdown` section (model + source bars) |
| New: Cache accordion | `index.html` | New `.cache-accordion` section |
| Chart logic | `app.js` | Replace `renderTokenChart` + `renderCacheTrendChart` with `renderCostChart(mode)` |
| KPI logic | `app.js` | Update `updateKPIs()` to use new card IDs; wire forecast card |
| CSS | `style.css` | Add `.spend-breakdown`, `.cache-accordion`, update `.kpi-grid` |

---

## Out of Scope

- Deep Dive page: no changes
- Insights / Analyze pages: no changes
- Backend / API: no changes
- Settings panel: no changes
