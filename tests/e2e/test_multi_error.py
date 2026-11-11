"""多错误离线修复：独立行为检查，失败保留原意边界。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

CASES = [
    (
        "semicolons",
        '#include <stdio.h>\nint main(void){\nint x=2\nprintf("%d\\n",x)\nreturn 0\n}\n',
        [("", "2\n")],
    ),
    (
        "combined",
        'include <stdio.h>\nint mian(void){\nint n=7；\nprinf("%d\\n",n)\nretrun 0\n}\n',
        [("", "7\n")],
    ),
    (
        "quoted",
        "#include <stdio.h>\nint main(void){\nprinf(“你好，世界”；\nretrun 0\n}\n",
        [("", "你好，世界")],
    ),
    (
        "unicode",
        '#include <stdio.h>\nint main(void){printf("你好，世界\\n") return 0;}\n',
        [("", "你好，世界\n")],
    ),
    (
        "loop",
        "#include <stdio.h>\nint main(void){\nint sum=0\n"
        'for(int i=0;i<4;i++){sum+=i}\nprintf("%d\\n",sum)\nreturn 0\n}\n',
        [("", "6\n")],
    ),
    (
        "branches",
        '#include <stdio.h>\nint main(void){\nint n;scanf("%d",&n)\n'
        'if(n<0){puts("neg")}else{puts("other")}\nreturn 0\n}\n',
        [("-2\n", "neg\n"), ("3\n", "other\n")],
    ),
    (
        "protected",
        "include <stdio.h>\n// prinf，保留“这段”注释\n"
        'int mian(void){puts("prinf，hello；"); retrun 0}\n',
        [("", "prinf，hello；\n")],
    ),
    ("brace", 'include <stdio.h>\nint mian(void){\nprinf("ok\\n");\nretrun 0\n', [("", "ok\n")]),
]


@pytest.mark.parametrize("line_ending", ["\n", "\r\n"], ids=["LF", "CRLF"])
@pytest.mark.parametrize(("name", "source", "checks"), CASES, ids=[row[0] for row in CASES])
def test_multi_error_behavior(
    name: str, source: str, checks: list, tmp_path: Path, line_ending: str
) -> None:
    path = tmp_path / (name + ".c")
    source = source.replace("\n", line_ending)
    path.write_bytes(source.encode("utf-8"))
    before = path.read_bytes()
    for stdin, expected in checks:
        result = subprocess.run(
            [sys.executable, "-m", "yescc", "run", path.name, "--offline", "--no-fallback"],
            cwd=tmp_path,
            input=stdin,
            capture_output=True,
            encoding="utf-8",
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == expected
        report = json.loads((tmp_path / ".yescc" / name / "report.json").read_text("utf-8"))
        assert report["result"]["status"] == "repaired"
        assert report["result"]["repair"]["candidate_count"] <= 24
        assert not report["model"] or report["model"]["calls"] == 0
        assert path.read_bytes() == before
    repaired = (tmp_path / ".yescc" / name / path.name).read_text("utf-8")
    if name == "protected":
        assert "// prinf，保留“这段”注释" in repaired
        assert '"prinf，hello；"' in repaired


@pytest.mark.parametrize("as_json", [False, True])
def test_cpp_is_not_silently_translated_and_guidance_matches_report(
    as_json: bool,
    tmp_path: Path,
) -> None:
    source = "include <stdio.h>\nint main(void){\ncout<<“hello”\n]\n"
    path = tmp_path / "mixed.c"
    path.write_text(source, encoding="utf-8")
    args = ["mixed.c", "--offline", "--json"] if as_json else ["run", "mixed.c", "--offline"]
    result = subprocess.run(
        [sys.executable, "-m", "yescc", *args],
        cwd=tmp_path,
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0
    report = json.loads((tmp_path / ".yescc/mixed/report.json").read_text("utf-8"))
    assert report["result"]["status"] == "creative_fallback"
    assert report["result"]["fallback"]["kind"] == "print_input"
    hints = report["result"]["fallback"]["guidance"]
    assert any("C++" in item for item in hints)
    assert any("--offline" in item for item in hints)
    assert any("回显输入" in item for item in hints)
    assert path.read_text("utf-8") == source
    assert not report["model"] or report["model"]["calls"] == 0
    if as_json:
        assert json.loads(result.stdout) == report and not result.stderr
    else:
        assert result.stdout == source
        assert all(item in result.stderr for item in hints)
    explained = subprocess.run(
        [sys.executable, "-m", "yescc", "mixed.c", "--explain"],
        cwd=tmp_path,
        capture_output=True,
        encoding="utf-8",
        timeout=10,
    )
    assert all(item in explained.stdout for item in hints)


def test_temp_build_preserves_crlf_for_compiler_coordinates(tmp_path: Path, monkeypatch) -> None:
    from yescc.adapters.c import CAdapter
    from yescc.fix.engine import attempt_repair

    source = next(row[1] for row in CASES if row[0] == "loop").replace("\n", "\r\n")
    path = tmp_path / "loop.c"
    path.write_bytes(source.encode())
    adapter = CAdapter()
    original = adapter.build(path, [], tmp_path)
    write = Path.write_text

    def windows_translation(target, text, *args, **kwargs):
        if (
            target.name == "loop.c"
            and target.parent.name.startswith("yescc-build-")
            and kwargs.get("newline") is None
        ):
            text = text.replace("\n", "\r\n")
        return write(target, text, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", windows_translation)
    repaired = attempt_repair(adapter, source, path.name, [], tmp_path, tmp_path, original.stderr)
    assert repaired.candidate is not None and repaired.artifact is not None
    actual = subprocess.run([str(repaired.artifact)], capture_output=True, text=True, timeout=5)
    assert actual.returncode == 0 and actual.stdout == "6\n"
    assert path.read_bytes() == source.encode()
