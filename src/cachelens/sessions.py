"""Per-session cost tracking for CacheLens.

Groups raw API calls into sessions based on time gaps. A session is a
contiguous sequence of calls from the same source where the gap between
consecutive calls is less than `gap_seconds` (default: 30 minutes).
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any


def detect_sessions(
    calls: list[dict[str, Any]],
    gap_seconds: int = 1800,
) -> list[dict[str, Any]]:
    """Detect sessions from a list of raw call dicts.

    Parameters
    ----------
    calls:
        Raw call dicts with at least: ts, source, model, input_tokens,
        output_tokens, cache_read_tokens, cost_usd.  Must be sorted by ts ASC.
    gap_seconds:
        Maximum gap between consecutive calls in the same session.

    Returns
    -------
    List of session dicts sorted by start_ts DESC (most recent first).
    """
    if not calls:
        return []

    by_source: dict[str, list[dict]] = {}
    for call in calls:
        src = call.get("source", "unknown")
        by_source.setdefault(src, []).append(call)

    sessions: list[dict[str, Any]] = []

    for source, source_calls in by_source.items():
        sorted_calls = sorted(source_calls, key=lambda c: c.get("ts", 0))
        session_calls: list[dict] = [sorted_calls[0]]

        for i in range(1, len(sorted_calls)):
            prev_ts = sorted_calls[i - 1].get("ts", 0)
            curr_ts = sorted_calls[i].get("ts", 0)

            if curr_ts - prev_ts > gap_seconds:
                sessions.append(_build_session(source, session_calls))
                session_calls = []

            session_calls.append(sorted_calls[i])

        if session_calls:
            sessions.append(_build_session(source, session_calls))

    sessions.sort(key=lambda s: s["start_ts"], reverse=True)
    return sessions


def _build_session(source: str, calls: list[dict]) -> dict[str, Any]:
    """Build a session dict from a group of calls."""
    start_ts = calls[0].get("ts", 0)
    end_ts = calls[-1].get("ts", 0)
    duration_minutes = max(0, (end_ts - start_ts) / 60)

    models: set[str] = set()
    total_cost = 0.0
    total_input = 0
    total_output = 0
    total_cache_read = 0

    for c in calls:
        models.add(c.get("model", "unknown"))
        total_cost += c.get("cost_usd", 0.0) or 0.0
        total_input += c.get("input_tokens", 0) or 0
        total_output += c.get("output_tokens", 0) or 0
        total_cache_read += c.get("cache_read_tokens", 0) or 0

    session_id = hashlib.sha256(
        f"{source}:{start_ts}:{end_ts}:{len(calls)}".encode()
    ).hexdigest()[:12]

    return {
        "session_id": session_id,
        "source": source,
        "start_ts": start_ts,
        "end_ts": end_ts,
        "start_time": datetime.fromtimestamp(start_ts, tz=timezone.utc).isoformat() if start_ts else None,
        "end_time": datetime.fromtimestamp(end_ts, tz=timezone.utc).isoformat() if end_ts else None,
        "duration_minutes": round(duration_minutes, 1),
        "call_count": len(calls),
        "total_cost_usd": total_cost,
        "models": sorted(models),
        "input_tokens": total_input,
        "output_tokens": total_output,
        "cache_read_tokens": total_cache_read,
    }
