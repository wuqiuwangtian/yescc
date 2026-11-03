"""通用工具函数：哈希、编码、截断、子进程。"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

_MAX_CAPTURE_CHARS = 4000


def sha256_file(path: Path) -> str:
    """返回文件内容的 sha256。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_text_utf8(path: Path) -> tuple[str, str]:
    """读取 UTF-8（含 BOM）文本，返回 (文本, 编码名)。"""
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig"), "utf-8-sig"
    return raw.decode("utf-8"), "utf-8"


def truncate(text: str, limit: int = _MAX_CAPTURE_CHARS) -> str:
    """截断过长的输出，避免报告膨胀。"""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…（已截断，原长 {len(text)} 字符）"


def display_path(path: Path, cwd: Path) -> str:
    """尽量以相对路径展示，跨平台一致。"""
    try:
        return str(path.resolve().relative_to(cwd.resolve()))
    except ValueError:
        return str(path)


def run_capture(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """在 cwd 中执行命令并捕获输出；不修改用户环境。"""
    return subprocess.run(
        command,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        env=dict(os.environ),
    )


def run_inherit(command: list[str], cwd: Path) -> int:
    """执行用户程序：继承 stdin/stdout/stderr，返回其退出码。"""
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            check=False,
            env=dict(os.environ),
        )
    except KeyboardInterrupt:
        return 130
    return completed.returncode


def python_executable() -> str:
    """当前解释器路径；Windows 下避免使用 python 别名。"""
    return sys.executable


def json_safe(value: Any) -> Any:
    """把 Path 等类型转换为可序列化值。"""
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value
