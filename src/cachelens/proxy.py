"""HTTP proxy handler for CacheLens.

Intercepts AI API calls at /proxy/<provider>[/<tag>]/<upstream-path>,
forwards them to the real provider API, and records usage metrics.
"""
from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any, AsyncIterator

import httpx
from fastapi.responses import Response

from cachelens.detector import ParsedProxy, parse_proxy_path
from cachelens.pricing import PricingTable
from cachelens.store import UsageStore
from cachelens.waste_detector import detect_waste, WasteItem

# ---------------------------------------------------------------------------
# Provider base URLs
# ---------------------------------------------------------------------------

PROVIDER_URLS: dict[str, str] = {
    "anthropic": "https://api.anthropic.com",
    "openai": "https://api.openai.com",
    "google": "https://generativelanguage.googleapis.com",
}

# Hop-by-hop headers that must not be forwarded upstream
_HOP_BY_HOP: frozenset[str] = frozenset({
    "host",
    "content-length",
    "transfer-encoding",
    "connection",
    "keep-alive",
    "te",
    "trailers",
    "upgrade",
    "proxy-authorization",
    "proxy-authenticate",
})


# ---------------------------------------------------------------------------
# Pure helper functions
# ---------------------------------------------------------------------------

def sha256_request(body: bytes) -> str:
    """Return SHA-256 hex digest of request body bytes."""
    return hashlib.sha256(body).hexdigest()


def is_streaming_request(body: bytes, provider: str) -> bool:
    """Return True if the request body requests streaming.

    - Anthropic/OpenAI: body JSON has "stream": true
    - Google: determined by URL path (not request body); returns False here
      since path-based detection happens at the handler level.
    """
    if not body:
        return False
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return False
    return bool(data.get("stream") is True)


def extract_usage_from_response(body: bytes, provider: str) -> dict | None:
    """Parse usage metadata from a complete (non-streaming) response body.

    Returns a dict with keys:
        model, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens

    Returns None if parsing fails or response has no usage.
    """
    if not body:
        return None
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None

    if provider == "anthropic":
        return _extract_anthropic_usage(data)
    if provider == "openai":
        return _extract_openai_usage(data)
    if provider == "google":
        return _extract_google_usage(data)
    return None


def _extract_anthropic_usage(data: dict) -> dict | None:
    usage = data.get("usage")
    if not usage:
        return None
    model = data.get("model", "")
    return {
        "model": model,
        "input_tokens": int(usage.get("input_tokens", 0)),
        "output_tokens": int(usage.get("output_tokens", 0)),
        "cache_read_tokens": int(usage.get("cache_read_input_tokens", 0)),
        "cache_write_tokens": int(usage.get("cache_creation_input_tokens", 0)),
    }


def _extract_openai_usage(data: dict) -> dict | None:
    usage = data.get("usage")
    if not usage:
        return None
    model = data.get("model", "")
    details = usage.get("prompt_tokens_details") or {}
    cached_tokens = int(details.get("cached_tokens", 0))
    return {
        "model": model,
        "input_tokens": int(usage.get("prompt_tokens", 0)),
        "output_tokens": int(usage.get("completion_tokens", 0)),
        "cache_read_tokens": cached_tokens,
        "cache_write_tokens": 0,
    }


def _extract_google_usage(data: dict) -> dict | None:
    usage = data.get("usageMetadata")
    if not usage:
        return None
    model = data.get("modelVersion", "")
    return {
        "model": model,
        "input_tokens": int(usage.get("promptTokenCount", 0)),
        "output_tokens": int(usage.get("candidatesTokenCount", 0)),
        "cache_read_tokens": int(usage.get("cachedContentTokenCount", 0)),
        "cache_write_tokens": 0,
    }


