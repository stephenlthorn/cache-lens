import json
from typing import Any

from .models import AnalysisInput, Call, Message


def parse_input(raw: str) -> AnalysisInput:
    """Detect input type and normalize to AnalysisInput."""
    raw = raw.strip("\ufeff\n\r\t ")

    try:
        data: Any = json.loads(raw)
    except Exception:
        return AnalysisInput.from_raw_text(raw)

    if isinstance(data, dict) and isinstance(data.get("calls"), list):
        return AnalysisInput.from_calls_payload(raw, data)

    if isinstance(data, dict) and isinstance(data.get("messages"), list):
        return AnalysisInput.from_messages_payload(raw, data)

    if isinstance(data, list) and len(data) > 0 and all(isinstance(x, dict) and "role" in x and "content" in x for x in data):
        return AnalysisInput.from_messages_payload(raw, {"messages": data})

    # fallback: stringify json
    return AnalysisInput.from_raw_text(json.dumps(data, indent=2, ensure_ascii=False))
