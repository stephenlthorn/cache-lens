from __future__ import annotations

from ..models import AnalysisInput, RepeatedBlock, Suggestion, WasteSummary


def build_suggestions(inp: AnalysisInput, repeated_blocks: list[RepeatedBlock], waste: WasteSummary) -> list[Suggestion]:
    out: list[Suggestion] = []

    if repeated_blocks:
        top = repeated_blocks[0]
        out.append(
            Suggestion(
                id="s1",
                type="consolidate_repeated",
                title="Extract repeated blocks into a shared prefix",
                description="One or more long blocks appear multiple times. Consolidate static instructions into a single system prefix to improve caching.",
                priority="high",
                estimated_savings_tokens=top.total_waste_tokens,
                estimated_savings_percentage=(top.total_waste_tokens / inp.calls[0].messages[0].token_count * 100.0)
                if inp.calls and inp.calls[0].messages and inp.calls[0].messages[0].token_count
                else 0.0,
                before_snippet=top.content_preview,
                after_snippet=None,
            )
        )

    return out
