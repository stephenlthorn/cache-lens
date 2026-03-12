from __future__ import annotations

from .tokenizer import TokenCounter
from .repeats import find_repeated_blocks
from .classifier import classify_static_dynamic
from .scorer import cacheability_score
from .waste import build_waste_summary
from .suggestions import build_suggestions
from .optimizer import build_optimized_structure
from ..models import AnalysisInput, AnalysisResult, InputSummary


def analyze(inp: AnalysisInput, min_tokens: int = 50) -> AnalysisResult:
    counter = TokenCounter()

    # token counting per message (create new Message objects to avoid mutation)
    total_tokens = 0
    total_messages = 0
    for call in inp.calls:
        new_messages = []
        for msg in call.messages:
            token_count = counter.count(msg.content)
            new_messages.append(msg.model_copy(update={"token_count": token_count}))
            total_tokens += token_count
            total_messages += 1
        call.messages = new_messages

    summary = InputSummary(total_calls=len(inp.calls), total_messages=total_messages, total_input_tokens=total_tokens)

    repeated = find_repeated_blocks(inp, counter=counter, min_tokens=min_tokens)

    # Static/dynamic classification
    static_dynamic = classify_static_dynamic(inp)

    score, score_breakdown, label = cacheability_score(inp, repeated, static_dynamic)

    waste_summary = build_waste_summary(
        total_input_tokens=total_tokens,
        repeated_blocks=repeated,
        static_dynamic_sections=static_dynamic.sections,
    )
    suggestions = build_suggestions(inp, repeated, waste_summary, static_dynamic)
    optimized_structure = build_optimized_structure(inp, repeated, counter)

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
        optimized_structure=optimized_structure,
    )
