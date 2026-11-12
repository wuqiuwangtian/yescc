"""创意源码的有限呈现护栏，不判断语义正确性或趣味。"""

from __future__ import annotations

import re

from yescc.fix.lexer import protected_spans

_UCN = re.compile(r"\\(?:u[0-9a-fA-F]{4}|U[0-9a-fA-F]{8})")


def _has_character_prefix(source: str, quote: int) -> bool:
    """Only an immediately adjacent, complete encoding prefix changes the literal."""
    for prefix in ("u8", "L", "u", "U"):
        start = quote - len(prefix)
        if start < 0 or source[start:quote] != prefix:
            continue
        if start == 0:
            return True
        previous = source[start - 1]
        if not (previous.isalnum() or ("_" + previous).isidentifier()):
            return True
    return False


def _has_non_ascii_character(source: str, start: int, end: int) -> bool:
    if _has_character_prefix(source, start):
        return False
    literal = source[start + 1:end]
    if any(ord(char) > 127 for char in literal):
        return True
    index = 0
    while index < len(literal):
        if literal[index] != "\\":
            index += 1
            continue
        # Skip escaped backslashes/quotes; their following text is not a UCN.
        ucn = _UCN.match(literal, index)
        if ucn and int(ucn.group()[2:], 16) > 127:
            return True
        index += 2
    return False


def creative_source_issues(source: str) -> list[str]:
    """拒绝直接混用宽窄 stdout API 和普通字符常量中的非 ASCII 字符。

    创意生成约束统一使用UTF-8字节字符串。即使两种调用在不同分支，此保守
    风格规则仍拒绝该创意候选；普通非ASCII字符常量即使在某实现中合法也拒绝。
    不判断宏/间接存储或完整C语义，不应用于原生源码，build检查不执行程序。
    """
    # C translation phase 2 splices physical lines before recognizing comments.
    code = re.sub(r"\\\r?\n", "", source)
    spans = protected_spans(code)
    non_ascii_character = any(
        code[span.start] == "'" and _has_non_ascii_character(code, span.start, span.end)
        for span in spans
    )
    for span in reversed(spans):
        code = code[:span.start] + " " * (span.end - span.start) + code[span.end:]
    wide = re.search(r"\b(?:wprintf|vwprintf|putwchar)\s*\(", code)
    narrow = re.search(r"\b(?:printf|vprintf|puts|putchar)\s*\(", code)
    issues = ["mixed_stdout_orientation"] if wide and narrow else []
    if non_ascii_character:
        issues.append("non_ascii_character_literal")
    return issues