def extract_usage_from_sse_chunks(chunks: list[bytes], provider: str) -> dict | None:
    """Parse usage from accumulated SSE chunks.

    Anthropic SSE:
      - "message_start" event carries model + input usage (with cache fields)
      - "message_delta" event carries final output_tokens

    OpenAI SSE:
      - The last non-[DONE] data line may carry a full "usage" field when
        stream_options={"include_usage": true} was requested.

    Google SSE:
      - Treat each chunk as a full response; last chunk with usageMetadata wins.
    """
    if not chunks:
        return None

    if provider == "anthropic":
        return _extract_anthropic_sse(chunks)
    if provider == "openai":
        return _extract_openai_sse(chunks)
    if provider == "google":
        return _extract_google_sse(chunks)
    return None


def _parse_sse_data_lines(chunks: list[bytes]) -> list[str]:
    """Yield all 'data: ...' line values across the chunk list."""
    lines: list[str] = []
    for chunk in chunks:
        for raw_line in chunk.decode(errors="replace").splitlines():
            line = raw_line.strip()
            if line.startswith("data:"):
                lines.append(line[len("data:"):].strip())
    return lines


def _parse_sse_events(chunks: list[bytes]) -> list[tuple[str, str]]:
    """Return list of (event_type, data) tuples from SSE chunks."""
    events: list[tuple[str, str]] = []
    current_event = "message"
    current_data_parts: list[str] = []

    for chunk in chunks:
        for raw_line in chunk.decode(errors="replace").splitlines():
            line = raw_line.strip()
            if line.startswith("event:"):
                current_event = line[len("event:"):].strip()
            elif line.startswith("data:"):
                current_data_parts.append(line[len("data:"):].strip())
            elif line == "":
                if current_data_parts:
                    events.append((current_event, "\n".join(current_data_parts)))
                current_event = "message"
                current_data_parts = []

    if current_data_parts:
        events.append((current_event, "\n".join(current_data_parts)))

    return events


def _extract_anthropic_sse(chunks: list[bytes]) -> dict | None:
    events = _parse_sse_events(chunks)
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    found_usage = False

    for event_type, data in events:
        try:
            payload = json.loads(data)
        except (json.JSONDecodeError, ValueError):
            continue

        if payload.get("type") == "message_start":
            message = payload.get("message", {})
            model = message.get("model", "")
            usage = message.get("usage", {})
            input_tokens = int(usage.get("input_tokens", 0))
            cache_read_tokens = int(usage.get("cache_read_input_tokens", 0))
            cache_write_tokens = int(usage.get("cache_creation_input_tokens", 0))
            found_usage = True

        elif payload.get("type") == "message_delta":
            usage = payload.get("usage", {})
            output_tokens = int(usage.get("output_tokens", output_tokens))

    if not found_usage:
        return None

    return {
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_write_tokens": cache_write_tokens,
    }


def _extract_openai_sse(chunks: list[bytes]) -> dict | None:
    data_lines = _parse_sse_data_lines(chunks)

    # Walk backwards to find the last data line with usage (before [DONE])
    for line in reversed(data_lines):
        if line == "[DONE]":
            continue
        try:
            payload = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        usage = payload.get("usage")
        if usage:
            model = payload.get("model", None)
            details = (usage.get("prompt_tokens_details") or {})
            cached_tokens = int(details.get("cached_tokens", 0))
            return {
                "model": model,
                "input_tokens": int(usage.get("prompt_tokens", 0)),
                "output_tokens": int(usage.get("completion_tokens", 0)),
                "cache_read_tokens": cached_tokens,
                "cache_write_tokens": 0,
            }
    return None


def _extract_google_sse(chunks: list[bytes]) -> dict | None:
    # Google SSE sends full JSON objects; last one with usageMetadata wins
    result: dict | None = None
    data_lines = _parse_sse_data_lines(chunks)
    for line in data_lines:
        try:
            payload = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        candidate = _extract_google_usage(payload)
        if candidate is not None:
            result = candidate
    return result


# ---------------------------------------------------------------------------
# Header filtering
# ---------------------------------------------------------------------------

