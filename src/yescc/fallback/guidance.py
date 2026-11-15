"""根据实际路径说明确定性兜底边界；不把语言特征提示当成自动转换。"""

from __future__ import annotations

import re

from yescc.fix.lexer import code_segments, protected_spans


def looks_like_cpp(text: str) -> bool:
    """只检查字符串和注释外的明确 C++ 风格标记，不更改语言路由。"""
    segments = code_segments(text, protected_spans(text))
    code = "".join(text[start:end] + " " for start, end in segments)
    return bool(re.search(r"\b(?:std\s*::|(?:cout|cin)\s*(?:<<|>>))", code))


def _token_limit(settings: object) -> int | None:
    if not isinstance(settings, dict):
        return None
    value = settings.get("max_completion_tokens", settings.get("max_tokens"))
    return value if type(value) is int and value > 0 else None


def model_output_guidance(model: dict[str, object] | None) -> list[str]:
    """Describe observed truncation without inferring it from configuration alone."""
    if model is None:
        return []
    responses = model.get("responses")
    truncated = [
        r for r in responses if isinstance(r, dict) and r.get("finish_reason") == "length"
    ] if isinstance(responses, list) else []
    # Old reports can lack per-response metadata. Do not pair their latest
    # configured settings with an earlier HTTP request or a cached candidate.
    if not truncated and model.get("response_source") == "http" \
            and model.get("finish_reason") == "length":
        truncated = [{"request_settings": model.get("request_settings")}]
    hints: list[str] = []
    if truncated:
        response = truncated[-1]
        hints.append("有模型响应达到输出上限（finish_reason=length），该响应未被采用。")
        actual = _token_limit(response.get("request_settings"))
        configured = _token_limit(response.get("configured_settings"))
        if actual is not None:
            if configured is not None and configured > actual:
                hints.append(
                    f"该响应配置上限为 {configured} tokens，受本次预算裁剪，"
                    f"实际请求上限为 {actual} tokens。"
                )
            else:
                hints.append(f"该响应实际请求上限为 {actual} tokens。")
        reasoning = response.get("reasoning_tokens")
        if type(reasoning) is int and reasoning > 0:
            hints.append(f"供应商报告该响应使用 {reasoning} 个推理 tokens，与正文共享输出额度。")
        configured_settings = response.get("configured_settings")
        request_settings = response.get("request_settings")
        controls = (
            configured_settings if isinstance(configured_settings, dict) else request_settings
        )
        thinking = controls.get("thinking") if isinstance(controls, dict) else None
        effort = controls.get("reasoning_effort") if isinstance(controls, dict) else None
        if (isinstance(thinking, dict) and thinking.get("type") == "enabled") \
                or (isinstance(effort, str) and effort != "none"):
            if response.get("provider") == "deepseek":
                hints.append(
                    "仅使用 DeepSeek 时，可尝试 --thinking disabled --reasoning-effort none "
                    "关闭推理；多供应商配置请分别调整。本次未自动切换设置。"
                )
            else:
                hints.append("若模型支持，可尝试降低推理等级后再运行；本次未自动切换设置。")
        hints.append("单独提高 --max-output-tokens 仍受 --budget 限制，不会自动增加费用上限。")
    if model.get("cost_known") is False and model.get("calls"):
        hints.append("模型总费用未知；报告中的 cost_usd 仅为已知费用小计。")
    return hints


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
    model: dict[str, object] | None = None,
    budget_reason: str | None = None,
) -> list[str]:
    hints = [
        "本地兜底仅回显输入，未实现原始意图。"
        if kind == "print_input"
        else "使用本地文化梗映射，未修复原始代码。"
    ]
    output_hints = [] if offline else model_output_guidance(model)
    hints.extend(output_hints)
    if offline:
        hints.append("已启用 --offline，本次未发起模型请求。")
    elif not model_available:
        hints.append("没有可用模型配置，本次使用确定性兜底；模型配置见 README。")
    elif budget_blocked:
        fee_reasons = {
            "剩余费用不足以支付输入的最大估算费用":
                "剩余预算不足以预留下次请求的输入费用，未继续请求。",
            "剩余费用不足以生成一个输出 token":
                "剩余预算不足以预留下次请求的输出费用，未继续请求。",
            "剩余费用不足以支付本次请求的最大估算费用":
                "剩余预算不足以下次请求的保守费用预留，未继续请求。",
        }
        hints.append(fee_reasons.get(budget_reason, f"模型处理停止：{budget_reason}。")
                     if budget_reason else
                     "模型路径已受预算、尝试次数或时间限制，详见报告 budget 字段。")
    elif model_attempted and not output_hints:
        hints.append("模型候选未产出可用结果，已降级为本地兜底。")
    if language == "c":
        hints.append(
            f"规则候选尝试 {rule_builds} 次，均未编译通过。"
            if rule_builds
            else "原生 C 构建失败，规则层没有生成修复候选。"
        )
        if looks_like_cpp(text):
            hints.append("检测到 C++ 风格标记；当前 AI 目标仅 C，请改用 C 语法或模型辅助改写。")
    hints.append(
        "若启用了 --play，请先去掉它；再用 --no-fallback 禁止创意兜底，"
        "或用 --strict 查看原生失败。"
    )
    return hints
