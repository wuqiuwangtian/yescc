"""创意产物的实际运行与有限输出检查；不判断作品语义或趣味。"""

from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from yescc.sandbox.runner import SandboxOutcome, _run_ai_command

DEFAULT_MAX_OUTPUT_BYTES = 65536


@dataclass(frozen=True)
class CreativeRun:
    """一次真实执行的结果；stdout/stderr 保留原始字节，失败内容不自动输出。"""

    sandbox: SandboxOutcome
    stdout: bytes
    stderr: bytes
    validation: dict[str, object]


def run_creative_command(
    command: list[str],
    *,
    unsafe: bool,
    cwd: Path,
    timeout_s: float | None = None,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
) -> CreativeRun:
    """检查一次创意执行；stdout+stderr合计额度，磁盘写入是轮询式尽力限制。

    内存中最多保留额度大小的原始输出前缀。被截断的 stdout 不能据此判断整段
    UTF-8 是否有效，utf8_valid 此时记 None。即使 unsafe=True，超时和输出额度仍在。
    此接口不打印，不改变普通 AI/原生程序的继承流行为，也不执行额外 Python 入口。
    """
    if max_output_bytes < 1:
        raise ValueError("max_output_bytes 必须大于0")
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        outcome = _run_ai_command(
            command,
            unsafe=unsafe,
            cwd=cwd,
            timeout_s=timeout_s,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=err,
            max_output_bytes=max_output_bytes,
        )
        stdout_size = os.fstat(out.fileno()).st_size
        stderr_size = os.fstat(err.fileno()).st_size
        out.seek(0)
        stdout = out.read(max_output_bytes)
        err.seek(0)
        stderr = err.read(max_output_bytes - len(stdout))

    reasons: list[str] = []
    if outcome.timed_out:
        reasons.append("timeout")
    if outcome.output_limited:
        reasons.append("output_limit")
    if outcome.exit_code != 0:
        reasons.append("exit_nonzero")

    stdout_complete = len(stdout) == stdout_size
    utf8_valid: bool | None = None
    visible = False
    if stdout_complete:
        try:
            text = stdout.decode("utf-8")
        except UnicodeDecodeError:
            utf8_valid = False
            reasons.append("stdout_invalid_utf8")
            visible = bool(stdout)
        else:
            utf8_valid = True
            visible = bool(text.strip())
    else:
        visible = bool(stdout.decode("utf-8", errors="replace").strip())
    if not visible:
        reasons.append("stdout_empty")

    validation: dict[str, object] = {
        "performed": True,
        "passed": not reasons,
        "reasons": reasons,
        "exit_code": outcome.exit_code,
        "stdout_bytes": stdout_size,
        "stderr_bytes": stderr_size,
        "stdout_complete": stdout_complete,
        "stderr_complete": len(stderr) == stderr_size,
        "utf8_valid": utf8_valid,
        "stdout_nonempty": visible,
        "output_limited": outcome.output_limited,
        "timed_out": outcome.timed_out,
        "max_output_bytes": max_output_bytes,
    }
    return CreativeRun(sandbox=outcome, stdout=stdout, stderr=stderr, validation=validation)


__all__ = ["CreativeRun", "DEFAULT_MAX_OUTPUT_BYTES", "run_creative_command"]