def _filter_headers(headers: dict[str, str]) -> dict[str, str]:
    """Remove hop-by-hop headers before forwarding upstream."""
    return {
        k: v
        for k, v in headers.items()
        if k.lower() not in _HOP_BY_HOP
    }


_STRIP_RESPONSE: frozenset[str] = _HOP_BY_HOP | frozenset({
    "content-encoding",
    "content-length",
})


def _filter_response_headers(headers: dict[str, str]) -> dict[str, str]:
    """Remove hop-by-hop and encoding headers from upstream response.

    httpx automatically decompresses gzip/deflate/br responses, so the body
    we forward is already decompressed.  Keeping the original
    ``content-encoding`` header causes downstream clients (e.g. Claude) to
    attempt a second decompression -> zlib error.  ``content-length`` is also
    stripped because the decompressed size differs from the original.
    """
    return {
        k: v
        for k, v in headers.items()
        if k.lower() not in _STRIP_RESPONSE
    }


# ---------------------------------------------------------------------------
# Proxy request handler (used by FastAPI route)
# ---------------------------------------------------------------------------

async def handle_proxy_request(
    *,
    path: str,
    method: str,
    headers: dict[str, str],
    body: bytes,
    store: UsageStore,
    pricing: PricingTable,
    on_call_recorded: Callable[[dict], Awaitable[None]] | None = None,
) -> Response:
    """Forward the request to the upstream provider and record usage.

    Returns a FastAPI Response (or StreamingResponse for streaming requests).
    """
    # Budget cap enforcement (Phase 7)
    budget_enabled = store.get_setting("budget.enabled") == "true"
    if budget_enabled:
        daily_limit_str = store.get_setting("budget.daily_limit_usd")
        monthly_limit_str = store.get_setting("budget.monthly_limit_usd")
        if daily_limit_str:
            daily_limit = float(daily_limit_str)
            if store.daily_spend_usd() >= daily_limit:
                return Response(
                    status_code=429,
                    content=json.dumps({
                        "error": "CacheLens daily budget exceeded",
                        "daily_spend_usd": store.daily_spend_usd(),
                        "daily_limit_usd": daily_limit,
                    }).encode(),
                    media_type="application/json",
                    headers={"Retry-After": "3600"},
                )
        if monthly_limit_str:
            monthly_limit = float(monthly_limit_str)
            if store.monthly_spend_usd() >= monthly_limit:
                return Response(
                    status_code=429,
                    content=json.dumps({
                        "error": "CacheLens monthly budget exceeded",
                        "monthly_spend_usd": store.monthly_spend_usd(),
                        "monthly_limit_usd": monthly_limit,
                    }).encode(),
                    media_type="application/json",
                    headers={"Retry-After": "3600"},
                )

    parsed = parse_proxy_path(path, headers)
    if parsed is None:
        return Response(
            status_code=404,
            content=b'{"error": "invalid proxy path"}',
            media_type="application/json",
        )

    base_url = PROVIDER_URLS.get(parsed.provider)
    if base_url is None:
        return Response(
            status_code=404,
            content=b'{"error": "unknown provider"}',
            media_type="application/json",
        )

    upstream_url = base_url + parsed.upstream_path
    forward_headers = _filter_headers(headers)
    request_hash = sha256_request(body)
    endpoint = parsed.upstream_path
    user_agent = next(
        (v for k, v in headers.items() if k.lower() == "user-agent"), ""
    )

    # Parse request body for analysis (shared across all v2 features)
    parsed_body: dict | None = None
    if method == "POST" and body:
        try:
            parsed_body = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass

    # Run waste detection on parsed request body
    _waste_items = detect_waste(parsed_body, parsed.provider) if parsed_body is not None else []

    # Extract max_tokens_requested from request body
    _max_tokens_requested: int | None = None
    if parsed_body is not None:
        for field in ("max_tokens", "maxOutputTokens"):
            val = parsed_body.get(field)
            if val is not None:
                try:
                    _max_tokens_requested = int(val)
                except (TypeError, ValueError):
                    pass
                break

    # Compute history metrics from request messages
    _message_count: int | None = None
    _history_tokens: int | None = None
    _history_ratio: float | None = None

    if parsed_body is not None:
        messages = parsed_body.get("messages") or []
        if isinstance(messages, list):
            _message_count = len(messages)
            if _message_count > 6:
                history_msgs = [
                    m for m in messages[:-1]
                    if m.get("role") in ("user", "assistant")
                ]
                if history_msgs:
                    try:
                        import tiktoken
                        enc = tiktoken.get_encoding("cl100k_base")

                        def _tok(m: dict) -> int:
                            c = m.get("content") or ""
                            if isinstance(c, str):
                                return len(enc.encode(c))
                            if isinstance(c, list):
                                text = " ".join(
                                    block.get("text", "") for block in c
                                    if isinstance(block, dict) and block.get("type") == "text"
                                )
                                return len(enc.encode(text)) if text else 0
                            return 0

                        _history_tokens = sum(_tok(m) for m in history_msgs)
                        total_input = sum(_tok(m) for m in messages)
                        if total_input > 0:
                            _history_ratio = _history_tokens / total_input
                    except Exception:
                        pass

    # Determine streaming: Google uses path (streamGenerateContent), others use body
    if parsed.provider == "google":
        streaming = "streamGenerateContent" in parsed.upstream_path
    elif parsed_body is not None:
        streaming = bool(parsed_body.get("stream") is True)
    else:
        streaming = is_streaming_request(body, parsed.provider)

    # Request deduplication (non-streaming only)
    dedup_enabled = (
        not streaming
        and store.get_setting("dedup.enabled") == "true"
    )
    if dedup_enabled:
        cached = store.get_cached_response(request_hash)
        if cached is not None:
            cached_headers = json.loads(cached["response_headers"])
            cached_headers["X-CacheLens-Cache"] = "HIT"
            return Response(
                content=cached["response_body"],
                status_code=cached["response_status"],
                headers=cached_headers,
            )

    if streaming:
        return _UpstreamStreamResponse(
            method=method,
            url=upstream_url,
            headers=forward_headers,
            body=body,
            parsed=parsed,
            request_hash=request_hash,
            endpoint=endpoint,
            store=store,
            pricing=pricing,
            on_call_recorded=on_call_recorded,
            user_agent=user_agent,
            parsed_body=parsed_body,
            _waste_items=_waste_items,
            _max_tokens_requested=_max_tokens_requested,
            _message_count=_message_count,
            _history_tokens=_history_tokens,
            _history_ratio=_history_ratio,
        )
    else:
        async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
            return await _handle_non_streaming(
                client=client,
                method=method,
                url=upstream_url,
                headers=forward_headers,
                body=body,
                parsed=parsed,
                request_hash=request_hash,
                endpoint=endpoint,
                store=store,
                pricing=pricing,
                on_call_recorded=on_call_recorded,
                user_agent=user_agent,
                dedup_enabled=dedup_enabled,
                parsed_body=parsed_body,
                _waste_items=_waste_items,
                _max_tokens_requested=_max_tokens_requested,
                _message_count=_message_count,
                _history_tokens=_history_tokens,
                _history_ratio=_history_ratio,
            )


