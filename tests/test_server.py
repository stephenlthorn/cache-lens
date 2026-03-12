"""Tests for the extended CacheLens FastAPI server.

Covers:
- Existing /api/analyze endpoint (regression)
- GET /api/status
- GET /api/usage/kpi
- GET /api/usage/daily
- GET /api/usage/sources
- GET /api/usage/recommendations
- WebSocket /api/live (connection + 10-connection limit)
- Proxy routes (unknown provider → 404)
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cachelens.pricing import PricingTable
from cachelens.server import create_app
from cachelens.store import UsageStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def test_store(tmp_path: Path) -> UsageStore:
    return UsageStore(tmp_path / "test.db")


@pytest.fixture
def pricing() -> PricingTable:
    return PricingTable()


@pytest.fixture
def client(test_store: UsageStore, pricing: PricingTable):  # type: ignore[return]
    app = create_app(store=test_store, pricing=pricing)
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Regression: /api/analyze still works
# ---------------------------------------------------------------------------


def test_analyze_endpoint_still_works(client: TestClient) -> None:
    """POST /api/analyze with valid input returns 200 with analysis result."""
    payload = {
        "input": (
            "<anthropic_input>\n"
            '{"model": "claude-sonnet-4-6", "messages": [{"role": "user", "content": "hello"}]}\n'
            "</anthropic_input>"
        )
    }
    response = client.post("/api/analyze", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert "total_tokens" in body or "sections" in body or isinstance(body, dict)


# ---------------------------------------------------------------------------
# /api/status
# ---------------------------------------------------------------------------


def test_status_endpoint_returns_running(client: TestClient) -> None:
    """GET /api/status returns 200 with daemon=running."""
    response = client.get("/api/status")
    assert response.status_code == 200
    body = response.json()
    assert body["daemon"] == "running"
    assert "pid" in body
    assert "port" in body
    assert "db_size_bytes" in body
    assert "raw_calls_today" in body
    assert "retention" in body
    assert "last_nightly_rollup" in body
    assert "last_yearly_rollup" in body


def test_status_pid_is_current_process(client: TestClient) -> None:
    """GET /api/status returns the current process PID."""
    response = client.get("/api/status")
    assert response.status_code == 200
    assert response.json()["pid"] == os.getpid()


def test_status_last_rollup_fields_are_present(client: TestClient) -> None:
    """GET /api/status always includes rollup timestamp fields (null or ISO string)."""
    response = client.get("/api/status")
    body = response.json()
    # Fields must be present; value is null or an ISO datetime string
    assert "last_nightly_rollup" in body
    assert "last_yearly_rollup" in body
    for key in ("last_nightly_rollup", "last_yearly_rollup"):
        value = body[key]
        assert value is None or isinstance(value, str)


# ---------------------------------------------------------------------------
# /api/usage/kpi
# ---------------------------------------------------------------------------


def test_kpi_endpoint_empty_store(client: TestClient) -> None:
    """GET /api/usage/kpi returns 200 with call_count=0 on empty store."""
    response = client.get("/api/usage/kpi")
    assert response.status_code == 200
    body = response.json()
    assert body["call_count"] == 0
    assert body["days"] == 30  # default
    assert "total_cost_usd" in body
    assert "input_tokens" in body
    assert "output_tokens" in body
    assert "cache_read_tokens" in body
    assert "cache_write_tokens" in body


def test_kpi_endpoint_custom_days(client: TestClient) -> None:
    """GET /api/usage/kpi?days=7 returns 200 with days=7."""
    response = client.get("/api/usage/kpi?days=7")
    assert response.status_code == 200
    body = response.json()
    assert body["days"] == 7


def test_kpi_endpoint_invalid_days_defaults_to_30(client: TestClient) -> None:
    """GET /api/usage/kpi?days=999 defaults to 30."""
    response = client.get("/api/usage/kpi?days=999")
    assert response.status_code == 200
    body = response.json()
    assert body["days"] == 30


def test_kpi_reflects_inserted_calls(client: TestClient, test_store: UsageStore) -> None:
    """KPI endpoint reflects calls inserted into the store."""
    import time
    test_store.insert_call(
        ts=int(time.time()),
        provider="anthropic",
        model="claude-sonnet-4-6",
        source="test",
        source_tag=None,
        input_tokens=1000,
        output_tokens=200,
        cache_read_tokens=50,
        cache_write_tokens=0,
        cost_usd=0.01,
        endpoint="/v1/messages",
        request_hash="abc123",
    )
    response = client.get("/api/usage/kpi?days=1")
    assert response.status_code == 200
    body = response.json()
    assert body["call_count"] == 1
    assert body["input_tokens"] == 1000
    assert body["output_tokens"] == 200


# ---------------------------------------------------------------------------
# /api/usage/daily
# ---------------------------------------------------------------------------


def test_daily_endpoint_empty_store(client: TestClient) -> None:
    """GET /api/usage/daily returns 200 with empty rows on empty store."""
    response = client.get("/api/usage/daily")
    assert response.status_code == 200
    body = response.json()
    assert "days" in body
    assert "rows" in body
    assert body["rows"] == []


def test_daily_endpoint_returns_rows(
    client: TestClient, test_store: UsageStore
) -> None:
    """GET /api/usage/daily returns daily_agg rows inserted into the store."""
    test_store.upsert_daily_agg(
        date="2026-03-10",
        provider="anthropic",
        model="claude-sonnet-4-6",
        source="claude-code",
        call_count=5,
        input_tokens=5000,
        output_tokens=1500,
        cache_read_tokens=800,
        cache_write_tokens=0,
        cost_usd=0.05,
    )
    response = client.get("/api/usage/daily?days=365")
    assert response.status_code == 200
    body = response.json()
    assert len(body["rows"]) >= 1
    row = body["rows"][0]
    assert row["provider"] == "anthropic"
    assert row["model"] == "claude-sonnet-4-6"
    assert row["source"] == "claude-code"
    assert row["call_count"] == 5
    assert row["input_tokens"] == 5000


def test_daily_endpoint_custom_days(client: TestClient) -> None:
    """GET /api/usage/daily?days=7 returns 200 with days=7."""
    response = client.get("/api/usage/daily?days=7")
    assert response.status_code == 200
    body = response.json()
    assert body["days"] == 7


def test_daily_endpoint_invalid_days_defaults_to_30(client: TestClient) -> None:
    """GET /api/usage/daily?days=999 should default to 30."""
    response = client.get("/api/usage/daily?days=999")
    assert response.status_code == 200
    assert response.json()["days"] == 30


# ---------------------------------------------------------------------------
# /api/usage/sources
# ---------------------------------------------------------------------------


def test_sources_endpoint_includes_todays_live_calls(
    client: TestClient, test_store: UsageStore
) -> None:
    """GET /api/usage/sources must include today's raw calls before nightly rollup."""
    import time
    test_store.insert_call(
        ts=int(time.time()),
        provider="anthropic",
        model="claude-sonnet-4-6",
        source="live-source",
        source_tag=None,
        input_tokens=100,
        output_tokens=50,
        cache_read_tokens=0,
        cache_write_tokens=0,
        cost_usd=0.01,
        endpoint="/v1/messages",
        request_hash="live-hash-001",
    )
    response = client.get("/api/usage/sources")
    assert response.status_code == 200
    source_names = [s["source"] for s in response.json()["sources"]]
    assert "live-source" in source_names


