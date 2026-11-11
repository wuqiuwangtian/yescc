"""Build reviewable native release candidates; never upload or tag them."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import zipfile
from importlib.metadata import version
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(output: Path) -> dict:
    """Build one-folder executables with notices and a content manifest."""
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    machine = platform.machine().lower()
    if machine not in {"amd64", "x86_64"} or sys.platform not in {"linux", "win32"}:
        raise RuntimeError("Native candidates currently target Linux/Windows x86_64 only")
    target = "windows-x86_64" if os.name == "nt" else "linux-x86_64"
    candidate_version = version("yescc")
    work = output / "work"
    work.mkdir(exist_ok=True)
    # Optional CLI/test dependencies installed for validation must not leak into
    # PyInstaller's import graph. Freeze an installed wheel in a dedicated venv.
    wheel_dir = work / "wheel"
    subprocess.run(["uv", "build", "--wheel", "--out-dir", str(wheel_dir)], cwd=ROOT, check=True)
    wheel = wheel_dir / f"yescc-{candidate_version}-py3-none-any.whl"
    constraints = work / "constraints.txt"
    constraints.write_bytes(
        subprocess.check_output(
            [
                "uv",
                "export",
                "--locked",
                "--all-extras",
                "--no-dev",
                "--no-emit-project",
                "--no-hashes",
            ],
            cwd=ROOT,
        )
    )
    builder = work / "builder"
    subprocess.run(
        ["uv", "venv", "--clear", "--python", sys.executable, str(builder)],
        cwd=ROOT,
        check=True,
    )
    builder_python = builder / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(builder_python),
            "--constraint",
            str(constraints),
            str(wheel),
            f"pyinstaller=={version('pyinstaller')}",
        ],
        cwd=ROOT,
        check=True,
    )
    command = [
        str(builder_python),
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--noupx",
        "--name",
        "yescc",
        "--distpath",
        str(output / "unpacked"),
        "--workpath",
        str(work),
        "--specpath",
        str(work),
        "--recursive-copy-metadata",
        "yescc",
        "--collect-all",
        "tree_sitter",
        "--collect-all",
        "tree_sitter_c",
        "--collect-data",
        "certifi",
        "--hidden-import",
        "anyio._backends._asyncio",
        "--exclude-module",
        "pytest",
        "--exclude-module",
        "twine",
        "--exclude-module",
        "PyInstaller",
    ]
    if os.name != "nt":
        command.extend(["--hidden-import", "resource"])
    command.append(str(ROOT / "freezer" / "entrypoint.py"))
    freeze_env = dict(os.environ)
    if os.name == "nt":
        # Hosted runners have JDK/SDK copies of CRT DLLs on PATH. They are not
        # Python dependencies and must not become accidental bundled origins.
        windows = Path(os.environ["SYSTEMROOT"])
        freeze_env["PATH"] = os.pathsep.join(
            map(
                str,
                (
                    builder_python.parent,
                    Path(sys.base_prefix),
                    Path(sys.base_prefix) / "DLLs",
                    windows / "System32",
                    windows,
                ),
            )
        )
    subprocess.run(command, cwd=ROOT, env=freeze_env, check=True, timeout=600)
    bundle = output / "unpacked" / "yescc"
    binary = bundle / ("yescc.exe" if os.name == "nt" else "yescc")
    subprocess.run([str(binary), "--version"], cwd=output, check=True, timeout=30)
    for name in ("LICENSE", "THIRD_PARTY_NOTICES.md", "README.md"):
        shutil.copy2(ROOT / name, bundle / name)
    shutil.copytree(ROOT / "examples", bundle / "examples", dirs_exist_ok=True)
    (bundle / "START-HERE.txt").write_text(
        "YesCC " + candidate_version + " candidate (not published)\n"
        "Keep the entire extracted directory, including _internal and licenses.\n"
        "The CLI bundles its Python runtime; user programs need external toolchains:\n"
        "C: GCC; JavaScript: Node.js; Python: Python 3 on PATH.\n"
        "Examples: ./yescc --version; ./yescc run examples/hello.c --offline\n"
        "Windows: use .\\yescc.exe in place of ./yescc.\n"
        "Read README.md for AI, privacy and best-effort isolation limits.\n"
        "This candidate has not been approved for distribution; see licenses inventory.\n",
        encoding="utf-8",
    )
    from bench.python_provenance import ensure_python_provenance
    from bench.windows_provenance import ensure_windows_provenance

    provenance = ensure_python_provenance(ROOT / "dist" / "python-provenance")
    if provenance is not None:
        shutil.copy2(provenance, output / "python-provenance.json")
    windows_provenance = ensure_windows_provenance(ROOT / "dist" / "windows-provenance", work)
    if windows_provenance is not None:
        shutil.copy2(windows_provenance, output / "windows-provenance.json")
    license_output = output / "binary-licenses.json"
    audit_command = [
        str(builder_python),
        "-c",
        "from pathlib import Path;from bench.binary_licenses import collect;import sys;"
        "collect(Path(sys.argv[1]),Path(sys.argv[2]),Path(sys.argv[3]),"
        "provenance=Path(sys.argv[4]) if sys.argv[4] else None,"
        "windows_provenance=Path(sys.argv[5]) if sys.argv[5] else None)",
        str(bundle),
        str(work),
        str(license_output),
        str(provenance) if provenance else "",
        str(windows_provenance) if windows_provenance else "",
    ]
    subprocess.run(audit_command, cwd=ROOT, check=True, timeout=120)
    license_report = json.loads(license_output.read_text("utf-8"))
    # The inventory cannot hash itself; the outer bundle manifest covers its bytes.
    shutil.copy2(license_output, bundle / "licenses" / "INVENTORY.json")
    manifest = {
        "version": candidate_version,
        "target": target,
        "python": platform.python_version(),
        "pyinstaller": version("pyinstaller"),
        "installed_wheel_sha256": digest(wheel),
        "dedicated_builder": True,
        "builder_dll_search": "python_and_system_only" if os.name == "nt" else "system_loader",
        "libc": platform.libc_ver() if os.name != "nt" else None,
        "code_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "source_dirty": bool(
            subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip()
        ),
        "licenses_complete": license_report["coverage_complete"],
        "files": {},
    }
    for path in sorted(bundle.rglob("*")):
        relative = path.relative_to(bundle).as_posix()
        if path.is_symlink():
            manifest["files"][relative] = {"symlink": os.readlink(path)}
        elif path.is_file():
            manifest["files"][relative] = {"sha256": digest(path), "bytes": path.stat().st_size}
    (bundle / "MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    archive_stem = f"yescc-{candidate_version}-{target}"
    if os.name == "nt":
        archive = output / (archive_stem + ".zip")
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
            for path in sorted(bundle.rglob("*")):
                if path.is_file():
                    zipped.write(path, (Path(archive_stem) / path.relative_to(bundle)).as_posix())
    else:
        archive = output / (archive_stem + ".tar.gz")
        with tarfile.open(archive, "w:gz", dereference=False) as packed:
            packed.add(bundle, arcname=archive_stem)
    record = {
        "version": candidate_version,
        "target": target,
        "binary": str(binary),
        "archive": archive.name,
        "archive_sha256": digest(archive),
        "licenses_complete": license_report["coverage_complete"],
        "manifest": manifest,
    }
    (output / "binary-build.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output / "SHA256SUMS").write_text(f"{digest(archive)}  {archive.name}\n", encoding="ascii")
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / "binary")
    args = parser.parse_args()
    record = build(args.output)
    print(json.dumps({k: v for k, v in record.items() if k != "manifest"}))


if __name__ == "__main__":
    main()
