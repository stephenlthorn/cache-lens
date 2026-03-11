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

    # Collect dynamic content (user messages that are not repeated)
    dynamic_contents: list[tuple[str, str]] = []  # (role, content)
    for call in inp.calls:
        for msg in call.messages:
            # Skip if this content is already in static
            if any(msg.content == sc for sc in static_contents):
                continue
            dynamic_contents.append((msg.role, msg.content))

    # Add dynamic messages
    seen_dynamic = set()
    for role, content in dynamic_contents:
        content_key = (role, content)
        if content_key in seen_dynamic:
            continue
        seen_dynamic.add(content_key)
        
        # System messages are considered static by default
        section_type = "static" if role == "system" else "dynamic"
        
        optimized_messages.append({
            "role": role,
            "content": content,
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
        savings_per_call=max(0, savings),
    )
