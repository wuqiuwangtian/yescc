"""临时构建复制产物时，原输入及其文件系统别名必须保持原字节。"""

from __future__ import annotations

import os
import shutil
from collections.abc import Sequence
from pathlib import Path

import pytest

from yescc.adapters.base import BuildResult, ToolchainInfo
from yescc.adapters.c import CAdapter
from yescc.errors import UsageError
from yescc.fix.engine import build_in_temp

_PROGRAM = '#include <stdio.h>\nint main(void) { puts("protected-source-ok"); return 0; }\n'


class RecordingAdapter(CAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[Path, list[str], Path]] = []

    def build(self, source: Path, passthrough: Sequence[str], cwd: Path) -> BuildResult:
        self.calls.append((source, list(passthrough), cwd))
        artifact = cwd / "a.out"
        artifact.write_bytes(b"fake-executable")
        return BuildResult(["fake-gcc", str(source), *passthrough], 0, "", "", artifact)


def test_default_output_matching_input_preserves_source_and_returns_runnable_artifact(
    tmp_path: Path,
) -> None:
    gcc = shutil.which("gcc")
    if gcc is None:
        pytest.skip("需要真实 GCC")
    adapter = CAdapter()
    adapter._cached = ToolchainInfo("c", gcc, "test")
    artifact_name = "a.exe" if os.name == "nt" else "a.out"
    source = tmp_path / artifact_name
    source.write_text("original natural-language source\n", encoding="utf-8")
    result = build_in_temp(
        adapter,
        _PROGRAM,
        "generated.c",
        ["-O2", "-std=c11"],
        tmp_path,
        tmp_path,
        protected_source=source,
    )
    assert result.build.returncode == 0
    assert result.artifact == tmp_path / ".yescc" / source.stem / artifact_name
    assert source.read_text(encoding="utf-8") == "original natural-language source\n"
    assert result.artifact is not None
    from yescc.sandbox.creative import run_creative_command

    execution = run_creative_command([str(result.artifact)], unsafe=False, cwd=tmp_path)
    assert execution.sandbox.exit_code == 0
    assert execution.stdout.strip() == b"protected-source-ok"


@pytest.mark.parametrize("joined", [False, True])
def test_explicit_source_output_is_rejected_before_compilation(
    tmp_path: Path,
    joined: bool,
) -> None:
    source = tmp_path / "input.c"
    source.write_text("original bytes\n", encoding="utf-8")
    adapter = RecordingAdapter()
    arguments = ["-o" + str(source)] if joined else ["-o", str(source)]
    with pytest.raises(UsageError, match="拒绝覆盖") as caught:
        build_in_temp(adapter, _PROGRAM, source.name, arguments, tmp_path, tmp_path)
    assert caught.value.exit_code == 100
    assert not adapter.calls
    assert source.read_text(encoding="utf-8") == "original bytes\n"


@pytest.mark.parametrize("alias", ["hardlink", "symlink"])
def test_explicit_source_alias_is_rejected_before_compilation(
    tmp_path: Path,
    alias: str,
) -> None:
    source = tmp_path / "input.c"
    source.write_text("original bytes\n", encoding="utf-8")
    destination = tmp_path / "alias"
    try:
        if alias == "hardlink":
            destination.hardlink_to(source)
        else:
            destination.symlink_to(source)
    except OSError as exc:
        pytest.skip(f"当前平台不能创建 {alias}: {exc}")
    adapter = RecordingAdapter()
    with pytest.raises(UsageError, match="拒绝覆盖"):
        build_in_temp(
            adapter,
            _PROGRAM,
            "generated.c",
            ["-o", str(destination)],
            tmp_path,
            tmp_path,
            protected_source=source,
        )
    assert not adapter.calls
    assert source.read_text(encoding="utf-8") == "original bytes\n"


def test_legacy_rule_call_infers_original_source_and_redirects_default(tmp_path: Path) -> None:
    source = tmp_path / "a.out"
    source.write_text("original bytes\n", encoding="utf-8")
    adapter = RecordingAdapter()
    result = build_in_temp(adapter, _PROGRAM, source.name, [], tmp_path, tmp_path)
    assert result.artifact == tmp_path / ".yescc" / "a" / "a.out"
    assert source.read_text(encoding="utf-8") == "original bytes\n"
    assert len(adapter.calls) == 1


@pytest.mark.parametrize("explicit", [False, True])
def test_unrelated_destination_and_compiler_arguments_are_unchanged(
    tmp_path: Path,
    explicit: bool,
) -> None:
    source = tmp_path / "input.c"
    source.write_text("original bytes\n", encoding="utf-8")
    adapter = RecordingAdapter()
    arguments = ["-O2", "-std=c11"]
    if explicit:
        arguments.extend(["-o", "bin/program"])
    result = build_in_temp(adapter, _PROGRAM, source.name, arguments, tmp_path, tmp_path)
    assert result.artifact == tmp_path / ("bin/program" if explicit else "a.out")
    assert source.read_text(encoding="utf-8") == "original bytes\n"
    assert len(adapter.calls) == 1
    assert adapter.calls[0][1] == ["-O2", "-std=c11", "-iquote", str(tmp_path)]


