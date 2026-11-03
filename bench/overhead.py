"""测量 fcomp 的包装额外开销（对应路线图 G1）。

用法：
    uv run python bench/overhead.py [--repeat 20]

指标：
    包装开销 = 进程墙钟 - 报告中的工具链耗时
    同时给出报告内的流水线耗时，便于区分解释器启动与工具链本身。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SOURCES: dict[str, tuple[str, str]] = {
    "python": ("main.py", "print('hi')\n"),
    "c": ("main.c", '#include <stdio.h>\nint main(void) { printf("hi\\n"); return 0; }\n'),
    "javascript": ("main.js", "console.log('hi');\n"),
}


def _percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round(ratio * (len(ordered) - 1))))
    return ordered[index]


def measure(language: str, repeat: int) -> dict[str, float] | None:
    filename, source = SOURCES[language]
    with tempfile.TemporaryDirectory() as raw_directory:
        directory = Path(raw_directory)
        (directory / filename).write_text(source, encoding="utf-8")
        wall: list[float] = []
        pipeline: list[float] = []
        toolchain: list[float] = []
        for _ in range(repeat):
            started = time.perf_counter()
            completed = subprocess.run(
                [sys.executable, "-m", "fcomp", filename, "--json"],
                cwd=directory,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            wall_ms = (time.perf_counter() - started) * 1000
            if completed.returncode != 0 or not completed.stdout.strip():
                message = f"[{language}] 第 {len(wall) + 1} 次运行失败：{completed.stderr}"
                print(message, file=sys.stderr)
                return None
            payload = json.loads(completed.stdout)
            wall.append(wall_ms)
            pipeline.append(float(payload["timings"]["total_ms"]))
            toolchain.append(float(payload["timings"]["toolchain_ms"]))
    overhead = [w - t for w, t in zip(wall, toolchain, strict=True)]
    return {
        "runs": float(len(wall)),
        "overhead_p50": _percentile(overhead, 0.50),
        "overhead_p95": _percentile(overhead, 0.95),
        "wall_p95": _percentile(wall, 0.95),
        "toolchain_p95": _percentile(toolchain, 0.95),
        "pipeline_p95": _percentile(pipeline, 0.95),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeat", type=int, default=20)
    args = parser.parse_args()

    header = (
        "| 语言 | 次数 | 包装开销 P50 (ms) | 包装开销 P95 (ms) | "
        "墙钟 P95 (ms) | 工具链 P95 (ms) |"
    )
    print(header)
    print("| --- | --- | --- | --- | --- | --- |")
    for language in SOURCES:
        result = measure(language, args.repeat)
        if result is None:
            return 1
        print(
            f"| {language} | {int(result['runs'])} | {result['overhead_p50']:.0f} | "
            f"{result['overhead_p95']:.0f} | {result['wall_p95']:.0f} | "
            f"{result['toolchain_p95']:.0f} |"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
