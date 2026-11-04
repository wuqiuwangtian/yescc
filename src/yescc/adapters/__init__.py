"""语言适配器包。"""

from yescc.adapters.base import (
    AdapterCapabilities,
    BuildResult,
    LanguageAdapter,
    RunResult,
    ToolchainInfo,
)
from yescc.adapters.registry import detect_language, get_adapter, list_languages

__all__ = [
    "AdapterCapabilities",
    "BuildResult",
    "LanguageAdapter",
    "RunResult",
    "ToolchainInfo",
    "detect_language",
    "get_adapter",
    "list_languages",
]
