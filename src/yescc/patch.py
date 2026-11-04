"""严格应用 unified diff，拒绝整文件改写。"""

from __future__ import annotations

import re

_HUNK_HEADER = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@"
)


class PatchError(ValueError):
    """补丁无法严格应用。"""


def _strip_fences(diff: str) -> str:
    text = diff.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines) + "\n"
    return text


def _line_body(line: str) -> str:
    return line.rstrip("\r\n")


def apply_unified_diff(original: str, diff: str) -> str:
    """按上下文严格应用补丁；任何不匹配都抛 PatchError。"""
    text = _strip_fences(diff)
    lines = text.splitlines(keepends=True)
    hunks: list[tuple[str, list[str]]] = []
    index = 0
    while index < len(lines):
        if lines[index].startswith("@@"):
            header = lines[index]
            index += 1
            body: list[str] = []
            while index < len(lines) and not lines[index].startswith("@@"):
                body.append(lines[index])
                index += 1
            hunks.append((header, body))
        else:
            index += 1

    if not hunks:
        raise PatchError("补丁中没有 @@ hunk")

    source = original.splitlines(keepends=True)
    result: list[str] = []
    position = 0
    changed = 0

    for header, body in hunks:
        match = _HUNK_HEADER.match(header.strip())
        if match is None:
            raise PatchError(f"无法解析 hunk 头：{header.strip()}")
        old_start = int(match.group("old_start"))
        old_count = int(match.group("old_count") or 1)
        start_index = old_start - 1 if old_count > 0 else old_start
        if start_index < position or start_index > len(source):
            raise PatchError("hunk 行号越界或重叠")

        result.extend(source[position:start_index])
        position = start_index

        for line in body:
            if line.startswith("\\"):
                continue
            if not line:
                raise PatchError("hunk 中出现空行")
            tag, content = line[0], line[1:]
            if tag == " ":
                if position >= len(source) or _line_body(source[position]) != _line_body(content):
                    raise PatchError("上下文不匹配")
                result.append(source[position])
                position += 1
            elif tag == "-":
                if position >= len(source) or _line_body(source[position]) != _line_body(content):
                    raise PatchError("删除行不匹配")
                position += 1
                changed += 1
            elif tag == "+":
                result.append(content if content.endswith(("\n", "\r")) else content + "\n")
                changed += 1
            else:
                raise PatchError(f"未知的补丁行前缀：{tag!r}")

    result.extend(source[position:])
    if len(source) > 10 and changed > max(10, int(len(source) * 0.6)):
        raise PatchError("补丁改动范围过大，疑似整文件重写")
    return "".join(result)
