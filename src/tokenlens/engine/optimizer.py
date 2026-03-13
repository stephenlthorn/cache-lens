from __future__ import annotations

from ..models import AnalysisInput, OptimizedStructure, RepeatedBlock
from .tokenizer import TokenCounter


def build_optimized_structure(
    inp: AnalysisInput,
    repeated_blocks: list[RepeatedBlock],
    counter: TokenCounter,
) -> OptimizedStructure:
    """
    Build an optimized message structure that maximizes cacheability.
    
    Strategy:
    1. Extract repeated/static content from repeated blocks into a single system message
    2. Move static content earlier in the message array
    3. Keep dynamic user content at the end
    """
    if not inp.calls:
        return OptimizedStructure(
            description="No calls to optimize",
            messages=[],
        )

    first_call = inp.calls[0]
    if not first_call.messages:
        return OptimizedStructure(
            description="No messages to optimize",
            messages=[],
        )

    # Collect static content from repeated blocks
    static_contents: list[str] = []
    for rb in repeated_blocks:
        if rb.content_full and rb.content_full not in static_contents:
            static_contents.append(rb.content_full)

    # Calculate original tokens per call
    original_tokens = sum(counter.count(m.content) for m in first_call.messages)

    # Build optimized messages list
    optimized_messages: list[dict[str, str]] = []

    # Add system message with static content if we have repeated blocks
    if static_contents:
        combined_static = "\n\n---\n\n".join(static_contents)
        optimized_messages.append({
            "role": "system",
            "content": combined_static,
            "section_type": "static",
        })

    # Build a set of static content for fast lookup
    static_set = set(static_contents)

    # Collect dynamic content from the first call only (representative per-call structure)
    for msg in first_call.messages:
        # Skip if entire message matches a static block
        if msg.content in static_set:
            continue
        # Skip if message is fully composed of extracted static blocks
        from .repeats import split_into_blocks
        msg_blocks = split_into_blocks(msg.content)
        if msg_blocks and all(b in static_set for b in msg_blocks):
            continue
        section_type = "static" if msg.role == "system" else "dynamic"
        optimized_messages.append({
            "role": msg.role,
            "content": msg.content,
            "section_type": section_type,
        })

    # Calculate estimated tokens per call
    if optimized_messages:
        estimated_tokens = sum(counter.count(m["content"]) for m in optimized_messages)
    else:
        estimated_tokens = original_tokens

    savings = original_tokens - estimated_tokens

    return OptimizedStructure(
        description="Restructured for optimal prefix caching",
        messages=optimized_messages,
        estimated_tokens_per_call=estimated_tokens,
        original_tokens_per_call=original_tokens,
        savings_per_call=savings,
    )
