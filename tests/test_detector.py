"""Tests for the source detector module.

Covers URL tag parsing, User-Agent detection, X-CacheLens-Source header,
priority ordering, provider validation, and sanitize_tag utility.
"""
from __future__ import annotations

import pytest

from cachelens.detector import ParsedProxy, detect_source_from_ua, parse_proxy_path, sanitize_tag


# ---------------------------------------------------------------------------
# sanitize_tag unit tests
# ---------------------------------------------------------------------------


def test_sanitize_tag_pure_alphanumeric() -> None:
    assert sanitize_tag("myapp123") == "myapp123"


def test_sanitize_tag_with_hyphens() -> None:
    assert sanitize_tag("my-app") == "my-app"


def test_sanitize_tag_strips_spaces() -> None:
    assert sanitize_tag("my app") == "myapp"


def test_sanitize_tag_strips_special_chars() -> None:
    assert sanitize_tag("my-app!") == "my-app"


def test_sanitize_tag_all_invalid_returns_none() -> None:
    assert sanitize_tag("!!! ???") is None


def test_sanitize_tag_empty_string_returns_none() -> None:
    assert sanitize_tag("") is None


def test_sanitize_tag_truncates_to_64_chars() -> None:
    long_tag = "a" * 100
    result = sanitize_tag(long_tag)
    assert result == "a" * 64


def test_sanitize_tag_truncates_after_stripping() -> None:
    # 70 valid chars + some invalid
    raw = "a" * 70 + "!!!"
    result = sanitize_tag(raw)
    assert result == "a" * 64


# ---------------------------------------------------------------------------
# detect_source_from_ua unit tests
# ---------------------------------------------------------------------------


def test_detect_ua_none_returns_none() -> None:
    assert detect_source_from_ua(None) is None


def test_detect_ua_empty_returns_none() -> None:
    assert detect_source_from_ua("") is None


def test_detect_ua_claude_code_lowercase() -> None:
    assert detect_source_from_ua("claude-code/1.2.3") == "claude-code"


def test_detect_ua_claude_code_titlecase() -> None:
    assert detect_source_from_ua("Claude-Code/2.0.0") == "claude-code"


def test_detect_ua_python_httpx() -> None:
    assert detect_source_from_ua("python-httpx/0.27.0") == "python-httpx"


def test_detect_ua_python_requests() -> None:
    assert detect_source_from_ua("python-requests/2.31.0") == "python-requests"


def test_detect_ua_openai_python() -> None:
    assert detect_source_from_ua("openai-python/1.0.0") == "openai-python"


def test_detect_ua_anthropic_sdk_python() -> None:
    assert detect_source_from_ua("anthropic-python/0.20.0") == "anthropic-python"


def test_detect_ua_node_fetch() -> None:
    assert detect_source_from_ua("node-fetch/3.3.2") == "node-fetch"


def test_detect_ua_axios() -> None:
    assert detect_source_from_ua("axios/1.6.0") == "axios"


def test_detect_ua_unknown_returns_none() -> None:
    assert detect_source_from_ua("Mozilla/5.0 (compatible)") is None


# ---------------------------------------------------------------------------
# parse_proxy_path — basic structure
# ---------------------------------------------------------------------------


def test_parse_no_tag_anthropic() -> None:
    result = parse_proxy_path("/proxy/anthropic/v1/messages")
    assert result is not None
    assert result.provider == "anthropic"
    assert result.source_tag is None
    assert result.source == "unknown"
    assert result.upstream_path == "/v1/messages"


def test_parse_no_tag_openai() -> None:
    result = parse_proxy_path("/proxy/openai/v1/chat/completions")
    assert result is not None
    assert result.provider == "openai"
    assert result.source_tag is None
    assert result.upstream_path == "/v1/chat/completions"


def test_parse_no_tag_google() -> None:
    result = parse_proxy_path("/proxy/google/v1/models")
    assert result is not None
    assert result.provider == "google"
    assert result.source_tag is None
    assert result.upstream_path == "/v1/models"


def test_parse_with_tag_claude_code() -> None:
    result = parse_proxy_path("/proxy/anthropic/claude-code/v1/messages")
    assert result is not None
    assert result.provider == "anthropic"
    assert result.source_tag == "claude-code"
    assert result.source == "claude-code"
    assert result.upstream_path == "/v1/messages"


def test_parse_with_tag_my_app() -> None:
    result = parse_proxy_path("/proxy/openai/my-app/v1/chat/completions")
    assert result is not None
    assert result.source_tag == "my-app"
    assert result.source == "my-app"
    assert result.upstream_path == "/v1/chat/completions"


# ---------------------------------------------------------------------------
# Tag disambiguation: v1 is NOT a tag
# ---------------------------------------------------------------------------