@pytest.mark.parametrize("alias", ["hardlink", "symlink"])
def test_unsafe_fallback_alias_is_also_rejected(tmp_path: Path, alias: str) -> None:
    source = tmp_path / "a.out"
    source.write_text("original bytes\n", encoding="utf-8")
    fallback = tmp_path / ".yescc" / "a" / "a.out"
    fallback.parent.mkdir(parents=True)
    try:
        if alias == "hardlink":
            fallback.hardlink_to(source)
        else:
            fallback.symlink_to(source)
    except OSError as exc:
        pytest.skip(f"当前平台不能创建 {alias}: {exc}")
    adapter = RecordingAdapter()
    with pytest.raises(UsageError, match="备选输出路径"):
        build_in_temp(adapter, _PROGRAM, source.name, [], tmp_path, tmp_path)
    assert len(adapter.calls) == 1
    assert source.read_text(encoding="utf-8") == "original bytes\n"


def test_checked_build_still_compiles_once_with_original_timeout_and_extra_flags(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yescc.sandbox import undefined

    source = tmp_path / "a.out"
    source.write_text("original bytes\n", encoding="utf-8")
    adapter = RecordingAdapter()
    adapter._cached = ToolchainInfo("c", "current-gcc", "test")
    calls: list[tuple[str, list[str], float]] = []

    def bounded(
        compiler: str,
        temporary_source: Path,
        arguments: Sequence[str],
        cwd: Path,
        *,
        timeout_s: float,
    ) -> BuildResult:
        calls.append((compiler, list(arguments), timeout_s))
        artifact = cwd / "a.out"
        artifact.write_bytes(b"checked-executable")
        return BuildResult([compiler, str(temporary_source), *arguments], 0, "", "", artifact)

    monkeypatch.setattr(undefined, "bounded_c_build", bounded)
    result = build_in_temp(
        adapter,
        _PROGRAM,
        "generated.c",
        ["-O2", "-std=c11"],
        tmp_path,
        tmp_path,
        extra_flags=["-fsanitize=undefined", "-fno-sanitize-recover=undefined"],
        compile_timeout_s=7.5,
        protected_source=source,
    )
    assert result.artifact == tmp_path / ".yescc" / "a" / "a.out"
    assert source.read_text(encoding="utf-8") == "original bytes\n"
    assert not adapter.calls
    assert calls == [
        (
            "current-gcc",
            [
                "-O2",
                "-std=c11",
                "-iquote",
                str(tmp_path),
                "-fsanitize=undefined",
                "-fno-sanitize-recover=undefined",
            ],
            7.5,
        )
    ]


@pytest.mark.parametrize("alias", ["hardlink", "symlink"])
def test_default_output_alias_redirects_away_from_input(tmp_path: Path, alias: str) -> None:
    source = tmp_path / "input.c"
    source.write_text("original bytes\n", encoding="utf-8")
    default = tmp_path / "a.out"
    try:
        if alias == "hardlink":
            default.hardlink_to(source)
        else:
            default.symlink_to(source)
    except OSError as exc:
        pytest.skip(f"当前平台不能创建 {alias}: {exc}")
    result = build_in_temp(RecordingAdapter(), _PROGRAM, source.name, [], tmp_path, tmp_path)
    assert result.artifact == tmp_path / ".yescc" / "input" / "a.out"
    assert source.read_text(encoding="utf-8") == "original bytes\n"
    assert default.read_text(encoding="utf-8") == "original bytes\n"


def test_attempt_repair_protects_real_source_using_its_directory_and_filename(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yescc.fix import engine
    from yescc.fix.c_rules import Candidate

    source = tmp_path / "input.c"
    original = "int main(void) { return 0 }\n"
    source.write_text(original, encoding="utf-8")
    try:
        (tmp_path / "a.out").hardlink_to(source)
    except OSError as exc:
        pytest.skip(f"当前平台不能创建 hardlink: {exc}")
    monkeypatch.setattr(
        engine,
        "generate_candidates",
        lambda text: [Candidate(_PROGRAM, ("test-rule",), 1)],
    )
    adapter = RecordingAdapter()
    result = engine.attempt_repair(adapter, original, source.name, [], tmp_path, source.parent)
    assert result.artifact == tmp_path / ".yescc" / "input" / "a.out"
    assert source.read_text(encoding="utf-8") == original
    assert len(adapter.calls) == 1
