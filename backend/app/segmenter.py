"""Deterministic long-text segmentation that preserves every non-whitespace character."""

from __future__ import annotations

import re


STRONG_END = set("。！？!?\n")
WEAK_END = set("；;：:，,、")


def _best_cut(text: str, limit: int) -> int:
    window = text[: limit + 1]
    for endings, floor_ratio in ((STRONG_END, 0.35), (WEAK_END, 0.55)):
        floor = int(limit * floor_ratio)
        for pos in range(min(limit, len(window) - 1), floor - 1, -1):
            if window[pos] in endings:
                return pos + 1
    # Prefer a word boundary for Latin text before a hard cut.
    matches = list(re.finditer(r"\s+", window[:limit]))
    if matches and matches[-1].end() >= int(limit * 0.6):
        return matches[-1].end()
    return min(limit, len(text))


def split_text(text: str, max_chars: int = 180) -> list[str]:
    if max_chars < 1:
        raise ValueError("max_chars must be positive")
    remaining = text.strip()
    if not remaining:
        return []
    parts: list[str] = []
    while len(remaining) > max_chars:
        cut = _best_cut(remaining, max_chars)
        part = remaining[:cut].strip()
        if not part:
            cut = min(max_chars, len(remaining))
            part = remaining[:cut]
        parts.append(part)
        remaining = remaining[cut:].strip()
    if remaining:
        parts.append(remaining)
    return parts
