"""Windows/Linux 同一协议：路径、字节编码、stdin、退出码和真实中断。"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import FakeModelServer

pytestmark = pytest.mark.e2e

SOURCES = {
    "python": (
        ".py",
        "import sys\nprint('你好:'+sys.stdin.readline().strip()+':'+ '|'.join(sys.argv[1:]))\n"
        "sys.stderr.write('warning\\n')\nsys.exit(7)\n",
    ),
    "javascript": (
        ".js",
        "const fs=require('fs');\nconsole.log('你好:'+fs.readFileSync(0,'utf8').trim()"
        "+':'+process.argv.slice(2).join('|'));\n"
        "process.stderr.write('warning\\n'); process.exitCode=7;\n",
    ),
    "c": (
        ".c",
        "#include <stdio.h>\n#include <string.h>\n"
        'int main(int argc,char **argv){char s[64]="";fgets(s,sizeof s,stdin);'
        's[strcspn(s,"\\r\\n")]=0;printf("你好:%s:%s|%s\\n",s,argv[1],argv[2]);'
        'fputs("warning\\n",stderr);return 7;}\n',
    ),
}


@pytest.mark.parametrize("lang", SOURCES)
@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig"])
@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["LF", "CRLF"])
def test_encoded_unicode_path_and_program_contract(
    lang: str, encoding: str, newline: str, tmp_path: Path
) -> None:
    directory = tmp_path / "中文 path [with spaces]"
    directory.mkdir()
    suffix, text = SOURCES[lang]
    source = directory / ("源 文件" + suffix)
    source.write_bytes(text.replace("\n", newline).encode(encoding))
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    if lang == "python":
        native = [sys.executable, str(source)]
    elif lang == "javascript":
        native = [shutil.which("node") or "node", str(source)]
    else:
        binary = directory / "native executable.exe"
        # 与 run 的默认相对输出保持同一条件；部分 MinGW ld 不接受非 ASCII 绝对输出路径。
        built = subprocess.run(
            ["gcc", str(source), "-o", binary.name], cwd=directory, capture_output=True
        )
        assert built.returncode == 0, built.stderr
        native = [str(binary)]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    options = dict(
        cwd=directory,
        input="42\n",
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=20,
    )
    args = ["arg space", "参数"]
    expected = subprocess.run([*native, *args], **options)
    actual = subprocess.run(
        [sys.executable, "-m", "yescc", "run", str(source), "--", *args], **options
    )
    assert (actual.returncode, actual.stdout, actual.stderr) == (
        expected.returncode,
        expected.stdout,
        expected.stderr,
    )
    assert actual.returncode == 7
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    report = json.loads((directory / ".yescc" / source.stem / "report.json").read_text("utf-8"))
    assert report["result"]["status"] == "native_ok"
    assert report["result"]["program_exit"] == 7
    assert report["privacy"]["cloud_used"] is False
    assert report["limits"]["sandboxed"] is False
    assert report["input"]["encoding"] == encoding


def test_repair_resolves_include_paths_from_original_directory(tmp_path: Path) -> None:
    include = tmp_path / "include space"
    include.mkdir()
    (include / "values.h").write_text("#define VALUE 7\n")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "extra.h").write_text("#define EXTRA 2\n")
    source = tmp_path / "源 文件.c"
    source.write_text(
        '#include <stdio.h>\n#include "values.h"\n#include "sub/extra.h"\n'
        'int main(void){prinf("%d\\n",VALUE+EXTRA);return 0;}\n',
        encoding="utf-8",
    )
    output = tmp_path / "output space" / "fixed.exe"
    # 旧 GCC 在链接阶段才报告 prinf，先保证输出目录存在，避免把环境错误当修复任务。
    output.parent.mkdir()
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "yescc",
            str(source),
            "-I",
            "include space",
            "-o",
            str(output),
            "--offline",
            "--json",
        ],
        cwd=tmp_path,
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["result"]["status"] == "repaired"
    ran = subprocess.run([str(output)], capture_output=True, encoding="utf-8", check=False)
    assert ran.stdout == "9\n"


@pytest.mark.parametrize("flag", ["-E", "-M", "-MM", "-fsyntax-only"])
def test_non_linking_build_does_not_report_stale_executable(tmp_path: Path, flag: str) -> None:
    (tmp_path / "a.out").write_bytes(b"stale")
    (tmp_path / "a.exe").write_bytes(b"stale")
    (tmp_path / "main.c").write_text("int main(void){return 0;}\n")
    result = subprocess.run(
        [sys.executable, "-m", "yescc", "main.c", flag, "--json"],
        cwd=tmp_path,
        capture_output=True,
        encoding="utf-8",
        timeout=20,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout)["result"]["artifact"] is None


def test_unicode_absolute_output_matches_native_toolchain_boundary(tmp_path: Path) -> None:
    source = tmp_path / "main.c"
    source.write_text("int main(void){return 0;}\n")
    directory = tmp_path / "中文 path"
    directory.mkdir()
    output = directory / "输出.exe"
    expected = subprocess.run(["gcc", str(source), "-o", str(output)], capture_output=True)
    actual = subprocess.run(
        [sys.executable, "-m", "yescc", str(source), "-o", str(output), "--strict", "--json"],
        cwd=tmp_path,
        capture_output=True,
        encoding="utf-8",
        timeout=20,
    )
    report = json.loads(actual.stdout)
    assert report["attempts"][0]["exit_code"] == expected.returncode
    assert actual.returncode == (0 if expected.returncode == 0 else 102)
    assert not report["privacy"]["cloud_used"]
    assert report["result"]["status"] == (
        "native_ok" if expected.returncode == 0 else "native_failed"
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows 原生 CTRL_BREAK 控制事件")
def test_windows_ctrl_break_writes_report_and_cleans_up(
    tmp_path: Path, fake_model: FakeModelServer
) -> None:
    fake_model.responses.append(
        {"source": '#include <stdio.h>\nint main(void){puts("ready");fflush(stdout);for(;;){}}\n'}
    )
    source = tmp_path / "notes.txt"
    source.write_text("写一个程序输出 ready\n", encoding="utf-8")
    controller = """
import ctypes,os,signal,subprocess,sys
from pathlib import Path
k=ctypes.windll.kernel32
allocated=bool(k.AllocConsole())
try:
    p=subprocess.Popen([sys.executable,'-m','yescc','run',sys.argv[1]],
        cwd=sys.argv[2],stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    try:
        assert p.stdout.readline().strip()==b'ready'
        p.send_signal(signal.CTRL_BREAK_EVENT)
        out,err=p.communicate(timeout=10)
        assert p.returncode==130,(p.returncode,err)
    finally:
        if p.poll() is None: p.kill()
        p.wait()
finally:
    if allocated: k.FreeConsole()
"""
    result = subprocess.run(
        [sys.executable, "-c", controller, str(source), str(tmp_path)],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=25,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads((tmp_path / ".yescc/notes/report.json").read_text("utf-8"))
    assert report["result"]["program_exit"] == 130
    assert report["limits"]["process_tree_cleanup"] == "windows_taskkill"
