"""G4：验证实际边界，包括明确允许的宿主访问，不把弱隔离包装成安全沙箱。"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

WRAPPER = """
import json,sys
from dataclasses import asdict
from pathlib import Path
from yescc.sandbox import run_ai_command
r=run_ai_command([sys.executable,'-c',sys.argv[1],*sys.argv[2:-1]],
                 unsafe=False,cwd=Path.cwd(),timeout_s=float(sys.argv[-1]))
print('__YESCC_OUTCOME__'+json.dumps(asdict(r)),file=sys.stderr,flush=True)
"""


@dataclass(frozen=True)
class Attack:
    name: str
    script: str
    expected: str | None = None
    exit_code: int = 0
    posix: bool = False
    stdin: str = ""


ATTACKS = (
    Attack("busy_loop", "while True: pass", exit_code=124),
    Attack("sleep_forever", "import time; time.sleep(60)", exit_code=124),
    Attack("large_stdout", "print('x'*131072)", "x" * 131072 + "\n"),
    Attack("large_stderr", "import sys; sys.stderr.write('x'*131072)", ""),
    Attack("stdin_eof", "import sys; print(len(sys.stdin.read()))", "0\n"),
    Attack("stdin_unicode", "print(input())", "你好\n", stdin="你好\n"),
    Attack("nonzero_exit", "raise SystemExit(7)", "", exit_code=7),
    Attack("reserved_exit", "raise SystemExit(103)", "", exit_code=103),
    Attack(
        "relative_read", "from pathlib import Path; print(Path('secret.txt').exists())", "False\n"
    ),
    Attack(
        "relative_write",
        "from pathlib import Path; Path('local.txt').write_text('x'); print('ok')",
        "ok\n",
    ),
    Attack(
        "absolute_read_allowed",
        "import sys; from pathlib import Path; print(Path(sys.argv[1]).read_text())",
        "dummy-secret\n",
    ),
    Attack(
        "absolute_write_allowed",
        "import sys; from pathlib import Path; "
        "Path(sys.argv[2]).write_text('changed'); print('ok')",
        "ok\n",
    ),
    Attack("parent_visible", "from pathlib import Path; print(Path('..').is_dir())", "True\n"),
    Attack(
        "host_directory_visible",
        "import sys; from pathlib import Path; print(Path(sys.argv[1]).parent.is_dir())",
        "True\n",
    ),
    Attack(
        "model_key_filtered",
        "import os; print(os.getenv('YESCC_REVIEW_SECRET','missing'))",
        "missing\n",
    ),
    Attack(
        "arbitrary_key_filtered",
        "import os; print(os.getenv('PROJECT_REVIEW_TOKEN','missing'))",
        "missing\n",
    ),
    Attack("path_preserved", "import os; print(bool(os.getenv('PATH')))", "True\n"),
    Attack("locale_preserved", "import os; print(os.getenv('LC_ALL'))", "C\n"),
    Attack(
        "child_process_allowed",
        "import subprocess,sys; "
        "subprocess.run([sys.executable,'-c',\"print('child')\"],check=True)",
        "child\n",
    ),
    Attack(
        "file_size_limit",
        "from pathlib import Path\n"
        "try: Path('large.bin').write_bytes(b'x'*(12*1024*1024))\n"
        "except OSError: print('limited')",
        "limited\n",
        posix=True,
    ),
    Attack(
        "memory_limit",
        "try: b=bytearray(700*1024*1024)\nexcept MemoryError: print('limited')",
        "limited\n",
        posix=True,
    ),
    Attack(
        "cpu_limit_present",
        "import resource; print(0<resource.getrlimit(resource.RLIMIT_CPU)[0]<=2)",
        "True\n",
        posix=True,
    ),
    Attack(
        "signal_exit",
        "import os,signal; os.kill(os.getpid(),signal.SIGTERM)",
        "",
        exit_code=-15,
        posix=True,
    ),
    Attack(
        "temporary_directory_removed",
        "from pathlib import Path; Path('marker').write_text('x'); print(Path.cwd())",
    ),
)


def run_script(
    script: str,
    tmp_path: Path,
    args: list[str] | None = None,
    stdin: str = "",
    timeout_s: float = 0.4,
) -> tuple[subprocess.CompletedProcess[str], dict[str, object], str]:
    result = subprocess.run(
        [sys.executable, "-c", WRAPPER, script, *(args or []), str(timeout_s)],
        cwd=tmp_path,
        input=stdin,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=12,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-1000:]
    stderr, marker, payload = result.stderr.rpartition("__YESCC_OUTCOME__")
    assert marker, "runner 未写出执行结果"
    return result, json.loads(payload), stderr


@pytest.mark.parametrize("attack", ATTACKS, ids=lambda a: a.name)
def test_declared_adversarial_boundary(
    attack: Attack, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if attack.posix and os.name != "posix":
        pytest.skip("此资源/信号声明仅适用于 POSIX")
    secret = tmp_path / "secret.txt"
    target = tmp_path / "target.txt"
    secret.write_text("dummy-secret", encoding="utf-8")
    monkeypatch.setenv("YESCC_REVIEW_SECRET", "dummy-never-upload")
    monkeypatch.setenv("PROJECT_REVIEW_TOKEN", "dummy-never-upload")
    monkeypatch.setenv("LC_ALL", "C")
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")
    result, outcome, stderr = run_script(
        attack.script, tmp_path, [str(secret), str(target)], attack.stdin
    )
    assert outcome["exit_code"] == attack.exit_code
    assert outcome["timed_out"] is (attack.exit_code == 124)
    assert outcome["sandboxed"] is True
    assert outcome["environment_filtered"] is True
    assert outcome["process_tree_cleanup"] == (
        "posix_group" if os.name == "posix" else "windows_taskkill"
    )
    assert outcome["resource_limits"] == ("posix_best_effort" if os.name == "posix" else "none")
    if attack.expected is not None:
        assert result.stdout == attack.expected
    if attack.name == "large_stderr":
        assert stderr == "x" * 131072
    if attack.name == "relative_write":
        assert not (tmp_path / "local.txt").exists()
    if attack.name == "absolute_write_allowed":
        assert target.read_text() == "changed", "绝对路径未隔离是明确记录的边界"
    if attack.name == "temporary_directory_removed":
        assert not Path(result.stdout.strip()).exists()


def test_loopback_network_matches_report(tmp_path: Path) -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        script = (
            "import socket\ntry:\n"
            f" s=socket.create_connection(('127.0.0.1',{port}),timeout=0.1); "
            "s.close(); print('connected')\nexcept OSError: print('blocked')"
        )
        result, outcome, _ = run_script(script, tmp_path, timeout_s=3)
    assert outcome["exit_code"] == 0
    assert result.stdout == ("blocked\n" if outcome["network_isolated"] else "connected\n")


@pytest.mark.parametrize("operation", ["read", "write"])
def test_absolute_symlink_is_not_filesystem_isolation(tmp_path: Path, operation: str) -> None:
    target = tmp_path / "dummy.txt"
    target.write_text("dummy-secret")
    link = tmp_path / "link"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("系统未授予创建符号链接的权限")
    script = "import sys; from pathlib import Path; p=Path(sys.argv[1]); "
    script += (
        "print(p.read_text())" if operation == "read" else "p.write_text('changed'); print('ok')"
    )
    result, outcome, _ = run_script(script, tmp_path, [str(link)])
    assert outcome["exit_code"] == 0
    assert result.stdout == ("dummy-secret\n" if operation == "read" else "ok\n")
    if operation == "write":
        assert target.read_text() == "changed"


def test_multiple_children_are_killed_on_timeout(tmp_path: Path) -> None:
    leaked = [tmp_path / f"leak-{i}" for i in range(3)]
    scripts = [f"import time; time.sleep(1); open({str(p)!r},'w').write('leaked')" for p in leaked]
    parent = "import subprocess,sys,time; "
    parent += "; ".join(f"subprocess.Popen([sys.executable,'-c',{s!r}])" for s in scripts)
    parent += "; print('ready',flush=True); time.sleep(60)"
    result, outcome, _ = run_script(parent, tmp_path)
    assert result.stdout == "ready\n"
    assert outcome["timed_out"] is True
    time.sleep(1.1)
    assert not any(p.exists() for p in leaked)
