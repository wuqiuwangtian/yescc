"""最小运行沙箱：临时目录、超时、环境白名单与尽力断网。"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
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


def default_timeout() -> float:
    raw = os.environ.get("YESCC_RUN_TIMEOUT_S")
    if not raw:
        return DEFAULT_TIMEOUT_S
    try:
        return max(float(raw), 0.1)
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
        import resource

        cpu = int(timeout_s) + 1
        limits = [
            (resource.RLIMIT_CPU, (cpu, cpu)),
            (resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024)),
            (resource.RLIMIT_FSIZE, (10 * 1024 * 1024, 10 * 1024 * 1024)),
        ]
        for which, value in limits:
            try:
                resource.setrlimit(which, value)
            except (ValueError, OSError):
                continue

    return apply


def _run_once(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout_s: float,
    preexec: Callable[[], None] | None,
) -> SandboxOutcome:
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            env=env,
            timeout=timeout_s,
            check=False,
            preexec_fn=preexec,
        )
    except subprocess.TimeoutExpired:
        return SandboxOutcome(
            exit_code=124,
            timed_out=True,
            sandboxed=True,
            network_isolated=False,
            timeout_s=timeout_s,
        )
    return SandboxOutcome(
        exit_code=completed.returncode,
        timed_out=False,
        sandboxed=True,
        network_isolated=False,
        timeout_s=timeout_s,
    )


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
        try:
            completed = subprocess.run(
                command,
                cwd=str(cwd),
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return SandboxOutcome(124, True, False, False, timeout)
        return SandboxOutcome(completed.returncode, False, False, False, timeout)

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
        return SandboxOutcome(
            exit_code=outcome.exit_code,
            timed_out=outcome.timed_out,
            sandboxed=True,
            network_isolated=network,
            timeout_s=timeout,
        )
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


__all__ = ["SandboxOutcome", "default_timeout", "run_ai_command"]
