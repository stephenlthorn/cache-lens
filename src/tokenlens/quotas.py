# src/tokenlens/quotas.py
"""Quota enforcement for per-source and per-model limits.

Config structure (stored in settings table as JSON under key 'quotas.config'):
{
    "source_limits": {
        "<source>": {
            "daily_limit_usd": <float>,      # optional
            "monthly_limit_usd": <float>,    # optional
        }
    },
    "model_limits": {
        "<model>": {
            "daily_call_limit": <int>,       # optional
        }
    },
    "kill_switches": ["<source>", ...]       # paused sources
}
"""
from __future__ import annotations

from typing import NamedTuple


class QuotaResult(NamedTuple):
    allowed: bool
    reason: str
    retry_after: int  # seconds; 0 if allowed


def check_quotas(
    *,
    config: dict,
    source: str,
    model: str,
    source_daily_spend: float,
    source_monthly_spend: float,
    model_calls_today: int,
) -> QuotaResult:
    """Check whether a request is allowed under the configured quotas.

    Returns QuotaResult with allowed=True if the request should proceed,
    or allowed=False with a human-readable reason and Retry-After value.
    """
    # 1. Kill switch — immediate block
    kill_switches = config.get("kill_switches") or []
    if source in kill_switches:
        return QuotaResult(
            allowed=False,
            reason="source paused via kill switch",
            retry_after=3600,
        )

    # 2. Per-source spend caps
    source_limits = (config.get("source_limits") or {}).get(source, {})
    daily_cap = source_limits.get("daily_limit_usd")
    if daily_cap is not None and source_daily_spend >= daily_cap:
        return QuotaResult(
            allowed=False,
            reason=f"source '{source}' daily spend ${source_daily_spend:.2f} >= cap ${daily_cap:.2f}",
            retry_after=3600,
        )
    monthly_cap = source_limits.get("monthly_limit_usd")
    if monthly_cap is not None and source_monthly_spend >= monthly_cap:
        return QuotaResult(
            allowed=False,
            reason=f"source '{source}' monthly spend ${source_monthly_spend:.2f} >= cap ${monthly_cap:.2f}",
            retry_after=3600,
        )

    # 3. Per-model call caps
    model_limits = (config.get("model_limits") or {}).get(model, {})
    daily_call_limit = model_limits.get("daily_call_limit")
    if daily_call_limit is not None and model_calls_today >= daily_call_limit:
        return QuotaResult(
            allowed=False,
            reason=f"model '{model}' daily calls {model_calls_today} >= limit {daily_call_limit}",
            retry_after=3600,
        )

    return QuotaResult(allowed=True, reason="", retry_after=0)
