from __future__ import annotations

from ..models import RepeatedBlock, WasteSource, WasteSummary


def build_waste_summary(total_input_tokens: int, repeated_blocks: list[RepeatedBlock]) -> WasteSummary:
    repeat_waste = sum(r.total_waste_tokens for r in repeated_blocks)

    sources: list[WasteSource] = []
    if repeat_waste > 0:
        sources.append(
            WasteSource(
                type="repeated_block",
                description=f"Repeated blocks across calls/messages ({len(repeated_blocks)} findings)",
                waste_tokens=repeat_waste,
                percentage_of_total=(repeat_waste / total_input_tokens * 100.0) if total_input_tokens else 0.0,
                priority_score=float(repeat_waste),
            )
        )

    waste_pct = (repeat_waste / total_input_tokens * 100.0) if total_input_tokens else 0.0
    return WasteSummary(total_waste_tokens=repeat_waste, waste_percentage=waste_pct, sources=sources)
