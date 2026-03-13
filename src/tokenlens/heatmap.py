"""Token heatmap — classify input tokens into labeled sections.

Sections:
  system_prompt       role='system' (first occurrence)
  tool_definitions    tools/functions array in request body
  context             content with <context>/<documents>/<retrieved> markers, or large mid-conversation blocks
  conversation_history  all user/assistant messages except last user message
  user_query          last user-role message
  other               anything unclassified
"""
from __future__ import annotations

import json
import re

import tiktoken

_TOKENIZER = tiktoken.get_encoding("cl100k_base")
_CONTEXT_RE = re.compile(r"<(context|documents|retrieved|doc)[^>]*>", re.IGNORECASE)
# Matches full tagged context blocks, e.g. <context>...</context>
_CONTEXT_BLOCK_RE = re.compile(
    r"<(context|documents|retrieved|doc)[^>]*>.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)


def _tok(text: str) -> int:
    if not text:
        return 0
    return len(_TOKENIZER.encode(text))


def _message_text(msg: dict) -> str:
    content = msg.get("content") or ""
    if isinstance(content, str):
        return content
    # Handle list-of-blocks format (Anthropic)
    if isinstance(content, list):
        return " ".join(
            block.get("text", "") for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


def compute_heatmap(
    messages: list[dict],
    tools: list | None,
    provider: str,
) -> dict:
    """Classify tokens in a request into labeled sections.

    Returns a dict:
        {system_prompt, tool_definitions, context, conversation_history, user_query, other, total}
    """
    counts = {
        "system_prompt": 0,
        "tool_definitions": 0,
        "context": 0,
        "conversation_history": 0,
        "user_query": 0,
        "other": 0,
        "total": 0,
    }

    if not messages:
        return counts

    # Tool definitions
    if tools:
        try:
            tools_str = json.dumps(tools)
            counts["tool_definitions"] = _tok(tools_str)
        except Exception:
            pass

    # Find last user message index
    last_user_idx = -1
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            last_user_idx = i
            break

    first_system_seen = False
    for i, msg in enumerate(messages):
        role = msg.get("role", "")
        text = _message_text(msg)
        token_count = _tok(text)

        if role == "system" and not first_system_seen:
            counts["system_prompt"] += token_count
            first_system_seen = True
        elif i == last_user_idx:
            # Check for context markers in user query
            if _CONTEXT_RE.search(text):
                # Extract tagged context blocks and count their tokens as context
                context_tokens = sum(
                    _tok(match.group(0))
                    for match in _CONTEXT_BLOCK_RE.finditer(text)
                )
                # Remaining text outside the blocks counts as user query
                query_text = _CONTEXT_BLOCK_RE.sub("", text).strip()
                query_tokens = _tok(query_text) if query_text else 0
                # If no full closing tags found, fall back to length-based heuristic
                if context_tokens == 0:
                    context_part = text[:max(0, len(text) - 100)]
                    query_part = text[-min(100, len(text)):]
                    context_tokens = _tok(context_part)
                    query_tokens = _tok(query_part)
                counts["context"] += context_tokens
                counts["user_query"] += query_tokens
            else:
                counts["user_query"] += token_count
        elif role in ("user", "assistant"):
            counts["conversation_history"] += token_count
        else:
            counts["other"] += token_count

    counts["total"] = sum(
        counts[k] for k in ("system_prompt", "tool_definitions", "context",
                            "conversation_history", "user_query", "other")
    )
    return counts
