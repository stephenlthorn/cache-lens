from __future__ import annotations

from ..models import AnalysisInput, RepeatedBlock, WasteSource, WasteSummary


def build_waste_summary(
    total_input_tokens: int, 
    repeated_blocks: list[RepeatedBlock],
    static_dynamic_sections: list[dict] | None = None,
) -> WasteSummary:
    """
    Build waste summary with all waste source types from section 5.5.
    
    Waste types:
    - repeated_block: Same content in multiple calls (priority 1.0)
    - misplaced_dynamic: Dynamic content before static content (priority 0.8)
    - interleaved: Static/dynamic alternating frequently (priority 0.6)
    - oversized_context: Single message > 50% of total tokens (priority 0.4)
    - redundant_instructions: Near-duplicate instruction blocks (priority 0.9)
    """
    sources: list[WasteSource] = []
    
    # 1. Repeated blocks (priority 1.0)
    repeat_waste = sum(r.total_waste_tokens for r in repeated_blocks)
    if repeat_waste > 0:
        sources.append(
            WasteSource(
                type="repeated_block",
                description=f"Repeated blocks across calls/messages ({len(repeated_blocks)} findings)",
                waste_tokens=repeat_waste,
                percentage_of_total=(repeat_waste / total_input_tokens * 100.0) if total_input_tokens else 0.0,
                priority_score=float(repeat_waste) * 1.0,
                related_block_hash=repeated_blocks[0].content_hash if repeated_blocks else None,
            )
        )
    
    # 2. Misplaced dynamic content (priority 0.8)
    # Dynamic content appearing before static content
    misplaced_tokens = 0
    if static_dynamic_sections:
        found_dynamic_before_static = False
        for section in static_dynamic_sections:
            if section.get("type") == "dynamic":
                found_dynamic_before_static = True
            elif section.get("type") == "static" and found_dynamic_before_static:
                # This static content comes after dynamic - that's misplaced
                misplaced_tokens += section.get("tokens", 0)
        
        if misplaced_tokens > 0:
            sources.append(
                WasteSource(
                    type="misplaced_dynamic",
                    description="Dynamic content appears before static content",
                    waste_tokens=misplaced_tokens,
                    percentage_of_total=(misplaced_tokens / total_input_tokens * 100.0) if total_input_tokens else 0.0,
                    priority_score=float(misplaced_tokens) * 0.8,
                )
            )
    
    # 3. Interleaved static/dynamic (priority 0.6)
    # Count transitions between static and dynamic
    interleave_waste = 0
    if static_dynamic_sections and len(static_dynamic_sections) > 1:
        transitions = 0
        for i in range(1, len(static_dynamic_sections)):
            if static_dynamic_sections[i].get("type") != static_dynamic_sections[i-1].get("type"):
                transitions += 1
                interleave_waste += static_dynamic_sections[i].get("tokens", 0)
        
        if interleave_waste > 0:
            sources.append(
                WasteSource(
                    type="interleaved",
                    description=f"Static/dynamic content interleaved ({transitions} transitions)",
                    waste_tokens=interleave_waste,
                    percentage_of_total=(interleave_waste / total_input_tokens * 100.0) if total_input_tokens else 0.0,
                    priority_score=float(interleave_waste) * 0.6,
                )
            )
    
    # 4. Oversized context (priority 0.4)
    # Single message > 50% of total tokens
    if static_dynamic_sections:
        half_total = total_input_tokens / 2
        for section in static_dynamic_sections:
            tokens = section.get("tokens", 0)
            if tokens > half_total:
                excess = int(tokens - half_total)
                sources.append(
                    WasteSource(
                        type="oversized_context",
                        description=f"Oversized message ({tokens} tokens, {tokens/total_input_tokens*100:.1f}% of total)",
                        waste_tokens=excess,
                        percentage_of_total=(excess / total_input_tokens * 100.0) if total_input_tokens else 0.0,
                        priority_score=float(excess) * 0.4,
                    )
                )
    
    # 5. Redundant instructions (priority 0.9) - stub
    # Near-duplicate instruction blocks (>80% similarity)
    # This would require more sophisticated similarity detection
    # For now, we check if there are multiple repeated blocks that might be instructions
    redundant_tokens = 0
    if len(repeated_blocks) > 1:
        # Multiple different repeated blocks could indicate redundant instructions
        for block in repeated_blocks:
            if block.tokens_per_occurrence < 200:  # Small blocks might be instructions
                redundant_tokens += block.total_waste_tokens
        
        if redundant_tokens > 0:
            sources.append(
                WasteSource(
                    type="redundant_instructions",
                    description=f"Multiple small repeated blocks that may be redundant instructions ({len(repeated_blocks)} blocks)",
                    waste_tokens=redundant_tokens,
                    percentage_of_total=(redundant_tokens / total_input_tokens * 100.0) if total_input_tokens else 0.0,
                    priority_score=float(redundant_tokens) * 0.9,
                )
            )
    
    # Sort by priority score descending
    sources.sort(key=lambda x: x.priority_score, reverse=True)
    
    # Calculate total waste
    total_waste = sum(s.waste_tokens for s in sources)
    waste_pct = (total_waste / total_input_tokens * 100.0) if total_input_tokens else 0.0
    
    return WasteSummary(
        total_waste_tokens=total_waste,
        waste_percentage=waste_pct,
        sources=sources,
    )
