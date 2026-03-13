from __future__ import annotations

from ..models import AnalysisInput, RepeatedBlock, StaticDynamicBreakdown, Suggestion, WasteSummary
from .helpers import stype as _stype, stokens as _stokens


def build_suggestions(
    inp: AnalysisInput,
    repeated_blocks: list[RepeatedBlock],
    waste: WasteSummary,
    static_dynamic: StaticDynamicBreakdown | None = None,
) -> list[Suggestion]:
    """Generate concrete suggestions per PRODUCT_SPEC §5.6."""

    out: list[Suggestion] = []
    sections = static_dynamic.sections if static_dynamic else []

    # 1) Consolidate repeated blocks
    if repeated_blocks:
        top = max(repeated_blocks, key=lambda b: b.total_waste_tokens)
        before = top.content_full[:200] + ("…" if len(top.content_full) > 200 else "")
        after = f"[CACHED SYSTEM PREFIX]\n{top.content_full[:140]}…\n\n[PER-CALL DYNAMIC CONTENT]"

        total_input = sum(
            (m.token_count or 0) for call in inp.calls for m in call.messages
        )
        denom = max(1, total_input)

        out.append(
            Suggestion(
                id="s1",
                type="consolidate_repeated",
                title="Extract repeated blocks into a shared prefix",
                description=(
                    f"Found {len(repeated_blocks)} repeated block(s). The largest repeats {top.occurrences} times; "
                    f"extracting it to a cached prefix would save ~{top.total_waste_tokens} tokens per workflow."
                ),
                priority="high",
                estimated_savings_tokens=top.total_waste_tokens,
                estimated_savings_percentage=top.total_waste_tokens / denom * 100.0,
                before_snippet=before,
                after_snippet=after,
            )
        )

        # 5) Trim redundant instructions (heuristic: multiple small repeated blocks)
        small = [b for b in repeated_blocks if b.tokens_per_occurrence < 200]
        if len(small) > 1:
            total_small = sum(b.total_waste_tokens for b in small)
            out.append(
                Suggestion(
                    id="s5",
                    type="trim_redundant_instructions",
                    title="Consolidate small repeated instruction blocks",
                    description=(
                        f"Found {len(small)} smaller repeated blocks that look like instructions. "
                        "Merge them into fewer, larger instruction sections to improve cache behavior."
                    ),
                    priority="medium",
                    estimated_savings_tokens=total_small,
                    estimated_savings_percentage=0.0,
                    before_snippet="\n\n".join(b.content_preview for b in small[:3]),
                    after_snippet="[CONSOLIDATED INSTRUCTIONS]",
                )
            )

    # 2) Reorder for cache efficiency (dynamic before static)
    if sections:
        first_static = next((i for i, s in enumerate(sections) if _stype(s) == "static"), None)
        first_dynamic = next((i for i, s in enumerate(sections) if _stype(s) == "dynamic"), None)

        if first_static is not None and first_dynamic is not None and first_dynamic < first_static:
            dynamic_before_tokens = sum(_stokens(s) for s in sections[:first_static] if _stype(s) == "dynamic")
            if dynamic_before_tokens > 0:
                out.append(
                    Suggestion(
                        id="s2",
                        type="reorder_prefix",
                        title="Move static content to the beginning",
                        description="Dynamic content appears before static content. For prefix caching, static should come first.",
                        priority="high",
                        estimated_savings_tokens=max(1, dynamic_before_tokens // 2),
                        estimated_savings_percentage=0.0,
                        before_snippet="[dynamic]\n[static]",
                        after_snippet="[static]\n[dynamic]",
                    )
                )

    # 3) Merge fragmented statics
    if sections:
        short_statics = [s for s in sections if _stype(s) == "static" and _stokens(s) < 100]
        if len(short_statics) >= 2:
            fragmented = sum(_stokens(s) for s in short_statics)
            out.append(
                Suggestion(
                    id="s3",
                    type="merge_statics",
                    title="Merge fragmented static blocks",
                    description=f"Found {len(short_statics)} small static blocks (<100 tokens). Merge into one contiguous prefix.",
                    priority="medium",
                    estimated_savings_tokens=max(1, fragmented // 2),
                    estimated_savings_percentage=0.0,
                    before_snippet="[static A]\n[dynamic]\n[static B]",
                    after_snippet="[static A + B]\n[dynamic]",
                )
            )

    # Sort: high → medium → low
    priority_order = {"high": 0, "medium": 1, "low": 2}
    out.sort(key=lambda s: priority_order.get(s.priority, 2))
    return out
