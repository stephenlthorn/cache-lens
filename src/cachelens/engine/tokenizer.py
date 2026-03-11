from __future__ import annotations

import tiktoken


class TokenCounter:
    def __init__(self, encoding: str = "cl100k_base") -> None:
        self.enc = tiktoken.get_encoding(encoding)

    def count(self, text: str) -> int:
        return len(self.enc.encode(text))
