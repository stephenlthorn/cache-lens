from tokenlens.parser import parse_input


def test_raw_text():
    inp = parse_input("hello")
    assert inp.input_type == "raw_text"
    assert inp.calls[0].messages[0].content == "hello"


def test_messages_payload():
    raw = '{"messages": [{"role": "user", "content": "hi"}]}'
    inp = parse_input(raw)
    assert inp.input_type == "prompt_chain"
    assert len(inp.calls) == 1
    assert inp.calls[0].messages[0].role == "user"


def test_calls_payload():
    raw = '{"calls": [{"messages": [{"role":"system","content":"x"}]}]}'
    inp = parse_input(raw)
    assert inp.input_type == "multi_call_trace"
    assert len(inp.calls) == 1
