from cachelens.parser import parse_input
from cachelens.engine.analyzer import analyze


def test_repeated_block_detected():
    raw = '{"calls": [{"messages": [{"role":"system","content":"A\\n\\nB\\n\\nC"}]},{"messages": [{"role":"system","content":"A\\n\\nB\\n\\nC"}]}]}'
    inp = parse_input(raw)
    res = analyze(inp, min_tokens=1)
    assert res.repeated_blocks
    assert res.waste_summary.total_waste_tokens > 0