class _UpstreamStreamResponse(Response):
    """ASGI response that opens an upstream stream and forwards its status,
    headers, and body chunks to the downstream client.

    This replaces the old StreamingResponse approach so that upstream HTTP
    status codes (e.g. 429, 500) and headers are correctly propagated instead
    of being silently overridden with a local 200.
    """

    def __init__(
        self,
        *,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes,
        parsed: ParsedProxy,
        request_hash: str,
        endpoint: str,
        store: UsageStore,
        pricing: PricingTable,
        on_call_recorded: Callable[[dict], Awaitable[None]] | None = None,
        user_agent: str = "",
        parsed_body: dict | None = None,
        _waste_items: list[WasteItem] | None = None,
        _max_tokens_requested: int | None = None,
        _message_count: int | None = None,
        _history_tokens: int | None = None,
        _history_ratio: float | None = None,
        _token_heatmap: str | None = None,
    ) -> None:
        super().__init__()
        self._method = method
        self._url = url
        self._headers = headers
        self._body = body
        self._parsed = parsed
        self._request_hash = request_hash
        self._endpoint = endpoint
        self._store = store
        self._pricing = pricing
        self._on_call_recorded = on_call_recorded
        self._user_agent = user_agent
        self._parsed_body = parsed_body
        self._waste_items = _waste_items or []
        self._max_tokens_requested = _max_tokens_requested
        self._message_count = _message_count
        self._history_tokens = _history_tokens
        self._history_ratio = _history_ratio
        self._token_heatmap = _token_heatmap

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        client = httpx.AsyncClient(timeout=300.0, follow_redirects=True)
        try:
            async with client.stream(
                self._method, self._url,
                headers=self._headers, content=self._body,
            ) as response:
                filtered = _filter_response_headers(dict(response.headers))
                if not any(k.lower() == "content-type" for k in filtered):
                    filtered["content-type"] = "text/event-stream"

                asgi_headers = [
                    (k.lower().encode("latin-1"), v.encode("latin-1"))
                    for k, v in filtered.items()
                ]
                await send({
                    "type": "http.response.start",
                    "status": response.status_code,
                    "headers": asgi_headers,
                })

                chunks: list[bytes] = []
                try:
                    async for chunk in response.aiter_bytes():
                        chunks.append(chunk)
                        await send({
                            "type": "http.response.body",
                            "body": chunk,
                            "more_body": True,
                        })
                except Exception:
                    pass

                await send({"type": "http.response.body", "body": b"", "more_body": False})

                if response.is_success:
                    usage = extract_usage_from_sse_chunks(chunks, self._parsed.provider)
                    if usage is not None:
                        event = _record_call(
                            store=self._store,
                            pricing=self._pricing,
                            parsed=self._parsed,
                            endpoint=self._endpoint,
                            request_hash=self._request_hash,
                            usage=usage,
                            user_agent=self._user_agent,
                            max_tokens_requested=self._max_tokens_requested,
                            message_count=self._message_count,
                            history_tokens=self._history_tokens,
                            history_ratio=self._history_ratio,
                            token_heatmap=self._token_heatmap,
                        )
                        if self._waste_items:
                            self._store.insert_waste_items(
                                call_id=event["id"],
                                items=[{"waste_type": w.waste_type, "waste_tokens": w.waste_tokens,
                                        "savings_usd": w.savings_usd, "detail": w.detail}
                                       for w in self._waste_items],
                            )
                        event["waste_tokens"] = sum(w.waste_tokens for w in self._waste_items)
                        if self._on_call_recorded is not None:
                            await self._on_call_recorded(event)
        finally:
            await client.aclose()


