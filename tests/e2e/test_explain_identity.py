"""同 stem 报告不得被解释为另一输入的构建结果。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e


def run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "yescc", *args, "--offline"],
        cwd=cwd,
        capture_output=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )


@pytest.mark.parametrize("as_json", [False, True])
@pytest.mark.parametrize(
    ("requested", "other"),
    [("hello.py", "hello.c"), ("one/hello.py", "two/hello.py")],
)
def test_explain_rejects_another_inputs_report(
    tmp_path: Path, requested: str, other: str, as_json: bool
) -> None:
    for name in (requested, other):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "int main(void){return 0;}\n" if path.suffix == ".c" else "print('python')\n",
            encoding="utf-8",
        )
    assert run(["run", requested], tmp_path).returncode == 0
    built = run([other, "--json"], tmp_path)
    assert built.returncode == 0 and json.loads(built.stdout)["result"]["status"] == "native_ok"
    report = tmp_path / ".yescc/hello/report.json"
    before = report.read_bytes()
    flags = ["--json"] if as_json else []
    rejected = run([requested, "--explain", *flags], tmp_path)
    assert rejected.returncode == 100
    if as_json:
        assert rejected.stderr == ""
        payload = json.loads(rejected.stdout)
        assert payload["report_kind"] == "command_error"
        assert payload["error"]["exit_code"] == 100 and payload["error"]["phase"] == "explain"
        assert "input" not in payload and "result" not in payload
        message = payload["error"]["message"]
    else:
        assert rejected.stdout == ""
        message = rejected.stderr
    assert "同名" in message and "重新构建" in message
    assert report.read_bytes() == before
    explained = run([other, "--explain", "--json"], tmp_path)
    assert explained.returncode == 0 and json.loads(explained.stdout) == json.loads(before)


@pytest.mark.parametrize("form", ["build_absolute", "explain_absolute", "outside_cwd"])
def test_explain_accepts_equivalent_path_forms(tmp_path: Path, form: str) -> None:
    cwd = tmp_path / "workspace"
    cwd.mkdir()
    source = (tmp_path if form == "outside_cwd" else cwd) / "目录" / "hello.py"
    source.parent.mkdir()
    source.write_text("print('same file')\n", encoding="utf-8")
    relative = "../目录/hello.py" if form == "outside_cwd" else "目录/hello.py"
    build_name = str(source) if form == "build_absolute" else relative
    explain_name = relative if form == "build_absolute" else str(source)
    built = run([build_name, "--json"], cwd)
    assert built.returncode == 0
    report = cwd / ".yescc/hello/report.json"
    before = report.read_bytes()
    explained = run([explain_name, "--explain", "--json"], cwd)
    assert explained.returncode == 0 and json.loads(explained.stdout) == json.loads(built.stdout)
    assert report.read_bytes() == before


@pytest.mark.parametrize("removed", [False, True])
def test_explain_reads_history_without_revalidating_source(tmp_path: Path, removed: bool) -> None:
    source = tmp_path / "hello.py"
    source.write_text("print('original')\n", encoding="utf-8")
    built = run(["hello.py", "--json"], tmp_path)
    assert built.returncode == 0
    report = tmp_path / ".yescc/hello/report.json"
    before = report.read_bytes()
    if removed:
        source.unlink()
    else:
        source.write_text("invalid current source !!!\n", encoding="utf-8")
    explained = run(["hello.py", "--explain", "--json"], tmp_path)
    assert explained.returncode == 0 and json.loads(explained.stdout) == json.loads(built.stdout)
    assert report.read_bytes() == before


def test_explain_preserves_literal_dash_file_behavior(tmp_path: Path) -> None:
    (tmp_path / "-").write_text("print('literal file')\n", encoding="utf-8")
    built = run(["--lang", "python", "-", "--json"], tmp_path)
    assert built.returncode == 0
    explained = run(["-", "--explain", "--json"], tmp_path)
    assert explained.returncode == 0 and json.loads(explained.stdout) == json.loads(built.stdout)


@pytest.mark.parametrize("identity", [None, {}, {"path": ""}, {"path": 4}, {"path": "\0"}])
def test_explain_rejects_unverifiable_report_identity(tmp_path: Path, identity: object) -> None:
    source = tmp_path / "hello.py"
    source.write_text("print('original')\n", encoding="utf-8")
    built = run(["hello.py", "--json"], tmp_path)
    assert built.returncode == 0
    payload = json.loads(built.stdout)
    payload["input"] = identity
    report = tmp_path / ".yescc/hello/report.json"
    before = json.dumps(payload, ensure_ascii=False).encode()
    report.write_bytes(before)
    explained = run(["hello.py", "--explain", "--json"], tmp_path)
    assert explained.returncode == 100 and explained.stderr == ""
    error = json.loads(explained.stdout)
    assert error["report_kind"] == "command_error" and error["error"]["phase"] == "explain"
    assert "重新构建" in error["error"]["message"]
    assert "input" not in error and "result" not in error
    assert report.read_bytes() == before
