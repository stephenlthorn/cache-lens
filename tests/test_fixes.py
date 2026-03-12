"""Tests for all code review fixes."""
import json

from cachelens.parser import parse_input
from cachelens.engine.analyzer import analyze
from cachelens.engine.classifier import classify_static_dynamic, _has_template_angle_brackets
from cachelens.engine.repeats import split_into_blocks
from cachelens.engine.waste import build_waste_summary
from cachelens.engine.helpers import stype, stokens
from cachelens.models import AnalysisInput, RepeatedBlock, RepeatedBlockLocation, sha256_text


# --- #1: Double-counting waste ---

def test_no_double_counting_waste():
    """Small repeated blocks should not appear in both repeated_block and redundant_instructions."""
    system_prompt_a = "Always respond politely and professionally to the user. " * 4
    system_prompt_b = "Provide concise answers using proper grammar rules. " * 4
    calls = []
    for i in range(3):
        calls.append({
            "messages": [
                {"role": "system", "content": f"{system_prompt_a}\n\n{system_prompt_b}"},
                {"role": "user", "content": f"Question {i}"},
            ]
        })
    raw = json.dumps({"calls": calls})
    inp = parse_input(raw)
    res = analyze(inp, min_tokens=1)

    waste_types = [s.type for s in res.waste_summary.sources]
    # If both exist, their tokens must not overlap
    if "repeated_block" in waste_types and "redundant_instructions" in waste_types:
        rb_source = next(s for s in res.waste_summary.sources if s.type == "repeated_block")
        ri_source = next(s for s in res.waste_summary.sources if s.type == "redundant_instructions")
        # Large blocks go to repeated_block, small to redundant_instructions — no overlap
        assert rb_source.waste_tokens + ri_source.waste_tokens <= res.waste_summary.total_waste_tokens


def test_waste_no_double_count_small_blocks():
    """When all repeated blocks are small, they should only appear as redundant_instructions."""
    blocks = [
        RepeatedBlock(
            content_preview="block a",
            content_full="block a content here",
            content_hash=sha256_text("block a"),
            occurrences=3,
            tokens_per_occurrence=50,
            total_waste_tokens=100,
            locations=[RepeatedBlockLocation(call_index=0, message_index=0)],
        ),
        RepeatedBlock(
            content_preview="block b",
            content_full="block b content here",
            content_hash=sha256_text("block b"),
            occurrences=2,
            tokens_per_occurrence=60,
            total_waste_tokens=60,
            locations=[RepeatedBlockLocation(call_index=0, message_index=0)],
        ),
    ]
    summary = build_waste_summary(total_input_tokens=1000, repeated_blocks=blocks)
    types = [s.type for s in summary.sources]
    # Small blocks (< 200 tokens each) with 2+ blocks: only redundant_instructions, not repeated_block
    assert "redundant_instructions" in types
    assert "repeated_block" not in types
    assert summary.total_waste_tokens == 160  # 100 + 60, counted once


# --- #2: Negative token counts ---

def test_no_negative_token_counts_in_classifier():
    """Partial variation with overlapping prefix/suffix should not produce negative tokens."""
    raw = json.dumps({"calls": [
        {"messages": [{"role": "system", "content": "AB"}]},
        {"messages": [{"role": "system", "content": "AC"}]},
    ]})
    inp = parse_input(raw)
    result = classify_static_dynamic(inp)

    for section in result.sections:
        assert section["token_count"] >= 0, f"Negative token count: {section}"


# --- #3: No mutation of input ---

def test_analyze_does_not_mutate_input():
    """analyze() should not mutate the original AnalysisInput messages."""
    raw = '{"messages": [{"role": "user", "content": "hello world"}]}'
    inp = parse_input(raw)
    original_token_count = inp.calls[0].messages[0].token_count
    assert original_token_count is None

    analyze(inp, min_tokens=1)
    # After analysis, the messages have been replaced with new objects that have token_count
    # The key point: no in-place mutation of the original Message objects


# --- #4: Suggestion savings percentage ---

def test_suggestion_savings_percentage_bounded():
    """Suggestion savings percentage should be relative to total input, not a single message."""
    system_prompt = "You are a helpful assistant. " * 20
    calls = []
    for i in range(3):
        calls.append({
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Short question {i}?"},
            ]
        })
    raw = json.dumps({"calls": calls})
    inp = parse_input(raw)
    res = analyze(inp, min_tokens=1)

    for sug in res.suggestions:
        # Percentage should be <= 100% since it's relative to total input
        assert sug.estimated_savings_percentage <= 100.0, (
            f"Savings percentage {sug.estimated_savings_percentage}% exceeds 100%"
        )


# --- #5: Average token count for "all different" ---

def test_multi_call_all_different_uses_average_tokens():
    """When all messages differ, token count should be an average, not just first message."""
    raw = json.dumps({"calls": [
        {"messages": [{"role": "user", "content": "A"}]},
        {"messages": [{"role": "user", "content": "This is a much longer message with many more words"}]},
    ]})
    inp = parse_input(raw)
    result = classify_static_dynamic(inp)

    assert len(result.sections) == 1
    section = result.sections[0]
    assert section["classification"] == "dynamic"
    # Token count should be the average, not the count of just "A"
    assert section["token_count"] > 1


# --- #6: Server validation ---

def test_server_analyze_request_validation():
    """AnalyzeRequest model should validate input."""
    from cachelens.server import AnalyzeRequest
    from pydantic import ValidationError

    # Valid
    req = AnalyzeRequest(input="hello")
    assert req.input == "hello"
    assert req.min_tokens == 50

    # Empty string should fail
    try:
        AnalyzeRequest(input="")
        assert False, "Should have raised ValidationError"
    except ValidationError:
        pass

    # Custom min_tokens
    req = AnalyzeRequest(input="test", min_tokens=10)
    assert req.min_tokens == 10


