"""端到端：AI 产物的最小运行沙箱。"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.conftest import FakeModelServer
from yescc.sandbox import run_ai_command

pytestmark = pytest.mark.e2e

PYTHON = sys.executable

READ_SECRET = """\
#include <stdio.h>

int main(void) {
    FILE *handle = fopen("secret.txt", "r");
    if (handle == NULL) {
        printf("missing\\n");
        return 0;
    }
    char buffer[64];
    if (fgets(buffer, sizeof buffer, handle) != NULL) {
        printf("%s", buffer);
    }
    fclose(handle);
    return 0;
}
"""


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [PYTHON, "-m", "yescc", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )


def _report(directory: Path, stem: str) -> dict[str, object]:
    return json.loads((directory / ".yescc" / stem / "report.json").read_text("utf-8"))


def test_ai_artifact_runs_in_temp_directory(
    tmp_path: Path, fake_model: FakeModelServer
) -> None:
    fake_model.responses.extend(
        [
            {"source": READ_SECRET, "assumptions": []},
            {"source": READ_SECRET, "assumptions": []},
        ]
    )
    (tmp_path / "secret.txt").write_text("secret\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("写一个程序读取 secret.txt 并输出内容\n", encoding="utf-8")

    sandboxed = _run(["run", "notes.txt"], tmp_path)
    assert sandboxed.returncode == 0
    assert sandboxed.stdout == "missing\n"
    report = _report(tmp_path, "notes")
    assert report["limits"]["sandboxed"] is True
    assert report["limits"]["timeout_s"] == 10.0

    unsafe = _run(["run", "notes.txt", "--unsafe-run"], tmp_path)
    assert unsafe.returncode == 0
    assert unsafe.stdout == "secret\n"
    unsafe_report = _report(tmp_path, "notes")
    assert unsafe_report["limits"]["sandboxed"] is False


def test_sandbox_timeout(
    tmp_path: Path, fake_model: FakeModelServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("YESCC_RUN_TIMEOUT_S", "1")
    infinite = "int main(void) { for (;;) {} }\n"
    fake_model.responses.append({"source": infinite, "assumptions": []})
    (tmp_path / "notes.txt").write_text("写一个程序输出 hello\n", encoding="utf-8")

    result = _run(["run", "notes.txt"], tmp_path)

    assert result.returncode == 124
    report = _report(tmp_path, "notes")
    assert report["result"]["timed_out"] is True
    assert report["limits"]["timeout_s"] == 1.0


@pytest.mark.parametrize("unsafe", [False, True])
def test_timeout_kills_child_processes(tmp_path: Path, unsafe: bool) -> None:
    leaked = tmp_path / "leaked.txt"
    ready = tmp_path / "ready"
    child = f"import time; time.sleep(2); open({str(leaked)!r}, 'w').write('leaked')"
    parent = (
        "import subprocess,sys,time; "
        f"subprocess.Popen([sys.executable, '-c', {child!r}]); "
        f"open({str(ready)!r}, 'w').write('ready'); time.sleep(60)"
    )
    outcome = run_ai_command(
        [PYTHON, "-c", parent], unsafe=unsafe, cwd=tmp_path, timeout_s=1
    )
    assert outcome.timed_out is True
    assert outcome.exit_code == 124
    assert ready.exists(), "父进程必须已创建子进程，否则未验证清理行为"
    time.sleep(2.1)
    assert not leaked.exists(), "超时后子进程仍然执行了写文件操作"


@pytest.mark.skipif(os.name != "posix", reason="POSIX 信号与独立进程组")
def test_interrupt_cleans_process_group(tmp_path: Path) -> None:
    ready = tmp_path / "ready"
    leaked = tmp_path / "leaked"
    child = f"import time; time.sleep(1.5); open({str(leaked)!r}, 'w').write('leaked')"
    parent = (
        "import subprocess,sys,time; "
        f"subprocess.Popen([sys.executable, '-c', {child!r}]); "
        f"open({str(ready)!r}, 'w').write('ready'); time.sleep(60)"
    )
    wrapper = (
        "import sys; from pathlib import Path; from yescc.sandbox import run_ai_command; "
        f"outcome=run_ai_command([sys.executable, '-c', {parent!r}], "
        f"unsafe=False, cwd=Path({str(tmp_path)!r}), timeout_s=30); "
        "sys.exit(outcome.exit_code)"
    )
    process = subprocess.Popen([PYTHON, "-c", wrapper])
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists()
        process.send_signal(signal.SIGINT)
        assert process.wait(timeout=5) == 130
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
    time.sleep(1.6)
    assert not leaked.exists(), "取消任务后子进程仍然执行了写文件操作"


@pytest.mark.skipif(os.name != "posix", reason="POSIX 进程组可清理已退出父进程的后代")
def test_normal_parent_exit_cleans_remaining_children(tmp_path: Path) -> None:
    leaked = tmp_path / "leaked"
    child = f"import time; time.sleep(0.5); open({str(leaked)!r}, 'w').write('leaked')"
    parent = f"import subprocess,sys; subprocess.Popen([sys.executable, '-c', {child!r}])"
    outcome = run_ai_command([PYTHON, "-c", parent], unsafe=False, cwd=tmp_path)
    assert outcome.exit_code == 0
    time.sleep(0.6)
    assert not leaked.exists()
