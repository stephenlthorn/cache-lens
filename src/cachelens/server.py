from __future__ import annotations

import asyncio
import json
import os
import threading
import webbrowser
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import AsyncGenerator

import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .aggregator import schedule_rollups
from .engine.analyzer import analyze
from .parser import parse_input
from .pricing import PricingTable
from .proxy import handle_proxy_request
from .recommender import generate_recommendations
from .store import UsageStore

# Default DB path used when no store is injected (production mode)
DEFAULT_DB_PATH: Path = Path.home() / ".cachelens" / "usage.db"

# Module-level set of active WebSocket connections
_ws_clients: set[WebSocket] = set()
_WS_MAX_CONNECTIONS = 10


async def _broadcast_event(event: dict) -> None:
    """Send event JSON to all connected WebSocket clients, pruning dead ones."""
    dead: set[WebSocket] = set()
    for ws in set(_ws_clients):
        try:
            await ws.send_text(json.dumps(event))
        except Exception:
            dead.add(ws)
    _ws_clients.difference_update(dead)


def _sync_broadcast_event(event: dict) -> None:
    """Thread-safe broadcast: schedule on the running event loop if available."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(_broadcast_event(event))
    except RuntimeError:
        pass


@asynccontextmanager
async def _lifespan(
    app: FastAPI,
    store: UsageStore,
    pricing: PricingTable,
) -> AsyncGenerator[None, None]:
    """FastAPI lifespan: initialise store/pricing, start rollup tasks."""
    app.state.store = store
    app.state.pricing = pricing
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

    app = FastAPI(title="CacheLens", lifespan=lifespan)
    # Store port on app for /api/status
    app.state.port = port

    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # ------------------------------------------------------------------
    # Existing routes
    # ------------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (static_dir / "index.html").read_text(encoding="utf-8")

    @app.post("/api/analyze")
    def api_analyze(payload: dict) -> JSONResponse:
        raw = payload.get("input", "")
        analysis_input = parse_input(raw)
        result = analyze(analysis_input)
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
        row = s.kpi_rolling(days)
        return JSONResponse(content={
            "days": days,
            "total_cost_usd": row["total_cost_usd"],
            "call_count": row["call_count"],
            "input_tokens": row["input_tokens"],
            "output_tokens": row["output_tokens"],
            "cache_read_tokens": row["cache_read_tokens"],
            "cache_write_tokens": row["cache_write_tokens"],
        })

    @app.get("/api/usage/daily")
    def api_daily(request: Request, days: int = 30) -> JSONResponse:
        s: UsageStore = request.app.state.store
        since = (date.today() - timedelta(days=days)).isoformat()
        rows = s.query_daily_agg_since(since)
        return JSONResponse(content={
            "days": days,
            "rows": rows,
        })

    @app.get("/api/usage/sources")
    def api_sources(request: Request) -> JSONResponse:
        s: UsageStore = request.app.state.store
        since = (date.today() - timedelta(days=30)).isoformat()
        rows = s.query_daily_agg_since(since)

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
    # WebSocket live feed
    # ------------------------------------------------------------------

    @app.websocket("/api/live")
    async def ws_live(websocket: WebSocket) -> None:
        if len(_ws_clients) >= _WS_MAX_CONNECTIONS:
            await websocket.close(code=1008)
            return
        await websocket.accept()
        _ws_clients.add(websocket)
        try:
            while True:
                await websocket.receive_text()  # keep-alive / ping handling
        except WebSocketDisconnect:
            _ws_clients.discard(websocket)
        except Exception:
            _ws_clients.discard(websocket)

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
        return await handle_proxy_request(
            method=request.method,
            path=full_path,
            headers=headers,
            body=body,
            store=request.app.state.store,
            pricing=request.app.state.pricing,
            on_call_recorded=_sync_broadcast_event,
        )

    return app


def run(port: int = 8420, open_browser: bool = True) -> None:
    app = create_app(port=port)

    if open_browser:
        def _open() -> None:
            webbrowser.open(f"http://127.0.0.1:{port}/")
        threading.Timer(0.5, _open).start()

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
