"""fcomp 命令行入口。

M0 仅提供 ``--version`` 与用法提示；编译、修复与运行能力在 M1 之后接入。
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from fcomp import __version__
from fcomp.errors import ExitCode

_DESCRIPTION = "包容你的编译器：合法代码原生直通，出错或在预算内修复后真实执行。"


def build_parser() -> argparse.ArgumentParser:
    """构造参数解析器。"""
    parser = argparse.ArgumentParser(prog="fcomp", description=_DESCRIPTION)
    parser.add_argument("--version", action="version", version=f"fcomp {__version__}")
    parser.add_argument(
        "input",
        nargs="?",
        help="源文件或自然语言描述文件（M1 起可用）",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """程序入口：返回退出码。"""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.input is None:
        parser.print_help(sys.stderr)
        return int(ExitCode.USAGE)
    print(
        "fcomp 仍处于 M0 骨架阶段：编译与修复能力尚未实现，"
        "进度见 docs/项目路线图与发布计划.md。",
        file=sys.stderr,
    )
    return int(ExitCode.UNSUPPORTED)
