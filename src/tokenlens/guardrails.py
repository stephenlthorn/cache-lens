# src/tokenlens/guardrails.py
"""Input/output guardrails for content filtering.

Scans text for PII patterns, prompt injection markers, and custom regex rules.
All detection is regex-based (no external dependencies).

Config structure (stored in settings table as JSON under key 'guardrails.config'):
{
    "pii_enabled": true,
    "injection_enabled": true,
    "custom_patterns": [
        {"name": "api_key", "pattern": "sk-[a-zA-Z0-9]{32,}", "action": "block"}
    ],
    "action": "warn"  # default action: "warn" (log + header) or "block" (reject request)
}
"""
from __future__ import annotations

import re
from typing import NamedTuple


class GuardrailConfig(NamedTuple):
    pii_enabled: bool
    injection_enabled: bool
    custom_patterns: list[dict]  # [{name, pattern, action?}]
    action: str  # "warn" or "block"


class GuardrailMatch(NamedTuple):
    pattern_type: str   # "pii", "injection", "custom"
    pattern_name: str   # "email", "phone", "ssn", etc.
    matched_text: str   # the actual matched substring
    action: str         # "warn" or "block"


def parse_guardrail_config(raw: dict | None) -> GuardrailConfig | None:
    """Parse a raw config dict into a GuardrailConfig. Returns None if not configured."""
    if not raw:
        return None
    return GuardrailConfig(
        pii_enabled=raw.get("pii_enabled", False),
        injection_enabled=raw.get("injection_enabled", False),
        custom_patterns=raw.get("custom_patterns") or [],
        action=raw.get("action", "warn"),
    )


# Built-in PII patterns
_PII_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("email", re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")),
    ("phone", re.compile(r"\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b")),
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("credit_card", re.compile(r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b")),
]


def scan_text(text: str, config: GuardrailConfig) -> list[GuardrailMatch]:
    """Scan text for guardrail violations. Returns list of matches."""
    matches: list[GuardrailMatch] = []

    if config.pii_enabled:
        for name, pattern in _PII_PATTERNS:
            for m in pattern.finditer(text):
                matches.append(GuardrailMatch(
                    pattern_type="pii",
                    pattern_name=name,
                    matched_text=m.group(),
                    action=config.action,
                ))

    if config.injection_enabled:
        matches.extend(_scan_injection(text, config.action))

    for rule in config.custom_patterns:
        rule_name = rule.get("name", "custom")
        rule_pattern = rule.get("pattern", "")
        rule_action = rule.get("action", config.action)
        if rule_pattern:
            try:
                compiled = re.compile(rule_pattern)
                for m in compiled.finditer(text):
                    matches.append(GuardrailMatch(
                        pattern_type="custom",
                        pattern_name=rule_name,
                        matched_text=m.group(),
                        action=rule_action,
                    ))
            except re.error:
                pass  # Skip invalid regex

    return matches


# Built-in prompt injection patterns
_INJECTION_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("ignore_instructions", re.compile(
        r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions|rules|guidelines)",
        re.IGNORECASE,
    )),
    ("system_prompt_leak", re.compile(
        r"(show|reveal|print|output|repeat)\s+(your\s+)?(system\s+prompt|instructions|rules)",
        re.IGNORECASE,
    )),
    ("role_override", re.compile(
        r"you\s+are\s+now\s+(DAN|a\s+new\s+AI|an?\s+unrestricted)",
        re.IGNORECASE,
    )),
    ("delimiter_injection", re.compile(
        r"```\s*system\s*\n|<\|im_start\|>system|<\|system\|>",
        re.IGNORECASE,
    )),
]


def _scan_injection(text: str, default_action: str) -> list[GuardrailMatch]:
    matches: list[GuardrailMatch] = []
    for name, pattern in _INJECTION_PATTERNS:
        for m in pattern.finditer(text):
            matches.append(GuardrailMatch(
                pattern_type="injection",
                pattern_name=name,
                matched_text=m.group(),
                action=default_action,
            ))
    return matches
