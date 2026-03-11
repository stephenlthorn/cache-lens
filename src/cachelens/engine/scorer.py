from __future__ import annotations

from ..models import AnalysisInput, RepeatedBlock, StaticDynamicBreakdown


def cacheability_score(
    inp: AnalysisInput,
    repeated_blocks: list[RepeatedBlock],
    static_dynamic: StaticDynamicBreakdown,
) -> tuple[int, dict[str, int], str]:
    # MVP: score based mostly on repeated waste ratio.
    total_input = inp and sum((m.token_count or 0) for c in inp.calls for m in c.messages) or 0
    repeat_waste = sum(r.total_waste_tokens for r in repeated_blocks)

    score = 100
    repetition_penalty = 0
    if total_input > 0:
        repetition_penalty = int(min((repeat_waste / total_input) * 100, 25))
        score -= repetition_penalty

    breakdown = {
        "static_prefix_penalty": 0,
        "repetition_penalty": -repetition_penalty,
        "interleave_penalty": 0,
        "no_prefix_penalty": 0,
        "fragmentation_penalty": 0,
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
