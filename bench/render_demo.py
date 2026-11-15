"""Render an offline CLI preview from real outputs (optional Pillow dependency)."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


def main() -> None:
    from PIL import Image, ImageDraw, ImageFont

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--font", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--record", type=Path, required=True)
    args = parser.parse_args()
    binary = args.binary.resolve()
    root = Path(__file__).resolve().parents[1]
    env = {k: v for k, v in os.environ.items() if not k.startswith(("YESCC_", "FCOMP_"))}
    env["YESCC_SUPPRESS_NOTICE"] = "1"
    rows = []
    with tempfile.TemporaryDirectory(prefix="yescc-demo-") as raw:
        cwd = Path(raw)
        shutil.copytree(root / "examples", cwd / "examples")
        for name in ("hello.c", "broken.c", "idea.txt"):
            source = cwd / "examples" / name
            before = source.read_bytes()
            command = [str(binary), "run", f"examples/{name}", "--offline"]
            result = subprocess.run(
                command, cwd=cwd, env=env, capture_output=True, timeout=30, check=False
            )
            assert result.returncode == 0 and before == source.read_bytes()
            rows.append(
                {
                    "command": f"./yescc run examples/{name} --offline",
                    "stdout": result.stdout.decode("utf-8"),
                    "stderr": result.stderr.decode("utf-8"),
                    "exit_code": result.returncode,
                    "source_unchanged": True,
                }
            )
    font = ImageFont.truetype(str(args.font), 21)
    lines = []
    frames = []
    durations = []

    def frame(duration: int) -> None:
        canvas = Image.new("RGB", (1040, 670), "#111827")
        draw = ImageDraw.Draw(canvas)
        draw.text((28, 18), "YesCC 0.1.0 | real offline outputs", font=font, fill="#cbd5e1")
        draw.line((28, 55, 1012, 55), fill="#334155")
        for i, (text, color) in enumerate(lines[-20:]):
            draw.text((28, 78 + 28 * i), text, font=font, fill=color)
        frames.append(canvas)
        durations.append(duration)

    def append(text: str, color: str) -> None:
        for original in text.splitlines():
            current = ""
            for char in original:
                if font.getlength(current + char) > 970:
                    lines.append((current, color))
                    current = ""
                current += char
            lines.append((current, color))

    frame(900)
    for row in rows:
        append("$ " + row["command"], "#86efac")
        frame(900)
        if row["stderr"]:
            append("[stderr] " + row["stderr"].strip(), "#fbbf24")
        append(row["stdout"].strip(), "#e2e8f0")
        append("", "#e2e8f0")
        frame(2400)
    frame(3000)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(
        args.output,
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        optimize=True,
    )
    record = {
        "kind": "actual_offline_cli_preview",
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "rows": rows,
        "paid_model_calls": 0,
    }
    args.record.parent.mkdir(parents=True, exist_ok=True)
    args.record.write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
