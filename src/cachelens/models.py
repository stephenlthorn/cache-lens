from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class Message(BaseModel):
    role: str
    content: str
    token_count: int | None = None


class Call(BaseModel):
    call_id: str | None = None
    model: str | None = None
    messages: list[Message]
    usage: dict[str, Any] | None = None


InputType = Literal["raw_text", "prompt_chain", "multi_call_trace"]


class InputSummary(BaseModel):
    total_calls: int
    total_messages: int
    total_input_tokens: int


class WasteSource(BaseModel):
    type: str
    description: str
    waste_tokens: int
    percentage_of_total: float
    priority_score: float
    related_block_hash: str | None = None


class WasteSummary(BaseModel):
    total_waste_tokens: int
    waste_percentage: float
    sources: list[WasteSource] = Field(default_factory=list)


class Suggestion(BaseModel):
    id: str
    type: str
    title: str
    description: str
    priority: Literal["high", "medium", "low"]
    estimated_savings_tokens: int
    estimated_savings_percentage: float
    before_snippet: str | None = None
    after_snippet: str | None = None


class RepeatedBlockLocation(BaseModel):
    call_index: int
    message_index: int
    role: str | None = None


class RepeatedBlock(BaseModel):
    content_preview: str
    content_full: str
    content_hash: str
    occurrences: int
    tokens_per_occurrence: int
    total_waste_tokens: int
    locations: list[RepeatedBlockLocation]


class StaticDynamicBreakdown(BaseModel):
    total_static_tokens: int
    total_dynamic_tokens: int
    static_percentage: float
    sections: list[dict[str, Any]] = Field(default_factory=list)


class OptimizedStructure(BaseModel):
    description: str
    messages: list[dict[str, Any]]
    estimated_tokens_per_call: int | None = None
    original_tokens_per_call: int | None = None
    savings_per_call: int | None = None


class AnalysisResult(BaseModel):
    version: str = "1.0.0"
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
    input_type: InputType
    input_summary: InputSummary

    cacheability_score: int
    cacheability_label: str
    score_breakdown: dict[str, int] = Field(default_factory=dict)

    waste_summary: WasteSummary
    static_dynamic_breakdown: StaticDynamicBreakdown

    repeated_blocks: list[RepeatedBlock] = Field(default_factory=list)
    suggestions: list[Suggestion] = Field(default_factory=list)
    optimized_structure: OptimizedStructure | None = None


class AnalysisInput(BaseModel):
    input_type: InputType
    raw_content: str
    calls: list[Call]

    @staticmethod
    def from_raw_text(raw: str) -> "AnalysisInput":
        return AnalysisInput(
            input_type="raw_text",
            raw_content=raw,
            calls=[Call(messages=[Message(role="user", content=raw)])],
        )

    @staticmethod
    def from_messages_payload(raw: str, payload: dict[str, Any]) -> "AnalysisInput":
        msgs = []
        for m in payload.get("messages", []) or []:
            if not isinstance(m, dict):
                continue
            role = str(m.get("role", ""))
            content = m.get("content")
            if not isinstance(content, str):
                continue
            msgs.append(Message(role=role, content=content))
        return AnalysisInput(
            input_type="prompt_chain",
            raw_content=raw,
            calls=[Call(messages=msgs)],
        )

    @staticmethod
    def from_calls_payload(raw: str, payload: dict[str, Any]) -> "AnalysisInput":
        calls = []
        for c in payload.get("calls", []) or []:
            if not isinstance(c, dict) or not isinstance(c.get("messages"), list):
                continue
            msgs = []
            for m in c.get("messages"):
                if not isinstance(m, dict):
                    continue
                role = str(m.get("role", ""))
                content = m.get("content")
                if not isinstance(content, str):
                    continue
                msgs.append(Message(role=role, content=content))
            calls.append(Call(call_id=c.get("call_id"), model=c.get("model"), messages=msgs, usage=c.get("usage")))
        return AnalysisInput(input_type="multi_call_trace", raw_content=raw, calls=calls)


def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
