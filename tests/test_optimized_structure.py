from cachelens.parser import parse_input
from cachelens.engine.analyzer import analyze


def test_repeated_block_detected():
    raw = '{"calls": [{"messages": [{"role":"system","content":"A\\n\\nB\\n\\nC"}]},{"messages": [{"role":"system","content":"A\\n\\nB\\n\\nC"}]}]}'
    inp = parse_input(raw)
    res = analyze(inp, min_tokens=1)
    assert res.repeated_blocks
    assert res.waste_summary.total_waste_tokens > 0


def test_optimized_structure_not_null_with_repeated_system_prompt():
    """Test that optimized_structure is populated for multi-call input with repeated system prompt."""
    # Multi-call with repeated system prompt content
    raw = '{"calls": [{"messages": [{"role":"system","content":"Common system prompt with instructions"}]},{"messages": [{"role":"system","content":"Common system prompt with instructions"}]}]}'
    inp = parse_input(raw)
    res = analyze(inp, min_tokens=1)
    
    # Verify optimized_structure is not null
    assert res.optimized_structure is not None
    
    # Verify it has messages with section_type
    assert len(res.optimized_structure.messages) > 0
    
    # Verify section_type is present in messages
    for msg in res.optimized_structure.messages:
        assert "section_type" in msg
    
    # Verify static content is in the system message
    system_msgs = [m for m in res.optimized_structure.messages if m.get("role") == "system"]
    assert len(system_msgs) > 0
    assert "Common system prompt with instructions" in system_msgs[0].get("content", "")


def test_optimized_structure_static_prefix():
    """Test that optimized structure puts static content first."""
    raw = '{"calls": [{"messages": [{"role":"system","content":"Static instructions"}, {"role":"user","content":"What is the weather?"}]}]}'
    inp = parse_input(raw)
    res = analyze(inp, min_tokens=1)
    
    assert res.optimized_structure is not None
    assert len(res.optimized_structure.messages) > 0
    
    # First message should be static
    first_msg = res.optimized_structure.messages[0]
    assert first_msg.get("section_type") == "static"


def test_optimized_structure_tokens():
    """Test that optimized structure includes token estimates."""
    raw = '{"calls": [{"messages": [{"role":"system","content":"A\\n\\nB\\n\\nC"}]},{"messages": [{"role":"system","content":"A\\n\\nB\\n\\nC"}]}]}'
    inp = parse_input(raw)
    res = analyze(inp, min_tokens=1)
    
    assert res.optimized_structure is not None
    assert res.optimized_structure.original_tokens_per_call is not None
    assert res.optimized_structure.estimated_tokens_per_call is not None
    assert res.optimized_structure.savings_per_call is not None
    
    # savings_per_call is an integer (can be negative when separator overhead exceeds savings)
    assert isinstance(res.optimized_structure.savings_per_call, int)