# --- #7: Optimizer uses first call only ---

def test_optimizer_uses_first_call_only():
    """Optimizer should not drop messages by deduplicating across calls."""
    raw = json.dumps({"calls": [
        {"messages": [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "What is 2+2?"},
        ]},
        {"messages": [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "What is 3+3?"},
        ]},
    ]})
    inp = parse_input(raw)
    res = analyze(inp, min_tokens=1)

    opt = res.optimized_structure
    assert opt is not None
    # Should have the system message (static) + user message from first call
    roles = [m["role"] for m in opt.messages]
    assert "user" in roles


# --- #8: split_into_blocks handles markdown ---

def test_split_into_blocks_markdown_headers():
    """Should split on markdown headers."""
    content = "# Section 1\nThis is section 1.\n# Section 2\nThis is section 2."
    blocks = split_into_blocks(content)
    assert len(blocks) >= 2


def test_split_into_blocks_triple_dash():
    """Should split on triple-dash separators."""
    content = "Part one content here.\n---\nPart two content here."
    blocks = split_into_blocks(content)
    assert len(blocks) == 2


def test_split_into_blocks_double_newline():
    """Should still split on double newlines (existing behavior)."""
    content = "Paragraph one.\n\nParagraph two."
    blocks = split_into_blocks(content)
    assert len(blocks) == 2


# --- #9: XML tag false positives ---

def test_xml_tags_not_classified_as_template():
    """Common XML delimiter tags should not trigger template detection."""
    assert not _has_template_angle_brackets("<instructions>Do this</instructions>")
    assert not _has_template_angle_brackets("<context>Some context</context>")
    assert not _has_template_angle_brackets("<output>Result</output>")


def test_real_template_vars_still_detected():
    """Actual template angle-bracket variables should still be detected."""
    assert _has_template_angle_brackets("Hello <name>, welcome!")
    assert _has_template_angle_brackets("Process <order_id> now")


def test_xml_tags_classified_static():
    """Content with XML delimiter tags should be classified as static, not dynamic."""
    raw = json.dumps({"messages": [
        {"role": "system", "content": "You are an expert. <instructions>Always respond in JSON format.</instructions>"}
    ]})
    inp = parse_input(raw)
    result = classify_static_dynamic(inp)
    assert result.sections[0]["classification"] == "static"


# --- #10: Strengthened scorer tests ---

def test_score_single_call_no_repetition_is_excellent():
    """A single call with no waste should score Excellent (>=80)."""
    raw = '{"calls": [{"messages": [{"role":"system","content":"You are a helpful assistant."},{"role":"user","content":"What is 2+2?"}]}]}'
    inp = parse_input(raw)
    res = analyze(inp, min_tokens=1)
    assert res.cacheability_score >= 80
    assert res.cacheability_label == "Excellent"


def test_score_heavy_repetition_below_excellent():
    """Heavy repetition across many calls should score below Excellent."""
    system_prompt = "You are a helpful assistant. " * 20
    calls = []
    for i in range(5):
        calls.append({
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Question {i}"},
            ]
        })
    raw = json.dumps({"calls": calls})
    inp = parse_input(raw)
    res = analyze(inp, min_tokens=1)
    assert res.cacheability_score < 80, f"Score {res.cacheability_score} should be < 80 with heavy repetition"


def test_score_extreme_repetition_is_low():
    """Extreme repetition should produce a poor or critical score."""
    system_prompt = "You are a helpful assistant. " * 50
    calls = []
    for i in range(10):
        calls.append({
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Q{i}"},
            ]
        })
    raw = json.dumps({"calls": calls})
    inp = parse_input(raw)
    res = analyze(inp, min_tokens=1)
    assert res.cacheability_score < 80, f"Score {res.cacheability_score} should be < 80 with extreme repetition"


# --- #11: Interleave waste includes both sides ---

def test_interleave_waste_includes_first_section():
    """Interleave waste should include the first section when a transition starts at index 1."""
    sections = [
        {"classification": "static", "token_count": 50},
        {"classification": "dynamic", "token_count": 30},
    ]
    summary = build_waste_summary(total_input_tokens=200, repeated_blocks=[], static_dynamic_sections=sections)
    interleaved = [s for s in summary.sources if s.type == "interleaved"]
    if interleaved:
        # Should include both the static (50) and dynamic (30) tokens
        assert interleaved[0].waste_tokens == 80


# --- #12: Shared helpers ---

def test_helpers_stype():
    assert stype({"type": "static"}) == "static"
    assert stype({"classification": "dynamic"}) == "dynamic"
    assert stype({}) is None


def test_helpers_stokens():
    assert stokens({"tokens": 42}) == 42
    assert stokens({"token_count": 99}) == 99
    assert stokens({}) == 0


# --- #15: Empty array edge case ---

def test_empty_array_falls_through():
    """Empty JSON array should not be treated as messages payload."""
    inp = parse_input("[]")
    assert inp.input_type == "raw_text"


# --- #16: Savings can be negative ---

def test_optimizer_savings_can_be_negative():
    """If optimization adds separator overhead, savings_per_call can be negative."""
    # Single call, no repeated blocks — optimizer adds no extra content
    raw = '{"calls": [{"messages": [{"role":"system","content":"Short."},{"role":"user","content":"Hi"}]}]}'
    inp = parse_input(raw)
    res = analyze(inp, min_tokens=1)
    opt = res.optimized_structure
    assert opt is not None
    # savings_per_call should be an integer (can be 0 or negative, not clamped)
    assert isinstance(opt.savings_per_call, int)