async def _handle_non_streaming(
    *,
    client: httpx.AsyncClient,
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes,
    parsed: ParsedProxy,
    request_hash: str,
    endpoint: str,
    store: UsageStore,
    pricing: PricingTable,
    on_call_recorded: Callable[[dict], Awaitable[None]] | None = None,
    user_agent: str = "",
    dedup_enabled: bool = False,
    parsed_body: dict | None = None,
    _waste_items: list[WasteItem] | None = None,
    _max_tokens_requested: int | None = None,
    _message_count: int | None = None,
    _history_tokens: int | None = None,
    _history_ratio: float | None = None,
    _token_heatmap: str | None = None,
) -> Response:
    t0 = time.time()
    response = await client.request(method, url, headers=headers, content=body)
    latency_ms = (time.time() - t0) * 1000
    response_body = response.content
    response_headers = _filter_response_headers(dict(response.headers))

    if response.is_success:
        usage = extract_usage_from_response(response_body, parsed.provider)
        if usage is not None:
            event = _record_call(
                store=store,
                pricing=pricing,
                parsed=parsed,
                endpoint=endpoint,
                request_hash=request_hash,
                usage=usage,
                user_agent=user_agent,
                latency_ms=latency_ms,
                status_code=response.status_code,
                max_tokens_requested=_max_tokens_requested,
                message_count=_message_count,
                history_tokens=_history_tokens,
                history_ratio=_history_ratio,
                token_heatmap=_token_heatmap,
            )
            if _waste_items:
                store.insert_waste_items(
                    call_id=event["id"],
                    items=[{"waste_type": w.waste_type, "waste_tokens": w.waste_tokens,
                            "savings_usd": w.savings_usd, "detail": w.detail}
                           for w in _waste_items],
                )
            event["waste_tokens"] = sum(w.waste_tokens for w in _waste_items)
            if on_call_recorded is not None:
                await on_call_recorded(event)

        # Cache the response if dedup is enabled
        if dedup_enabled and usage is not None:
            store.set_cached_response(
                request_hash=request_hash,
                response_body=response_body,
                response_status=response.status_code,
                response_headers=json.dumps(dict(response_headers)),
                provider=parsed.provider,
                model=usage.get("model", "unknown"),
                ttl_seconds=300,
            )
    else:
        # Record non-success calls for latency/status tracking
        _record_call(
            store=store,
            pricing=pricing,
            parsed=parsed,
            endpoint=endpoint,
            request_hash=request_hash,
            usage={"model": "unknown", "input_tokens": 0, "output_tokens": 0,
                   "cache_read_tokens": 0, "cache_write_tokens": 0},
            user_agent=user_agent,
            latency_ms=latency_ms,
            status_code=response.status_code,
        )

    return Response(
        content=response_body,
        status_code=response.status_code,
        headers=response_headers,
    )


