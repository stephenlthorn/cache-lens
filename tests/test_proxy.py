"""Tests for the proxy handler pure functions."""
from __future__ import annotations

import json

import pytest

from cachelens.proxy import (
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
