"""Integration tests for TokenLens end-to-end flows.

Tests exercise the full stack through the FastAPI server using a real
(in-memory/tmp) SQLite database. No real HTTP calls are made to upstream
AI provider APIs.
"""
from __future__ import annotations

import time
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tokenlens.pricing import PricingTable
from tokenlens.server import create_app
from tokenlens.store import UsageStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> UsageStore:
    return UsageStore(tmp_path / "test.db")


@pytest.fixture
def pricing() -> PricingTable:
    return PricingTable()


@pytest.fixture
def client(store: UsageStore, pricing: PricingTable):  # type: ignore[return]
    app = create_app(store=store, pricing=pricing)
    with TestClient(app) as c:
        yield c


def _yesterday() -> str:
    return (date.today() - timedelta(days=1)).isoformat()


# ---------------------------------------------------------------------------
# /api/status
# ---------------------------------------------------------------------------


def test_status_endpoint_returns_all_required_fields(client: TestClient) -> None:
    """GET /api/status must return all fields from the spec."""
    r = client.get("/api/status")
    assert r.status_code == 200
    data = r.json()
    required_keys = [
        "daemon", "pid", "port", "db_size_bytes", "raw_calls_today",
        "retention", "last_nightly_rollup", "last_yearly_rollup",
    ]
    for key in required_keys:
        assert key in data, f"Missing key: {key}"
    assert data["daemon"] == "running"
    assert data["raw_calls_today"] == 0


# ---------------------------------------------------------------------------
# /api/usage/kpi
# ---------------------------------------------------------------------------


def test_kpi_endpoint_with_no_data(client: TestClient) -> None:
    """GET /api/usage/kpi returns zeros when the store is empty."""
    r = client.get("/api/usage/kpi?days=30")
    assert r.status_code == 200
    data = r.json()
    assert data["call_count"] == 0
    assert data["total_cost_usd"] == 0.0


def test_kpi_endpoint_with_data(client: TestClient, store: UsageStore) -> None:
    """KPI reflects calls inserted directly into the store."""
    store.insert_call(
        ts=int(time.time()),
        provider="anthropic",
        model="claude-sonnet-4-6",
        source="test",
        source_tag=None,
        input_tokens=5000,
        output_tokens=2000,
        cache_read_tokens=500,
        cache_write_tokens=0,
        cost_usd=0.50,
        endpoint="/v1/messages",
        request_hash="abc123integration",
    )
    r = client.get("/api/usage/kpi?days=7")
    assert r.status_code == 200
    data = r.json()
    assert data["call_count"] == 1
    assert abs(data["total_cost_usd"] - 0.50) < 0.001


# ---------------------------------------------------------------------------
# /api/usage/daily
# ---------------------------------------------------------------------------


def test_daily_endpoint_with_data(client: TestClient, store: UsageStore) -> None:
    """GET /api/usage/daily returns rows that were inserted via daily_agg."""
    store.upsert_daily_agg(
        date=_yesterday(),
        provider="openai",
        model="gpt-4o",
        source="myapp",
        call_count=5,
        input_tokens=1000,
        output_tokens=500,
        cache_read_tokens=0,
        cache_write_tokens=0,
        cost_usd=0.025,
    )
    r = client.get("/api/usage/daily?days=7")
    assert r.status_code == 200
    data = r.json()
    assert len(data["rows"]) == 1
    assert data["rows"][0]["model"] == "gpt-4o"


# ---------------------------------------------------------------------------
# /api/usage/sources
# ---------------------------------------------------------------------------


def test_sources_endpoint_aggregates_by_source(
    client: TestClient, store: UsageStore
) -> None:
    """GET /api/usage/sources aggregates call_count and cost_usd per source."""
    yesterday = _yesterday()
    store.upsert_daily_agg(
        date=yesterday,
        provider="anthropic",
        model="claude-sonnet-4-6",
        source="app-a",
        call_count=3,
        input_tokens=300,
        output_tokens=100,
        cache_read_tokens=0,
        cache_write_tokens=0,
        cost_usd=0.10,
    )
    store.upsert_daily_agg(
        date=yesterday,
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        source="app-a",
        call_count=2,
        input_tokens=200,
        output_tokens=50,
        cache_read_tokens=0,
        cache_write_tokens=0,
        cost_usd=0.02,
    )
    r = client.get("/api/usage/sources")
    assert r.status_code == 200
    data = r.json()
    sources = {s["source"]: s for s in data["sources"]}
    assert "app-a" in sources
    assert sources["app-a"]["call_count"] == 5
    assert abs(sources["app-a"]["cost_usd"] - 0.12) < 0.001


# ---------------------------------------------------------------------------
# /api/usage/recommendations
# ---------------------------------------------------------------------------