# ---------------------------------------------------------------------------
# Store helper
# ---------------------------------------------------------------------------

def _record_call(
    *,
    store: UsageStore,
    pricing: PricingTable,
    parsed: ParsedProxy,
    endpoint: str,
    request_hash: str,
    usage: dict,
    user_agent: str = "",
    latency_ms: float | None = None,
    status_code: int | None = None,
    max_tokens_requested: int | None = None,
    message_count: int | None = None,
    history_tokens: int | None = None,
    history_ratio: float | None = None,
    token_heatmap: str | None = None,
) -> dict:
    """Record call in store and return event dict for WebSocket broadcast."""
    model = usage.get("model") or "unknown"
    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)
    cache_read_tokens = usage.get("cache_read_tokens", 0)
    cache_write_tokens = usage.get("cache_write_tokens", 0)
    ts = int(time.time())

    cost = pricing.cost_usd(
        provider=parsed.provider,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
    )

    output_utilization: float | None = None
    if max_tokens_requested and max_tokens_requested > 0 and output_tokens > 0:
        output_utilization = output_tokens / max_tokens_requested

    call_id = store.insert_call(
        ts=ts,
        provider=parsed.provider,
        model=model,
        source=parsed.source,
        source_tag=parsed.source_tag,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        cost_usd=cost,
        endpoint=endpoint,
        request_hash=request_hash,
        user_agent=user_agent,
        latency_ms=latency_ms,
        status_code=status_code,
        max_tokens_requested=max_tokens_requested,
        output_utilization=output_utilization,
        message_count=message_count,
        history_tokens=history_tokens,
        history_ratio=history_ratio,
        token_heatmap=token_heatmap,
    )

    return {
        "id": call_id,
        "ts": ts,
        "provider": parsed.provider,
        "model": model,
        "source": parsed.source,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_write_tokens": cache_write_tokens,
        "cost_usd": cost,
        "endpoint": endpoint,
    }
