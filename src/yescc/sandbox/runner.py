"""最小运行沙箱：临时目录、超时、环境白名单与尽力断网。"""

from __future__ import annotations

import importlib
import math
import os
import platform
import shutil
import signal
import subprocess
import tempfile
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path

DEFAULT_TIMEOUT_S = 10.0
_ALLOWED_ENV = {
    "PATH",
    "HOME",
    "USERPROFILE",
    "TMPDIR",
    "TEMP",
    "TMP",
    "LANG",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "NUMBER_OF_PROCESSORS",
}


@dataclass(frozen=True)
class SandboxOutcome:
    """一次沙箱执行的结果。"""

    exit_code: int
    timed_out: bool
    sandboxed: bool
    network_isolated: bool
    timeout_s: float
    environment_filtered: bool = False
    process_tree_cleanup: str = "none"
    resource_limits: str = "none"


def default_timeout() -> float:
    raw = os.environ.get("YESCC_RUN_TIMEOUT_S")
    if not raw:
        return DEFAULT_TIMEOUT_S
    try:
        value = float(raw)
        return max(value, 0.1) if math.isfinite(value) else DEFAULT_TIMEOUT_S
    except ValueError:
        return DEFAULT_TIMEOUT_S


@lru_cache(maxsize=1)
def _network_isolation_supported() -> bool:
    """探测 unshare -n 是否可用；失败时如实返回 False。"""
    if platform.system() != "Linux":
        return False
    unshare = shutil.which("unshare")
    if unshare is None:
        return False
    try:
        completed = subprocess.run(
            [unshare, "-n", "true"],
            capture_output=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def _clean_env() -> dict[str, str]:
    return {
        key: value
        for key, value in os.environ.items()
        if key in _ALLOWED_ENV or key.startswith("LC_")
    }


def _resource_limits(timeout_s: float) -> Callable[[], None] | None:
    """POSIX 资源限额；Windows 返回 None。"""
    if os.name != "posix":
        return None

    def apply() -> None:
        resource = importlib.import_module("resource")
        setrlimit = getattr(resource, "setrlimit", None)
        if setrlimit is None:
            return
        cpu = int(timeout_s) + 1
        limits = [
            ("RLIMIT_CPU", (cpu, cpu)),
            ("RLIMIT_AS", (512 * 1024 * 1024, 512 * 1024 * 1024)),
            ("RLIMIT_FSIZE", (10 * 1024 * 1024, 10 * 1024 * 1024)),
        ]
        for name, value in limits:
            which = getattr(resource, name, None)
            if which is None:
                continue
            try:
                setrlimit(which, value)
            except (ValueError, OSError):
                continue

    return apply


def _run_once(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None,
    timeout_s: float,
    preexec: Callable[[], None] | None,
) -> SandboxOutcome:
    process = subprocess.Popen(
        command,
        cwd=str(cwd),
        env=env,
        preexec_fn=preexec,
        start_new_session=os.name == "posix",
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0,
    )
    timed_out = False
    try:
        returncode = process.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        returncode = 124
    except KeyboardInterrupt:
        returncode = 130
    finally:
        _stop_process_tree(process)
    return SandboxOutcome(
        exit_code=returncode,
        timed_out=timed_out,
        sandboxed=True,
        network_isolated=False,
        timeout_s=timeout_s,
        environment_filtered=env is not None,
        process_tree_cleanup="posix_group" if os.name == "posix" else "windows_taskkill",
        resource_limits="posix_best_effort" if preexec is not None else "none",
    )


def _stop_process_tree(process: subprocess.Popen[bytes]) -> None:
    """清理独立进程组；Windows 通过 taskkill 尽力终止仍挂在根进程下的子树。"""
    if os.name == "posix":
        killpg = getattr(os, "killpg", None)
        if killpg is not None:
            with suppress(ProcessLookupError):
                killpg(process.pid, getattr(signal, "SIGKILL", 9))
    elif process.poll() is None:
        with suppress(OSError, subprocess.SubprocessError):
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
    if process.poll() is None:
        process.kill()
    process.wait()


def run_ai_command(
    command: list[str],
    *,
    unsafe: bool,
    cwd: Path,
    timeout_s: float | None = None,
) -> SandboxOutcome:
    """运行 AI 产物。默认在临时目录中执行，`unsafe=True` 时在真实 cwd 执行。"""
    timeout = timeout_s if timeout_s is not None else default_timeout()
    if unsafe:
        outcome = _run_once(command, cwd=cwd, env=None, timeout_s=timeout, preexec=None)
        return replace(outcome, sandboxed=False)

    network = _network_isolation_supported()
    full_command = (["unshare", "-n", "--", *command]) if network else command
    temporary = Path(tempfile.mkdtemp(prefix="yescc-run-"))
    try:
        outcome = _run_once(
            full_command,
            cwd=temporary,
            env=_clean_env(),
            timeout_s=timeout,
            preexec=_resource_limits(timeout),
        )
        return replace(outcome, network_isolated=network)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


__all__ = ["SandboxOutcome", "default_timeout", "run_ai_command"]
