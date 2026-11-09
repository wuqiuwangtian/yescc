"""在仓库外从 wheel 安装并验收 CLI，同时保留实际运行时依赖的许可原文。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from pathlib import Path

from tests.conftest import FakeModelServer

ROOT = Path(__file__).resolve().parents[1]
# 新依赖或许可变化必须显式复核；不是兼容性法律结论。
LICENSES = {
    "yescc": "MIT",
    "anyio": "MIT",
    "certifi": "MPL-2.0",
    "h11": "MIT",
    "httpcore": "BSD-3-Clause",
    "httpx": "BSD-3-Clause",
    "idna": "BSD-3-Clause",
    "platformdirs": "MIT",
    "tree-sitter": "MIT",
    "tree-sitter-c": "MIT",
    "typing-extensions": "PSF-2.0",
}
INVENTORY = r"""
import hashlib,json
from importlib.metadata import distributions
rows=[]
for dist in distributions():
    name=dist.metadata['Name'].lower().replace('_','-')
    files=[]
    for path in dist.files or []:
        if '.dist-info/licenses/' not in str(path).replace('\\','/'):
            continue
        data=dist.locate_file(path).read_bytes()
        files.append({'path':str(path).replace('\\','/'),
                      'sha256':hashlib.sha256(data).hexdigest(),
                      'text':data.decode('utf-8')})
    rows.append({'name':name,'version':dist.version,
                 'expression':dist.metadata.get('License-Expression'),
                 'legacy_license':dist.metadata.get('License'),
                 'project_urls':dist.metadata.get_all('Project-URL') or [],
                 'files':files})
