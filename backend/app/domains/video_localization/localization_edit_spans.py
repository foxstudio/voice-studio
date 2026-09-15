"""Deterministic structural edit boundaries, not linguistic sentence analysis.

Sentence punctuation inside paired quotes is not an independently editable
boundary. All consumers use the same original character offsets. Ambiguous or
malformed quotes remain visible as one paragraph, but are not editable.
"""

from __future__ import annotations

from dataclasses import dataclass


_PAIRS = {"“": "”", "‘": "’", "「": "」", "『": "』", '"': '"'}
_CLOSERS = frozenset(_PAIRS.values())
_TERMINALS = frozenset("。！？!?；;")
_TRAILING = frozenset('”’」』）》】"。！？!?；;')


def _quote_boundaries(value: str) -> list[bool]:
    stack: list[str] = []
    closed = [True]
    for index, char in enumerate(value):
        # Apostrophes inside a word, and possessives without a matching opening
        # single quote, are lexical marks. A real closing quote takes priority
        # for the latter case so ‘James’ remains a balanced quotation.
        apostrophe = (
            char == "’"
            and index > 0
            and value[index - 1].isascii()
            and value[index - 1].isalpha()
            and (
                (index + 1 < len(value) and value[index + 1].isascii()
                 and value[index + 1].isalpha())
                or not stack or stack[-1] != "’"
            )
        )
        # A double mark following a number denotes inches unless it closes an
        # already open ASCII quotation ("27"). Never rewrite either notation.
        measurement_mark = (
            char == '"' and index > 0 and value[index - 1].isdecimal()
            and (not stack or stack[-1] != '"')
        )
        if apostrophe or measurement_mark:
            pass
        elif char == '"' and stack and stack[-1] == '"':
            stack.pop()
        elif char in _PAIRS:
            stack.append(_PAIRS[char])
        elif char in _CLOSERS:
            if not stack or stack[-1] != char:
                raise ValueError("引号结构不完整或嵌套顺序不明确，不能独立编辑。")
            stack.pop()
        closed.append(not stack)
    if stack:
        raise ValueError("引号结构不完整或嵌套顺序不明确，不能独立编辑。")
    return closed


def validate_quote_structure(value: str) -> None:
    """Reject uncertain structure; never invent, remove or move delimiters."""

    _quote_boundaries(value)


def editable_spans(value: str) -> list[tuple[int, int, str]]:
    """Return exact, quote-closed ranges; preserve every non-whitespace byte."""

    try:
        closed = _quote_boundaries(value)
    except ValueError:
        text = value.strip()
        return [(len(value) - len(value.lstrip()), len(value.rstrip()), text)] if text else []
    spans: list[tuple[int, int, str]] = []
    start = 0
    index = 0
    while index < len(value):
        char = value[index]
        if char in _TERMINALS or char == "\n":
            end = index + 1
            while end < len(value) and value[end] in _TRAILING:
                end += 1
            if closed[end] and (end == len(value) or value[end] not in "，、：,:；;"):
                raw = value[start:end]
                if raw.strip():
                    left = start + len(raw) - len(raw.lstrip())
                    right = end - (len(raw) - len(raw.rstrip()))
                    spans.append((left, right, value[left:right]))
                start = end
                index = end
                continue
        index += 1
    raw = value[start:]
    if raw.strip():
        left = start + len(raw) - len(raw.lstrip())
        right = len(value.rstrip())
        spans.append((left, right, value[left:right]))
    return spans


@dataclass(frozen=True)
class TextEdit:
    start: int
    end: int
    replacement: str


def apply_text_edits(value: str, edits: list[TextEdit]) -> str:
    """Splice once against immutable input; reject overlapping edit ownership."""

    ordered = sorted(edits, key=lambda edit: (edit.start, edit.end))
    parts: list[str] = []
    cursor = 0
    previous: TextEdit | None = None
    for edit in ordered:
        if not 0 <= edit.start <= edit.end <= len(value) or edit.start < cursor:
            raise ValueError("局部修改范围重叠或超出原文。")
        if previous is not None and previous.start == previous.end == edit.start == edit.end:
            raise ValueError("多个移动或插入动作共享同一位置，顺序不明确。")
        parts.extend((value[cursor : edit.start], edit.replacement))
        cursor = edit.end
        previous = edit
    parts.append(value[cursor:])
    return "".join(parts)
