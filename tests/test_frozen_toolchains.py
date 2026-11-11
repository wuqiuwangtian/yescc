"""冻结 CLI 必须调用真实外部工具链，并撤销 loader 对子进程的影响。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from yescc import util
from yescc.adapters import python as python_adapter
from yescc.errors import ToolchainUnavailable
from yescc.sandbox import runner

PYTHON = sys.executable


def freeze(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)


def test_unfrozen_preserves_interpreter_and_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    env = {"PATH": "user-path", "LD_LIBRARY_PATH": "user-libs", "CUSTOM": "value"}
    assert util.external_environment(env) == env
    assert util.external_environment(env) is not env
    assert util.python_executable() == PYTHON


@pytest.mark.parametrize("original", [None, "", "/custom/lib:/other/lib"])
def test_frozen_linux_restores_original_library_path(
    original: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    freeze(monkeypatch)
    monkeypatch.setattr(sys, "platform", "linux")
    env = {"PATH": "user-path", "LD_LIBRARY_PATH": "/bundle/internal", "CUSTOM": "keep"}
    if original is not None:
        env["LD_LIBRARY_PATH_ORIG"] = original
    restored = util.external_environment(env)
    assert restored.get("LD_LIBRARY_PATH") == original
    assert restored["PATH"] == "user-path" and restored["CUSTOM"] == "keep"
    assert env["LD_LIBRARY_PATH"] == "/bundle/internal"


def test_filtered_environment_does_not_reintroduce_host_libraries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    freeze(monkeypatch)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/host/lib")
    assert util.external_environment({"PATH": "/bin"}) == {"PATH": "/bin"}


def test_frozen_windows_path_removes_only_bundle_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    freeze(monkeypatch)
    monkeypatch.setattr(sys, "platform", "win32")
    bundle = tmp_path / "bundle"
    user_path = tmp_path / "bundle-neighbor"
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    env = {"PATH": os.pathsep.join([str(bundle), str(bundle / "dll"), str(user_path), ""])}
    assert util.external_environment(env)["PATH"] == os.pathsep.join([str(user_path), ""])


def test_windows_dll_search_directory_restored_after_launch_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    freeze(monkeypatch)
    monkeypatch.setattr(sys, "platform", "win32")
    values: list[str | None] = []
    monkeypatch.setattr(util, "_windows_dll_directory", lambda: "original-directory")
    monkeypatch.setattr(util, "_set_windows_dll_directory", values.append)
    with pytest.raises(OSError), util.external_command_context():
        assert values == [None]
        raise OSError("external process failed")
    assert values == [None, "original-directory"]


def test_frozen_python_selects_external_interpreter(monkeypatch: pytest.MonkeyPatch) -> None:
    freeze(monkeypatch)
    monkeypatch.setattr(sys, "executable", str(Path(PYTHON).with_name("yescc-bootloader")))
    monkeypatch.setattr(util.shutil, "which", lambda *args, **kwargs: PYTHON)
    assert util.python_executable() == PYTHON


def test_frozen_python_never_recurses_into_bootloader(monkeypatch: pytest.MonkeyPatch) -> None:
    freeze(monkeypatch)
    monkeypatch.setattr(util.shutil, "which", lambda *args, **kwargs: sys.executable)
    with pytest.raises(ToolchainUnavailable):
        util.python_executable()


def test_frozen_python_missing_toolchain_is_environment_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    freeze(monkeypatch)
    monkeypatch.setattr(util.shutil, "which", lambda *args, **kwargs: None)
    with pytest.raises(ToolchainUnavailable):
        python_adapter.PythonAdapter().toolchain()


def test_frozen_python_skips_windows_store_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    freeze(monkeypatch)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "executable", str(Path(PYTHON).with_name("yescc-bootloader")))
    monkeypatch.setattr(util, "_windows_dll_directory", lambda: "")
    monkeypatch.setattr(util, "_set_windows_dll_directory", lambda directory: None)
    calls: list[str] = []

    def which(name: str, **kwargs: Any) -> str:
        return "/Users/test/Microsoft/WindowsApps/python.exe" if name == "python" else PYTHON

    def probe(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(command[0])
        return subprocess.CompletedProcess(command, 0, "3\n", "")

    monkeypatch.setattr(util.shutil, "which", which)
    monkeypatch.setattr(util.subprocess, "run", probe)
    assert util.python_executable() == PYTHON
    assert calls == [PYTHON]


def test_frozen_python_uses_external_version_and_cache_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    freeze(monkeypatch)
    monkeypatch.setattr(sys, "executable", str(Path(PYTHON).with_name("yescc-bootloader")))
    monkeypatch.setattr(util.shutil, "which", lambda *args, **kwargs: PYTHON)
    monkeypatch.setattr(python_adapter.platform, "python_version", lambda: "embedded-version")
    monkeypatch.setenv("PYTHONPYCACHEPREFIX", str(tmp_path / "external-cache"))
    monkeypatch.setenv("PYTHONOPTIMIZE", "1")
    source = tmp_path / "source.py"
    source.write_text("print('external')\n")
    adapter = python_adapter.PythonAdapter()
    info = adapter.toolchain()
    assert info.command == PYTHON and info.version != "embedded-version"
    expected_version = subprocess.check_output(
        [PYTHON, "-I", "-c", "import platform;print(platform.python_version())"], text=True
    ).strip()
    assert info.version == expected_version
    result = adapter.build(source, [], tmp_path)
    assert result.returncode == 0
    assert result.command[0] == PYTHON
    assert result.artifact is not None and result.artifact.exists()
    assert result.artifact.is_relative_to(tmp_path / "external-cache")
    assert result.artifact.name.endswith(".opt-1.pyc")


@pytest.mark.parametrize("inherit", [False, True])
def test_native_subprocess_wrappers_receive_sanitized_environment(
    inherit: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    freeze(monkeypatch)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/bundle/internal")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/user/original")
    observed: list[dict[str, str]] = []

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        observed.append(kwargs["env"])
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(util.subprocess, "run", run)
    if inherit:
        assert util.run_inherit(["external"], tmp_path) == 0
    else:
        assert util.run_capture(["external"], tmp_path).returncode == 0
    assert observed[0]["LD_LIBRARY_PATH"] == "/user/original"
    assert os.environ["LD_LIBRARY_PATH"] == "/bundle/internal"


def test_frozen_ai_program_uses_clean_child_library_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    freeze(monkeypatch)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/bundle/internal")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/host/original")
    monkeypatch.setattr(runner, "_network_isolation_supported", lambda: False)
    output = tmp_path / "environment.json"
    code = (
        "import json,os;from pathlib import Path;"
        f"Path({str(output)!r}).write_text(json.dumps(dict(os.environ)))"
    )
    outcome = runner.run_ai_command([PYTHON, "-c", code], unsafe=False, cwd=tmp_path)
    assert outcome.exit_code == 0 and outcome.environment_filtered
    actual = json.loads(output.read_text())
    assert "LD_LIBRARY_PATH" not in actual
    assert "LD_LIBRARY_PATH_ORIG" not in actual
