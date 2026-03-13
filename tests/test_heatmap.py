"""Tests for heatmap.py — token section classification."""
import json
import pytest
from cachelens.heatmap import compute_heatmap


def test_classifies_system_prompt():
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello"},
    ]
    result = compute_heatmap(messages=messages, tools=None, provider="anthropic")
    assert result["system_prompt"] > 0
    assert result["user_query"] > 0
    assert result["total"] > 0


def test_classifies_tool_definitions():
    tools = [{"name": "search", "description": "Search the web",
               "input_schema": {"type": "object", "properties": {}}}]
    messages = [
        {"role": "user", "content": "Search for cats"},
    ]
    result = compute_heatmap(messages=messages, tools=tools, provider="anthropic")
    assert result["tool_definitions"] > 0


def test_classifies_conversation_history():
    messages = [
        {"role": "user", "content": "What is Python?"},
        {"role": "assistant", "content": "Python is a programming language."},
        {"role": "user", "content": "Tell me more."},
        {"role": "assistant", "content": "It was created by Guido van Rossum."},
        {"role": "user", "content": "What version is current?"},
    ]
    result = compute_heatmap(messages=messages, tools=None, provider="anthropic")
    assert result["conversation_history"] > 0
    assert result["user_query"] > 0


def test_classifies_context_markers():
    messages = [
        {"role": "user", "content": "<context>\nThis is injected context about the topic.\n</context>\nNow answer my question."},
    ]
    result = compute_heatmap(messages=messages, tools=None, provider="anthropic")
    assert result["context"] > 0


def test_heatmap_total_matches_sum():
    messages = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello!"},
        {"role": "user", "content": "How are you?"},
    ]
    result = compute_heatmap(messages=messages, tools=None, provider="anthropic")
    section_sum = (
        result["system_prompt"] + result["tool_definitions"] + result["context"]
        + result["conversation_history"] + result["user_query"] + result["other"]
    )
    assert abs(result["total"] - section_sum) <= 5  # allow small rounding


def test_empty_messages_returns_zero_heatmap():
    result = compute_heatmap(messages=[], tools=None, provider="anthropic")
    assert result["total"] == 0
    assert result["user_query"] == 0
