from __future__ import annotations

from ..models import RepeatedBlock, WasteSource, WasteSummary
from .helpers import stype, stokens


def build_waste_summary(
    total_input_tokens: int,
    repeated_blocks: list[RepeatedBlock],
    static_dynamic_sections: list[dict] | None = None,
) -> WasteSummary:
    """Build waste summary per PRODUCT_SPEC §5.5.

    Note: this stays deterministic and intentionally heuristic for MVP.
    """

    sources: list[WasteSource] = []

    # Split repeated blocks into large vs small to avoid double-counting
    small_blocks = [b for b in repeated_blocks if b.tokens_per_occurrence < 200]
    large_blocks = [b for b in repeated_blocks if b.tokens_per_occurrence >= 200]

    large_waste = sum(b.total_waste_tokens for b in large_blocks)
    small_waste = sum(b.total_waste_tokens for b in small_blocks)

    # 1) repeated_block (priority 1.0) — only large blocks
    if large_waste > 0:
        sources.append(
            WasteSource(
                type="repeated_block",
                description=f"Repeated blocks across calls/messages ({len(large_blocks)} findings)",
                waste_tokens=large_waste,
                percentage_of_total=(large_waste / total_input_tokens * 100.0) if total_input_tokens else 0.0,
                priority_score=float(large_waste) * 1.0,
                related_block_hash=large_blocks[0].content_hash if large_blocks else None,
            )
        )
    elif small_waste > 0 and len(small_blocks) < 2:
        # Single small repeated block: report as repeated_block (not redundant_instructions)
        sources.append(
            WasteSource(
                type="repeated_block",
                description=f"Repeated blocks across calls/messages ({len(small_blocks)} findings)",
                waste_tokens=small_waste,
                percentage_of_total=(small_waste / total_input_tokens * 100.0) if total_input_tokens else 0.0,
                priority_score=float(small_waste) * 1.0,
                related_block_hash=small_blocks[0].content_hash if small_blocks else None,
            )
        )

    sections = static_dynamic_sections or []

    # 2) misplaced_dynamic (priority 0.8)
    misplaced_tokens = 0
    if sections:
        found_dynamic = False
        for s in sections:
            if stype(s) == "dynamic":
                found_dynamic = True
            elif stype(s) == "static" and found_dynamic:
                misplaced_tokens += stokens(s)

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

    # 3) interleaved (priority 0.6) — include both sides of transitions
    interleave_waste = 0
    transitions = 0
    if len(sections) > 1:
        for i in range(1, len(sections)):
            if stype(sections[i]) != stype(sections[i - 1]):
                transitions += 1
                interleave_waste += stokens(sections[i])
                if i == 1:
                    interleave_waste += stokens(sections[0])

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

    # 4) oversized_context (priority 0.4)
    if sections and total_input_tokens > 0:
        half_total = total_input_tokens / 2
        for s in sections:
            tokens = stokens(s)
            if tokens > half_total:
                excess = int(tokens - half_total)
                sources.append(
                    WasteSource(
                        type="oversized_context",
                        description=f"Oversized message ({tokens} tokens, {tokens/total_input_tokens*100:.1f}% of total)",
                        waste_tokens=excess,
                        percentage_of_total=(excess / total_input_tokens * 100.0),
                        priority_score=float(excess) * 0.4,
                    )
                )

    # 5) redundant_instructions (priority 0.9) — small blocks only, no overlap with repeated_block
    if len(small_blocks) > 1:
        sources.append(
            WasteSource(
                type="redundant_instructions",
                description=f"Multiple small repeated blocks that may be redundant instructions ({len(small_blocks)} blocks)",
                waste_tokens=small_waste,
                percentage_of_total=(small_waste / total_input_tokens * 100.0) if total_input_tokens else 0.0,
                priority_score=float(small_waste) * 0.9,
            )
        )

    sources.sort(key=lambda x: x.priority_score, reverse=True)

    total_waste = sum(s.waste_tokens for s in sources)
    waste_pct = (total_waste / total_input_tokens * 100.0) if total_input_tokens else 0.0

    return WasteSummary(total_waste_tokens=total_waste, waste_percentage=waste_pct, sources=sources)
