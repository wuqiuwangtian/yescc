"""真实模型小样本评测（手动运行，不进 CI）。

用法：
    set -a; . ~/.config/yescc/model.env; set +a
    uv run python bench/model_eval.py

覆盖 C 规则无法处理的修复，以及确定性的自然语言任务。
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Case:
    name: str
    kind: str
    source: str
    expected: str
    stdin: str = ""
    args: tuple[str, ...] = ()


REPAIRS: tuple[Case, ...] = (
    Case(
        "repair_missing_semicolon_printf",
        "repair",
        '#include <stdio.h>\nint main(void) { printf("hi\\n") return 0; }\n',
        "hi\n",
    ),
    Case(
        "repair_missing_semicolon_return",
        "repair",
        '#include <stdio.h>\nint main(void) { printf("ok\\n"); return 0 }\n',
        "ok\n",
    ),
    Case(
        "repair_missing_expression",
        "repair",
        '#include <stdio.h>\nint main(void) { printf("%d\\n", 1 + ); return 0; }\n',
        "1\n",
    ),
    Case(
        "repair_missing_semicolon_var",
        "repair",
        '#include <stdio.h>\nint main(void) { int x = 2 printf("%d\\n", x); return 0; }\n',
        "2\n",
    ),
)

NL: tuple[Case, ...] = (
    Case("nl_hello", "nl", "输出 hello\n", "hello\n"),
    Case("nl_count", "nl", "输出 1 到 5，每个一行\n", "1\n2\n3\n4\n5\n"),
    Case(
        "nl_sum",
        "nl",
        "读入两个整数，输出它们的和\n",
        "5\n",
        stdin="2 3\n",
    ),
    Case(
        "nl_max",
        "nl",
        "读入三个整数，输出最大值\n",
        "9\n",
        stdin="1 9 4\n",
    ),
    Case("nl_factorial", "nl", "输出 5 的阶乘\n", "120\n"),
    Case(
        "nl_strlen",
        "nl",
        "读入一行文本，输出它的长度\n",
        "5\n",
        stdin="hello\n",
    ),
    Case(
        "nl_fib",
        "nl",
        "输出斐波那契数列前 6 项，用空格分隔，最后换行\n",
        "0 1 1 2 3 5\n",
    ),
    Case(
        "nl_sum_n",
        "nl",
        "读入一个整数 n，输出 1 到 n 的和\n",
        "55\n",
        stdin="10\n",
    ),
)


def run_case(case: Case, directory: Path) -> dict[str, object]:
    suffix = ".c" if case.kind == "repair" else ".txt"
    source = directory / f"{case.name}{suffix}"
    source.write_text(case.source, encoding="utf-8")
    started = time.perf_counter()
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "yescc",
            "run",
            str(source),
            "--",
            *case.args,
        ],
        cwd=directory,
        input=case.stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        check=False,
    )
    wall_ms = int((time.perf_counter() - started) * 1000)
    report_file = directory / ".yescc" / case.name / "report.json"
    report = json.loads(report_file.read_text("utf-8")) if report_file.exists() else {}
    model = report.get("model") or {}
    passed = completed.stdout == case.expected and completed.returncode == 0
    return {
        "name": case.name,
        "kind": case.kind,
        "passed": passed,
        "status": (report.get("result") or {}).get("status"),
        "wall_ms": wall_ms,
        "calls": model.get("calls", 0),
        "tokens": int(model.get("prompt_tokens", 0)) + int(model.get("completion_tokens", 0)),
        "cost": float(model.get("cost_usd", 0.0)),
        "stdout": completed.stdout[:80],
    }


def main() -> int:
    cases = REPAIRS + NL
    results: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="yescc-eval-") as raw:
        root = Path(raw)
        for case in cases:
            directory = root / case.name
            directory.mkdir()
            results.append(run_case(case, directory))

    print("| 用例 | 类型 | 结果 | 状态 | 墙钟 ms | 调用 | tokens | 费用 USD |")
    print("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for item in results:
        print(
            f"| {item['name']} | {item['kind']} | {'PASS' if item['passed'] else 'FAIL'} | "
            f"{item['status']} | {item['wall_ms']} | {item['calls']} | {item['tokens']} | "
            f"{float(item['cost']):.6f} |"
        )
    passed = sum(1 for item in results if item["passed"])
    total_cost = sum(float(item["cost"]) for item in results)
    print(
        f"\n通过 {passed}/{len(results)}；总费用 ${total_cost:.6f}；"
        f"总 tokens {sum(int(item['tokens']) for item in results)}"
    )
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
