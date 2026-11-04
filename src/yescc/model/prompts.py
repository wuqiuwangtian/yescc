"""提示词与响应解析。"""

from __future__ import annotations

import json

from yescc.model.base import Message, ModelResponseError

PROMPT_VERSION = "m3-1"

_SYSTEM = (
    "You are YesCC, a compiler repair and code generation assistant. "
    "Respond with exactly one JSON object and no markdown fences or commentary."
)


def repair_messages(
    source: str,
    filename: str,
    diagnostics: str,
    feedback: str = "",
) -> list[Message]:
    user = f"""Fix the C compilation errors in the file below with the smallest possible change.

Rules:
- Return JSON: {{"diff": "<unified diff>", "assumptions": ["..."]}}
- The diff must apply exactly to the given source, with 3 lines of context.
- Do not rewrite unrelated lines. Do not change the contents of strings or comments.
- If you cannot fix it, return {{"diff": "", "assumptions": ["..."]}}.

File: {filename}

Source:
{source}

Diagnostics:
{diagnostics}
"""
    if feedback:
        user += f"\nThe previous attempt failed: {feedback}\n"
    return [Message(role="system", content=_SYSTEM), Message(role="user", content=user)]


def generate_messages(text: str, *, creative: bool, feedback: str = "") -> list[Message]:
    if creative:
        instruction = (
            "The input below is not a normal program specification. Write a short, "
            "self-contained C program inspired by it. It is fine if the relationship "
            "is playful or surprising, but the program must compile and run."
        )
    else:
        instruction = (
            "Write a complete, self-contained C program that satisfies the request below. "
            "Infer missing details with conservative defaults and list them as assumptions."
        )
    user = f"""{instruction}

Rules:
- Return JSON: {{"source": "<complete C program>", "assumptions": ["..."], "io_contract": "..."}}
- Use only the C standard library; include the headers you use.
- Do not use network, file system, system(), exec, or shell commands.
- Read stdin only if the request needs input; print results to stdout.
- Keep it under 120 lines.

Input:
{text}
"""
    if feedback:
        user += f"\nThe previous program failed to compile: {feedback}\n"
    return [Message(role="system", content=_SYSTEM), Message(role="user", content=user)]


def parse_json_object(text: str) -> dict[str, object]:
    """从模型输出中提取 JSON 对象；容忍代码围栏与前后说明。"""
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ModelResponseError("模型输出中没有 JSON 对象")
    try:
        data = json.loads(stripped[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ModelResponseError(f"模型输出不是合法 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise ModelResponseError("模型输出的 JSON 不是对象")
    return data
