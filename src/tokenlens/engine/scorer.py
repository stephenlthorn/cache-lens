from __future__ import annotations

from ..models import AnalysisInput, RepeatedBlock, StaticDynamicBreakdown
from .helpers import stype as _stype, stokens as _stokens


def _detect_static_dynamic(inp: AnalysisInput, repeated_blocks: list[RepeatedBlock]) -> StaticDynamicBreakdown:
    """Fallback heuristic static/dynamic detection.

    - If an entire message equals a repeated block, treat it as static.
    - Otherwise dynamic.

    This is only used if caller doesn't provide a classifier output.
    """
    total_static = 0
    total_dynamic = 0
    sections: list[dict] = []

    for call_idx, call in enumerate(inp.calls):
        for msg_idx, msg in enumerate(call.messages):
            tokens = msg.token_count or 0
            if tokens <= 0:
                continue

            section_type = "dynamic"
            for block in repeated_blocks:
                if block.content_full == msg.content:
                    section_type = "static"
                    break

            if section_type == "static":
                total_static += tokens
            else:
                total_dynamic += tokens

            sections.append(
                {
                    "call_index": call_idx,
                    "message_index": msg_idx,
                    "role": msg.role,
                    "type": section_type,
                    "classification": section_type,
                    "tokens": tokens,
                    "token_count": tokens,
                    "content_preview": (msg.content or "")[:100],
                }
            )

    total = total_static + total_dynamic
    static_pct = (total_static / total * 100.0) if total else 0.0

    return StaticDynamicBreakdown(
        total_static_tokens=total_static,
        total_dynamic_tokens=total_dynamic,
        static_percentage=static_pct,
        sections=sections,
    )


def _has_static_prefix(sections: list[dict]) -> bool:
    if not sections:
        return False
    return _stype(sections[0]) == "static"


def _count_static_dynamic_transitions(sections: list[dict]) -> int:
    if len(sections) < 2:
        return 0
    transitions = 0
    for i in range(1, len(sections)):
        if _stype(sections[i]) != _stype(sections[i - 1]):
            transitions += 1
    return transitions


def _count_short_static_blocks(sections: list[dict], threshold: int = 100) -> int:
    count = 0
    in_static = False
    static_tokens = 0

    for section in sections:
        if _stype(section) == "static":
            static_tokens += _stokens(section)
            in_static = True
        else:
            if in_static and 0 < static_tokens < threshold:
                count += 1
            in_static = False
            static_tokens = 0

    if in_static and 0 < static_tokens < threshold:
        count += 1

    return count


def _calculate_static_prefix_ratio(sections: list[dict]) -> float:
    if not sections:
        return 0.0

    total_static_tokens = sum(_stokens(s) for s in sections if _stype(s) == "static")
    if total_static_tokens == 0:
        return 0.0

    prefix_static = 0
    found_dynamic = False
    for section in sections:
        if _stype(section) == "dynamic":
            found_dynamic = True
        elif _stype(section) == "static" and not found_dynamic:
            prefix_static += _stokens(section)

    return prefix_static / total_static_tokens


def cacheability_score(
    inp: AnalysisInput,
    repeated_blocks: list[RepeatedBlock],
    static_dynamic: StaticDynamicBreakdown,
) -> tuple[int, dict[str, int], str]:
    """Compute cacheability score per PRODUCT_SPEC §5.4."""

    if not static_dynamic or not static_dynamic.sections:
        static_dynamic = _detect_static_dynamic(inp, repeated_blocks)

    total_input = sum((m.token_count or 0) for call in inp.calls for m in call.messages)
    repeat_waste = sum(r.total_waste_tokens for r in repeated_blocks)
    sections = static_dynamic.sections or []

    has_static = any(_stype(s) == "static" for s in sections)
    has_dynamic = any(_stype(s) == "dynamic" for s in sections)

    score = 100

    # 1) static prefix penalty (-30 max)
    static_prefix_penalty = 0
    if has_static:
        static_prefix_ratio = _calculate_static_prefix_ratio(sections)
        static_prefix_penalty = int((1 - static_prefix_ratio) * 30)

    # 2) repetition penalty (-25 max)
    repetition_penalty = 0
    if total_input > 0:
        repeat_waste_ratio = repeat_waste / total_input
        repetition_penalty = int(min(repeat_waste_ratio * 100, 25))

    # 3) interleave penalty (-20 max)
    interleave_penalty = 0
    if has_static and has_dynamic:
        interleave_count = _count_static_dynamic_transitions(sections)
        interleave_penalty = int(min(interleave_count * 5, 20))

    # 4) no-prefix penalty (-15)
    no_prefix_penalty = 0
    if has_static and not _has_static_prefix(sections):
        no_prefix_penalty = 15

    # 5) fragmentation penalty (-10 max)
    fragmentation_penalty = 0
    if has_static:
        fragmented = _count_short_static_blocks(sections, threshold=100)
        fragmentation_penalty = int(min(fragmented * 2, 10))

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
