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


def apply_unified_diff(original: str, diff: str, *, normalizations: list[str] | None = None) -> str:
    """按唯一精确旧文本定位，允许重算元数据；正文不匹配仍拒绝。"""
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
    deleted = 0

    for hunk_number, (header, body) in enumerate(hunks, 1):
        match = _HUNK_HEADER.match(header.strip())
        if match is None:
            raise PatchError(f"无法解析 hunk 头：{header.strip()}")
        old_start = int(match.group("old_start"))
        old_count = int(match.group("old_count") or 1)
        new_count = int(match.group("new_count") or 1)
        new_start = int(match.group("new_start"))
        meaningful = [line for line in body if not line.startswith("\\")]
        if any(not line or line[0] not in " +-" for line in meaningful):
            raise PatchError("未知的补丁行前缀")
        old_lines = [_line_body(line[1:]) for line in meaningful if line[0] in " -"]
        actual_new_count = sum(line[0] in " +" for line in meaningful)
        if old_lines:
            # 在未改动的原文中定位；不能因前面 hunk 已消费而隐藏歧义。
            source_bodies = [_line_body(line) for line in source]
            locations = [
                offset
                for offset in range(len(source) - len(old_lines) + 1)
                if source_bodies[offset : offset + len(old_lines)] == old_lines
            ]
            if len(locations) != 1:
                raise PatchError("上下文不匹配或匹配不唯一")
            start_index = locations[0]
        else:
            # 无旧文本的插入无法重新定位，必须使用合法且自洽的地址。
            if old_count != 0 or new_count != actual_new_count:
                raise PatchError("无上下文插入的行数不匹配")
            start_index = old_start
        if start_index < position or start_index > len(source):
            raise PatchError("hunk 行号越界或重叠")
        expected_new_start = len(result) + start_index - position + (actual_new_count > 0)
        if (old_start, old_count, new_start, new_count) != (
            start_index + (1 if old_lines else 0),
            len(old_lines),
            expected_new_start,
            actual_new_count,
        ) and normalizations is not None:
            normalizations.append(
                f"hunk {hunk_number}: "
                f"old {old_start},{old_count} -> "
                f"{start_index + (1 if old_lines else 0)},{len(old_lines)}; "
                f"new {new_start},{new_count} -> {expected_new_start},{actual_new_count}; "
                "unique exact context"
            )

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
                deleted += 1
            elif tag == "+":
                result.append(content if content.endswith(("\n", "\r")) else content + "\n")
                changed += 1
            else:
                raise PatchError(f"未知的补丁行前缀：{tag!r}")

    result.extend(source[position:])
    if deleted == len(source) and source:
        raise PatchError("补丁删除全部原文，疑似整文件重写")
    if changed == 0:
        raise PatchError("补丁没有改动")
    if len(source) > 10 and changed > max(10, int(len(source) * 0.6)):
        raise PatchError("补丁改动范围过大，疑似整文件重写")
    return "".join(result)
