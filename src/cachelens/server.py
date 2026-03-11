from __future__ import annotations

import json
import threading
import webbrowser
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .parser import parse_input
from .engine.analyzer import analyze


def create_app() -> FastAPI:
    app = FastAPI(title="CacheLens")

    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (static_dir / "index.html").read_text(encoding="utf-8")

    @app.post("/api/analyze")
    def api_analyze(payload: dict) -> JSONResponse:
        raw = payload.get("input", "")
        analysis_input = parse_input(raw)
        result = analyze(analysis_input)
        return JSONResponse(content=json.loads(result.model_dump_json()))

    return app


def run(port: int = 8420, open_browser: bool = True) -> None:
    app = create_app()

    if open_browser:
        def _open() -> None:
            webbrowser.open(f"http://127.0.0.1:{port}/")
        threading.Timer(0.5, _open).start()

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
