from __future__ import annotations

import re
from collections import defaultdict

from ..models import AnalysisInput, RepeatedBlock, RepeatedBlockLocation, sha256_text
from .tokenizer import TokenCounter


_ws = re.compile(r"\s+")


def normalize(text: str) -> str:
    return _ws.sub(" ", text.strip().lower())


def split_into_blocks(content: str) -> list[str]:
    # Start with paragraphs; fall back to sentences if there are no paragraphs.
    paras = [p.strip() for p in content.split("\n\n") if p.strip()]
    if len(paras) >= 2:
        return paras
    # crude sentence split
    sents = re.split(r"(?<=[.!?])\s+", content.strip())
    return [s.strip() for s in sents if s.strip()]


def find_repeated_blocks(inp: AnalysisInput, counter: TokenCounter, min_tokens: int = 50) -> list[RepeatedBlock]:
    block_map: dict[str, list[tuple[RepeatedBlockLocation, str]]] = defaultdict(list)

    for ci, call in enumerate(inp.calls):
        for mi, msg in enumerate(call.messages):
            for block in split_into_blocks(msg.content):
                if counter.count(block) < min_tokens:
                    continue
                norm = normalize(block)
                loc = RepeatedBlockLocation(call_index=ci, message_index=mi, role=msg.role)
                block_map[norm].append((loc, block))

    out: list[RepeatedBlock] = []
    for norm, occ in block_map.items():
        if len(occ) < 2:
            continue
        first_block = occ[0][1]
        tok = counter.count(first_block)
        out.append(
            RepeatedBlock(
                content_preview=(first_block[:200] + ("…" if len(first_block) > 200 else "")),
                content_full=first_block,
                content_hash=sha256_text(first_block),
                occurrences=len(occ),
                tokens_per_occurrence=tok,
                total_waste_tokens=tok * (len(occ) - 1),
                locations=[x[0] for x in occ],
            )
        )

    out.sort(key=lambda r: r.total_waste_tokens, reverse=True)
    return out