def test_sources_endpoint_empty_store(client: TestClient) -> None:
    """GET /api/usage/sources returns 200 with empty sources list."""
    response = client.get("/api/usage/sources")
    assert response.status_code == 200
    body = response.json()
    assert "sources" in body
    assert body["sources"] == []


def test_sources_endpoint_returns_sources(
    client: TestClient, test_store: UsageStore
) -> None:
    """GET /api/usage/sources returns aggregated source data."""
    test_store.upsert_daily_agg(
        date="2026-03-10",
        provider="anthropic",
        model="claude-sonnet-4-6",
        source="claude-code",
        call_count=100,
        input_tokens=100000,
        output_tokens=25000,
        cache_read_tokens=10000,
        cache_write_tokens=0,
        cost_usd=3.50,
    )
    test_store.upsert_daily_agg(
        date="2026-03-10",
        provider="openai",
        model="gpt-4o",
        source="claude-code",
        call_count=10,
        input_tokens=10000,
        output_tokens=2500,
        cache_read_tokens=0,
        cache_write_tokens=0,
        cost_usd=0.50,
    )
    response = client.get("/api/usage/sources")
    assert response.status_code == 200
    body = response.json()
    sources = body["sources"]
    assert len(sources) >= 1
    # claude-code source should be present
    claude_code = next((s for s in sources if s["source"] == "claude-code"), None)
    assert claude_code is not None
    assert "cost_usd" in claude_code
    assert "call_count" in claude_code
    assert "providers" in claude_code
    assert "anthropic" in claude_code["providers"]


