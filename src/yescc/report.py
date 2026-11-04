"""报告读写与摘要。

schema v1：只增不改；结构见 docs/项目路线图与发布计划.md §6.6。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from yescc.util import json_safe

SCHEMA_VERSION = "1"


def report_path(cwd: Path, source: Path) -> Path:
    """报告固定落在工作目录的 .yescc/<stem>/report.json。"""
    return cwd / ".yescc" / source.stem / "report.json"


def write_report(path: Path, payload: dict[str, Any]) -> None:
    """原子写入报告，避免半截文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    text = json.dumps(json_safe(payload), ensure_ascii=False, indent=2) + "\n"
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def load_report(path: Path) -> dict[str, Any] | None:
    """读取报告；不存在或损坏时返回 None。"""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def summarize(payload: dict[str, Any]) -> str:
    """把报告转成简短的人类可读摘要。"""
    result = payload.get("result", {})
    status = result.get("status", "unknown")
    language = payload.get("invocation", {}).get("lang", "?")
    toolchain = payload.get("toolchain", {})
    timings = payload.get("timings", {})
    artifact = result.get("artifact")

    command = toolchain.get("command", "?")
    version = toolchain.get("version", "")
    lines = [
        f"状态：{status}",
        f"语言：{language}（{command} {version}）".rstrip(),
        f"耗时：{timings.get('total_ms', 0)} ms（工具链 {timings.get('toolchain_ms', 0)} ms）",
    ]
    if artifact:
        lines.append(f"产物：{artifact}")
    if result.get("program_exit") is not None:
        lines.append(f"程序退出码：{result['program_exit']}")
    return "\n".join(lines)
