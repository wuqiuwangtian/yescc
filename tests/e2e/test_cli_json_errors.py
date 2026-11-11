"""独立黑盒 D1/D2：预期命令错误必须可机器读取，退出码优先级保持不变。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e


def run(args: list[str], root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "yescc", *args],
        cwd=root,
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )


@pytest.mark.parametrize(
    ("args", "code", "phase"),
    [
        (["missing.c", "--json"], 100, "execute"),
        (["--budget", "-1", "ok.c", "--json"], 100, "parse"),
        (["--max-rounds", "bad", "--json", "ok.c"], 100, "parse"),
        (["input.xyz", "--strict", "--json"], 101, "execute"),
        (["reject.txt", "--no-fallback", "--json"], 101, "execute"),
        (["bad.py", "--json"], 101, "execute"),
        (["--json"], 100, "parse"),
        (["run", "ok.c", "--json"], 100, "parse"),
        (["run", "ok.c", "-I", "--json"], 100, "parse"),
        (["ok.c", "--explain", "--json"], 100, "explain"),
        (["--explain", "--json"], 100, "explain"),
    ],
)
def test_expected_command_errors_are_json(
    args: list[str],
    code: int,
    phase: str,
    tmp_path: Path,
) -> None:
    (tmp_path / "input.xyz").write_text("hello\n")
    (tmp_path / "reject.txt").write_text("请创造一台时光机\n", encoding="utf-8")
    (tmp_path / "bad.py").write_bytes(b"\xff\xfe")
    result = run([*args, "--offline"], tmp_path)
    assert result.returncode == code and result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "1"
    assert payload["report_kind"] == "command_error"
    assert payload["error"]["exit_code"] == code
    assert payload["error"]["phase"] == phase
    assert payload["error"]["message"]
    assert all(key not in payload for key in ("input", "privacy", "budget", "model", "toolchain"))
    assert not (tmp_path / ".yescc").exists()


def test_command_error_does_not_overwrite_previous_report(tmp_path: Path) -> None:
    source = tmp_path / "ok.c"
    source.write_text("int main(void){return 0;}\n")
    first = run(["ok.c", "--offline", "--json"], tmp_path)
    assert first.returncode == 0
    payload = json.loads(first.stdout)
    assert payload["result"]["status"] == "native_ok" and "report_kind" not in payload
    report = tmp_path / ".yescc/ok/report.json"
    before = report.read_bytes()
    source.unlink()
    failed = run(["ok.c", "--json", "--offline"], tmp_path)
    assert json.loads(failed.stdout)["report_kind"] == "command_error"
    assert report.read_bytes() == before


@pytest.mark.parametrize(
    "args",
    [
        ["run", "missing.py", "--", "--json"],
        ["--model", "--json"],
        ["-o", "--json"],
        ["--budget", "--json"],
    ],
)
def test_argument_values_and_program_arguments_are_not_json_flags(
    args: list[str],
    tmp_path: Path,
) -> None:
    result = run(args, tmp_path)
    assert result.returncode == 100 and not result.stdout
    assert result.stderr.startswith("yescc:")


def test_program_json_argument_is_passed_through(tmp_path: Path) -> None:
    (tmp_path / "args.py").write_text("import sys;print(sys.argv[1])\n")
    result = run(["run", "args.py", "--", "--json"], tmp_path)
    assert result.returncode == 0 and result.stdout == "--json\n" and not result.stderr


@pytest.mark.parametrize(
    "flags",
    [
        ["--lang", "nonesuch"],
        ["--lang=nonesuch"],
        ["--lang", "nonesuch", "--play"],
        ["--lang", "nonesuch", "--strict"],
        ["--lang", "nonesuch", "--no-fallback"],
    ],
)
def test_unsupported_explicit_language_preserves_report(
    flags: list[str], tmp_path: Path
) -> None:
    source = tmp_path / "ok.c"
    source.write_text("int main(void){return 0;}\n")
    before_source = source.read_bytes()
    first = run(["ok.c", "--json", "--offline"], tmp_path)
    assert first.returncode == 0
    assert json.loads(first.stdout)["result"]["status"] == "native_ok"
    report = tmp_path / ".yescc/ok/report.json"
    before_report = report.read_bytes()
    failed = run(["ok.c", *flags, "--json", "--offline"], tmp_path)
    assert failed.returncode == 101 and failed.stderr == ""
    payload = json.loads(failed.stdout)
    assert payload["report_kind"] == "command_error"
    assert payload["error"]["exit_code"] == 101
    assert payload["error"]["phase"] == "execute"
    assert report.read_bytes() == before_report
    assert source.read_bytes() == before_source
    assert not (report.parent / "fallback.c").exists()


@pytest.mark.parametrize("prefix", [[], ["run"]])
def test_json_language_value_is_not_an_output_flag(prefix: list[str], tmp_path: Path) -> None:
    (tmp_path / "ok.c").write_text("int main(void){return 0;}\n")
    failed = run([*prefix, "ok.c", "--lang", "--json", "--offline"], tmp_path)
    assert failed.returncode == 101 and not failed.stdout
    assert failed.stderr.startswith("yescc:")
    assert not (tmp_path / ".yescc").exists()


def test_explicit_language_alias_and_implicit_fallback_remain_available(tmp_path: Path) -> None:
    (tmp_path / "ok.c").write_text("int main(void){return 0;}\n")
    native = run(["ok.c", "--lang=GCC", "--json", "--offline"], tmp_path)
    assert native.returncode == 0
    assert json.loads(native.stdout)["result"]["status"] == "native_ok"
    (tmp_path / "note.xyz").write_text("hello\n")
    creative = run(["note.xyz", "--json", "--offline"], tmp_path)
    assert creative.returncode == 0
    assert json.loads(creative.stdout)["result"]["status"] == "creative_fallback"
