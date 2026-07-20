"""Numeric validator (CLAUDE.md rule 3 — never weaken or bypass).

Every number in rendered prose must exist in the fact block's allowed set.
Any mismatch → reject (caller retries once, then falls back to the plain
template). No unvalidated prose ever ships.
"""
from __future__ import annotations

import re

NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


def extract_numbers(prose: str) -> list[str]:
    out = []
    for tok in NUMBER_RE.findall(prose):
        canon = tok.rstrip("0").rstrip(".") if "." in tok else tok
        out.append(canon or "0")
    return out


def validate_numbers(prose: str, allowed: set) -> tuple[bool, list[str]]:
    """(passed, offending_numbers)."""
    allowed_canon = {str(a) for a in allowed}
    bad = [n for n in extract_numbers(prose) if n not in allowed_canon]
    return (not bad, bad)
