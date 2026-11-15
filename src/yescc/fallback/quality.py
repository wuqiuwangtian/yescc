"""创意源码的有限呈现护栏，不判断语义正确性或趣味。"""

from __future__ import annotations

import re

from yescc.fix.lexer import protected_spans


def creative_source_issues(source: str) -> list[str]:
    """拒绝直接混用宽窄 stdout API；忽略字符串和注释中的函数名。

    创意生成约束统一使用UTF-8字节字符串。即使两种调用在不同分支，此保守
    风格规则仍拒绝该创意候选；不是任意C程序的完整静态分析，也不应用于原生源码。
    """
    # C translation phase 2 splices physical lines before recognizing comments.
    code = re.sub(r"\\\r?\n", "", source)
    for span in reversed(protected_spans(code)):
        code = code[:span.start] + " " * (span.end - span.start) + code[span.end:]
    wide = re.search(r"\b(?:wprintf|vwprintf|putwchar)\s*\(", code)
    narrow = re.search(r"\b(?:printf|vprintf|puts|putchar)\s*\(", code)
    return ["mixed_stdout_orientation"] if wide and narrow else []
