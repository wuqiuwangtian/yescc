"""确定性创意兜底：不需要模型也能把一段文本变成可运行程序。"""

from __future__ import annotations

from dataclasses import dataclass

EASTER_EGGS: dict[str, str] = {
    "天王盖地虎": "宝塔镇河妖",
    "sudo make me a sandwich": "Okay.",
    "今天不想上班": "今天也不上班。",
}

_MAX_MESSAGE = 2000


@dataclass(frozen=True)
class FallbackOutput:
    kind: str
    message: str


def fallback_output(text: str) -> FallbackOutput:
    """选择兜底输出：已知梗直接映射，否则原样打印输入。"""
    stripped = text.strip()
    if stripped in EASTER_EGGS:
        return FallbackOutput(kind="easter_egg", message=EASTER_EGGS[stripped])
    message = stripped[:_MAX_MESSAGE]
    return FallbackOutput(kind="print_input", message=message)


def generate_c_program(message: str) -> str:
    """生成一个打印给定文本的 C 程序。"""
    escaped = (
        message.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r", "")
        .replace("\n", "\\n")
    )
    return (
        "#include <stdio.h>\n"
        "\n"
        "int main(void) {\n"
        f'    puts("{escaped}");\n'
        "    return 0;\n"
        "}\n"
    )
