"""端到端：AI 产物的最小运行沙箱。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import FakeModelServer

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
