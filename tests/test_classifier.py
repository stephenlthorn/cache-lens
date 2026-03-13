from tokenlens.parser import parse_input
from tokenlens.engine.analyzer import analyze
from tokenlens.engine.classifier import classify_static_dynamic


def test_multi_call_static_detection():
    """Test that identical messages across calls are classified as static."""
    raw = '{"calls": [{"messages": [{"role":"system","content":"You are a helpful assistant."}]},{"messages": [{"role":"system","content":"You are a helpful assistant."}]}]}'
    inp = parse_input(raw)
    result = classify_static_dynamic(inp)
    
    assert result.total_static_tokens > 0
    assert result.total_dynamic_tokens == 0
    assert result.static_percentage == 100.0
    assert len(result.sections) > 0
    assert result.sections[0]["classification"] == "static"


def test_multi_call_dynamic_detection():
    """Test that different messages across calls are classified as dynamic."""
    raw = '{"calls": [{"messages": [{"role":"user","content":"Hello"}]},{"messages": [{"role":"user","content":"Hi"}]}]}'
    inp = parse_input(raw)
    result = classify_static_dynamic(inp)
    
    assert result.total_dynamic_tokens > 0
    assert result.sections[0]["classification"] == "dynamic"


def test_raw_text_template_dynamic():
    """Test that template variables are classified as dynamic."""
    raw = '{"messages": [{"role":"user","content":"Hello {{name}}, your order {{order_id}} is ready."}]}'
    inp = parse_input(raw)
    result = classify_static_dynamic(inp)
    
    assert result.total_dynamic_tokens > 0
    assert result.sections[0]["classification"] == "dynamic"
    assert result.sections[0]["confidence"] == 0.95


def test_raw_text_persona_static():
    """Test that persona instructions are classified as static."""
    raw = '{"messages": [{"role":"system","content":"You are an expert Python developer. Respond in JSON format."}]}'
    inp = parse_input(raw)
    result = classify_static_dynamic(inp)
    
    # Should be classified as static with high confidence
    assert result.total_static_tokens > 0
    assert result.sections[0]["classification"] == "static"


def test_raw_text_uuid_dynamic():
    """Test that UUIDs are classified as dynamic."""
    raw = '{"messages": [{"role":"user","content":"Process this request ID: 550e8400-e29b-41d4-a716-446655440000"}]}'
    inp = parse_input(raw)
    result = classify_static_dynamic(inp)
    
    assert result.sections[0]["classification"] == "dynamic"


def test_analyzer_integration():
    """Test that analyzer uses classifier correctly."""
    raw = '{"calls": [{"messages": [{"role":"system","content":"You are a helpful assistant."}]}]}'
    inp = parse_input(raw)
    result = analyze(inp)
    
    # Analyzer should produce non-zero static/dynamic breakdown
    assert result.static_dynamic_breakdown.total_static_tokens > 0
    assert result.static_dynamic_breakdown.static_percentage > 0


def test_sections_have_required_fields():
    """Test that sections have all required fields per PRODUCT_SPEC."""
    raw = '{"calls": [{"messages": [{"role":"system","content":"You are an expert."}]}]}'
    inp = parse_input(raw)
    result = classify_static_dynamic(inp)
    
    section = result.sections[0]
    required_fields = ["classification", "confidence", "token_count", "content_preview", "position"]
    for field in required_fields:
        assert field in section, f"Missing field: {field}"


def test_partial_variation_prefix_suffix():
    """Test partial variation detection with common prefix/suffix."""
    raw = '''{"calls": [
        {"messages": [{"role":"system","content":"System: Always respond with Hello {{name}}. Thank you."}]},
        {"messages": [{"role":"system","content":"System: Always respond with Hello {{user}}. Thank you."}]}
    ]}'''
    inp = parse_input(raw)
    result = classify_static_dynamic(inp)
    
    # Should have sections for prefix (static), middle (dynamic), suffix (static)
    assert len(result.sections) >= 1
