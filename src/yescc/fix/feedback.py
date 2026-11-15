"""仅从 GCC 的分号诊断产生局部插入候选；不删除语句或猜测语义。"""

from __future__ import annotations

import json
import re

from yescc.fix.lexer import in_spans, protected_spans

_DIAGNOSTIC = re.compile(r"^(.+?):(\d+):\d+:\s*(?:fatal )?error:\s*(.+)$", re.MULTILINE)

_FIXIT = re.compile(r'^fix-it:("(?:[^"\\]|\\.)*"):\{(\d+):(\d+)-(\d+):(\d+)\}:";"$', re.MULTILINE)


def _in_for_header(text: str, position: int) -> bool:
    chars = list(text[:position])
    for span in protected_spans(text):
        for index in range(span.start, min(span.end, len(chars))):
            chars[index] = " "
    stack: list[bool] = []
    for match in re.finditer(r"\bfor\s*\(|[()]", "".join(chars)):
        token = match.group()
        if token == ")":
            if stack:
                stack.pop()
        else:
            stack.append(token != "(")
    return any(stack)


def _fixit_semicolons(text: str, diagnostics: str, filename: str) -> str | None:
    lines = text.splitlines(keepends=True)
    spans = protected_spans(text)
    positions: set[int] = set()
    for match in _FIXIT.finditer(diagnostics):
        try:
            path = json.loads(match.group(1))
        except ValueError:
            continue
        row, column, end_row, end_column = map(int, match.groups()[1:])
        if path.replace("\\", "/").rsplit("/", 1)[-1] != filename:
            continue
        if (row, column) != (end_row, end_column) or not (1 <= row <= len(lines)):
            continue
        encoded = lines[row - 1].encode("utf-8")
        if not 1 <= column <= len(encoded) + 1:
            continue
        try:
            offset = len(encoded[: column - 1].decode("utf-8"))
        except UnicodeDecodeError:
            continue
        position = sum(map(len, lines[: row - 1])) + offset
        if in_spans(position, spans) or _in_for_header(text, position):
            continue
        if lines[row - 1].lstrip().startswith("#"):
            continue
        if row > 1 and lines[row - 2].rstrip().endswith("\\"):
            continue
        positions.add(position)
        if len(positions) == 8:
            break
    if not positions:
        return None
    for position in sorted(positions, reverse=True):
        text = text[:position] + ";" + text[position:]
    return text


def semicolon_from_diagnostics(text: str, diagnostics: str, filename: str) -> str | None:
    """只尝试报错行前一条完整物理行的末尾；不用易受 Unicode 影响的显示列号。"""
    fixed = _fixit_semicolons(text, diagnostics, filename)
    if fixed is not None:
        return fixed
    lines = text.splitlines(keepends=True)
    spans = protected_spans(text)
    for match in _DIAGNOSTIC.finditer(diagnostics):
        if match.group(1).replace("\\", "/").rsplit("/", 1)[-1] != filename:
            continue
        message = match.group(3)
        if not (message.startswith("expected ") and ";" in message and " before " in message):
            continue
        row = int(match.group(2)) - 2
        if row < 0 or row >= len(lines):
            continue
        line = lines[row].rstrip("\r\n")
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "//", "/*")):
            continue
        if re.match(r"(?:if|for|while|switch|else|do)\b", stripped):
            continue
        # 不补写宏、续行表达式或已经具有语句/块终止符的行。
        if stripped.endswith((";", "{", "}", ":", ",", "\\")):
            continue
        if row and lines[row - 1].rstrip().endswith("\\"):
            continue
        position = sum(map(len, lines[:row])) + len(line.rstrip())
        # 字符串结尾可以补分号，注释和未闭合字符串内部不能。
        covering = next((span for span in spans if span.start <= position - 1 < span.end), None)
        if covering is not None and text.startswith(("//", "/*"), covering.start):
            continue
        if in_spans(position - 1, spans) and (
            not line.rstrip().endswith(('"', "'")) or in_spans(position, spans)
        ):
            continue
        return text[:position] + ";" + text[position:]
    return None
