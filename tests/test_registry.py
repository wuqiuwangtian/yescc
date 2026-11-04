"""语言识别与适配器注册表测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from yescc.adapters import detect_language, get_adapter, list_languages
from yescc.errors import UnsupportedInput


def test_registered_languages() -> None:
    assert list_languages() == ["c", "javascript", "python"]


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("main.py", "python"),
        ("main.c", "c"),
        ("main.js", "javascript"),
        ("main.mjs", "javascript"),
        ("main.cjs", "javascript"),
    ],
)
def test_detect_by_extension(filename: str, expected: str) -> None:
    assert detect_language(Path(filename), None) == expected


@pytest.mark.parametrize(
    ("explicit", "expected"),
    [("py", "python"), ("PYTHON", "python"), ("gcc", "c"), ("node", "javascript")],
)
def test_detect_by_explicit_alias(explicit: str, expected: str) -> None:
    assert detect_language(Path("unknown.txt"), explicit) == expected


def test_detect_by_shebang(tmp_path: Path) -> None:
    script = tmp_path / "run"
    script.write_text("#!/usr/bin/env python3\nprint('hi')\n", encoding="utf-8")
    assert detect_language(script, None) == "python"


def test_detect_unknown_extension(tmp_path: Path) -> None:
    with pytest.raises(UnsupportedInput):
        detect_language(Path("notes.txt"), None)


def test_detect_unknown_explicit() -> None:
    with pytest.raises(UnsupportedInput):
        detect_language(Path("main.c"), "rust")


def test_get_adapter_capabilities() -> None:
    adapter = get_adapter("python")
    capabilities = adapter.capabilities()
    assert capabilities.native_build and capabilities.run
    assert not capabilities.model_fix
