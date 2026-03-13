from __future__ import annotations

import re
from typing import Any

from ..models import AnalysisInput, Call, Message, StaticDynamicBreakdown
from .tokenizer import TokenCounter


# Heuristic patterns for static/dynamic classification
TEMPLATE_PATTERNS = [
    r"\{\{[^}]+\}\}",  # {{var}}
    r"\{[a-zA-Z_][a-zA-Z0-9_]*\}",  # {var}
    r"\$[a-zA-Z_][a-zA-Z0-9_]*",  # $var
]

# XML-style tags commonly used as static delimiters in prompts (not template vars)
_STATIC_XML_TAGS = {
    "instructions", "context", "output", "input", "system", "user",
    "assistant", "example", "examples", "response", "query", "document",
    "documents", "tools", "tool", "function", "functions", "rules",
    "constraints", "prompt", "task", "format", "schema", "thinking",
}


def _has_template_angle_brackets(text: str) -> bool:
    """Check for <var>-style template variables, excluding common XML delimiter tags."""
    for m in re.finditer(r"<([a-zA-Z_][a-zA-Z0-9_]*)>", text):
        if m.group(1).lower() not in _STATIC_XML_TAGS:
            return True
    return False

DYNAMIC_PATTERNS = [
    r"\d{4}-\d{2}-\d{2}",  # ISO dates
    r"\d{2}:\d{2}:\d{2}",  # times
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",  # UUIDs
    r"https?://[^?]+\?[^ ]+",  # URLs with query params
]

STATIC_INDICATORS = [
    r"^you are a?",
    r"^your role is",
    r"^your task is",
    r"^you are an expert",
    r"respond in",
    r"format as",
    r"output as",
    r"json schema",
    r"xml schema",
]

DYNAMIC_INDICATORS = [
    r"\buser[_\s]?name\b",
    r"\bemail\b",
    r"\baddress\b",
    r"history:",
    r"previous:",
    r"last message:",
]


def _find_patterns(text: str, patterns: list[str]) -> bool:
    """Check if any pattern matches in the text."""
    for p in patterns:
        if re.search(p, text, re.IGNORECASE):
            return True
    return False


def _common_prefix(strings: list[str]) -> str:
    """Find common prefix across all strings."""
    if not strings:
        return ""
    if len(strings) == 1:
        return strings[0]
    prefix = strings[0]
    for s in strings[1:]:
        while not s.startswith(prefix):
            prefix = prefix[:-1]
            if not prefix:
                return ""
    return prefix


def _common_suffix(strings: list[str]) -> str:
    """Find common suffix across all strings (reversed for easier handling)."""
    if not strings:
        return ""
    if len(strings) == 1:
        return strings[0]
    reversed_strings = [s[::-1] for s in strings]
    prefix = _common_prefix(reversed_strings)
    return prefix[::-1]


def _classify_by_heuristics(content: str, counter: TokenCounter) -> tuple[str, float]:
    """Classify a single block using heuristics."""
    # Check for template variables (high confidence dynamic)
    if _find_patterns(content, TEMPLATE_PATTERNS) or _has_template_angle_brackets(content):
        return "dynamic", 0.95

    # Check for timestamps, UUIDs, URLs with params (dynamic)
    if _find_patterns(content, DYNAMIC_PATTERNS):
        return "dynamic", 0.9

    # Check for user-specific data (dynamic)
    if _find_patterns(content, DYNAMIC_INDICATORS):
        return "dynamic", 0.7

    # Check for static indicators (instructions, persona, formatting)
    if _find_patterns(content, STATIC_INDICATORS):
        return "static", 0.85

    # Default: assume static for long instruction-like content
    token_count = counter.count(content)
    if token_count > 100:
        return "static", 0.5

    return "dynamic", 0.5  # default to dynamic for short ambiguous content