def test_parse_v1_segment_is_upstream_not_tag() -> None:
    result = parse_proxy_path("/proxy/anthropic/v1/messages")
    assert result is not None
    assert result.source_tag is None
    assert result.upstream_path == "/v1/messages"


def test_parse_v2_segment_is_upstream_not_tag() -> None:
    result = parse_proxy_path("/proxy/openai/v2/chat/completions")
    assert result is not None
    assert result.source_tag is None
    assert result.upstream_path == "/v2/chat/completions"


# ---------------------------------------------------------------------------
# Tag sanitization in URL
# ---------------------------------------------------------------------------


def test_parse_fully_invalid_tag_falls_back_to_unknown() -> None:
    # "!!!" sanitizes to "" → no tag, no UA → unknown
    result = parse_proxy_path("/proxy/anthropic/!!!/v1/messages")
    assert result is not None
    assert result.source_tag is None
    assert result.source == "unknown"


def test_parse_partially_invalid_tag_sanitized() -> None:
    # "my-app!" → "my-app"
    result = parse_proxy_path("/proxy/anthropic/my-app!/v1/messages")
    assert result is not None
    assert result.source_tag == "my-app"
    assert result.source == "my-app"


def test_parse_tag_truncated_to_64_chars() -> None:
    long_tag = "a" * 80
    path = f"/proxy/anthropic/{long_tag}/v1/messages"
    result = parse_proxy_path(path)
    assert result is not None
    assert result.source_tag == "a" * 64
    assert len(result.source_tag) == 64


# ---------------------------------------------------------------------------
# Header-based source detection
# ---------------------------------------------------------------------------


def test_parse_user_agent_claude_code() -> None:
    result = parse_proxy_path(
        "/proxy/anthropic/v1/messages",
        headers={"User-Agent": "claude-code/1.2.3"},
    )
    assert result is not None
    assert result.source == "claude-code"
    assert result.source_tag is None


def test_parse_user_agent_python_httpx() -> None:
    result = parse_proxy_path(
        "/proxy/anthropic/v1/messages",
        headers={"User-Agent": "python-httpx/0.27.0"},
    )
    assert result is not None
    assert result.source == "python-httpx"


def test_parse_x_cachelens_source_header() -> None:
    result = parse_proxy_path(
        "/proxy/anthropic/v1/messages",
        headers={"X-CacheLens-Source": "my-custom-tool"},
    )
    assert result is not None
    assert result.source == "my-custom-tool"
    assert result.source_tag is None


# ---------------------------------------------------------------------------
# Priority ordering
# ---------------------------------------------------------------------------


def test_priority_url_tag_wins_over_user_agent() -> None:
    result = parse_proxy_path(
        "/proxy/anthropic/my-tag/v1/messages",
        headers={"User-Agent": "claude-code/1.2.3"},
    )
    assert result is not None
    assert result.source_tag == "my-tag"
    assert result.source == "my-tag"


def test_priority_url_tag_wins_over_x_cachelens_source() -> None:
    result = parse_proxy_path(
        "/proxy/anthropic/my-tag/v1/messages",
        headers={"X-CacheLens-Source": "other-source"},
    )
    assert result is not None
    assert result.source == "my-tag"


def test_priority_user_agent_wins_over_x_cachelens_source() -> None:
    result = parse_proxy_path(
        "/proxy/anthropic/v1/messages",
        headers={
            "User-Agent": "claude-code/1.2.3",
            "X-CacheLens-Source": "other-source",
        },
    )
    assert result is not None
    assert result.source == "claude-code"


def test_priority_x_cachelens_source_wins_over_unknown() -> None:
    result = parse_proxy_path(
        "/proxy/anthropic/v1/messages",
        headers={"X-CacheLens-Source": "my-custom-tool"},
    )
    assert result is not None
    assert result.source == "my-custom-tool"


def test_priority_all_absent_falls_back_to_unknown() -> None:
    result = parse_proxy_path("/proxy/anthropic/v1/messages")
    assert result is not None
    assert result.source == "unknown"


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


def test_parse_unknown_provider_returns_none() -> None:
    result = parse_proxy_path("/proxy/cohere/v1/chat")
    assert result is None


def test_parse_path_not_starting_with_proxy_returns_none() -> None:
    result = parse_proxy_path("/api/anthropic/v1/messages")
    assert result is None


def test_parse_missing_upstream_path_returns_none() -> None:
    # /proxy/anthropic with no upstream at all
    result = parse_proxy_path("/proxy/anthropic")
    assert result is None


def test_parse_proxy_only_root_returns_none() -> None:
    result = parse_proxy_path("/proxy/")
    assert result is None


def test_parse_returns_named_tuple() -> None:
    result = parse_proxy_path("/proxy/anthropic/v1/messages")
    assert isinstance(result, ParsedProxy)
