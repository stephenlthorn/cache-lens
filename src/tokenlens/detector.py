"""Source detector for the TokenLens proxy.

Parses incoming proxy URLs and request headers to determine the originating
source (e.g. claude-code, python-httpx, a user-supplied tag).

URL structure:
    /proxy/<provider>[/<tag>]/<upstream-path>

Source detection priority (highest to lowest):
    1. URL tag (sanitized alphanumeric + hyphens, max 64 chars)
    2. User-Agent header pattern matching → canonical name
    3. X-TokenLens-Source request header → value as-is
    4. Falls back to "unknown"
"""
from __future__ import annotations

import re
from typing import NamedTuple

VALID_TAG_RE = re.compile(r"[^a-zA-Z0-9\-]")
MAX_TAG_LEN = 64

# Segments that start with v<digit> (e.g. v1, v2) are API version markers,
# not source tags.
VERSION_SEGMENT_RE = re.compile(r"^v\d")

KNOWN_PROVIDERS: frozenset[str] = frozenset({"anthropic", "openai", "google"})

# User-Agent pattern → canonical source name.
# Prefix patterns are matched case-sensitively at the start of the UA string
# (order matters; more specific first). A secondary substring pass handles
# compound UAs like "anthropic-typescript/X claude-code/Y" and space-separated
# variants like "Claude Code/0.23.0".
# App-level patterns: matched as case-sensitive prefix OR case-insensitive
# substring.  These always win over SDK library patterns below.
# Order matters within each tier (more specific first).
_APP_PATTERNS: list[tuple[str, str]] = [
    ("claude-code/", "claude-code"),
    ("Claude-Code/", "claude-code"),
    ("Claude Code/", "claude-code"),
    ("claude-desktop/", "claude-desktop"),
    ("Claude-Desktop/", "claude-desktop"),
    ("Claude Desktop/", "claude-desktop"),
    ("cursor/", "cursor"),
    ("Cursor/", "cursor"),
    ("windsurf/", "windsurf"),
    ("Windsurf/", "windsurf"),
    ("continue/", "continue"),
    ("Continue/", "continue"),
    ("aider/", "aider"),
    ("Aider/", "aider"),
    ("cline/", "cline"),
    ("Cline/", "cline"),
    ("zed/", "zed"),
    ("Zed/", "zed"),
]

# SDK library patterns: matched as prefix, only when no app pattern fires.
_SDK_PATTERNS: list[tuple[str, str]] = [
    ("anthropic-python/", "anthropic-python"),
    ("anthropic-typescript/", "anthropic-typescript"),
    ("openai-python/", "openai-python"),
    ("openai-node/", "openai-node"),
    ("python-httpx/", "python-httpx"),
    ("python-requests/", "python-requests"),
    ("node-fetch/", "node-fetch"),
    ("axios/", "axios"),
]

# Keep UA_PATTERNS as the union for any code that still references it.
UA_PATTERNS: list[tuple[str, str]] = _APP_PATTERNS + _SDK_PATTERNS

# Case-insensitive substring tokens for compound UAs like
# "anthropic-typescript/X claude-code/Y".  Checked after app prefix patterns
# but before SDK prefix patterns.
_UA_SUBSTRING_TOKENS: list[tuple[str, str]] = [
    ("claude-code", "claude-code"),
    ("claude-desktop", "claude-desktop"),
    ("cursor", "cursor"),
    ("windsurf", "windsurf"),
    ("continue-dev", "continue"),
    ("aider", "aider"),
    ("cline", "cline"),
]


class ParsedProxy(NamedTuple):
    provider: str           # e.g. "anthropic"
    source_tag: str | None  # raw sanitized URL tag (None if absent)
    source: str             # resolved canonical source for grouping
    upstream_path: str      # path to forward, e.g. "/v1/messages"


def sanitize_tag(raw: str) -> str | None:
    """Strip invalid characters and truncate to MAX_TAG_LEN.

    Invalid characters are stripped first, then the result is truncated to
    MAX_TAG_LEN characters. Returns None if nothing valid remains after stripping.
    """
    cleaned = VALID_TAG_RE.sub("", raw)[:MAX_TAG_LEN]
    return cleaned if cleaned else None