def test_recommendations_endpoint_with_qualifying_data(
    client: TestClient, store: UsageStore
) -> None:
    """200+ Anthropic calls with 0 cache reads triggers low_cache_hit_rate."""
    store.upsert_daily_agg(
        date=_yesterday(),
        provider="anthropic",
        model="claude-sonnet-4-6",
        source="noCache",
        call_count=200,
        input_tokens=100000,
        output_tokens=50000,
        cache_read_tokens=0,
        cache_write_tokens=0,
        cost_usd=2.0,
    )
    r = client.get("/api/usage/recommendations")
    assert r.status_code == 200
    data = r.json()
    types = [rec["type"] for rec in data["recommendations"]]
    assert "low_cache_hit_rate" in types


def test_recommendations_endpoint_empty_with_no_data(client: TestClient) -> None:
    """GET /api/usage/recommendations returns empty list when no data exists."""
    r = client.get("/api/usage/recommendations")
    assert r.status_code == 200
    data = r.json()
    assert data["recommendations"] == []


# ---------------------------------------------------------------------------
# /api/analyze (regression)
# ---------------------------------------------------------------------------


def test_analyze_endpoint_still_works(client: TestClient) -> None:
    """The existing /api/analyze endpoint must still function correctly."""
    payload = {
        "input": '{"calls": [{"messages": [{"role": "user", "content": "Hello"}]}]}'
    }
    r = client.post("/api/analyze", json=payload)
    assert r.status_code == 200
    data = r.json()
    assert "cacheability_score" in data


# ---------------------------------------------------------------------------
# Proxy routes
# ---------------------------------------------------------------------------


def test_proxy_route_unknown_provider_returns_404(client: TestClient) -> None:
    """GET /proxy/unknown/v1/test returns 404 for an unrecognised provider."""
    r = client.get("/proxy/unknown/v1/test")
    assert r.status_code == 404


