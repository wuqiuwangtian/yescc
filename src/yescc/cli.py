"""yescc 命令行入口与参数解析。"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from yescc import __version__
from yescc.errors import FcompError, UsageError
from yescc.pipeline import Invocation, execute, explain

_HELP = """包容你的编译器（yescc）

用法：
  yescc <文件> [工具链参数...]        构建（类似 gcc）
  yescc run <文件> [-- 程序参数...]    构建后运行
  yescc <文件> --explain              查看最近一次报告

选项：
      --lang <语言>   指定语言：python | c | javascript
      --strict        原生模式：显示原始诊断；M3 起同时关闭 AI 与兜底
      --json          以 JSON 输出报告（仅构建模式）
      --no-cache      禁用缓存（预留，M3 生效）
  -h, --help          显示帮助
      --version       显示版本

说明：
  * 合法输入原样交给真实工具链，不修改源码、不调用模型。
  * 报告保存于 .yescc/<文件名>/report.json。
  * 退出码：0 成功；100 用法；101 不支持；102 构建失败；103 预算/超时；104 内部错误。
"""

Action = Literal["help", "version", "explain", "execute"]

# 这些 gcc/cc 选项的值与前一个参数分开出现，需要一起透传。
_VALUE_OPTIONS = {
    "-o",
    "-I",
    "-L",
    "-D",
    "-U",
    "-x",
    "-include",
    "-isystem",
    "-iquote",
    "-idirafter",
    "-MF",
    "-MT",
    "-MQ",
    "-Xlinker",
    "-Xpreprocessor",
    "-Xassembler",
}


@dataclass
class ParsedCommand:
    """解析后的命令。"""

    action: Action
    invocation: Invocation


def _take_value(argv: list[str], index: int, flag: str) -> tuple[str, int]:
    if index + 1 >= len(argv):
        raise UsageError(f"{flag} 需要一个值。")
    value = argv[index + 1]
    if not value:
        raise UsageError(f"{flag} 需要一个非空值。")
    return value, index + 2


def parse(argv: Sequence[str]) -> ParsedCommand:
    """解析命令行；未知选项在构建模式下作为工具链参数透传。"""
    args = list(argv)
    mode: str = "build"
    if args and args[0] == "run":
        mode = "run"
        args = args[1:]

    invocation = Invocation(mode="run" if mode == "run" else "build")
    index = 0
    while index < len(args):
        arg = args[index]

        if arg in ("-h", "--help"):
            return ParsedCommand(action="help", invocation=invocation)
        if arg == "--version":
            return ParsedCommand(action="version", invocation=invocation)
        if arg == "--strict":
            invocation.strict = True
            index += 1
            continue
        if arg == "--json":
            invocation.json_output = True
            index += 1
            continue
        if arg == "--no-cache":
            invocation.no_cache = True
            index += 1
            continue
        if arg == "--explain":
            invocation.explain = True
            index += 1
            continue
        if arg == "--lang":
            value, index = _take_value(args, index, "--lang")
            invocation.lang = value
            continue
        if arg.startswith("--lang="):
            invocation.lang = arg.split("=", 1)[1]
            if not invocation.lang:
                raise UsageError("--lang 需要一个非空值。")
            index += 1
            continue
        if arg == "--":
            if mode == "run":
                invocation.run_args.extend(args[index + 1 :])
            else:
                invocation.passthrough.extend(args[index + 1 :])
            break
        if arg.startswith("-") and arg != "-":
            if mode == "run":
                raise UsageError(
                    f"run 模式不接受工具链参数：{arg}；程序参数请放在 -- 之后。"
                )
            invocation.passthrough.append(arg)
            if arg in _VALUE_OPTIONS:
                value, index = _take_value(args, index, arg)
                invocation.passthrough.append(value)
            else:
                index += 1
            continue

        if invocation.source is not None:
            raise UsageError("v0.1 仅支持单个源文件；多文件与项目构建不在本版范围。")
        invocation.source = Path(arg)
        index += 1

    if invocation.explain:
        if mode == "run":
            raise UsageError("--explain 只用于构建模式。")
        return ParsedCommand(action="explain", invocation=invocation)

    if invocation.json_output and mode == "run":
        raise UsageError("run 模式下 --json 不可用：程序本身拥有 stdout。")

    if invocation.source is None:
        raise UsageError("缺少输入文件。用法见 `yescc --help`。")

    return ParsedCommand(action="execute", invocation=invocation)


def _configure_stdio() -> None:
    """非交互式管道下固定 UTF-8。

    Windows 的默认代码页会把中文转成 \\uXXXX 转义甚至直接抛 UnicodeEncodeError；
    终端交互时保持原样，避免破坏控制台编码。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream.isatty():
                continue
            reconfigure = getattr(stream, "reconfigure", None)
            if reconfigure is not None:
                reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            continue


def main(argv: Sequence[str] | None = None) -> int:
    """程序入口：返回退出码。"""
    _configure_stdio()
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        parsed = parse(args)
        if parsed.action == "help":
            print(_HELP)
            return 0
        if parsed.action == "version":
            print(f"yescc {__version__}")
            return 0
        if parsed.action == "explain":
            return explain(parsed.invocation.source, parsed.invocation.json_output)
        return execute(args, parsed.invocation)
    except FcompError as exc:
        print(f"yescc: {exc.message}", file=sys.stderr)
        return int(exc.exit_code)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
