"""C 语言的确定性修复规则。

每条规则只生成候选文本；是否成立由真实 gcc 验证（见 engine.py）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from yescc.fix.lexer import Span, code_segments, protected_spans, unterminated_quote

FULLWIDTH_MAP: dict[str, str] = {
    "，": ",",
    "；": ";",
    "：": ":",
    "（": "(",
    "）": ")",
    "｛": "{",
    "｝": "}",
    "［": "[",
    "］": "]",
    "！": "!",
    "？": "?",
    "＝": "=",
    "＋": "+",
    "－": "-",
    "＊": "*",
    "／": "/",
    "＞": ">",
    "＜": "<",
    "＆": "&",
    "｜": "|",
    "％": "%",
    "＃": "#",
    "＠": "@",
    "“": '"',
    "”": '"',
    "‘": "'",
    "’": "'",
    "。": ".",
}

TYPO_MAP: dict[str, str] = {
    "prinf": "printf",
    "pritnf": "printf",
    "retrun": "return",
    "mian": "main",
    "whlie": "while",
}

_TYPO_PATTERN = re.compile(r"\b(" + "|".join(TYPO_MAP) + r")\b")


@dataclass(frozen=True)
class Candidate:
    """一个待验证的修复候选。"""

    text: str
    rules: tuple[str, ...]
    changed: int


def _changed(original: str, patched: str) -> int:
    common = min(len(original), len(patched))
    diff = sum(1 for index in range(common) if original[index] != patched[index])
    return diff + abs(len(original) - len(patched))


def _replace_outside(text: str, spans: list[Span]) -> str:
    chars = list(text)
    for start, end in code_segments(text, spans):
        for index in range(start, end):
            replacement = FULLWIDTH_MAP.get(chars[index])
            if replacement is not None:
                chars[index] = replacement
    return "".join(chars)


def _fix_typos(text: str, spans: list[Span]) -> str:
    chars = list(text)
    for start, end in code_segments(text, spans):
        segment = "".join(chars[start:end])
        for match in _TYPO_PATTERN.finditer(segment):
            replacement = TYPO_MAP[match.group(0)]
            absolute = start + match.start()
            chars[absolute : absolute + len(match.group(0))] = list(replacement)
    return "".join(chars)


def _unmatched_openers(text: str) -> list[tuple[str, int]]:
    """返回未闭合的开括号，按出现顺序。"""
    spans = protected_spans(text)
    stack: list[tuple[str, int]] = []
    pairs = {")": "(", "]": "[", "}": "{"}
    for start, end in code_segments(text, spans):
        for index in range(start, end):
            char = text[index]
            if char in "([{":
                stack.append((char, index))
            elif char in ")]}":
                for index in range(len(stack) - 1, -1, -1):
                    if stack[index][0] == pairs[char]:
                        del stack[index]
                        break
    return stack


def _bracket_variants(text: str) -> list[tuple[str, tuple[str, ...]]]:
    """生成括号修复候选：尾部补全 / 在语句结束前插入。"""
    openers = _unmatched_openers(text)
    if not openers:
        return []

    variants: list[tuple[str, tuple[str, ...]]] = []

    # 变体 1：所有缺失的闭括号追加到末尾（适合缺失函数右花括号）。
    closers = "".join({"{": "}", "(": ")", "[": "]"}[char] for char, _ in reversed(openers))
    variants.append((text + closers, ("bracket_eof",)))

    # 变体 2：圆括号/方括号在下一个分号前插入，花括号追加到末尾。
    chars = list(text)
    insertions: list[tuple[int, str]] = []
    for char, position in reversed(openers):
        if char == "{":
            insertions.append((len(text), "}"))
            continue
        semicolon = _next_code_char(text, position, ";")
        insertions.append((semicolon, ")" if char == "(" else "]"))
    for position, closer in sorted(insertions, key=lambda item: item[0], reverse=True):
        chars.insert(position, closer)
    variants.append(("".join(chars), ("bracket_statement",)))

    return variants


def _next_code_char(text: str, start: int, target: str) -> int:
    spans = protected_spans(text)
    for index in range(start, len(text)):
        if text[index] == target:
            from yescc.fix.lexer import in_spans

            if not in_spans(index, spans):
                return index
    return len(text)


def _quote_variants(text: str) -> list[tuple[str, tuple[str, ...]]]:
    found = unterminated_quote(text)
    if found is None:
        return []
    start, quote = found
    variants: list[tuple[str, tuple[str, ...]]] = [(text + quote, ("quote_eof",))]
    for index in range(start + 1, len(text)):
        if text[index] in ";)":
            variants.append((text[:index] + quote + text[index:], ("quote_statement",)))
            break
        if text[index] == "\n":
            variants.append((text[:index] + quote + text[index:], ("quote_line",)))
            break
    return variants


def generate_candidates(original: str) -> list[Candidate]:
    """生成去重后的候选，按改动量排序；调用方负责编译验证。"""
    spans = protected_spans(original)
    bases: list[tuple[str, tuple[str, ...]]] = [(original, ())]

    fullwidth = _replace_outside(original, spans)
    if fullwidth != original:
        bases.append((fullwidth, ("fullwidth",)))
        bases.append((_fix_typos(fullwidth, protected_spans(fullwidth)), ("fullwidth", "typo")))

    typos = _fix_typos(original, spans)
    if typos != original:
        bases.append((typos, ("typo",)))

    expanded: list[tuple[str, tuple[str, ...]]] = []
    for text, rules in bases:
        expanded.append((text, rules))
        for bracketed, bracket_rules in _bracket_variants(text):
            expanded.append((bracketed, rules + bracket_rules))
            for quoted, quote_rules in _quote_variants(bracketed):
                expanded.append((quoted, rules + bracket_rules + quote_rules))
        for quoted, quote_rules in _quote_variants(text):
            expanded.append((quoted, rules + quote_rules))

    seen: dict[str, Candidate] = {}
    for text, rules in expanded:
        if text == original:
            continue
        candidate = Candidate(text=text, rules=rules, changed=_changed(original, text))
        previous = seen.get(text)
        if previous is None or len(candidate.rules) < len(previous.rules):
            seen[text] = candidate

    return sorted(seen.values(), key=lambda item: (item.changed, len(item.rules)))[:12]
