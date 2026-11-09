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
    repair = result.get("repair")
    if isinstance(repair, dict):
        lines.append(f"修复规则：{', '.join(repair.get('rules', [])) or '无'}")
        lines.append(f"改动字符：{repair.get('changed', 0)}")
        patched = repair.get("patched_source")
        if patched:
            lines.append(f"修复后源码：{patched}")
    fallback = result.get("fallback")
    if isinstance(fallback, dict):
        lines.append(f"兜底类型：{fallback.get('kind', 'unknown')}")
        lines.append(f"兜底原因：{fallback.get('reason', '')}")
        guidance = fallback.get("guidance")
        if isinstance(guidance, list):
            lines.extend(str(item) for item in guidance)
        source = fallback.get("source")
        if source:
            lines.append(f"兜底源码：{source}")
    generated = result.get("generated")
    if isinstance(generated, dict):
        lines.append(f"生成源码：{generated.get('source', '')}")
        contract = generated.get("io_contract")
        if contract:
            lines.append(f"输入输出约定：{contract}")
    model = payload.get("model")
    if isinstance(model, dict) and model.get("calls"):
        cost = f"${model.get('cost_usd', 0)}" if model.get("cost_known", True) else "费用未知"
        lines.append(
            f"模型：{model.get('provider')}/{model.get('name')}（{model.get('tier')}，"
            f"{model.get('calls')} 次，{model.get('prompt_tokens', 0)}+"
            f"{model.get('completion_tokens', 0)} tokens，{cost}）"
        )
    assumptions = result.get("repair") or {}
    if isinstance(assumptions, dict):
        items = assumptions.get("assumptions")
        if isinstance(items, list) and items:
            lines.append("假设：" + "；".join(str(item) for item in items))
    return "\n".join(lines)
