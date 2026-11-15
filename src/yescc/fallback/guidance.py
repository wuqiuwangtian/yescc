"""根据实际路径说明确定性兜底边界；不把语言特征提示当成自动转换。"""

from __future__ import annotations

import re

from yescc.fix.lexer import code_segments, protected_spans


def looks_like_cpp(text: str) -> bool:
    """只检查字符串和注释外的明确 C++ 风格标记，不更改语言路由。"""
    segments = code_segments(text, protected_spans(text))
    code = "".join(text[start:end] + " " for start, end in segments)
    return bool(re.search(r"\b(?:std\s*::|(?:cout|cin)\s*(?:<<|>>))", code))


def degradation_guidance(
    text: str,
    *,
    language: str | None,
    kind: str,
    offline: bool,
    model_available: bool,
    model_attempted: bool,
    budget_blocked: bool,
    rule_builds: int,
) -> list[str]:
    hints = [
        "本地兜底仅回显输入，未实现原始意图。"
        if kind == "print_input"
        else "使用本地文化梗映射，未修复原始代码。"
    ]
    if offline:
        hints.append("已启用 --offline，本次未发起模型请求。")
    elif not model_available:
        hints.append("没有可用模型配置，本次使用确定性兜底；模型配置见 README。")
    elif budget_blocked:
        hints.append("模型路径已受预算、尝试次数或时间限制，详见报告 budget 字段。")
    elif model_attempted:
        hints.append("模型候选未产出可用结果，已降级为本地兜底。")
    if language == "c":
        hints.append(
            f"规则候选尝试 {rule_builds} 次，均未编译通过。"
            if rule_builds
            else "原生 C 构建失败，规则层没有生成修复候选。"
        )
        if looks_like_cpp(text):
            hints.append("检测到 C++ 风格标记；当前 AI 目标仅 C，请改用 C 语法或模型辅助改写。")
    hints.append("用 --no-fallback 禁止创意兜底，或用 --strict 查看原生失败。")
    return hints
