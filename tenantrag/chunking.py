"""Deterministic, lossless character chunking for plain text and Markdown.

Offsets are Python Unicode character indexes, not bytes, tokens or graphemes.
Markdown is not rendered or parsed: ATX headings outside fenced code are
preferred boundaries, then blank lines and newlines. Oversized paragraphs and
code blocks are split at the character limit. No input text is normalized.
"""
from __future__ import annotations

from bisect import bisect_right
import re


_PARAGRAPH = re.compile(r"\r?\n[ \t]*\r?\n")
_HEADING = re.compile(r"^ {0,3}#{1,6}(?:[ \t]+|$)")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


def _heading_offsets(text: str) -> list[int]:
    offsets = []
    offset = 0
    fence_char = ""
    fence_length = 0
    for line in text.splitlines(keepends=True):
        match = _FENCE.match(line.rstrip("\r\n"))
        if fence_char:
            if (match and match[1][0] == fence_char
                    and len(match[1]) >= fence_length and not match[2].strip()):
                fence_char = ""
        elif match:
            fence_char, fence_length = match[1][0], len(match[1])
        elif _HEADING.match(line):
            offsets.append(offset)
        offset += len(line)
    return offsets


def chunk_text(
    text: str,
    max_chars: int = 1000,
    overlap: int = 100,
    format: str = "text",
) -> list[dict]:
    """Return ``{text, start, end}`` chunks with half-open source offsets.

    Every source character is covered; non-final chunks contain more than
    ``overlap`` characters and adjacent chunks overlap by exactly that amount.
    A preferred boundary is used only in the latter half of the available
    window (and beyond overlap), ensuring progress even with large overlaps.
    The maximum size is a character limit, not a provider's token limit.
    Empty text produces no chunks, while whitespace is preserved normally.
    """
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    if type(max_chars) is not int or max_chars <= 0:
        raise ValueError("max_chars must be a positive integer")
    if type(overlap) is not int or not 0 <= overlap < max_chars:
        raise ValueError("overlap must be an integer from zero to max_chars - 1")
    if format not in ("text", "markdown"):
        raise ValueError("format must be 'text' or 'markdown'")

    if len(text) > 1_000_000:
        raise ValueError("text exceeds 1,000,000 character chunking limit")
    boundaries = []
    if format == "markdown":
        boundaries.append(_heading_offsets(text))
    boundaries.extend([
        [match.end() for match in _PARAGRAPH.finditer(text)],
        [match.end() for match in re.finditer("\n", text)],
    ])
    chunks = []
    start = 0
    materialized = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            earliest = start + max(overlap + 1, (max_chars + 1) // 2)
            for candidates in boundaries:
                position = bisect_right(candidates, end) - 1
                if position >= 0 and candidates[position] >= earliest:
                    end = candidates[position]
                    break
        materialized += end - start
        if len(chunks) >= 10_000 or materialized > 10_000_000:
            raise ValueError("chunk count or total chunk characters exceed ingestion limits")
        chunks.append({"text": text[start:end], "start": start, "end": end})
        if end == len(text):
            break
        start = end - overlap
    return chunks