def _classify_multi_call(inp: AnalysisInput, counter: TokenCounter) -> list[dict[str, Any]]:
    """Classify content using diff-based detection across calls."""
    if not inp.calls or not inp.calls[0].messages:
        return []

    sections = []
    max_messages = max(len(call.messages) for call in inp.calls)

    for msg_idx in range(max_messages):
        # Collect content at this position across all calls
        contents: list[str] = []
        for call in inp.calls:
            if msg_idx < len(call.messages):
                contents.append(call.messages[msg_idx].content)

        if not contents:
            continue

        # Get role from first call
        role = inp.calls[0].messages[msg_idx].role if msg_idx < len(inp.calls[0].messages) else "unknown"

        unique_contents = set(contents)

        if len(unique_contents) == 1:
            # All identical -> static
            classification = "static"
            confidence = 1.0
            content_preview = contents[0][:100]
            sections.append({
                "classification": classification,
                "confidence": confidence,
                "token_count": counter.count(contents[0]),
                "content_preview": content_preview,
                "position": f"message_{msg_idx}",
                "role": role,
            })
        elif len(unique_contents) == len(contents):
            # All different -> dynamic; use average token count
            avg_tokens = sum(counter.count(c) for c in contents) // len(contents)
            sections.append({
                "classification": "dynamic",
                "confidence": 1.0,
                "token_count": avg_tokens,
                "content_preview": contents[0][:100],
                "position": f"message_{msg_idx}",
                "role": role,
            })
        else:
            # Partial variation - find common prefix/suffix
            prefix = _common_prefix(contents)
            suffix = _common_suffix(contents)

            if len(prefix) > 10:  # Only include meaningful prefix
                sections.append({
                    "classification": "static",
                    "confidence": 0.9,
                    "token_count": counter.count(prefix),
                    "content_preview": prefix[:100],
                    "position": f"message_{msg_idx}_prefix",
                    "role": role,
                })

            # Middle portion varies — clamp to 0 to avoid negatives
            middle_tokens = max(0, counter.count(contents[0]) - counter.count(prefix) - counter.count(suffix))
            sections.append({
                "classification": "dynamic",
                "confidence": 0.9,
                "token_count": middle_tokens,
                "content_preview": "[varies]",
                "position": f"message_{msg_idx}_varies",
                "role": role,
            })

            if len(suffix) > 10:  # Only include meaningful suffix
                sections.append({
                    "classification": "static",
                    "confidence": 0.9,
                    "token_count": counter.count(suffix),
                    "content_preview": suffix[:100],
                    "position": f"message_{msg_idx}_suffix",
                    "role": role,
                })

    return sections


def _classify_single_input(inp: AnalysisInput, counter: TokenCounter) -> list[dict[str, Any]]:
    """Classify content using heuristics for single prompts."""
    sections = []

    for call in inp.calls:
        for idx, msg in enumerate(call.messages):
            classification, confidence = _classify_by_heuristics(msg.content, counter)
            sections.append({
                "classification": classification,
                "confidence": confidence,
                "token_count": counter.count(msg.content),
                "content_preview": msg.content[:100],
                "position": f"call_{call.call_id or 0}_message_{idx}",
                "role": msg.role,
            })

    return sections


def classify_static_dynamic(inp: AnalysisInput) -> StaticDynamicBreakdown:
    """Main entry point for static/dynamic classification."""
    counter = TokenCounter()

    if inp.input_type == "multi_call_trace":
        sections = _classify_multi_call(inp, counter)
    else:
        sections = _classify_single_input(inp, counter)

    # Calculate totals
    total_static = sum(s["token_count"] for s in sections if s["classification"] == "static")
    total_dynamic = sum(s["token_count"] for s in sections if s["classification"] == "dynamic")
    total = total_static + total_dynamic

    static_percentage = (total_static / total * 100) if total > 0 else 0

    return StaticDynamicBreakdown(
        total_static_tokens=total_static,
        total_dynamic_tokens=total_dynamic,
        static_percentage=round(static_percentage, 1),
        sections=sections,
    )
