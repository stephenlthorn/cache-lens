"""Junk token detector for TokenLens v2.

Detects four waste types in AI API request bodies:
- whitespace: excessive newlines/spaces
- polite_filler: social niceties in system prompts
- redundant_instruction: same block appearing 2+ times
- empty_message: messages with <5 tokens

Token counts use tiktoken (cl100k_base) as cross-provider approximation.
Accuracy: 5-15% drift for non-OpenAI providers vs. provider-reported counts.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

import tiktoken

_TOKENIZER = tiktoken.get_encoding("cl100k_base")

_FILLER_PHRASES = [
    "certainly!", "certainly,", "i'd be happy to", "i would be happy to",
    "sure thing!", "of course!", "of course,", "great question!",
    "absolutely!", "absolutely,", "definitely!", "definitely,",
    "i'm here to help", "i am here to help", "feel free to",
    "i'd love to", "i would love to", "no problem!", "no problem,",
    "happy to help", "glad to help", "by all means", "with pleasure",
    "without a doubt", "to clarify,", "as i mentioned",
    "as mentioned", "as previously stated", "let me help you with that",
]

_EXCESS_NEWLINES = re.compile(r"\n{3,}")
_TRAILING_SPACES = re.compile(r"[ \t]{3,}")


@dataclass(frozen=True)
class WasteItem:
    waste_type: str  # 'whitespace' | 'polite_filler' | 'redundant_instruction' | 'empty_message'
    waste_tokens: int
    savings_usd: float
    detail: str  # JSON string with match context


def _count_tokens(text: str) -> int:
    return len(_TOKENIZER.encode(text))


def _estimate_savings(waste_tokens: int, provider: str) -> float:
    """Rough USD savings estimate: uses Sonnet-level input pricing as baseline."""
    # ~$3 per million input tokens (sonnet pricing)
    return waste_tokens * 3.0 / 1_000_000


def _detect_whitespace(messages: list[dict], provider: str) -> list[WasteItem]:
    items: list[WasteItem] = []
    for i, msg in enumerate(messages):
        content = msg.get("content") or ""
        if not isinstance(content, str):
            continue
        # Count excess newlines: each run of 3+ newlines has (len-2) excess
        excess_nl = sum(len(m.group()) - 2 for m in _EXCESS_NEWLINES.finditer(content))
        # Count excess spaces: each run of 3+ spaces has (len-2) excess
        excess_sp = sum(len(m.group()) - 2 for m in _TRAILING_SPACES.finditer(content))
        total_excess_chars = excess_nl + excess_sp
        if total_excess_chars < 5:
            continue
        # Rough token estimate: ~4 chars/token, minimum 1
        waste_tokens = max(1, total_excess_chars // 4)
        items.append(WasteItem(
            waste_type="whitespace",
            waste_tokens=waste_tokens,
            savings_usd=_estimate_savings(waste_tokens, provider),
            detail=json.dumps({"location": f"message[{i}]", "excess_chars": total_excess_chars}),
        ))
    return items


def _detect_polite_filler(messages: list[dict], provider: str) -> list[WasteItem]:
    """Detect polite filler ONLY in system-role messages."""
    items: list[WasteItem] = []
    for i, msg in enumerate(messages):
        if msg.get("role") != "system":
            continue
        content = msg.get("content") or ""
        if not isinstance(content, str):
            continue
        content_lower = content.lower()
        matched = [p for p in _FILLER_PHRASES if p in content_lower]
        if not matched:
            continue
        # Estimate tokens: sum of matched phrase token counts
        waste_tokens = sum(_count_tokens(p) for p in matched)
        if waste_tokens < 1:
            continue
        items.append(WasteItem(
            waste_type="polite_filler",
            waste_tokens=waste_tokens,
            savings_usd=_estimate_savings(waste_tokens, provider),
            detail=json.dumps({"location": f"message[{i}]", "matched": matched[:5]}),
        ))
    return items


def _ngrams_from_content(content: str, min_len: int = 50) -> list[str]:
    """Extract overlapping substrings of min_len+ chars from content.

    Splits only on newlines (preserving sentence structure with periods),
    then generates sliding-window ngrams across whitespace-delimited tokens
    to find repeated multi-word phrases.
    """
    ngrams: list[str] = []
    lines = re.split(r"\n+", content)
    for line in lines:
        line = line.strip()
        if len(line) >= min_len:
            ngrams.append(line)
            continue
    # Also try whole-content sliding window over word tokens
    words = content.split()
    for start in range(len(words)):
        for end in range(start + 5, len(words) + 1):
            phrase = " ".join(words[start:end])
            if len(phrase) >= min_len:
                ngrams.append(phrase)
                break  # Only need smallest match starting at this word
    return ngrams


def _detect_redundant_instructions(messages: list[dict], provider: str) -> list[WasteItem]:
    """Detect identical instruction blocks (50+ chars) appearing in 2+ messages."""
    all_content = [
        (i, msg.get("content") or "")
        for i, msg in enumerate(messages)
        if isinstance(msg.get("content"), str) and msg.get("content")
    ]

    blocks: dict[str, list[int]] = {}
    for i, content in all_content:
        for phrase in _ngrams_from_content(content):
            locs = blocks.setdefault(phrase, [])
            if i not in locs:
                locs.append(i)

    # Collect candidates: phrases appearing in 2+ distinct messages
    candidates = [
        (block, locs)
        for block, locs in blocks.items()
        if len(locs) >= 2
    ]
    # Sort longest-first so superstrings are processed before substrings
    candidates.sort(key=lambda x: len(x[0]), reverse=True)

    items: list[WasteItem] = []
    accepted: list[str] = []
    for block, locs in candidates:
        # Skip if this block is a substring of an already-accepted longer block
        if any(block in longer for longer in accepted):
            continue
        accepted.append(block)
        waste_tokens = _count_tokens(block) * (len(locs) - 1)
        items.append(WasteItem(
            waste_type="redundant_instruction",
            waste_tokens=waste_tokens,
            savings_usd=_estimate_savings(waste_tokens, provider),
            detail=json.dumps({"locations": locs, "snippet": block[:100]}),
        ))
    return items


def _detect_empty_messages(messages: list[dict], provider: str) -> list[WasteItem]:
    """Detect messages with < 5 tokens of content."""
    items: list[WasteItem] = []
    for i, msg in enumerate(messages):
        content = msg.get("content")
        if content is None or not isinstance(content, str):
            continue
        tok_count = _count_tokens(content)
        if tok_count < 5:
            items.append(WasteItem(
                waste_type="empty_message",
                waste_tokens=tok_count,
                savings_usd=_estimate_savings(tok_count, provider),
                detail=json.dumps({"location": f"message[{i}]", "tokens": tok_count}),
            ))
    return items


def detect_waste(request_body: dict, provider: str) -> list[WasteItem]:
    """Detect waste in an AI API request body.

    Args:
        request_body: Parsed JSON request body dict.
        provider: Provider name ('anthropic', 'openai', 'google').

    Returns:
        List of WasteItem instances. Empty list if no waste detected.
    """
    messages = request_body.get("messages")
    if not messages or not isinstance(messages, list):
        return []

    items: list[WasteItem] = []
    items.extend(_detect_whitespace(messages, provider))
    items.extend(_detect_polite_filler(messages, provider))
    items.extend(_detect_redundant_instructions(messages, provider))
    items.extend(_detect_empty_messages(messages, provider))
    return items
