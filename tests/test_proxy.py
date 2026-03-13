"""Tests for the proxy handler pure functions."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cachelens.proxy import (
    _filter_response_headers,
    _record_call,
    extract_usage_from_response,
    extract_usage_from_sse_chunks,
    is_streaming_request,
    sha256_request,
)


# ---------------------------------------------------------------------------
# sha256_request
# ---------------------------------------------------------------------------

def test_sha256_request_returns_hex_string():
    result = sha256_request(b"hello")
    assert isinstance(result, str)
    assert len(result) == 64
    assert all(c in "0123456789abcdef" for c in result)


def test_sha256_request_consistent():
    body = b'{"model": "claude-sonnet-4-6", "messages": []}'
    assert sha256_request(body) == sha256_request(body)


def test_sha256_request_different_inputs_differ():
    assert sha256_request(b"foo") != sha256_request(b"bar")


# ---------------------------------------------------------------------------
# is_streaming_request
# ---------------------------------------------------------------------------

def test_is_streaming_request_anthropic_true():
    body = json.dumps({"model": "claude-sonnet-4-6", "stream": True}).encode()
    assert is_streaming_request(body, "anthropic") is True


def test_is_streaming_request_anthropic_false():
    body = json.dumps({"model": "claude-sonnet-4-6"}).encode()
    assert is_streaming_request(body, "anthropic") is False


def test_is_streaming_request_openai_true():
    body = json.dumps({"model": "gpt-4o", "stream": True}).encode()
    assert is_streaming_request(body, "openai") is True


def test_is_streaming_request_openai_false():
    body = json.dumps({"model": "gpt-4o", "stream": False}).encode()
    assert is_streaming_request(body, "openai") is False


def test_is_streaming_request_google_stream_path():
    body = b"{}"
    # Google uses path to indicate streaming; provider=google with body is False
    assert is_streaming_request(body, "google") is False


def test_is_streaming_request_invalid_json_returns_false():
    assert is_streaming_request(b"not json {{", "anthropic") is False


def test_is_streaming_request_empty_body_returns_false():
    assert is_streaming_request(b"", "openai") is False


# ---------------------------------------------------------------------------
# extract_usage_from_response — Anthropic
# ---------------------------------------------------------------------------

def test_extract_usage_anthropic_complete():
    body = json.dumps({
        "model": "claude-sonnet-4-6",
        "usage": {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_read_input_tokens": 20,
            "cache_creation_input_tokens": 5,
        },
    }).encode()
    result = extract_usage_from_response(body, "anthropic")
    assert result == {
        "model": "claude-sonnet-4-6",
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_read_tokens": 20,
        "cache_write_tokens": 5,
    }


def test_extract_usage_anthropic_no_cache_tokens():
    body = json.dumps({
        "model": "claude-sonnet-4-6",
        "usage": {
            "input_tokens": 200,
            "output_tokens": 80,
        },
    }).encode()
    result = extract_usage_from_response(body, "anthropic")
    assert result is not None
    assert result["cache_read_tokens"] == 0
    assert result["cache_write_tokens"] == 0
    assert result["input_tokens"] == 200
    assert result["output_tokens"] == 80


# ---------------------------------------------------------------------------
# extract_usage_from_response — OpenAI
# ---------------------------------------------------------------------------

def test_extract_usage_openai_complete():
    body = json.dumps({
        "model": "gpt-4o",
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "prompt_tokens_details": {"cached_tokens": 20},
        },
    }).encode()
    result = extract_usage_from_response(body, "openai")
    assert result == {
        "model": "gpt-4o",
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_read_tokens": 20,
        "cache_write_tokens": 0,
    }


def test_extract_usage_openai_no_cache_details():
    body = json.dumps({
        "model": "gpt-4o",
        "usage": {
            "prompt_tokens": 30,
            "completion_tokens": 10,
        },
    }).encode()
    result = extract_usage_from_response(body, "openai")
    assert result is not None
    assert result["cache_read_tokens"] == 0
    assert result["cache_write_tokens"] == 0


# ---------------------------------------------------------------------------
# extract_usage_from_response — Google
# ---------------------------------------------------------------------------

def test_extract_usage_google_complete():
    body = json.dumps({
        "modelVersion": "gemini-2.0-flash",
        "usageMetadata": {
            "promptTokenCount": 100,
            "candidatesTokenCount": 50,
        },
    }).encode()
    result = extract_usage_from_response(body, "google")
    assert result == {
        "model": "gemini-2.0-flash",
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    }


def test_extract_usage_google_with_cached_tokens():
    body = json.dumps({
        "modelVersion": "gemini-2.0-flash",
        "usageMetadata": {
            "promptTokenCount": 100,
            "candidatesTokenCount": 50,
            "cachedContentTokenCount": 25,
        },
    }).encode()
    result = extract_usage_from_response(body, "google")
    assert result is not None
    assert result["cache_read_tokens"] == 25


# ---------------------------------------------------------------------------
# extract_usage_from_response — error cases
# ---------------------------------------------------------------------------

def test_extract_usage_missing_usage_returns_none():
    body = json.dumps({"model": "gpt-4o", "choices": []}).encode()
    result = extract_usage_from_response(body, "openai")
    assert result is None


def test_extract_usage_invalid_json_returns_none():
    result = extract_usage_from_response(b"not valid json {{", "anthropic")
    assert result is None


def test_extract_usage_empty_body_returns_none():
    result = extract_usage_from_response(b"", "anthropic")
    assert result is None


# ---------------------------------------------------------------------------
# extract_usage_from_sse_chunks — Anthropic
# ---------------------------------------------------------------------------

ANTHROPIC_SSE_CHUNKS = [
    b'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_01","type":"message","role":"assistant","content":[],"model":"claude-sonnet-4-6","stop_reason":null,"stop_sequence":null,"usage":{"input_tokens":100,"output_tokens":0,"cache_read_input_tokens":20,"cache_creation_input_tokens":0}}}\n\n',
    b'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n',
    b'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null},"usage":{"output_tokens":50}}\n\n',
    b'event: message_stop\ndata: {"type":"message_stop"}\n\n',
]


def test_extract_usage_from_sse_chunks_anthropic():
    result = extract_usage_from_sse_chunks(ANTHROPIC_SSE_CHUNKS, "anthropic")
    assert result is not None
    assert result["model"] == "claude-sonnet-4-6"
    assert result["input_tokens"] == 100
    assert result["output_tokens"] == 50
    assert result["cache_read_tokens"] == 20
    assert result["cache_write_tokens"] == 0


# ---------------------------------------------------------------------------
# extract_usage_from_sse_chunks — OpenAI
# ---------------------------------------------------------------------------

OPENAI_SSE_CHUNKS = [
    b'data: {"id":"chatcmpl-1","object":"chat.completion.chunk","choices":[{"delta":{"content":"Hello"}}]}\n\n',
    b'data: {"id":"chatcmpl-1","object":"chat.completion.chunk","choices":[{"delta":{"content":""}}],"usage":{"prompt_tokens":10,"completion_tokens":5,"prompt_tokens_details":{"cached_tokens":0}}}\n\n',
    b'data: [DONE]\n\n',
]


def test_extract_usage_from_sse_chunks_openai():
    result = extract_usage_from_sse_chunks(OPENAI_SSE_CHUNKS, "openai")
    assert result is not None
    assert result["input_tokens"] == 10
    assert result["output_tokens"] == 5


# ---------------------------------------------------------------------------
# extract_usage_from_sse_chunks — no usage / empty
# ---------------------------------------------------------------------------

def test_extract_usage_from_sse_chunks_no_usage_returns_none():
    chunks = [
        b'data: {"choices": [{"delta": {"content": "hi"}}]}\n\n',
        b'data: [DONE]\n\n',
    ]
    result = extract_usage_from_sse_chunks(chunks, "openai")
    assert result is None


def test_extract_usage_from_sse_chunks_empty_returns_none():
    result = extract_usage_from_sse_chunks([], "anthropic")
    assert result is None


# ---------------------------------------------------------------------------
# _filter_response_headers — hop-by-hop stripping
# ---------------------------------------------------------------------------

def test_filter_response_headers_strips_hop_by_hop():
    headers = {
        "content-type": "application/json",
        "transfer-encoding": "chunked",
        "connection": "keep-alive",
        "keep-alive": "timeout=5",
        "x-custom-header": "value",
    }
    result = _filter_response_headers(headers)
    assert "transfer-encoding" not in result
    assert "connection" not in result
    assert "keep-alive" not in result
    assert result["content-type"] == "application/json"
    assert result["x-custom-header"] == "value"


def test_filter_response_headers_strips_content_encoding():
    """content-encoding must be stripped because httpx decompresses automatically."""
    headers = {
        "content-type": "application/json",
        "content-encoding": "gzip",
        "content-length": "1234",
        "x-request-id": "abc",
    }
    result = _filter_response_headers(headers)
    assert "content-encoding" not in result
    assert "content-length" not in result
    assert result["content-type"] == "application/json"
    assert result["x-request-id"] == "abc"


@pytest.mark.parametrize("encoding", ["gzip", "deflate", "br", "zstd"])
def test_filter_response_headers_strips_all_encoding_types(encoding: str):
    """Every content-encoding variant must be stripped to prevent double-decompression."""
    headers = {"content-type": "application/json", "content-encoding": encoding}
    result = _filter_response_headers(headers)
    assert "content-encoding" not in result


@pytest.mark.parametrize("header_name", [
    "Content-Encoding",
    "CONTENT-ENCODING",
    "content-encoding",
    "Content-encoding",
])
def test_filter_response_headers_strips_content_encoding_any_case(header_name: str):
    """content-encoding stripping must be case-insensitive (real servers vary)."""
    headers = {"content-type": "application/json", header_name: "gzip"}
    result = _filter_response_headers(headers)
    assert header_name not in result
    assert result["content-type"] == "application/json"


def test_filter_response_headers_preserves_non_hop_by_hop():
    headers = {
        "content-type": "text/event-stream",
        "x-request-id": "abc123",
        "cache-control": "no-cache",
    }
    result = _filter_response_headers(headers)
    assert result == {
        "content-type": "text/event-stream",
        "x-request-id": "abc123",
        "cache-control": "no-cache",
    }


def test_filter_response_headers_case_insensitive():
    headers = {
        "Transfer-Encoding": "chunked",
        "Connection": "close",
        "Content-Type": "application/json",
    }
    result = _filter_response_headers(headers)
    assert "Transfer-Encoding" not in result
    assert "Connection" not in result
    assert result["Content-Type"] == "application/json"


def test_filter_response_headers_empty_dict():
    assert _filter_response_headers({}) == {}


# ---------------------------------------------------------------------------
# _record_call — endpoint uses upstream_path not full URL
# ---------------------------------------------------------------------------

def test_record_call_uses_upstream_path_as_endpoint():
    from cachelens.detector import ParsedProxy

    store = MagicMock()
    pricing = MagicMock()
    pricing.cost_usd.return_value = 0.001

    parsed = ParsedProxy(
        provider="anthropic",
        source_tag=None,
        source="claude-code",
        upstream_path="/v1/messages",
    )
    usage = {
        "model": "claude-sonnet-4-6",
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    }

    _record_call(
        store=store,
        pricing=pricing,
        parsed=parsed,
        endpoint="/v1/messages",
        request_hash="abc123",
        usage=usage,
    )

    call_kwargs = store.insert_call.call_args[1]
    # endpoint must be the upstream path, not a full URL
    assert call_kwargs["endpoint"] == "/v1/messages"
    assert not call_kwargs["endpoint"].startswith("http")


# ---------------------------------------------------------------------------
# _UpstreamStreamResponse — upstream status and header forwarding
# ---------------------------------------------------------------------------


async def _run_asgi(response, scope=None):
    """Invoke an ASGI callable and return list of sent messages."""
    sent = []

    async def receive():
        return {}

    async def send(message):
        sent.append(message)

    await response(scope or {"type": "http"}, receive, send)
    return sent


class _FakeStreamCM:
    """Async context manager wrapping a fake upstream httpx streaming response."""

    def __init__(self, *, status_code=200, headers=None, chunks=()):
        self._status_code = status_code
        self._headers = headers or {}
        self._chunks = list(chunks)

    async def __aenter__(self):
        self.status_code = self._status_code
        self.headers = self._headers
        self.is_success = 200 <= self._status_code < 300

        async def aiter_bytes():
            for c in self._chunks:
                yield c

        self.aiter_bytes = aiter_bytes
        return self

    async def __aexit__(self, *args):
        pass


class _FakeClient:
    def __init__(self, stream_cm):
        self._stream_cm = stream_cm

    def stream(self, method, url, **kwargs):
        return self._stream_cm

    async def aclose(self):
        pass


def test_upstream_stream_response_forwards_upstream_429_status():
    """_UpstreamStreamResponse must forward upstream HTTP 429 status to client."""
    import asyncio
    from cachelens.proxy import _UpstreamStreamResponse
    from cachelens.detector import ParsedProxy
    from unittest.mock import MagicMock

    parsed = ParsedProxy(
        provider="anthropic",
        source_tag=None,
        source="claude-code",
        upstream_path="/v1/messages",
    )
    stream_cm = _FakeStreamCM(
        status_code=429,
        headers={"x-ratelimit-remaining": "0"},
        chunks=[b"rate limited"],
    )
    fake_client = _FakeClient(stream_cm)

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("cachelens.proxy.httpx.AsyncClient", lambda **kw: fake_client)
        asgi_resp = _UpstreamStreamResponse(
            method="POST",
            url="https://api.anthropic.com/v1/messages",
            headers={},
            body=b"{}",
            parsed=parsed,
            request_hash="abc",
            endpoint="/v1/messages",
            store=MagicMock(),
            pricing=MagicMock(),
            on_call_recorded=None,
        )
        sent = asyncio.run(_run_asgi(asgi_resp))

    start = next(m for m in sent if m["type"] == "http.response.start")
    assert start["status"] == 429


def test_upstream_stream_response_forwards_upstream_headers():
    """_UpstreamStreamResponse must forward upstream headers to client."""
    import asyncio
    from cachelens.proxy import _UpstreamStreamResponse
    from cachelens.detector import ParsedProxy
    from unittest.mock import MagicMock

    parsed = ParsedProxy(
        provider="openai",
        source_tag=None,
        source="myapp",
        upstream_path="/v1/chat/completions",
    )
    stream_cm = _FakeStreamCM(
        status_code=200,
        headers={"content-type": "text/event-stream", "x-request-id": "req-123"},
        chunks=[b"data: {}\n\n"],
    )
    fake_client = _FakeClient(stream_cm)

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("cachelens.proxy.httpx.AsyncClient", lambda **kw: fake_client)
        asgi_resp = _UpstreamStreamResponse(
            method="POST",
            url="https://api.openai.com/v1/chat/completions",
            headers={},
            body=b"{}",
            parsed=parsed,
            request_hash="def",
            endpoint="/v1/chat/completions",
            store=MagicMock(),
            pricing=MagicMock(),
            on_call_recorded=None,
        )
        sent = asyncio.run(_run_asgi(asgi_resp))

    start = next(m for m in sent if m["type"] == "http.response.start")
    header_dict = {k.decode(): v.decode() for k, v in start["headers"]}
    assert "x-request-id" in header_dict
    assert header_dict["x-request-id"] == "req-123"


def test_upstream_stream_response_strips_content_encoding():
    """Regression: streaming responses must NOT forward content-encoding.

    httpx decompresses gzip/deflate/br automatically via aiter_bytes().
    Forwarding the original content-encoding header causes downstream clients
    (e.g. Claude Code) to attempt double-decompression → zlib error.
    """
    import asyncio
    from cachelens.proxy import _UpstreamStreamResponse
    from cachelens.detector import ParsedProxy

    parsed = ParsedProxy(
        provider="anthropic",
        source_tag=None,
        source="claude-code",
        upstream_path="/v1/messages",
    )
    stream_cm = _FakeStreamCM(
        status_code=200,
        headers={
            "content-type": "text/event-stream",
            "content-encoding": "gzip",
            "content-length": "999",
            "x-request-id": "req-gzip",
        },
        chunks=[b"data: {}\n\n"],
    )
    fake_client = _FakeClient(stream_cm)

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("cachelens.proxy.httpx.AsyncClient", lambda **kw: fake_client)
        asgi_resp = _UpstreamStreamResponse(
            method="POST",
            url="https://api.anthropic.com/v1/messages",
            headers={},
            body=b"{}",
            parsed=parsed,
            request_hash="gzip-regression",
            endpoint="/v1/messages",
            store=MagicMock(),
            pricing=MagicMock(),
            on_call_recorded=None,
        )
        sent = asyncio.run(_run_asgi(asgi_resp))

    start = next(m for m in sent if m["type"] == "http.response.start")
    header_dict = {k.decode(): v.decode() for k, v in start["headers"]}
    assert "content-encoding" not in header_dict, (
        "content-encoding must be stripped to prevent zlib double-decompression"
    )
    assert "content-length" not in header_dict
    assert header_dict["x-request-id"] == "req-gzip"


def test_record_call_does_not_store_full_url_as_endpoint():
    from cachelens.detector import ParsedProxy

    store = MagicMock()
    pricing = MagicMock()
    pricing.cost_usd.return_value = 0.0

    parsed = ParsedProxy(
        provider="openai",
        source_tag="myapp",
        source="myapp",
        upstream_path="/v1/chat/completions",
    )
    usage = {
        "model": "gpt-4o",
        "input_tokens": 10,
        "output_tokens": 5,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    }

    _record_call(
        store=store,
        pricing=pricing,
        parsed=parsed,
        endpoint=parsed.upstream_path,
        request_hash="def456",
        usage=usage,
    )

    call_kwargs = store.insert_call.call_args[1]
    assert "api.openai.com" not in call_kwargs["endpoint"]
    assert call_kwargs["endpoint"] == "/v1/chat/completions"


def test_record_call_returns_event_with_id(tmp_path):
    """_record_call must return event dict that includes 'id' field."""
    from cachelens.store import UsageStore
    from cachelens.pricing import PricingTable
    from cachelens.proxy import _record_call
    from cachelens.detector import ParsedProxy

    store = UsageStore(tmp_path / "test.db")
    pricing = PricingTable()
    parsed = ParsedProxy(provider="anthropic", upstream_path="/v1/messages",
                         source="test", source_tag=None)
    event = _record_call(
        store=store, pricing=pricing, parsed=parsed,
        endpoint="/v1/messages", request_hash="abc",
        usage={"model": "claude-sonnet-4-6", "input_tokens": 100,
               "output_tokens": 50, "cache_read_tokens": 0, "cache_write_tokens": 0},
    )
    assert "id" in event
    assert isinstance(event["id"], int)
    assert event["id"] > 0