def test_proxy_route_registered_for_anthropic(
    store: UsageStore, pricing: PricingTable, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Proxy route for anthropic is registered and attempts the upstream call."""

    class _FakeResponse:
        status_code = 200
        content = b'{"model":"claude-sonnet-4-6","usage":{"input_tokens":0,"output_tokens":0}}'
        headers = {"content-type": "application/json"}
        is_success = True

    class _FakeAsyncClient:
        def __init__(self, *a: object, **kw: object) -> None:
            pass

        async def __aenter__(self) -> "_FakeAsyncClient":
            return self

        async def __aexit__(self, *a: object) -> None:
            pass

        async def request(self, *a: object, **kw: object) -> _FakeResponse:
            return _FakeResponse()

    monkeypatch.setattr("tokenlens.proxy.httpx.AsyncClient", _FakeAsyncClient)

    app = create_app(store=store, pricing=pricing)
    with TestClient(app) as tc:
        r = tc.post(
            "/proxy/anthropic/v1/messages",
            json={"model": "claude-sonnet-4-6", "messages": []},
            headers={"x-api-key": "test-key"},
        )
        # Route is registered — must NOT be a routing 404
        assert r.status_code != 404


def test_proxy_route_registered_for_openai(
    store: UsageStore, pricing: PricingTable, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Proxy route for openai is registered and attempts the upstream call."""

    class _FakeResponse:
        status_code = 200
        content = b'{"model":"gpt-4o","usage":{"prompt_tokens":0,"completion_tokens":0}}'
        headers = {"content-type": "application/json"}
        is_success = True

    class _FakeAsyncClient:
        def __init__(self, *a: object, **kw: object) -> None:
            pass

        async def __aenter__(self) -> "_FakeAsyncClient":
            return self

        async def __aexit__(self, *a: object) -> None:
            pass

        async def request(self, *a: object, **kw: object) -> _FakeResponse:
            return _FakeResponse()

    monkeypatch.setattr("tokenlens.proxy.httpx.AsyncClient", _FakeAsyncClient)

    app = create_app(store=store, pricing=pricing)
    with TestClient(app) as tc:
        r = tc.post(
            "/proxy/openai/v1/chat/completions",
            json={"model": "gpt-4o", "messages": []},
            headers={"authorization": "Bearer test-key"},
        )
        assert r.status_code != 404


def test_proxy_route_registered_for_google(
    store: UsageStore, pricing: PricingTable, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Proxy route for google is registered and attempts the upstream call."""

    class _FakeResponse:
        status_code = 200
        content = b'{"modelVersion":"gemini-pro","usageMetadata":{}}'
        headers = {"content-type": "application/json"}
        is_success = True

    class _FakeAsyncClient:
        def __init__(self, *a: object, **kw: object) -> None:
            pass

        async def __aenter__(self) -> "_FakeAsyncClient":
            return self

        async def __aexit__(self, *a: object) -> None:
            pass

        async def request(self, *a: object, **kw: object) -> _FakeResponse:
            return _FakeResponse()

    monkeypatch.setattr("tokenlens.proxy.httpx.AsyncClient", _FakeAsyncClient)

    app = create_app(store=store, pricing=pricing)
    with TestClient(app) as tc:
        r = tc.post(
            "/proxy/google/v1/models/gemini-pro:generateContent",
            json={"contents": []},
            headers={"x-goog-api-key": "test-key"},
        )
        assert r.status_code != 404


# ---------------------------------------------------------------------------
# Regression: proxy must strip content-encoding (zlib double-decompression)
# ---------------------------------------------------------------------------


def test_proxy_non_streaming_strips_content_encoding(
    store: UsageStore, pricing: PricingTable, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-streaming proxy response must NOT contain content-encoding.

    httpx decompresses gzip/deflate/br automatically.  If the proxy forwards
    the original content-encoding header, the downstream client attempts
    double-decompression and crashes with a zlib error.
    """
    body = b'{"model":"claude-sonnet-4-6","usage":{"input_tokens":100,"output_tokens":50}}'

    class _FakeResponse:
        status_code = 200
        content = body
        headers = {
            "content-type": "application/json",
            "content-encoding": "gzip",
            "content-length": "999",
            "x-request-id": "zlib-regression",
        }
        is_success = True

    class _FakeAsyncClient:
        def __init__(self, *a: object, **kw: object) -> None:
            pass

        async def __aenter__(self) -> "_FakeAsyncClient":
            return self

        async def __aexit__(self, *a: object) -> None:
            pass

        async def request(self, *a: object, **kw: object) -> _FakeResponse:
            return _FakeResponse()

    monkeypatch.setattr("tokenlens.proxy.httpx.AsyncClient", _FakeAsyncClient)

    app = create_app(store=store, pricing=pricing)
    with TestClient(app) as tc:
        r = tc.post(
            "/proxy/anthropic/v1/messages",
            json={"model": "claude-sonnet-4-6", "messages": []},
            headers={"x-api-key": "test-key"},
        )
        assert r.status_code == 200
        assert "content-encoding" not in r.headers, (
            "content-encoding must be stripped to prevent zlib double-decompression"
        )
        assert r.json()["model"] == "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# Gateway pipeline integration tests (Tasks 5.1 & 5.2)
# ---------------------------------------------------------------------------

import json as _json
import asyncio as _asyncio
from unittest.mock import MagicMock, AsyncMock, patch

from tokenlens.proxy import handle_proxy_request
from tokenlens.store import UsageStore as _UsageStore
from tokenlens.pricing import PricingTable as _PricingTable


@pytest.fixture
def gateway_store(tmp_path):
    s = _UsageStore(db_path=tmp_path / "test.db")
    # Configure: quotas + guardrails + routing
    s.set_setting("quotas.config", _json.dumps({
        "source_limits": {"expensive-agent": {"daily_limit_usd": 10.0}},
        "model_limits": {},
        "kill_switches": ["banned-agent"],
    }))
    s.set_setting("guardrails.config", _json.dumps({
        "pii_enabled": True,
        "injection_enabled": True,
        "custom_patterns": [],
        "action": "block",
    }))
    s.set_setting("routing.config", _json.dumps({
        "aliases": {"gpt-4": "claude-sonnet-4-6"},
        "fallback_chains": {},
        "weights": {},
    }))
    return s


def test_gateway_kill_switch_blocks(gateway_store):
    pricing = _PricingTable()
    resp = _asyncio.run(handle_proxy_request(
        path="/proxy/anthropic/banned-agent/v1/messages",
        method="POST",
        headers={"content-type": "application/json"},
        body=b'{"model": "claude-sonnet-4-6", "messages": []}',
        store=gateway_store,
        pricing=pricing,
    ))
    assert resp.status_code == 429
    assert b"kill switch" in resp.body


def test_gateway_pii_blocks(gateway_store):
    pricing = _PricingTable()
    resp = _asyncio.run(handle_proxy_request(
        path="/proxy/anthropic/clean-agent/v1/messages",
        method="POST",
        headers={"content-type": "application/json"},
        body=_json.dumps({
            "model": "claude-sonnet-4-6",
            "messages": [{"role": "user", "content": "My SSN is 123-45-6789"}],
        }).encode(),
        store=gateway_store,
        pricing=pricing,
    ))
    assert resp.status_code == 400
    assert b"guardrail" in resp.body.lower()


def test_gateway_clean_request_passes(gateway_store):
    pricing = _PricingTable()

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = b'{"usage": {"input_tokens": 10, "output_tokens": 5}, "model": "claude-sonnet-4-6"}'
    mock_response.headers = {}
    mock_response.is_success = True

    with patch("tokenlens.proxy.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.request = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        resp = _asyncio.run(handle_proxy_request(
            path="/proxy/anthropic/clean-agent/v1/messages",
            method="POST",
            headers={"content-type": "application/json"},
            body=_json.dumps({
                "model": "claude-sonnet-4-6",
                "messages": [{"role": "user", "content": "Hello world"}],
            }).encode(),
            store=gateway_store,
            pricing=pricing,
        ))
        assert resp.status_code == 200
