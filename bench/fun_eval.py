"""整活输入小评测：检查是否产出可运行程序（手动运行）。"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

INPUTS: tuple[str, ...] = (
    "天王盖地虎",
    "今天不想上班",
    "让计算机感受到孤独",
    "sudo make me a sandwich",
    "此处应该有一个程序",
    "用表情符号写一首诗 😀😃😄",
    "我昨天梦到一只会写代码的猫",
    "请把这句话变成一个程序",
    "1234567890",
    "这是一段完全不像需求的胡言乱语，请随意发挥",
)


def main() -> int:
    rows: list[tuple[str, bool, str, str, int, int, float | None, int]] = []
    with tempfile.TemporaryDirectory(prefix="yescc-fun-") as raw:
        root = Path(raw)
        for index, text in enumerate(INPUTS):
            directory = root / f"case{index}"
            directory.mkdir()
            source = directory / "input.txt"
            source.write_text(text + "\n", encoding="utf-8")
            started = time.perf_counter()
            completed = subprocess.run(
                [sys.executable, "-m", "yescc", "run", str(source)],
                cwd=directory,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=180,
                check=False,
            )
            wall_ms = int((time.perf_counter() - started) * 1000)
            report_file = directory / ".yescc" / "input" / "report.json"
            report = json.loads(report_file.read_text("utf-8")) if report_file.exists() else {}
            result = report.get("result") or {}
            model = report.get("model") or {}
            artifact = result.get("artifact")
            ok = completed.returncode == 0 and artifact is not None
            rows.append(
                (
                    text,
                    ok,
                    str(result.get("status")),
                    str(((report.get("result") or {}).get("fallback") or {}).get("kind")),
                    int(model.get("calls", 0)),
                    int(model.get("prompt_tokens", 0)) + int(model.get("completion_tokens", 0)),
                    float(model.get("cost_usd", 0.0)) if model.get("cost_known", True) else None,
                    wall_ms,
                )
            )
            print(
                f"{'PASS' if ok else 'FAIL'} | {text} | {result.get('status')} | "
                f"kind={((report.get('result') or {}).get('fallback') or {}).get('kind')} | "
                f"calls={model.get('calls', 0)} | {wall_ms} ms"
            )
    passed = sum(1 for row in rows if row[1])
    cost = sum(row[6] for row in rows if row[6] is not None)
    unknown_costs = sum(row[6] is None for row in rows)
    print(f"\n可执行 {passed}/{len(rows)}；已知费用小计 ${cost:.6f}（{unknown_costs} 例费用未知）")
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
