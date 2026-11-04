"""适配器注册表与语言识别。"""

from __future__ import annotations

from pathlib import Path

from yescc.adapters.base import LanguageAdapter
from yescc.adapters.c import CAdapter
from yescc.adapters.javascript import JavaScriptAdapter
from yescc.adapters.python import PythonAdapter
from yescc.errors import UnsupportedInput

_ADAPTERS: dict[str, LanguageAdapter] = {
    "python": PythonAdapter(),
    "c": CAdapter(),
    "javascript": JavaScriptAdapter(),
}

_ALIASES = {
    "py": "python",
    "python": "python",
    "python3": "python",
    "c": "c",
    "gcc": "c",
    "js": "javascript",
    "javascript": "javascript",
    "node": "javascript",
}

_EXTENSIONS = {
    extension: name for name, adapter in _ADAPTERS.items() for extension in adapter.extensions
}


def list_languages() -> list[str]:
    """已注册语言名。"""
    return sorted(_ADAPTERS)


def get_adapter(name: str) -> LanguageAdapter:
    """按语言名取适配器；未知语言抛 UnsupportedInput。"""
    adapter = _ADAPTERS.get(name)
    if adapter is None:
        supported = "、".join(list_languages())
        raise UnsupportedInput(f"不支持的语言：{name}（当前支持：{supported}）")
    return adapter


def _shebang_language(source: Path) -> str | None:
    try:
        with source.open("r", encoding="utf-8", errors="replace") as handle:
            first_line = handle.readline()
    except OSError:
        return None
    if not first_line.startswith("#!"):
        return None
    lowered = first_line.lower()
    if "python" in lowered:
        return "python"
    if "node" in lowered:
        return "javascript"
    return None


def detect_language(source: Path, explicit: str | None) -> str:
    """按显式参数、扩展名、shebang 的顺序识别语言。"""
    if explicit is not None:
        normalized = _ALIASES.get(explicit.lower())
        if normalized is None:
            supported = "、".join(list_languages())
            raise UnsupportedInput(f"不支持的语言：{explicit}（当前支持：{supported}）")
        return normalized

    by_extension = _EXTENSIONS.get(source.suffix.lower())
    if by_extension is not None:
        return by_extension

    by_shebang = _shebang_language(source)
    if by_shebang is not None:
        return by_shebang

    raise UnsupportedInput(
        f"无法从扩展名识别 {source.name} 的语言；请用 --lang 指定（当前支持："
        + "、".join(list_languages())
        + "）。"
    )
