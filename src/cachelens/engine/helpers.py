from __future__ import annotations


def stype(section: dict) -> str | None:
    """Return section type ('static'|'dynamic') supporting multiple schemas."""
    return section.get("type") or section.get("classification")


def stokens(section: dict) -> int:
    """Return token count supporting multiple schemas."""
    v = section.get("tokens")
    if isinstance(v, int):
        return v
    v = section.get("token_count")
    if isinstance(v, int):
        return v
    return 0
