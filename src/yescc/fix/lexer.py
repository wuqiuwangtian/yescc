"""C 源码的词法护栏：找出字符串、字符与注释区间。

规则替换只能发生在这些区间之外，避免把用户想输出的中文标点改掉。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Span:
    """字符区间 [start, end)。"""

    start: int
    end: int


def protected_spans(text: str) -> list[Span]:
    """返回字符串/字符/注释的区间。未闭合的字符串会一直保护到文末。"""
    spans: list[Span] = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if text.startswith("//", index):
            end = text.find("\n", index)
            end = length if end == -1 else end
            spans.append(Span(index, end))
            index = end
        elif text.startswith("/*", index):
            end = text.find("*/", index + 2)
            end = length if end == -1 else end + 2
            spans.append(Span(index, end))
            index = end
        elif char in ('"', "'"):
            quote = char
            cursor = index + 1
            while cursor < length:
                if text[cursor] == "\\":
                    cursor += 2
                    continue
                if text[cursor] == quote:
                    cursor += 1
                    break
                cursor += 1
            end = min(cursor, length)
            spans.append(Span(index, end))
            index = end
        else:
            index += 1
    return spans


def in_spans(position: int, spans: list[Span]) -> bool:
    """位置是否落在任一保护区间内。"""
    return any(span.start <= position < span.end for span in spans)


def code_segments(text: str, spans: list[Span]) -> list[tuple[int, int]]:
    """返回非保护区间。"""
    segments: list[tuple[int, int]] = []
    cursor = 0
    for span in sorted(spans, key=lambda item: item.start):
        if cursor < span.start:
            segments.append((cursor, span.start))
        cursor = max(cursor, span.end)
    if cursor < len(text):
        segments.append((cursor, len(text)))
    return segments


def unterminated_quote(text: str) -> tuple[int, str] | None:
    """返回 (起始引号位置, 引号字符)；没有未闭合引号时返回 None。"""
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if text.startswith("//", index):
            end = text.find("\n", index)
            index = length if end == -1 else end
        elif text.startswith("/*", index):
            end = text.find("*/", index + 2)
            index = length if end == -1 else end + 2
        elif char in ('"', "'"):
            quote = char
            cursor = index + 1
            closed = False
            while cursor < length:
                if text[cursor] == "\\":
                    cursor += 2
                    continue
                if text[cursor] == quote:
                    cursor += 1
                    closed = True
                    break
                cursor += 1
            if not closed:
                return index, quote
            index = cursor
        else:
            index += 1
    return None