# ---------------------------------------------------------------------------
# /api/usage/recommendations
# ---------------------------------------------------------------------------


def test_recommendations_endpoint_empty(client: TestClient) -> None:
    """GET /api/usage/recommendations returns empty list when no data."""
    response = client.get("/api/usage/recommendations")
    assert response.status_code == 200
    body = response.json()
    assert "recommendations" in body
    assert body["recommendations"] == []


def test_recommendations_endpoint_structure(
    client: TestClient, test_store: UsageStore
) -> None:
    """GET /api/usage/recommendations returns properly structured recommendations."""
    # Insert data that triggers low_cache_hit_rate recommendation
    test_store.upsert_daily_agg(
        date="2026-03-10",
        provider="anthropic",
        model="claude-sonnet-4-6",
        source="myapp",
        call_count=200,
        input_tokens=200000,
        output_tokens=50000,
        cache_read_tokens=0,  # No cache hits → triggers recommendation
        cache_write_tokens=0,
        cost_usd=2.0,
    )
    response = client.get("/api/usage/recommendations")
    assert response.status_code == 200
    body = response.json()
    assert "recommendations" in body
    recs = body["recommendations"]
    assert len(recs) >= 1
    rec = recs[0]
    assert "id" in rec
    assert "type" in rec
    assert "title" in rec
    assert "description" in rec
    assert "estimated_impact" in rec
    assert "deep_dive_link" in rec
    assert "metrics" in rec


# ---------------------------------------------------------------------------
# WebSocket /api/live
# ---------------------------------------------------------------------------


def test_websocket_connection_accepted(client: TestClient) -> None:
    """WebSocket client can connect to /api/live."""
    with client.websocket_connect("/api/live") as ws:
        # Connection accepted — no exception means success
        assert ws is not None


def test_websocket_max_10_connections(
    test_store: UsageStore, pricing: PricingTable
) -> None:
    """11th WebSocket connection is rejected."""
    app = create_app(store=test_store, pricing=pricing)

    connections = []
    with TestClient(app) as test_client:
        try:
            # Open 10 connections
            for _ in range(10):
                ws = test_client.websocket_connect("/api/live")
                ws.__enter__()
                connections.append(ws)

            # 11th connection should be rejected
            with pytest.raises(Exception):
                with test_client.websocket_connect("/api/live") as ws11:
                    pass
        finally:
            for ws in connections:
                try:
                    ws.__exit__(None, None, None)
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# Proxy routes
# ---------------------------------------------------------------------------


def test_proxy_route_unknown_provider_returns_error(client: TestClient) -> None:
    """GET /proxy/unknown/v1/test returns 404 for unknown provider."""
    response = client.get("/proxy/unknown/v1/test")
    assert response.status_code == 404


def test_proxy_route_known_provider_route_is_registered(
    test_store: UsageStore, pricing: PricingTable, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The proxy route for a known provider is registered (not a 404 routing error)."""
    import httpx

    # Mock httpx.AsyncClient to avoid hitting the real network
    class _FakeResponse:
        status_code = 401
        content = b'{"error": "unauthorized"}'
        headers = {"content-type": "application/json"}
        is_success = False

        def __init__(self, *a: object, **kw: object) -> None:
            pass

    class _FakeAsyncClient:
        def __init__(self, *a: object, **kw: object) -> None:
            pass

        async def __aenter__(self) -> "_FakeAsyncClient":
            return self

        async def __aexit__(self, *a: object) -> None:
            pass

        async def request(self, *a: object, **kw: object) -> _FakeResponse:
            return _FakeResponse()

    monkeypatch.setattr("cachelens.proxy.httpx.AsyncClient", _FakeAsyncClient)

    app = create_app(store=test_store, pricing=pricing)
    with TestClient(app) as tc:
        response = tc.post(
            "/proxy/anthropic/v1/messages",
            json={"model": "claude-sonnet-4-6", "messages": []},
            headers={"x-api-key": "test-key"},
        )
        # Route is registered and attempts to proxy — should not be a 404
        assert response.status_code != 404
