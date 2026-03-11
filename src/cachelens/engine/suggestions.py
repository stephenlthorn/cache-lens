from __future__ import annotations

from ..models import AnalysisInput, RepeatedBlock, Suggestion, WasteSummary, StaticDynamicBreakdown


def build_suggestions(
    inp: AnalysisInput, 
    repeated_blocks: list[RepeatedBlock], 
    waste: WasteSummary,
    static_dynamic: StaticDynamicBreakdown | None = None,
) -> list[Suggestion]:
    """
    Generate concrete restructuring suggestions from section 5.6.
    
    Rules:
    1. Consolidate repeated blocks: Extract to system prompt prefix
    2. Reorder for cache efficiency: Move static before dynamic
    3. Merge fragmented statics: Combine small static blocks
    4. Extract templates: Dynamic content following patterns
    5. Trim redundant instructions: Consolidate near-duplicates
    """
    out: list[Suggestion] = []
    sections = static_dynamic.sections if static_dynamic else []
    
    # 1. Consolidate repeated blocks
    if repeated_blocks:
        # Get the largest repeated block
        top = max(repeated_blocks, key=lambda b: b.total_waste_tokens)
        
        # Create before/after example
        before = top.content_preview[:200] + "..." if len(top.content_preview) > 200 else top.content_preview
        after = f"[SYSTEM PROMPT]\n{top.content_preview[:100]}...\n\n[PER-CALL DYNAMIC]"
        
        out.append(
            Suggestion(
                id="s1",
                type="consolidate_repeated",
                title="Extract repeated blocks into a shared prefix",
                description=f"Found {len(repeated_blocks)} repeated block(s) appearing {top.occurrences} times. "
                           f"Moving this to a system prompt prefix would save ~{top.total_waste_tokens} tokens per request.",
                priority="high",
                estimated_savings_tokens=top.total_waste_tokens,
                estimated_savings_percentage=(
                    top.total_waste_tokens / (inp.calls[0].messages[0].token_count or 1) * 100.0
                ) if inp.calls and inp.calls[0].messages else 0.0,
                before_snippet=before,
                after_snippet=after,
            )
        )
        
        # 5. Trim redundant instructions (if multiple small blocks)
        small_blocks = [b for b in repeated_blocks if b.tokens_per_occurrence < 200]
        if len(small_blocks) > 1:
            total_small_waste = sum(b.total_waste_tokens for b in small_blocks)
            out.append(
                Suggestion(
                    id="s5",
                    type="trim_redundant_instructions",
                    title="Consolidate small repeated instruction blocks",
                    description=f"Found {len(small_blocks)} small instruction blocks that repeat. "
                               f"Consider merging them into fewer, larger instruction sets.",
                    priority="medium",
                    estimated_savings_tokens=total_small_waste,
                    estimated_savings_percentage=(
                        total_small_waste / 1000 * 10  # Rough estimate
                    ),
                    before_snippet="\n".join(b.content_preview[:80] for b in small_blocks[:3]),
                    after_snippet="[CONSOLIDATED INSTRUCTIONS]",
                )
            )
    
    # 2. Reorder for cache efficiency (if dynamic before static)
    if sections:
        has_dynamic_before_static = False
        dynamic_before_tokens = 0
        first_static_idx = None
        first_dynamic_idx = None
        
        for i, section in enumerate(sections):
            if section.get("type") == "static" and first_static_idx is None:
                first_static_idx = i
            if section.get("type") == "dynamic" and first_dynamic_idx is None:
                first_dynamic_idx = i
                if first_static_idx is not None and i > first_static_idx:
                    has_dynamic_before_static = True
                    break
        
        if first_dynamic_idx is not None and first_static_idx is not None:
            if first_dynamic_idx < first_static_idx:
                # Dynamic comes before static - need to reorder
                # Find the dynamic tokens before static
                for section in sections[:first_static_idx]:
                    if section.get("type") == "dynamic":
                        dynamic_before_tokens += section.get("tokens", 0)
                
                if dynamic_before_tokens > 0:
                    out.append(
                        Suggestion(
                            id="s2",
                            type="reorder_for_cache",
                            title="Move static content to the beginning",
                            description="Dynamic content appears before static content. "
                                       "For optimal caching, static content should come first.",
                            priority="high",
                            estimated_savings_tokens=dynamic_before_tokens // 2,  # Rough estimate
                            estimated_savings_percentage=5.0,
                            before_snippet="[dynamic content...]\n[static content...]",
                            after_snippet="[static content...]\n[dynamic content...]",
                        )
                    )
    
    # 3. Merge fragmented statics (if multiple short static blocks)
    if sections:
        static_sections = [s for s in sections if s.get("type") == "static" and s.get("tokens", 0) < 100]
        if len(static_sections) >= 2:
            fragmented_tokens = sum(s.get("tokens", 0) for s in static_sections)
            out.append(
                Suggestion(
                    id="s3",
                    type="merge_fragmented_statics",
                    title="Merge fragmented static blocks",
                    description=f"Found {len(static_sections)} small static blocks (<100 tokens). "
                               f"Merging them into a single contiguous block would improve cache efficiency.",
                    priority="medium",
                    estimated_savings_tokens=fragmented_tokens // 2,  # Rough estimate
                    estimated_savings_percentage=2.0,
                    before_snippet="[static A]\n[dynamic]\n[static B]",
                    after_snippet="[static A + B]\n[dynamic]",
                )
            )
    
    # 4. Extract templates (if repeated dynamic patterns) - stub
    # This would require more sophisticated pattern detection
    
    # Sort by priority
    priority_order = {"high": 0, "medium": 1, "low": 2}
    out.sort(key=lambda s: priority_order.get(s.priority, 2))
    
    return out