def detect_source_from_ua(user_agent: str | None) -> str | None:
    """Return canonical source name from User-Agent header, or None if no match.

    Detection order (highest → lowest priority):
    1. App prefix match (case-sensitive) against _APP_PATTERNS.
    2. Case-insensitive substring match against _UA_SUBSTRING_TOKENS —
       handles compound UAs like "anthropic-typescript/X claude-code/Y".
    3. SDK prefix match (case-sensitive) against _SDK_PATTERNS.
    """
    if not user_agent:
        return None
    # Tier 1: app-level prefix match
    for prefix, canonical in _APP_PATTERNS:
        if user_agent.startswith(prefix):
            return canonical
    # Tier 2: substring fallback — catches compound/space-separated UAs
    ua_lower = user_agent.lower()
    for token, canonical in _UA_SUBSTRING_TOKENS:
        if token in ua_lower:
            return canonical
    # Tier 3: SDK library prefix match
    for prefix, canonical in _SDK_PATTERNS:
        if user_agent.startswith(prefix):
            return canonical
    return None


def _get_header(headers: dict[str, str] | None, name: str) -> str | None:
    """Case-insensitive header lookup."""
    if not headers:
        return None
    name_lower = name.lower()
    for key, value in headers.items():
        if key.lower() == name_lower:
            return value
    return None


def parse_proxy_path(
    path: str,
    headers: dict[str, str] | None = None,
) -> ParsedProxy | None:
    """Parse a /proxy/<provider>[/<tag>]/<upstream-path> URL.

    Returns None if the path doesn't match the expected structure or the
    provider is not in KNOWN_PROVIDERS.

    Tag disambiguation: if the segment after the provider consists solely of
    alphanumeric characters and hyphens (but does NOT look like an API version
    such as v1/v2), it is treated as a tag candidate. If the candidate
    sanitizes to a non-empty string it becomes source_tag. If it sanitizes to
    empty (e.g. "!!!"), the segment is silently consumed and the remaining path
    is used as the upstream path unchanged — invalid tag segments are never
    forwarded to the provider.

    headers: optional dict of request headers used for source detection.
    """
    # Must start with /proxy/
    prefix = "/proxy/"
    if not path.startswith(prefix):
        return None

    remainder = path[len(prefix):]
    if not remainder:
        return None

    parts = remainder.split("/")

    # First segment is the provider
    provider = parts[0]
    if provider not in KNOWN_PROVIDERS:
        return None

    remaining = parts[1:]

    # Need at least one upstream segment
    if not remaining:
        return None

    # Attempt tag disambiguation: if the first remaining segment contains only
    # valid tag characters AND does NOT look like an API version (v<digit>),
    # treat it as a source tag.
    source_tag: str | None = None
    upstream_segments: list[str]

    first = remaining[0]
    is_version_segment = bool(VERSION_SEGMENT_RE.match(first))

    # A segment is a tag candidate if it is NOT an API version marker and
    # there is at least one more segment to serve as the upstream path.
    # We then sanitize the candidate; if sanitization yields a non-empty
    # string, it becomes the tag.  If it yields nothing, no tag is recorded.
    if not is_version_segment and len(remaining) >= 2:
        candidate = sanitize_tag(first)
        if candidate:
            source_tag = candidate
            upstream_segments = remaining[1:]
        else:
            # Fully sanitized away — consume the invalid segment, don't forward it
            upstream_segments = remaining[1:]
    else:
        upstream_segments = remaining

    # Build upstream path — must not be empty
    if not upstream_segments:
        return None

    upstream_path = "/" + "/".join(upstream_segments)

    # Resolve source using priority chain
    if source_tag is not None:
        source = source_tag
    else:
        ua_source = detect_source_from_ua(_get_header(headers, "User-Agent"))
        if ua_source is not None:
            source = ua_source
        else:
            custom = _get_header(headers, "X-TokenLens-Source")
            source = custom if custom is not None else "unknown"

    return ParsedProxy(
        provider=provider,
        source_tag=source_tag,
        source=source,
        upstream_path=upstream_path,
    )
