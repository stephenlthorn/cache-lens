from __future__ import annotations

from .tokenizer import TokenCounter
from .repeats import find_repeated_blocks
from .scorer import cacheability_score
from .waste import build_waste_summary
from .suggestions import build_suggestions
from .classifier import classify_static_dynamic
from ..models import AnalysisInput, AnalysisResult, InputSummary, WasteSummary


def analyze(inp: AnalysisInput, min_tokens: int = 50) -> AnalysisResult:
    counter = TokenCounter()

    # token counting per message
    total_tokens = 0
    total_messages = 0
    for call in inp.calls:
        for msg in call.messages:
            msg.token_count = counter.count(msg.content)
            total_tokens += msg.token_count
            total_messages += 1

    summary = InputSummary(total_calls=len(inp.calls), total_messages=total_messages, total_input_tokens=total_tokens)

    repeated = find_repeated_blocks(inp, counter=counter, min_tokens=min_tokens)

    # Static/dynamic classification
    static_dynamic = classify_static_dynamic(inp)

    score, score_breakdown, label = cacheability_score(inp, repeated, static_dynamic)

    waste_summary = build_waste_summary(total_input_tokens=total_tokens, repeated_blocks=repeated)
    suggestions = build_suggestions(inp, repeated, waste_summary)

    return AnalysisResult(
        input_type=inp.input_type,
        input_summary=summary,
        cacheability_score=score,
        cacheability_label=label,
        score_breakdown=score_breakdown,
        waste_summary=waste_summary,
        static_dynamic_breakdown=static_dynamic,
        repeated_blocks=repeated,
        suggestions=suggestions,
        optimized_structure=None,
    )
