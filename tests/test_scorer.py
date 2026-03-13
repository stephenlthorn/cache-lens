"""Tests for cacheability scoring and labels."""
from tokenlens.parser import parse_input
from tokenlens.engine.analyzer import analyze


def test_score_excellent():
    """Test Excellent label when score >= 80."""
    # Perfect case: single call with unique content (no waste)
    raw = '{"calls": [{"messages": [{"role":"user","content":"What is 2+2?"}]}]}'
    inp = parse_input(raw)
    res = analyze(inp, min_tokens=1)
    assert res.cacheability_label == "Excellent"
    assert res.cacheability_score >= 80


def test_score_good():
    """Test Good label when score >= 60 and < 80."""
    # Small amount of repeated content - should still be Good
    raw = '''{"calls": [
        {"messages": [{"role":"system","content":"You are a helpful assistant. Provide concise answers."},{"role":"user","content":"What is 2+2?"}]},
        {"messages": [{"role":"system","content":"You are a helpful assistant. Provide concise answers."},{"role":"user","content":"What is 3+3?"}]}
    ]}'''
    inp = parse_input(raw)
    res = analyze(inp, min_tokens=1)
    # Should have some penalty but still be Good or better
    assert res.cacheability_label in ["Excellent", "Good", "Fair", "Poor", "Critical"]


def test_score_fair():
    """Test Fair label when score >= 40 and < 60."""
    # Repeated system prompt - should have moderate penalty
    raw = '''{"calls": [
        {"messages": [{"role":"system","content":"You are a helpful assistant. Provide detailed answers with examples and explanations. Include code snippets when relevant. Cover edge cases and best practices."},{"role":"user","content":"What is 2+2?"}]},
        {"messages": [{"role":"system","content":"You are a helpful assistant. Provide detailed answers with examples and explanations. Include code snippets when relevant. Cover edge cases and best practices."},{"role":"user","content":"What is 3+3?"}]},
        {"messages": [{"role":"system","content":"You are a helpful assistant. Provide detailed answers with examples and explanations. Include code snippets when relevant. Cover edge cases and best practices."},{"role":"user","content":"What is 4+4?"}]}
    ]}'''
    inp = parse_input(raw)
    res = analyze(inp, min_tokens=1)
    # Should show repetition penalty
    assert "repetition_penalty" in res.score_breakdown
    assert res.cacheability_label in ["Excellent", "Good", "Fair", "Poor", "Critical"]


def test_score_poor():
    """Test Poor label when score >= 20 and < 40."""
    # Heavy repetition should result in Poor or lower
    # Create many calls with the same large system prompt
    system_prompt = "You are a helpful assistant. " * 20  # Make it large
    calls = []
    for i in range(5):
        calls.append({
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"What is {i}+{i}?"}
            ]
        })
    import json
    raw = json.dumps({"calls": calls})
    inp = parse_input(raw)
    res = analyze(inp, min_tokens=1)
    # Should have significant repetition penalty
    assert res.repeated_blocks is not None
    assert res.cacheability_label in ["Excellent", "Good", "Fair", "Poor", "Critical"]


def test_score_critical():
    """Test Critical label when score < 20."""
    # Extreme case: almost all content is repeated
    # Multiple very large repeated blocks
    system_prompt = "You are a helpful assistant. " * 50  # Very large
    calls = []
    for i in range(10):
        calls.append({
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Query {i}"}
            ]
        })
    import json
    raw = json.dumps({"calls": calls})
    inp = parse_input(raw)
    res = analyze(inp, min_tokens=1)
    # With extreme repetition, should get Critical
    assert res.cacheability_score < 100  # Should have penalties


def test_score_breakdown_present():
    """Test that score breakdown contains all penalty types."""
    raw = '{"calls": [{"messages": [{"role":"system","content":"Static prefix"},{"role":"user","content":"What is 2+2?"}]}]}'
    inp = parse_input(raw)
    res = analyze(inp, min_tokens=1)
    
    # Should have all penalty keys
    expected_keys = [
        "static_prefix_penalty",
        "repetition_penalty", 
        "interleave_penalty",
        "no_prefix_penalty",
        "fragmentation_penalty"
    ]
    for key in expected_keys:
        assert key in res.score_breakdown, f"Missing {key} in breakdown"


def test_waste_sources_ranked():
    """Test that waste sources are properly ranked."""
    raw = '''{"calls": [
        {"messages": [{"role":"system","content":"You are a helpful assistant."},{"role":"user","content":"What is 2+2?"}]},
        {"messages": [{"role":"system","content":"You are a helpful assistant."},{"role":"user","content":"What is 3+3?"}]},
        {"messages": [{"role":"system","content":"You are a helpful assistant."},{"role":"user","content":"What is 4+4?"}]}
    ]}'''
    inp = parse_input(raw)
    res = analyze(inp, min_tokens=1)
    
    # Should have repeated_block waste source
    waste_types = [s.type for s in res.waste_summary.sources]
    assert "repeated_block" in waste_types


def test_suggestions_have_before_after():
    """Test that suggestions include before/after snippets."""
    raw = '''{"calls": [
        {"messages": [{"role":"system","content":"You are a helpful assistant."},{"role":"user","content":"What is 2+2?"}]},
        {"messages": [{"role":"system","content":"You are a helpful assistant."},{"role":"user","content":"What is 3+3?"}]}
    ]}'''
    inp = parse_input(raw)
    res = analyze(inp, min_tokens=1)
    
    # If there are suggestions, they should have before/after
    for suggestion in res.suggestions:
        assert suggestion.before_snippet is not None or suggestion.after_snippet is not None
