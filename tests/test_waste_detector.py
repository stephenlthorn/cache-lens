"""Tests for waste_detector.py — junk token detection."""
import pytest
from cachelens.waste_detector import detect_waste, WasteItem


def _make_request(messages, max_tokens=None, tools=None):
    body = {"messages": messages}
    if max_tokens is not None:
        body["max_tokens"] = max_tokens
    if tools is not None:
        body["tools"] = tools
    return body


def test_detects_whitespace_bloat():
    body = _make_request([
        {"role": "user", "content": "Hello\n\n\n\n\nworld    \n\n\n   "}
    ])
    items = detect_waste(body, provider="anthropic")
    whitespace_items = [i for i in items if i.waste_type == "whitespace"]
    assert len(whitespace_items) > 0
    assert whitespace_items[0].waste_tokens > 0


def test_no_whitespace_in_clean_message():
    body = _make_request([
        {"role": "user", "content": "Hello world. How are you?"}
    ])
    items = detect_waste(body, provider="anthropic")
    whitespace_items = [i for i in items if i.waste_type == "whitespace"]
    assert len(whitespace_items) == 0


def test_detects_polite_filler_in_system():
    body = _make_request([
        {"role": "system", "content": "Certainly! I'd be happy to help you with that. Sure thing!"},
        {"role": "user", "content": "What is 2+2?"},
    ])
    items = detect_waste(body, provider="anthropic")
    filler_items = [i for i in items if i.waste_type == "polite_filler"]
    assert len(filler_items) > 0


def test_no_polite_filler_in_user_messages():
    """Polite filler only detected in system role."""
    body = _make_request([
        {"role": "user", "content": "Certainly! I'd be happy to help!"},
    ])
    items = detect_waste(body, provider="anthropic")
    filler_items = [i for i in items if i.waste_type == "polite_filler"]
    assert len(filler_items) == 0


def test_detects_redundant_instructions():
    instruction = "Always respond in JSON format. Include a 'status' field."
    body = _make_request([
        {"role": "system", "content": f"Be helpful. {instruction}"},
        {"role": "user", "content": f"Do something. {instruction}"},
    ])
    items = detect_waste(body, provider="anthropic")
    redundant = [i for i in items if i.waste_type == "redundant_instruction"]
    assert len(redundant) > 0


def test_no_redundant_without_repetition():
    body = _make_request([
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "What is the weather?"},
    ])
    items = detect_waste(body, provider="anthropic")
    redundant = [i for i in items if i.waste_type == "redundant_instruction"]
    assert len(redundant) == 0


def test_detects_empty_messages():
    body = _make_request([
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": ""},
        {"role": "user", "content": "Go"},
    ])
    items = detect_waste(body, provider="anthropic")
    empty = [i for i in items if i.waste_type == "empty_message"]
    assert len(empty) > 0


def test_no_empty_for_normal_messages():
    body = _make_request([
        {"role": "user", "content": "This is a normal message with content."},
    ])
    items = detect_waste(body, provider="anthropic")
    empty = [i for i in items if i.waste_type == "empty_message"]
    assert len(empty) == 0


def test_waste_item_has_savings_usd():
    """Every WasteItem must have a non-negative savings_usd."""
    body = _make_request([
        {"role": "user", "content": "Hello\n\n\n\n\n\n\n\n\nworld"},
    ])
    items = detect_waste(body, provider="anthropic")
    for item in items:
        assert item.savings_usd >= 0.0
        assert isinstance(item.detail, str)


def test_empty_body_returns_no_waste():
    items = detect_waste({}, provider="anthropic")
    assert items == []


def test_waste_item_dataclass():
    item = WasteItem(
        waste_type="whitespace",
        waste_tokens=10,
        savings_usd=0.001,
        detail='{"location": "message[0]"}',
    )
    assert item.waste_type == "whitespace"
    assert item.waste_tokens == 10


def test_redundant_instructions_no_inflation():
    """Redundant detection should return 1 WasteItem, not multiple overlapping ones."""
    instruction = "Always respond in JSON format. Include a 'status' field."
    body = _make_request([
        {"role": "system", "content": f"Be helpful. {instruction}"},
        {"role": "user", "content": f"Do something. {instruction}"},
    ])
    items = detect_waste(body, provider="anthropic")
    redundant = [i for i in items if i.waste_type == "redundant_instruction"]
    assert len(redundant) == 1
    assert redundant[0].waste_tokens > 0
