"""端到端：模型修复、生成、创意兜底、缓存与预算。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import FakeModelServer

pytestmark = pytest.mark.e2e

PYTHON = sys.executable

REPAIR_DIFF = """--- a/main.c
+++ b/main.c
@@ -1,2 +1,2 @@
 #include <stdio.h>
-int main(void) { printf("hi\\n") return 0; }
+int main(void) { printf("hi\\n"); return 0; }
"""

BROKEN_C = '#include <stdio.h>\nint main(void) { printf("hi\\n") return 0; }\n'

HELLO_SOURCE = (
    '#include <stdio.h>\nint main(void) { printf("hello\\n"); return 0; }\n'
)


def _run(args: list[str], cwd: Path, stdin: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [PYTHON, "-m", "yescc", *args],
        cwd=str(cwd),
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )


def _report(directory: Path, stem: str) -> dict[str, object]:
    return json.loads((directory / ".yescc" / stem / "report.json").read_text("utf-8"))


def test_model_repair_applies_diff(tmp_path: Path, fake_model: FakeModelServer) -> None:
    fake_model.responses.append({"diff": REPAIR_DIFF, "assumptions": ["missing semicolon"]})
    (tmp_path / "main.c").write_text(BROKEN_C, encoding="utf-8")

    result = _run(["run", "main.c"], tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout == "hi\n"
    report = _report(tmp_path, "main")
    assert report["result"]["status"] == "repaired"
    assert report["result"]["repair"]["rules"] == ["model"]
    assert report["model"]["calls"] == 1
    assert (tmp_path / ".yescc" / "main" / "main.c").exists()


def test_model_generation(tmp_path: Path, fake_model: FakeModelServer) -> None:
    fake_model.responses.append(
        {"source": HELLO_SOURCE, "assumptions": [], "io_contract": "stdout: hello"}
    )
    (tmp_path / "notes.txt").write_text("写一个程序输出 hello\n", encoding="utf-8")

    result = _run(["run", "notes.txt"], tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout == "hello\n"
    report = _report(tmp_path, "notes")
    assert report["result"]["status"] == "generated"
    assert report["result"]["generated"]["source"] == ".yescc/notes/generated.c"
    assert report["model"]["calls"] == 1


def test_creative_fallback_uses_model(tmp_path: Path, fake_model: FakeModelServer) -> None:
    creative_source = '#include <stdio.h>\nint main(void) { puts("creative"); return 0; }\n'
    fake_model.responses.append({"source": creative_source, "assumptions": []})
    (tmp_path / "meme.txt").write_text(
        "这是一段完全不像需求的胡言乱语，请随意发挥\n", encoding="utf-8"
    )

    result = _run(["run", "meme.txt"], tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout == "creative\n"
    report = _report(tmp_path, "meme")
    assert report["result"]["status"] == "creative_fallback"
    assert report["result"]["fallback"]["kind"] == "model_creative"


def test_model_cache_avoids_second_call(
    tmp_path: Path, fake_model: FakeModelServer
) -> None:
    fake_model.responses.extend(
        [
            {"source": HELLO_SOURCE, "assumptions": []},
            {"source": HELLO_SOURCE, "assumptions": []},
        ]
    )
    (tmp_path / "notes.txt").write_text("写一个程序输出 hello\n", encoding="utf-8")

    first = _run(["notes.txt"], tmp_path)
    second = _run(["notes.txt"], tmp_path)

    assert first.returncode == second.returncode == 0
    assert len(fake_model.requests) == 1
    report = _report(tmp_path, "notes")
    assert report["model"]["calls"] == 0
    assert report["model"]["cache_hits"] == 1


def test_no_cache_calls_twice(tmp_path: Path, fake_model: FakeModelServer) -> None:
    fake_model.responses.extend(
        [
            {"source": HELLO_SOURCE, "assumptions": []},
            {"source": HELLO_SOURCE, "assumptions": []},
        ]
    )
    (tmp_path / "notes.txt").write_text("写一个程序输出 hello\n", encoding="utf-8")
    assert _run(["notes.txt", "--no-cache"], tmp_path).returncode == 0
    assert _run(["notes.txt", "--no-cache"], tmp_path).returncode == 0
    assert len(fake_model.requests) == 2


def test_offline_never_calls_model(tmp_path: Path, fake_model: FakeModelServer) -> None:
    (tmp_path / "notes.txt").write_text("写一个程序输出 hello\n", encoding="utf-8")
    result = _run(["run", "notes.txt", "--offline"], tmp_path)
    assert result.returncode == 0
    assert len(fake_model.requests) == 0
    report = _report(tmp_path, "notes")
    assert report["result"]["status"] == "creative_fallback"
    assert report["model"] is None or report["model"]["calls"] == 0


def test_invalid_model_output_falls_back(
    tmp_path: Path, fake_model: FakeModelServer
) -> None:
    fake_model.responses.append("this is not json")
    (tmp_path / "notes.txt").write_text("写一个程序输出 hello\n", encoding="utf-8")
    result = _run(["notes.txt"], tmp_path)
    assert result.returncode == 0
    report = _report(tmp_path, "notes")
    assert report["result"]["status"] == "creative_fallback"
    assert report["result"]["fallback"]["kind"] == "print_input"


def test_max_rounds_limits_calls(tmp_path: Path, fake_model: FakeModelServer) -> None:
    fake_model.responses.append({"diff": "", "assumptions": []})
    (tmp_path / "main.c").write_text(BROKEN_C, encoding="utf-8")
    result = _run(["main.c", "--max-rounds", "1"], tmp_path)
    assert result.returncode == 0
    report = _report(tmp_path, "main")
    assert report["model"]["calls"] == 1
    assert report["result"]["status"] == "creative_fallback"


def test_trace_and_emit_source(tmp_path: Path, fake_model: FakeModelServer) -> None:
    fake_model.responses.append({"source": HELLO_SOURCE, "assumptions": []})
    (tmp_path / "notes.txt").write_text("写一个程序输出 hello\n", encoding="utf-8")
    result = _run(["notes.txt", "--emit-source", "--trace"], tmp_path)
    assert result.returncode == 0
    assert "int main(void)" in result.stdout
    assert "yescc: trace" in result.stderr
