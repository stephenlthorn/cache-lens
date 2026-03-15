from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import threading
import webbrowser
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import AsyncGenerator

import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .aggregator import schedule_rollups
from .engine.analyzer import analyze
from .forecast import compute_forecast
from .parser import parse_input
from .pricing import PricingTable
from .proxy import handle_proxy_request
from .recommender import generate_recommendations
from .sessions import detect_sessions
from .store import UsageStore
from .webhooks import dispatch_webhook, should_fire_webhook

# Default DB path used when no store is injected (production mode)
DEFAULT_DB_PATH: Path = Path.home() / ".tokenlens" / "usage.db"

_WS_MAX_CONNECTIONS = 10


class AnalyzeRequest(BaseModel):
    input: str = Field(..., min_length=1, max_length=2_000_000)
    min_tokens: int = Field(default=50, ge=1, le=10_000)


def _compute_trend(data: list[dict]) -> str:
    """Simple linear regression slope on cache_hit_pct series."""
    if len(data) < 3:
        return "insufficient_data"
    n = len(data)
    xs = list(range(n))
    ys = [d["cache_hit_pct"] for d in data]
    x_mean = sum(xs) / n
    y_mean = sum(ys) / n
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
    denominator = sum((x - x_mean) ** 2 for x in xs)
    if denominator == 0:
        return "stable"
    slope = numerator / denominator
    if slope > 1:
        return "improving"
    if slope < -1:
        return "degrading"
    return "stable"


@asynccontextmanager
async def _lifespan(
    app: FastAPI,
    store: UsageStore,
    pricing: PricingTable,
) -> AsyncGenerator[None, None]:
    """FastAPI lifespan: initialise store/pricing, start rollup tasks."""
    app.state.store = store
    app.state.pricing = pricing
    app.state.ws_clients = set()
    overrides_str = store.get_setting("pricing.overrides")
    if overrides_str:
        pricing.apply_overrides_from_dict(json.loads(overrides_str))
    tasks = schedule_rollups(store)
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()


