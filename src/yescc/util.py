"""通用工具函数：哈希、编码、截断、子进程。"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from yescc.errors import ToolchainUnavailable

_MAX_CAPTURE_CHARS = 4000


def is_frozen() -> bool:
    """PyInstaller 的入口是 bootloader，不能用作外部 Python 解释器。"""
    return bool(getattr(sys, "frozen", False))


def external_environment(environment: Mapping[str, str] | None = None) -> dict[str, str]:
    """只在冻结版中撤销 loader 对外部程序的库搜索路径影响。"""
    env = dict(os.environ if environment is None else environment)
    if not is_frozen():
        return env
    if sys.platform == "win32":
        bundle = getattr(sys, "_MEIPASS", None)
        if bundle and "PATH" in env:
            root = Path(bundle).resolve()
            env["PATH"] = os.pathsep.join(
                entry
                for entry in env["PATH"].split(os.pathsep)
                if not entry or not Path(entry).resolve().is_relative_to(root)
            )
    elif sys.platform != "darwin":
        original = env.get("LD_LIBRARY_PATH_ORIG")
        if original is None:
            env.pop("LD_LIBRARY_PATH", None)
        else:
            env["LD_LIBRARY_PATH"] = original
    return env


def _windows_dll_directory() -> str:
    if sys.platform != "win32":
        raise OSError("DLL search directories are only available on Windows")
    import ctypes

    function = ctypes.windll.kernel32.GetDllDirectoryW
    function.argtypes = [ctypes.c_ulong, ctypes.c_wchar_p]
    function.restype = ctypes.c_ulong
    size = function(0, None)
    buffer = ctypes.create_unicode_buffer(size + 1)
    function(len(buffer), buffer)
    return buffer.value


def _set_windows_dll_directory(directory: str | None) -> None:
    if sys.platform != "win32":
        raise OSError("DLL search directories are only available on Windows")
    import ctypes

    function = ctypes.windll.kernel32.SetDllDirectoryW
    function.argtypes = [ctypes.c_wchar_p]
    function.restype = ctypes.c_int
    if not function(directory):
        raise ctypes.WinError()


@contextmanager
def external_command_context() -> Iterator[None]:
    """Windows 执行外部程序时暂时恢复系统 DLL 搜索路径，之后还原主进程。"""
    if not is_frozen() or sys.platform != "win32":
        yield
        return
    original = _windows_dll_directory()
    _set_windows_dll_directory(None)
    try:
        yield
    finally:
        _set_windows_dll_directory(original or None)


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
    with external_command_context():
        return subprocess.run(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            env=external_environment(),
        )


def run_inherit(command: list[str], cwd: Path) -> int:
    """执行用户程序：继承 stdin/stdout/stderr，返回其退出码。"""
    try:
        with external_command_context():
            completed = subprocess.run(
                command,
                cwd=str(cwd),
                check=False,
                env=external_environment(),
            )
    except KeyboardInterrupt:
        return 130
    return completed.returncode


def python_executable() -> str:
    """源码/wheel 用当前解释器；冻结版使用 PATH 中真实可运行的 Python 3。"""
    if not is_frozen():
        return sys.executable
    search_path = external_environment().get("PATH", "")
    candidates = ("python", "python3") if sys.platform == "win32" else ("python3", "python")
    checked: set[str] = set()
    for candidate in candidates:
        executable = shutil.which(candidate, path=search_path)
        if executable is None or executable in checked:
            continue
        checked.add(executable)
        if Path(executable).resolve() == Path(sys.executable).resolve():
            continue
        if (
            sys.platform == "win32"
            and "microsoft/windowsapps/" in executable.replace("\\", "/").lower()
        ):
            continue
        try:
            with external_command_context():
                result = subprocess.run(
                    [executable, "-I", "-c", "import sys; print(sys.version_info[0])"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=external_environment(),
                    timeout=3,
                    check=False,
                )
            if result.returncode == 0 and result.stdout.strip() == "3":
                return executable
        except (OSError, subprocess.SubprocessError):
            continue
    raise ToolchainUnavailable(
        "未找到可运行的 Python 3。独立二进制的 Python 原生直通需要 PATH 中的 python3/python。"
    )


def json_safe(value: Any) -> Any:
    """把 Path 等类型转换为可序列化值。"""
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value
