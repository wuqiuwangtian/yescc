"""Verify a clean public-index install; --wheel rehearses without publishing."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from packaging.version import Version


def verify(expected_version: str, wheel: Path | None = None) -> dict:
    expected_version = str(Version(expected_version))
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("YESCC_", "FCOMP_", "PYTHON", "PIP_", "UV_")) and k != "VIRTUAL_ENV"
    }

    def run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            args,
            cwd=cwd,
            env=env,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
        )

    rows = []
    with tempfile.TemporaryDirectory(prefix="yescc-index-install-") as raw:
        root = Path(raw).resolve()
        home = root / "home"
        home.mkdir()
        env.update(
            {
                "HOME": str(home),
                "USERPROFILE": str(home),
                "XDG_CONFIG_HOME": str(home / "config"),
                "XDG_CACHE_HOME": str(home / "cache"),
                "APPDATA": str(home / "appdata"),
                "LOCALAPPDATA": str(home / "localappdata"),
                "YESCC_CONFIG_DIR": str(home / "config"),
                "YESCC_CACHE_DIR": str(home / "cache"),
                "PYTHONIOENCODING": "utf-8",
            }
        )
        environment = root / "venv"
        created = run(
            ["uv", "--no-config", "venv", "--python", sys.executable, str(environment)], root
        )
        assert created.returncode == 0, created.stderr
        folder = environment / ("Scripts" if os.name == "nt" else "bin")
        python = folder / ("python.exe" if os.name == "nt" else "python")
        console = folder / ("yescc.exe" if os.name == "nt" else "yescc")
        target = str(wheel.resolve()) if wheel else f"yescc=={expected_version}"
        installed = run(
            [
                "uv",
                "--no-config",
                "pip",
                "install",
                "--python",
                str(python),
                "--index-url",
                "https://pypi.org/simple/",
                target,
            ],
            root,
        )
        assert installed.returncode == 0, installed.stderr
        identity = run(
            [
                str(python),
                "-I",
                "-c",
                "import json,yescc;from importlib.metadata import "
                "distributions;print(json.dumps({'version':yescc.__version__,'path':yescc.__file__,"
                "'packages':{d.metadata['Name']:d.version for d in distributions()}}))",
            ],
            root,
        )
        assert identity.returncode == 0, identity.stderr
        metadata = json.loads(identity.stdout)
        assert metadata["version"] == expected_version
        assert Path(metadata["path"]).resolve().is_relative_to(environment)
        version = run([str(console), "--version", "--offline"], root)
        assert version.returncode == 0 and version.stdout.strip() == f"yescc {expected_version}"
        cases = [
            (
                "hello.c",
                '#include <stdio.h>\nint main(void){puts("hello");return 0;}\n',
                "hello\n",
                "",
                0,
                "native_ok",
            ),
            (
                "broken.c",
                '#include <stdio.h>\nint main(void){puts("repaired");return 0；}\n',
                "repaired\n",
                "",
                0,
                "repaired",
            ),
            (
                "idea.txt",
                "一颗石头开始讲笑话\n",
                "一颗石头开始讲笑话\n",
                None,
                0,
                "creative_fallback",
            ),
            (
                "native.py",
                'import sys;print("python");sys.stderr.write("err\\n");raise SystemExit(7)\n',
                "python\n",
                "err\n",
                7,
                "native_ok",
            ),
            (
                "native.js",
                'process.stdout.write("node\\n");process.stderr.write("err\\n");process.exit(7);\n',
                "node\n",
                "err\n",
                7,
                "native_ok",
            ),
        ]
        for name, text, stdout, stderr, code, status in cases:
            source = root / name
            source.write_text(text, encoding="utf-8")
            before = hashlib.sha256(source.read_bytes()).hexdigest()
            result = run([str(console), "run", name, "--offline"], root)
            report = json.loads((root / ".yescc" / source.stem / "report.json").read_text("utf-8"))
            assert result.returncode == code and result.stdout == stdout, (name, result)
            if stderr is not None:
                assert result.stderr == stderr, (name, result.stderr)
            else:
                assert "creative_fallback" not in result.stdout and "print_input" in result.stderr
            assert report["result"]["status"] == status
            assert report["model"] is None and not report["privacy"]["cloud_used"]
            assert hashlib.sha256(source.read_bytes()).hexdigest() == before
            rows.append(
                {
                    "name": name,
                    "status": status,
                    "exit_code": code,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "source_sha256": before,
                    "source_unchanged": True,
                    "passed": True,
                }
            )
        error = run([str(console), "missing.c", "--json", "--offline"], root)
        payload = json.loads(error.stdout)
        assert error.returncode == 100 and payload["report_kind"] == "command_error"
        assert payload["error"]["exit_code"] == 100 and not error.stderr
        rows.append({"name": "missing", "exit_code": 100, "passed": True})
        return {
            "version": expected_version,
            "platform": os.name,
            "source": "local_wheel_rehearsal" if wheel else "public_pypi",
            "index": "https://pypi.org/simple/",
            "constraints": "public dependency ranges",
            "packages": metadata["packages"],
            "model_mode": "offline",
            "paid_model_calls": 0,
            "total": len(rows),
            "passed": len(rows),
            "rows": rows,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = verify(args.expected_version, args.wheel)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({k: v for k, v in report.items() if k != "rows"}))


if __name__ == "__main__":
    main()
