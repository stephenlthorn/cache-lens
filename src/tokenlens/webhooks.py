from __future__ import annotations

import httpx


async def dispatch_webhook(url: str, event: dict, timeout: float = 5.0) -> bool:
    """POST event JSON to URL. Returns True on 2xx, False on failure."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=event, timeout=timeout)
            return 200 <= resp.status_code < 300
    except Exception:
        return False


def should_fire_webhook(event_type: str, enabled_events: str) -> bool:
    """Check if event_type is in comma-separated enabled_events string."""
    if not enabled_events:
        return False
    return event_type in [e.strip() for e in enabled_events.split(",")]