def create_app(
    store: UsageStore | None = None,
    pricing: PricingTable | None = None,
    port: int = 8420,
) -> FastAPI:
    """Create and return the FastAPI application.

    Parameters
    ----------
    store:
        Optional UsageStore instance.  If None, a production store at
        DEFAULT_DB_PATH is created inside the lifespan handler.
    pricing:
        Optional PricingTable instance.  If None, a default one is created.
    port:
        Port number reported in /api/status.
    """
    _store = store if store is not None else UsageStore(DEFAULT_DB_PATH)
    _pricing = pricing if pricing is not None else PricingTable()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        async with _lifespan(app, _store, _pricing):
            yield

    app = FastAPI(title="TokenLens", lifespan=lifespan)
    # Store port on app for /api/status
    app.state.port = port

    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # ------------------------------------------------------------------
    # Existing routes
    # ------------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> str:
        root_path = request.scope.get("root_path", "").rstrip("/")
        html = (static_dir / "index.html").read_text(encoding="utf-8")
        # Rewrite absolute static asset references to be prefix-aware
        html = html.replace('href="/static/', f'href="{root_path}/static/')
        html = html.replace('src="/static/', f'src="{root_path}/static/')
        # Inject BASE_PATH so JS fetch/WebSocket calls can prefix correctly
        injection = f'<script>window.BASE_PATH = {json.dumps(root_path)};</script>'
        html = html.replace("</head>", f"  {injection}\n</head>")
        return html

    @app.post("/api/analyze")
    def api_analyze(payload: AnalyzeRequest) -> JSONResponse:
        raw = payload.input.strip()
        if not raw:
            return JSONResponse(status_code=400, content={"error": "Input is empty"})
        analysis_input = parse_input(raw)
        result = analyze(analysis_input, min_tokens=payload.min_tokens)
        return JSONResponse(content=json.loads(result.model_dump_json()))

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    @app.get("/api/status")
    def api_status(request: Request) -> JSONResponse:
        s: UsageStore = request.app.state.store
        nightly_dt = s.last_rollup_time("nightly")
        yearly_dt = s.last_rollup_time("yearly")
        return JSONResponse(content={
            "daemon": "running",
            "pid": os.getpid(),
            "port": request.app.state.port,
            "db_size_bytes": s.db_size_bytes(),
            "raw_calls_today": s.raw_calls_last_24h(),
            "retention": {
                "raw_days": 1,
                "daily_days": 365,
                "aggregate": True,
            },
            "last_nightly_rollup": nightly_dt.isoformat() if nightly_dt else None,
            "last_yearly_rollup": yearly_dt.isoformat() if yearly_dt else None,
        })

    # ------------------------------------------------------------------
    # Usage API
    # ------------------------------------------------------------------

    _VALID_DAYS = {1, 7, 30, 365}

    @app.get("/api/usage/kpi")
    def api_kpi(request: Request, days: int = 30) -> JSONResponse:
        if days not in _VALID_DAYS:
            days = 30
        s: UsageStore = request.app.state.store
        p: PricingTable = request.app.state.pricing
        row = s.kpi_rolling(days)

        # Calculate savings from per-model data (rates vary by model)
        since = (date.today() - timedelta(days=days)).isoformat()
        today = date.today().isoformat()
        savings = 0.0
        for r in s.query_daily_agg_since(since):
            if r["date"] != today:
                savings += p.savings_usd(r["provider"], r["model"], r["cache_read_tokens"])
        for r in s.aggregate_calls_for_date(today):
            savings += p.savings_usd(r["provider"], r["model"], r["cache_read_tokens"])

        return JSONResponse(content={
            "days": days,
            "total_cost_usd": row["total_cost_usd"],
            "savings_usd": savings,
            "call_count": row["call_count"],
            "input_tokens": row["input_tokens"],
            "output_tokens": row["output_tokens"],
            "cache_read_tokens": row["cache_read_tokens"],
            "cache_write_tokens": row["cache_write_tokens"],
        })

    @app.get("/api/usage/daily")
    def api_daily(request: Request, days: int = 30) -> JSONResponse:
        if days not in _VALID_DAYS:
            days = 30
        s: UsageStore = request.app.state.store
        p: PricingTable = request.app.state.pricing
        since = (date.today() - timedelta(days=days)).isoformat()
        rows = s.query_daily_agg_since(since)

        # Supplement with today's live data (not yet in daily_agg until nightly rollup)
        today = date.today().isoformat()
        today_in_agg = {(r["provider"], r["model"], r["source"]) for r in rows if r["date"] == today}
        for r in s.aggregate_calls_for_date(today):
            if (r["provider"], r["model"], r["source"]) not in today_in_agg:
                rows.append({
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

        # Add per-row savings
        for r in rows:
            r["savings_usd"] = p.savings_usd(r["provider"], r["model"], r["cache_read_tokens"])

        rows.sort(key=lambda r: r["date"])
        return JSONResponse(content={
            "days": days,
            "rows": rows,
        })

    @app.get("/api/usage/recent")
    def api_recent(request: Request, limit: int = 50) -> JSONResponse:
        s: UsageStore = request.app.state.store
        raw = s.recent_calls(limit=min(limit, 200))
        calls = [
            {
                "timestamp": datetime.fromtimestamp(row["ts"], tz=timezone.utc).isoformat(),
                "provider": row["provider"],
                "model": row["model"],
                "source": row["source"],
                "source_tag": row["source_tag"],
                "input_tokens": row["input_tokens"],
                "output_tokens": row["output_tokens"],
                "cache_read_tokens": row["cache_read_tokens"],
                "cache_write_tokens": row["cache_write_tokens"],
                "cost_usd": row["cost_usd"],
                "endpoint": row["endpoint"],
                "user_agent": row["user_agent"] if row["user_agent"] else None,
            }
            for row in raw
        ]
        return JSONResponse(content={"calls": calls})

    @app.get("/api/usage/sources")
    def api_sources(request: Request) -> JSONResponse:
        s: UsageStore = request.app.state.store
        since = (date.today() - timedelta(days=30)).isoformat()
        rows = s.query_daily_agg_since(since)

        # Supplement with today's live data (not yet in daily_agg until nightly rollup)
        today = date.today().isoformat()
        today_in_agg = {(r["provider"], r["model"], r["source"]) for r in rows if r["date"] == today}
        for r in s.aggregate_calls_for_date(today):
            if (r["provider"], r["model"], r["source"]) not in today_in_agg:
                rows.append({
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

        # Aggregate by source
        by_source: dict[str, dict] = {}
        for row in rows:
            src = row["source"]
            if src not in by_source:
                by_source[src] = {
                    "source": src,
                    "cost_usd": 0.0,
                    "call_count": 0,
                    "providers": set(),
                }
            by_source[src]["cost_usd"] += row["cost_usd"] or 0.0
            by_source[src]["call_count"] += row["call_count"] or 0
            by_source[src]["providers"].add(row["provider"])

        sources = [
            {
                "source": v["source"],
                "cost_usd": v["cost_usd"],
                "call_count": v["call_count"],
                "providers": sorted(v["providers"]),
            }
            for v in by_source.values()
        ]
        sources.sort(key=lambda x: x["cost_usd"], reverse=True)

        return JSONResponse(content={"sources": sources})

    @app.get("/api/usage/recommendations")
    def api_recommendations(request: Request) -> JSONResponse:
        s: UsageStore = request.app.state.store
        recs = generate_recommendations(s)
        return JSONResponse(content={
            "recommendations": [
                {
                    "id": r.id,
                    "type": r.type,
                    "title": r.title,
                    "description": r.description,
                    "estimated_impact": r.estimated_impact,
                    "deep_dive_link": r.deep_dive_link,
                    "metrics": r.metrics,
                }
                for r in recs
            ]
        })

    # ------------------------------------------------------------------
    # CSV Export (Phase 2)
    # ------------------------------------------------------------------

    @app.get("/api/export/csv")
    def api_export_csv(request: Request, days: int = 30) -> Response:
        if days not in _VALID_DAYS:
            days = 30
        s: UsageStore = request.app.state.store
        p: PricingTable = request.app.state.pricing
        since = (date.today() - timedelta(days=days)).isoformat()
        rows = s.query_daily_agg_since(since)

        today = date.today().isoformat()
        today_in_agg = {(r["provider"], r["model"], r["source"]) for r in rows if r["date"] == today}
        for r in s.aggregate_calls_for_date(today):
            if (r["provider"], r["model"], r["source"]) not in today_in_agg:
                rows.append({
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

        for r in rows:
            r["savings_usd"] = p.savings_usd(r["provider"], r["model"], r["cache_read_tokens"])

        rows.sort(key=lambda r: r["date"])

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            "date", "provider", "model", "source", "call_count",
            "input_tokens", "output_tokens", "cache_read_tokens",
            "cache_write_tokens", "cost_usd", "savings_usd",
        ])
        for r in rows:
            writer.writerow([
                r.get("date", ""), r.get("provider", ""), r.get("model", ""),
                r.get("source", ""), r.get("call_count", 0),
                r.get("input_tokens", 0), r.get("output_tokens", 0),
                r.get("cache_read_tokens", 0), r.get("cache_write_tokens", 0),
                f"{r.get('cost_usd', 0.0):.6f}", f"{r.get('savings_usd', 0.0):.6f}",
            ])

        filename = f"tokenlens-export-{date.today().isoformat()}.csv"
        return Response(
            content=buf.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # ------------------------------------------------------------------
    # Cache Hit Rate Trend (Phase 3)
    # ------------------------------------------------------------------

    @app.get("/api/usage/cache-trend")
    def api_cache_trend(request: Request, days: int = 30) -> JSONResponse:
        if days not in _VALID_DAYS:
            days = 30
        s: UsageStore = request.app.state.store
        data_points = s.daily_cache_hit_trend(days)

        result = []
        for dp in data_points:
            total = (dp["input_tokens"] or 0) + (dp["cache_read_tokens"] or 0)
            pct = (dp["cache_read_tokens"] or 0) / total * 100 if total > 0 else 0
            result.append({
                "date": dp["date"],
                "cache_hit_pct": round(pct, 1),
                "total_input_tokens": dp["input_tokens"] or 0,
                "cache_read_tokens": dp["cache_read_tokens"] or 0,
            })

        trend = _compute_trend(result)
        return JSONResponse(content={
            "days": days,
            "trend": trend,
            "data": result,
        })

    # ------------------------------------------------------------------
    # Model Comparison (Phase 4)
    # ------------------------------------------------------------------

    @app.get("/api/usage/compare")
    def api_model_compare(
        request: Request,
        from_model: str = "",
        to_model: str = "",
        days: int = 30,
    ) -> JSONResponse:
        if not from_model or not to_model:
            return JSONResponse(
                status_code=400,
                content={"error": "from_model and to_model are required"},
            )
        if from_model == to_model:
            return JSONResponse(
                status_code=400,
                content={"error": "from_model and to_model must differ"},
            )

        s: UsageStore = request.app.state.store
        p: PricingTable = request.app.state.pricing
        since = (date.today() - timedelta(days=days)).isoformat()
        rows = s.query_daily_agg_since(since)

        today = date.today().isoformat()
        for r in s.aggregate_calls_for_date(today):
            rows.append({
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

        matching = [r for r in rows if r["model"] == from_model]
        if not matching:
            return JSONResponse(content={
                "from_model": from_model,
                "to_model": to_model,
                "actual_cost_usd": 0,
                "hypothetical_cost_usd": 0,
                "savings_usd": 0,
                "savings_pct": 0,
                "call_count": 0,
            })

        total_cost = sum(r["cost_usd"] or 0 for r in matching)
        total_calls = sum(r["call_count"] or 0 for r in matching)
        total_input = sum(r["input_tokens"] or 0 for r in matching)
        total_output = sum(r["output_tokens"] or 0 for r in matching)
        total_cache_read = sum(r["cache_read_tokens"] or 0 for r in matching)
        total_cache_write = sum(r["cache_write_tokens"] or 0 for r in matching)

        provider = matching[0]["provider"]
        hypothetical = p.cost_usd(
            provider, to_model,
            total_input, total_output, total_cache_read, total_cache_write,
        )

        savings = total_cost - hypothetical
        savings_pct = (savings / total_cost * 100) if total_cost > 0 else 0

        return JSONResponse(content={
            "from_model": from_model,
            "to_model": to_model,
            "actual_cost_usd": round(total_cost, 4),
            "hypothetical_cost_usd": round(hypothetical, 4),
            "savings_usd": round(savings, 4),
            "savings_pct": round(savings_pct, 1),
            "call_count": total_calls,
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
        })

    # --- Token Cost Breakdown ---

    @app.get("/api/usage/token-breakdown")
    def api_token_breakdown(request: Request, days: int = 30) -> JSONResponse:
        if days not in _VALID_DAYS:
            days = 30
        s: UsageStore = request.app.state.store
        p: PricingTable = request.app.state.pricing
        since = (date.today() - timedelta(days=days)).isoformat()
        rows = s.query_daily_agg_since(since)

        today = date.today().isoformat()
        today_in_agg = {(r["provider"], r["model"], r["source"]) for r in rows if r["date"] == today}
        for r in s.aggregate_calls_for_date(today):
            if (r["provider"], r["model"], r["source"]) not in today_in_agg:
                rows.append({
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

        by_date: dict[str, dict[str, float]] = {}
        for r in rows:
            d = r["date"]
            if d not in by_date:
                by_date[d] = {
                    "input_cost": 0.0,
                    "output_cost": 0.0,
                    "cache_read_cost": 0.0,
                    "cache_write_cost": 0.0,
                }
            rates = p._row(r["provider"], r["model"])
            by_date[d]["input_cost"] += (r["input_tokens"] or 0) * rates["input"] / 1_000_000
            by_date[d]["output_cost"] += (r["output_tokens"] or 0) * rates["output"] / 1_000_000
            by_date[d]["cache_read_cost"] += (r["cache_read_tokens"] or 0) * rates["cache_read"] / 1_000_000
            by_date[d]["cache_write_cost"] += (r["cache_write_tokens"] or 0) * rates["cache_write"] / 1_000_000

        data = []
        for d in sorted(by_date.keys()):
            entry = by_date[d]
            total = entry["input_cost"] + entry["output_cost"] + entry["cache_read_cost"] + entry["cache_write_cost"]
            data.append({
                "date": d,
                "input_cost": round(entry["input_cost"], 6),
                "output_cost": round(entry["output_cost"], 6),
                "cache_read_cost": round(entry["cache_read_cost"], 6),
                "cache_write_cost": round(entry["cache_write_cost"], 6),
                "total_cost": round(total, 6),
            })

        return JSONResponse(content={"days": days, "data": data})

    # ------------------------------------------------------------------
    # Sessions (Phase 5)
    # ------------------------------------------------------------------

    @app.get("/api/usage/sessions")
    def api_sessions(
        request: Request,
        days: int = 1,
        source: str = "",
    ) -> JSONResponse:
        s: UsageStore = request.app.state.store
        calls = s.raw_calls_for_period(days, source=source if source else None)
        sessions = detect_sessions(calls)
        return JSONResponse(content={
            "sessions": sessions,
            "note": "Sessions are detected from raw calls (24h retention)."
                    if days > 1
                    else None,
        })

    # ------------------------------------------------------------------
    # Settings: Alerts (Phase 6)
    # ------------------------------------------------------------------

    @app.get("/api/settings/alerts")
    def api_get_alerts(request: Request) -> JSONResponse:
        s: UsageStore = request.app.state.store
        threshold = s.get_setting("alerts.daily_cost_threshold")
        enabled = s.get_setting("alerts.enabled")
        return JSONResponse(content={
            "daily_cost_threshold": float(threshold) if threshold else None,
            "alerts_enabled": enabled == "true" if enabled else False,
        })

    @app.put("/api/settings/alerts")
    async def api_set_alerts(request: Request) -> JSONResponse:
        s: UsageStore = request.app.state.store
        body = await request.json()
        if "daily_cost_threshold" in body:
            val = body["daily_cost_threshold"]
            if val is not None:
                s.set_setting("alerts.daily_cost_threshold", str(float(val)))
            else:
                s.delete_setting("alerts.daily_cost_threshold")
        if "alerts_enabled" in body:
            s.set_setting("alerts.enabled", "true" if body["alerts_enabled"] else "false")
        return JSONResponse(content={"status": "ok"})

    # ------------------------------------------------------------------
    # Settings: Budget (Phase 7)
    # ------------------------------------------------------------------

    @app.get("/api/settings/budget")
    def api_get_budget(request: Request) -> JSONResponse:
        s: UsageStore = request.app.state.store
        daily = s.get_setting("budget.daily_limit_usd")
        monthly = s.get_setting("budget.monthly_limit_usd")
        enabled = s.get_setting("budget.enabled")
        return JSONResponse(content={
            "daily_limit_usd": float(daily) if daily else None,
            "monthly_limit_usd": float(monthly) if monthly else None,
            "enabled": enabled == "true" if enabled else False,
        })

    @app.put("/api/settings/budget")
    async def api_set_budget(request: Request) -> JSONResponse:
        s: UsageStore = request.app.state.store
        body = await request.json()
        if "daily_limit_usd" in body:
            val = body["daily_limit_usd"]
            if val is not None:
                s.set_setting("budget.daily_limit_usd", str(float(val)))
            else:
                s.delete_setting("budget.daily_limit_usd")
        if "monthly_limit_usd" in body:
            val = body["monthly_limit_usd"]
            if val is not None:
                s.set_setting("budget.monthly_limit_usd", str(float(val)))
            else:
                s.delete_setting("budget.monthly_limit_usd")
        if "enabled" in body:
            s.set_setting("budget.enabled", "true" if body["enabled"] else "false")
        return JSONResponse(content={"status": "ok"})

    # ------------------------------------------------------------------
    # Settings: Quotas (per-source + per-model limits)
    # ------------------------------------------------------------------

    @app.get("/api/config/quotas")
    def api_get_quotas(request: Request) -> JSONResponse:
        s: UsageStore = request.app.state.store
        config_str = s.get_setting("quotas.config")
        if config_str:
            try:
                config = json.loads(config_str)
            except (json.JSONDecodeError, ValueError):
                config = {}
        else:
            config = {}
        return JSONResponse(content={
            "source_limits": config.get("source_limits", {}),
            "model_limits": config.get("model_limits", {}),
            "kill_switches": config.get("kill_switches", []),
        })

    @app.put("/api/config/quotas")
    async def api_set_quotas(request: Request) -> JSONResponse:
        s: UsageStore = request.app.state.store
        body = await request.json()
        config = {
            "source_limits": body.get("source_limits", {}),
            "model_limits": body.get("model_limits", {}),
            "kill_switches": body.get("kill_switches", []),
        }
        s.set_setting("quotas.config", json.dumps(config))
        return JSONResponse(content={"status": "ok"})

    # ------------------------------------------------------------------
    # Settings: Routing (model aliases, fallback chains, weighted balancing)
    # ------------------------------------------------------------------

    @app.get("/api/config/routing")
    def api_get_routing(request: Request) -> JSONResponse:
        s: UsageStore = request.app.state.store
        config_str = s.get_setting("routing.config")
        if config_str:
            try:
                config = json.loads(config_str)
            except (json.JSONDecodeError, ValueError):
                config = {}
        else:
            config = {}
        return JSONResponse(content={
            "aliases": config.get("aliases", {}),
            "fallback_chains": config.get("fallback_chains", {}),
            "weights": config.get("weights", {}),
        })

    @app.put("/api/config/routing")
    async def api_set_routing(request: Request) -> JSONResponse:
        s: UsageStore = request.app.state.store
        body = await request.json()
        config = {
            "aliases": body.get("aliases", {}),
            "fallback_chains": body.get("fallback_chains", {}),
            "weights": body.get("weights", {}),
        }
        s.set_setting("routing.config", json.dumps(config))
        return JSONResponse(content={"status": "ok"})

    # --- Custom Pricing ---

    @app.get("/api/settings/pricing")
    def api_get_pricing(request: Request) -> JSONResponse:
        p: PricingTable = request.app.state.pricing
        return JSONResponse(content={"models": p.get_all_prices()})

    @app.put("/api/settings/pricing")
    async def api_set_pricing(request: Request) -> JSONResponse:
        s: UsageStore = request.app.state.store
        p: PricingTable = request.app.state.pricing
        body = await request.json()
        overrides = body.get("overrides", {})
        if overrides:
            s.set_setting("pricing.overrides", json.dumps(overrides))
            p.apply_overrides_from_dict(overrides)
        return JSONResponse(content={"status": "ok"})

    @app.get("/api/usage/budget-status")
    def api_budget_status(request: Request) -> JSONResponse:
        s: UsageStore = request.app.state.store
        daily_limit = s.get_setting("budget.daily_limit_usd")
        monthly_limit = s.get_setting("budget.monthly_limit_usd")
        enabled = s.get_setting("budget.enabled") == "true"
        return JSONResponse(content={
            "enabled": enabled,
            "daily_spend_usd": round(s.daily_spend_usd(), 4),
            "monthly_spend_usd": round(s.monthly_spend_usd(), 4),
            "daily_limit_usd": float(daily_limit) if daily_limit else None,
            "monthly_limit_usd": float(monthly_limit) if monthly_limit else None,
        })

    # --- Spend Forecasting ---

    @app.get("/api/usage/forecast")
    def api_forecast(request: Request) -> JSONResponse:
        s: UsageStore = request.app.state.store
        series = s.daily_cost_series(90)
        forecast = compute_forecast(series)
        return JSONResponse(content=forecast)

    # --- Cost Allocation Tags ---

    @app.get("/api/usage/by-tag")
    def api_usage_by_tag(request: Request, days: int = 30) -> JSONResponse:
        if days not in _VALID_DAYS:
            days = 30
        s: UsageStore = request.app.state.store
        return JSONResponse(content=s.query_by_tag(days))

    # --- Provider Health (Latency/Status Tracking) ---

    @app.get("/api/usage/provider-health")
    def api_provider_health(request: Request, days: int = 1) -> JSONResponse:
        s: UsageStore = request.app.state.store
        return JSONResponse(content=s.provider_health(days=days))

    # --- Rate Limit Tracking ---

    @app.get("/api/usage/rate-limits")
    def api_rate_limits(request: Request, days: int = 1) -> JSONResponse:
        s: UsageStore = request.app.state.store
        return JSONResponse(content={
            "summary": s.rate_limit_summary(days=days),
            "timeline": s.rate_limit_events(days=days),
        })

    # --- Waste Analysis ---

    @app.get("/api/usage/waste-summary")
    def api_waste_summary(request: Request, days: int = 30) -> JSONResponse:
        s: UsageStore = request.app.state.store
        return JSONResponse(content=s.waste_summary(days=days))

    @app.get("/api/usage/waste/{call_id}")
    def api_waste_detail(call_id: int, request: Request) -> JSONResponse:
        s: UsageStore = request.app.state.store
        return JSONResponse(content=s.get_waste_for_call(call_id))

    @app.get("/api/usage/output-efficiency")
    def api_output_efficiency(request: Request, days: int = 30) -> JSONResponse:
        s: UsageStore = request.app.state.store
        return JSONResponse(content=s.output_efficiency(days=days))

    @app.get("/api/usage/conversation-efficiency")
    def api_conversation_efficiency(request: Request, days: int = 30) -> JSONResponse:
        s: UsageStore = request.app.state.store
        return JSONResponse(content=s.conversation_efficiency(days=days))

    @app.get("/api/usage/token-heatmap")
    def api_token_heatmap(request: Request, days: int = 30) -> JSONResponse:
        s: UsageStore = request.app.state.store
        return JSONResponse(content=s.token_heatmap_summary(days=days))

    @app.get("/api/usage/anomalies")
    def api_anomalies(request: Request, days: int = 30) -> JSONResponse:
        from tokenlens.anomaly import detect_anomalies
        s: UsageStore = request.app.state.store
        return JSONResponse(content=detect_anomalies(store=s, days=days))

    @app.get("/api/usage/right-sizing")
    def api_right_sizing(request: Request, days: int = 30) -> JSONResponse:
        from tokenlens.right_sizing import analyze_right_sizing
        s: UsageStore = request.app.state.store
        p: PricingTable = request.app.state.pricing
        return JSONResponse(content=analyze_right_sizing(store=s, pricing=p, days=days))

    # --- Weekly Digest ---

    @app.get("/api/usage/digest")
    def api_digest(request: Request, days: int = 7) -> JSONResponse:
        from tokenlens.digest import generate_digest
        s: UsageStore = request.app.state.store
        p: PricingTable = request.app.state.pricing
        return JSONResponse(content=generate_digest(store=s, pricing=p, days=days))

    # --- Webhook Notifications ---

    @app.get("/api/settings/webhooks")
    def api_get_webhooks(request: Request) -> JSONResponse:
        s: UsageStore = request.app.state.store
        url = s.get_setting("webhook.url")
        events = s.get_setting("webhook.events")
        enabled = s.get_setting("webhook.enabled")
        return JSONResponse(content={
            "url": url,
            "events": events,
            "enabled": enabled == "true" if enabled else False,
        })

    @app.put("/api/settings/webhooks")
    async def api_set_webhooks(request: Request) -> JSONResponse:
        s: UsageStore = request.app.state.store
        body = await request.json()
        if "url" in body:
            val = body["url"]
            if val is not None:
                s.set_setting("webhook.url", str(val))
            else:
                s.delete_setting("webhook.url")
        if "events" in body:
            val = body["events"]
            if val is not None:
                s.set_setting("webhook.events", str(val))
            else:
                s.delete_setting("webhook.events")
        if "enabled" in body:
            s.set_setting("webhook.enabled", "true" if body["enabled"] else "false")
        return JSONResponse(content={"status": "ok"})

    # --- Request/Response Logging ---

    @app.get("/api/logs")
    def api_logs(request: Request, limit: int = 20) -> JSONResponse:
        s: UsageStore = request.app.state.store
        logs = s.get_request_logs(limit=min(limit, 200))
        result = [
            {
                "id": log["id"],
                "call_id": log["call_id"],
                "ts": log["ts"],
                "provider": log.get("provider"),
                "model": log.get("model"),
                "source": log.get("source"),
                "endpoint": log.get("endpoint"),
            }
            for log in logs
        ]
        return JSONResponse(content={"logs": result})

    @app.get("/api/logs/{log_id}")
    def api_log_detail(log_id: int, request: Request) -> JSONResponse:
        s: UsageStore = request.app.state.store
        log = s.get_request_log(log_id)
        if log is None:
            return JSONResponse(status_code=404, content={"error": "log not found"})
        return JSONResponse(content={
            "id": log["id"],
            "call_id": log["call_id"],
            "ts": log["ts"],
            "provider": log.get("provider"),
            "model": log.get("model"),
            "source": log.get("source"),
            "endpoint": log.get("endpoint"),
            "request_body": log.get("request_body"),
            "response_body": log.get("response_body"),
        })

    # --- Prometheus Metrics ---

    @app.get("/metrics")
    def api_metrics(request: Request) -> Response:
        from tokenlens.metrics import render_prometheus_metrics
        s: UsageStore = request.app.state.store
        return Response(
            content=render_prometheus_metrics(s),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    # ------------------------------------------------------------------
    # WebSocket live feed
    # ------------------------------------------------------------------

    @app.websocket("/api/live")
    async def ws_live(websocket: WebSocket) -> None:
        ws_clients: set[WebSocket] = websocket.app.state.ws_clients
        if len(ws_clients) >= _WS_MAX_CONNECTIONS:
            await websocket.close(code=1008)
            return
        await websocket.accept()
        ws_clients.add(websocket)
        try:
            while True:
                await websocket.receive_text()  # keep-alive / ping handling
        except WebSocketDisconnect:
            ws_clients.discard(websocket)
        except Exception:
            ws_clients.discard(websocket)

    # ------------------------------------------------------------------
    # Proxy routes
    # ------------------------------------------------------------------

    @app.api_route(
        "/proxy/{provider}/{path:path}",
        methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
    )
    async def proxy_route(
        provider: str, path: str, request: Request
    ) -> Response:
        full_path = f"/proxy/{provider}/{path}"
        body = await request.body()
        headers = dict(request.headers)
        ws_clients: set[WebSocket] = request.app.state.ws_clients

        async def on_call_recorded(event: dict) -> None:
            dead: set[WebSocket] = set()
            for ws in list(ws_clients):
                try:
                    await ws.send_json(event)
                except Exception:
                    dead.add(ws)
            ws_clients.difference_update(dead)

            # Cost alert check (Phase 6)
            store = request.app.state.store
            threshold_str = store.get_setting("alerts.daily_cost_threshold")
            alerts_enabled = store.get_setting("alerts.enabled") == "true"
            cost_alert_fired = False
            if alerts_enabled and threshold_str:
                threshold = float(threshold_str)
                daily_cost = store.daily_spend_usd()
                if daily_cost >= threshold:
                    alert_key = f"alert_sent_{date.today().isoformat()}_{threshold}"
                    if not hasattr(request.app.state, "_alert_cooldown"):
                        request.app.state._alert_cooldown = set()
                    if alert_key not in request.app.state._alert_cooldown:
                        request.app.state._alert_cooldown.add(alert_key)
                        cost_alert_fired = True
                        alert_event = {
                            "type": "cost_alert",
                            "daily_cost_usd": round(daily_cost, 4),
                            "threshold_usd": threshold,
                            "message": f"Daily spend ${daily_cost:.2f} exceeded threshold ${threshold:.2f}",
                        }
                        for ws in list(ws_clients):
                            try:
                                await ws.send_json(alert_event)
                            except Exception:
                                pass

            # Webhook dispatch
            webhook_url = store.get_setting("webhook.url")
            webhook_enabled = store.get_setting("webhook.enabled")
            webhook_events = store.get_setting("webhook.events") or ""
            if webhook_url and webhook_enabled == "true":
                if should_fire_webhook("call_recorded", webhook_events):
                    asyncio.create_task(dispatch_webhook(
                        webhook_url, {"type": "call_recorded", **event},
                    ))
                if cost_alert_fired and should_fire_webhook("cost_alert", webhook_events):
                    asyncio.create_task(dispatch_webhook(
                        webhook_url, alert_event,
                    ))

        return await handle_proxy_request(
            method=request.method,
            path=full_path,
            headers=headers,
            body=body,
            store=request.app.state.store,
            pricing=request.app.state.pricing,
            on_call_recorded=on_call_recorded,
        )

    return app


def run(port: int = 8420, open_browser: bool = True, base_path: str = "") -> None:
    app = create_app(port=port)

    if open_browser:
        def _open() -> None:
            webbrowser.open(f"http://127.0.0.1:{port}/")
        threading.Timer(0.5, _open).start()

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info", root_path=base_path)
