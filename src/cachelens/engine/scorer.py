from __future__ import annotations

from ..models import AnalysisInput, RepeatedBlock, StaticDynamicBreakdown


def _detect_static_dynamic(inp: AnalysisInput, repeated_blocks: list[RepeatedBlock]) -> StaticDynamicBreakdown:
    """
    Simple heuristic-based static/dynamic detection.
    - Content that appears in repeated blocks is considered static
    - All other content is considered dynamic
    """
    # Build set of repeated block hashes
    static_hashes = {block.content_hash for block in repeated_blocks}
    
    total_static = 0
    total_dynamic = 0
    sections = []
    
    for call_idx, call in enumerate(inp.calls):
        for msg_idx, msg in enumerate(call.messages):
            tokens = msg.token_count or 0
            if tokens == 0:
                continue
                
            # Check if this message content matches any repeated block
            is_static = False
            section_type = "dynamic"
            
            for block in repeated_blocks:
                if block.content_full == msg.content:
                    is_static = True
                    section_type = "static"
                    break
            
            # Also check if content starts with any repeated block (prefix match)
            if not is_static:
                for block in repeated_blocks:
                    if msg.content.startswith(block.content_full[:50]):
                        is_static = True
                        section_type = "static"
                        break
            
            if is_static:
                total_static += tokens
            else:
                total_dynamic += tokens
                
            sections.append({
                "call_index": call_idx,
                "message_index": msg_idx,
                "role": msg.role,
                "type": section_type,
                "tokens": tokens,
                "content_preview": msg.content[:100] if msg.content else "",
            })
    
    total = total_static + total_dynamic
    static_pct = (total_static / total * 100.0) if total > 0 else 0.0
    
    return StaticDynamicBreakdown(
        total_static_tokens=total_static,
        total_dynamic_tokens=total_dynamic,
        static_percentage=static_pct,
        sections=sections,
    )


def _has_static_prefix(sections: list[dict]) -> bool:
    """Check if the first section is static (static content at the beginning)."""
    if not sections:
        return False
    return sections[0].get("type") == "static"


def _count_static_dynamic_transitions(sections: list[dict]) -> int:
    """Count the number of times static/dynamic alternates."""
    if len(sections) < 2:
        return 0
    transitions = 0
    for i in range(1, len(sections)):
        if sections[i].get("type") != sections[i-1].get("type"):
            transitions += 1
    return transitions


def _count_short_static_blocks(sections: list[dict], threshold: int = 100) -> int:
    """Count static blocks below the threshold token count."""
    count = 0
    in_static = False
    static_tokens = 0
    
    for section in sections:
        if section.get("type") == "static":
            static_tokens += section.get("tokens", 0)
            in_static = True
        else:
            if in_static and static_tokens > 0 and static_tokens < threshold:
                count += 1
            in_static = False
            static_tokens = 0
    
    # Check last block
    if in_static and static_tokens > 0 and static_tokens < threshold:
        count += 1
    
    return count


def _calculate_static_prefix_ratio(sections: list[dict]) -> float:
    """
    Calculate ratio of static tokens that appear in the prefix (before first dynamic).
    Higher = better for caching.
    """
    if not sections:
        return 0.0
    
    total_static_tokens = sum(s.get("tokens", 0) for s in sections if s.get("type") == "static")
    if total_static_tokens == 0:
        return 0.0
    
    prefix_static = 0
    found_dynamic = False
    
    for section in sections:
        if section.get("type") == "dynamic":
            found_dynamic = True
        elif section.get("type") == "static" and not found_dynamic:
            prefix_static += section.get("tokens", 0)
    
    return prefix_static / total_static_tokens if total_static_tokens > 0 else 0.0


def cacheability_score(
    inp: AnalysisInput,
    repeated_blocks: list[RepeatedBlock],
    static_dynamic: StaticDynamicBreakdown,
) -> tuple[int, dict[str, int], str]:
    """
    Calculate cacheability score from 0 (uncacheable) to 100 (perfectly structured).
    
    Penalties:
    - static_prefix_penalty: -30 max (static content not at beginning)
    - repetition_penalty: -25 max (repeated blocks across calls)
    - interleave_penalty: -20 max (dynamic content interleaved with static)
    - no_prefix_penalty: -15 (no clear static prefix)
    - fragmentation_penalty: -10 max (very short static blocks <100 tokens)
    """
    # Detect static/dynamic if not provided
    if not static_dynamic or not static_dynamic.sections:
        static_dynamic = _detect_static_dynamic(inp, repeated_blocks)
    
    total_input = sum(
        m.token_count or 0 
        for call in inp.calls 
        for m in call.messages
    )
    
    repeat_waste = sum(r.total_waste_tokens for r in repeated_blocks)
    sections = static_dynamic.sections or []
    
    # Check if there's any static content
    has_static = any(s.get("type") == "static" for s in sections)
    total_static_tokens = sum(s.get("tokens", 0) for s in sections if s.get("type") == "static")
    
    # Start with perfect score
    score = 100
    
    # 1. Static prefix penalty (-30 max)
    # Static content should come first for optimal caching
    # Only apply if there's static content
    static_prefix_penalty = 0
    if has_static:
        static_prefix_ratio = _calculate_static_prefix_ratio(sections)
        static_prefix_penalty = int((1 - static_prefix_ratio) * 30)
    
    # 2. Repetition penalty (-25 max)
    # Repeated blocks across calls waste cache
    repetition_penalty = 0
    if total_input > 0:
        repeat_waste_ratio = repeat_waste / total_input
        repetition_penalty = int(min(repeat_waste_ratio * 100, 25))
    
    # 3. Interleaving penalty (-20 max)
    # Static/dynamic alternating frequently reduces cache efficiency
    # Only apply if there's both static and dynamic
    interleave_penalty = 0
    has_dynamic = any(s.get("type") == "dynamic" for s in sections)
    if has_static and has_dynamic:
        interleave_count = _count_static_dynamic_transitions(sections)
        interleave_penalty = int(min(interleave_count * 5, 20))
    
    # 4. No prefix penalty (-15)
    # No clear static prefix at all (but only if there's static content that should be at prefix)
    no_prefix_penalty = 0
    if has_static and not _has_static_prefix(sections):
        no_prefix_penalty = 15
    
    # 5. Fragmentation penalty (-10 max)
    # Very short static blocks that could be merged
    # Only apply if there's static content
    fragmentation_penalty = 0
    if has_static:
        fragmented_statics = _count_short_static_blocks(sections, threshold=100)
        fragmentation_penalty = int(min(fragmented_statics * 2, 10))
    
    # Apply all penalties
    score -= static_prefix_penalty
    score -= repetition_penalty
    score -= interleave_penalty
    score -= no_prefix_penalty
    score -= fragmentation_penalty
    
    breakdown = {
        "static_prefix_penalty": -static_prefix_penalty,
        "repetition_penalty": -repetition_penalty,
        "interleave_penalty": -interleave_penalty,
        "no_prefix_penalty": -no_prefix_penalty,
        "fragmentation_penalty": -fragmentation_penalty,
    }
    
    # Determine label based on score
    if score >= 80:
        label = "Excellent"
    elif score >= 60:
        label = "Good"
    elif score >= 40:
        label = "Fair"
    elif score >= 20:
        label = "Poor"
    else:
        label = "Critical"
    
    return max(0, score), breakdown, label