print(json.dumps(sorted(rows,key=lambda row:row['name'])))
"""


def run(command: list[str], cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )


def verify(wheel: Path, directory: Path) -> dict[str, object]:
    expected_version = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"][
        "version"
    ]
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        assert "yescc/py.typed" in names
        assert any(name.endswith(".dist-info/licenses/LICENSE") for name in names)
        assert any(name.endswith(".dist-info/licenses/THIRD_PARTY_NOTICES.md") for name in names)
    env = {
        key: value for key, value in os.environ.items() if not key.startswith(("YESCC_", "FCOMP_"))
    }
    env.update({"PYTHONIOENCODING": "utf-8", "LC_ALL": "C", "NO_PROXY": "127.0.0.1,localhost"})
    exported = run(
        ["uv", "export", "--locked", "--no-dev", "--no-emit-project", "--no-hashes"], ROOT, env
    )
    assert exported.returncode == 0, exported.stderr
    constraints = directory / "runtime.txt"
    constraints.write_text(exported.stdout, encoding="utf-8")
    created = run(["uv", "venv", "--python", sys.executable, str(directory / "venv")], ROOT, env)
    assert created.returncode == 0, created.stderr
    binary_dir = directory / "venv" / ("Scripts" if os.name == "nt" else "bin")
    python = binary_dir / ("python.exe" if os.name == "nt" else "python")
    console = binary_dir / ("yescc.exe" if os.name == "nt" else "yescc")
    installed = run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(python),
            "--constraint",
            str(constraints),
            str(wheel),
        ],
        directory,
        env,
    )
    assert installed.returncode == 0, installed.stderr
    metadata = run(
        [
            str(python),
            "-I",
            "-c",
            "import json,yescc; "
            "from importlib.metadata import version; "
            "print(json.dumps([yescc.__version__,version('yescc'),yescc.__file__]))",
        ],
        directory,
        env,
    )
    assert metadata.returncode == 0, metadata.stderr
    module_version, package_version, module_path = json.loads(metadata.stdout)
    assert module_version == package_version == expected_version
    assert Path(module_path).is_relative_to(directory / "venv")
    for command in ([str(console), "--version"], [str(python), "-I", "-m", "yescc", "--version"]):
        version = run(command, directory, env)
        assert version.returncode == 0 and version.stdout.strip() == f"yescc {expected_version}"
    inventory = run([str(python), "-I", "-c", INVENTORY], directory, env)
    assert inventory.returncode == 0, inventory.stderr
    licenses = json.loads(inventory.stdout)
    expected_names = {
        line.split("==")[0]
        for line in exported.stdout.splitlines()
        if line and not line[0].isspace() and not line.startswith("#")
    } | {"yescc"}
    assert {row["name"] for row in licenses} == expected_names
    for row in licenses:
        assert row["files"], f"missing license text: {row['name']}"
        reviewed = LICENSES[row["name"]]
        declared = row["expression"] or row["legacy_license"]
        assert declared == reviewed or (
            row["name"] == "tree-sitter"
            and declared is None
            and all("The MIT License" in item["text"] for item in row["files"])
        ), row
        row["reviewed_license"] = reviewed
    server = FakeModelServer()
    server.start()
    key = "dummy-rc-smoke-secret"
    env.update(
        {
            "YESCC_CONFIG_DIR": str(directory / "config"),
            "YESCC_CACHE_DIR": str(directory / "cache"),
            "YESCC_SUPPRESS_NOTICE": "1",
        }
    )
    for tier in ("L2", "L3"):
        for setting, value in {
            "BASE_URL": server.base_url,
            "NAME": "rc-local-fake",
            "API_KEY": key,
            "INPUT_PRICE_PER_MTOK": "1",
            "OUTPUT_PRICE_PER_MTOK": "1",
        }.items():
            env[f"YESCC_MODEL_{tier}_{setting}"] = value
    rows = []

    def case(
        name: str,
        suffix: str,
        source: str,
        status: str,
        stdout: str | None,
        flags: tuple[str, ...] = ("--offline",),
        exit_code: int = 0,
        response: object = None,
        json_build: bool = False,
    ) -> dict:
        cwd = directory / name
        cwd.mkdir()
        file = cwd / (name + suffix)
        file.write_text(source, encoding="utf-8")
        before = file.read_bytes()
        calls = len(server.requests)
        if response is not None:
            server.responses.append(response)
        args = [file.name, *flags, "--json"] if json_build else ["run", file.name, *flags]
        result = run([str(python), "-I", "-m", "yescc", *args], cwd, env)
        assert result.returncode == exit_code, (name, result.returncode, result.stderr)
        report = json.loads((cwd / ".yescc" / name / "report.json").read_text("utf-8"))
        assert report["result"]["status"] == status, (name, report)
        assert file.read_bytes() == before
        if stdout is not None:
            assert result.stdout == stdout, (name, result.stdout)
        if status == "creative_fallback":
            assert result.stdout.strip()
        if json_build:
            assert json.loads(result.stdout) == report
        actual_calls = len(server.requests) - calls
        assert actual_calls == int(response is not None), (name, actual_calls)
        assert (report.get("model") or {}).get("calls", 0) == actual_calls
        if status in {"failed", "native_failed"}:
            assert report["result"]["artifact"] is None
        if response is not None:
            assert key not in json.dumps(report) + result.stdout + result.stderr
        rows.append(
            {
                "id": name,
                "status": status,
                "exit_code": result.returncode,
                "source_unchanged": True,
                "model_calls": actual_calls,
                "passed": True,
            }
        )
        return report

    try:
        case(
            "native_c",
            ".c",
            '#include <stdio.h>\nint main(void){puts("native");return 0;}\n',
            "native_ok",
            "native\n",
        )
        case("native_py", ".py", 'print("python")\n', "native_ok", "python\n")
        case("native_js", ".js", 'console.log("javascript");\n', "native_ok", "javascript\n")
        case(
            "rule_c",
            ".c",
            '#include <stdio.h>\nint main(void){puts("rule");return 0；}\n',
            "repaired",
            "rule\n",
        )
        repaired = case(
            "model_c",
            ".c",
            '#include <stdio.h>\nint main(void) {\nputs("model");\nreturn unknown_rc_status;\n}\n',
            "repaired",
            "model\n",
            flags=("--no-cache", "--no-fallback"),
            response={
                "diff": (
                    '@@ -99,9 +99,9 @@\n puts("model");\n'
                    "-return unknown_rc_status;\n+return 0;\n }\n"
                )
            },
        )
        assert repaired["result"]["repair"]["patch_normalizations"]
        case(
            "generated",
            ".txt",
            "写一个程序输出 generated\n",
            "generated",
            "generated\n",
            flags=("--no-cache",),
            response={
                "source": '#include <stdio.h>\nint main(void){puts("generated");return 0;}\n'
            },
        )
        case(
            "creative",
            ".txt",
            "一颗石头开始讲笑话\n",
            "creative_fallback",
            None,
            flags=("--play", "--offline"),
        )
        case(
            "multi_error",
            ".c",
            (ROOT / "examples/multi_error.c").read_text("utf-8"),
            "repaired",
            "value=7\n",
            flags=("--offline", "--no-fallback"),
        )
        mixed = case(
            "mixed_language",
            ".c",
            "int main(void){cout<<“hello”;}\n",
            "creative_fallback",
            None,
            flags=("--offline",),
            json_build=True,
        )
        assert any("C++" in item for item in mixed["result"]["fallback"]["guidance"])
        case(
            "strict",
            ".c",
            "int main(void){return strict_missing;}\n",
            "native_failed",
            "",
            flags=("--strict",),
            exit_code=102,
        )
        case("program_exit", ".py", "raise SystemExit(7)\n", "native_ok", "", exit_code=7)
        server.status_code = 429
        failed = case(
            "redacted",
            ".c",
            "int main(void){return privacy_missing;}\n",
            "failed",
            None,
            flags=("--no-cache", "--no-fallback", "--max-rounds", "1", "--trace"),
            exit_code=103,
            response=key,
            json_build=True,
        )
        assert "[REDACTED]" in json.dumps(failed)
        for path in (directory / "redacted" / ".yescc").rglob("*"):
            if path.is_file():
                assert key.encode() not in path.read_bytes(), path
    finally:
        server.stop()
    return {
        "version": expected_version,
        "platform": platform.system(),
        "python": platform.python_version(),
        "wheel": wheel.name,
        "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        "lock_sha256": hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest(),
        "isolated_install": True,
        "paid_model_calls": 0,
        "total": len(rows),
        "passed": len(rows),
        "rows": rows,
        "licenses": licenses,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="yescc-package-") as raw:
        version = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]["version"]
        wheel = args.wheel or ROOT / "dist" / f"yescc-{version}-py3-none-any.whl"
        evidence = verify(wheel.resolve(), Path(raw))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {key: value for key, value in evidence.items() if key not in {"licenses", "rows"}}
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
