"""按实际冻结产物采集声明与来源；未知原生依赖明确阻止完整覆盖结论。"""

from __future__ import annotations

import ast
import hashlib
import json
import platform
import re
import shutil
import subprocess
import sys
import sysconfig
import tomllib
from importlib import metadata
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
# 这些扩展可能把附带库静态链接进去，单有 CPython LICENSE 不能覆盖它们。
_EXTENSION_COMPONENTS = {
    "_ssl": "OpenSSL",
    "_hashlib": "OpenSSL",
    "_bz2": "bzip2",
    "_lzma": "xz/liblzma",
    "_ctypes": "libffi",
    "pyexpat": "Expat",
    "_decimal": "libmpdec",
    "_sqlite3": "SQLite",
    "readline": "readline/libedit",
    "zlib": "zlib",
}
_DYNAMIC_PREFIXES = {
    "OpenSSL": ("libssl", "libcrypto"),
    "bzip2": ("libbz2",),
    "xz/liblzma": ("liblzma",),
    "libffi": ("libffi",),
    "Expat": ("libexpat",),
    "libmpdec": ("libmpdec",),
    "SQLite": ("libsqlite3",),
    "readline/libedit": ("libreadline", "libedit"),
    "zlib": ("libz.so",),
}
_REFERENCES = {
    "pyinstaller_license": "https://pyinstaller.org/en/stable/license.html",
    "python_license": "https://docs.python.org/3/license.html",
    "runtime_provenance": "https://docs.astral.sh/python-build-standalone/distributions/",
    "certifi_source": "https://github.com/certifi/python-certifi",
    "mpl": "https://www.mozilla.org/en-US/MPL/2.0/",
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(command: list[str]) -> str | None:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _toc_entries(value: object) -> list[tuple[str, str, str]]:
    """TOC 是 Python 字面量；只用 literal_eval，不执行打包器生成的代码。"""
    rows: list[tuple[str, str, str]] = []
    if isinstance(value, (list, tuple)):
        if len(value) == 3 and all(isinstance(item, str) for item in value):
            destination, source, kind = value
            if kind in {"BINARY", "EXTENSION", "EXECUTABLE", "DATA", "SYMLINK", "PYZ", "PKG"}:
                rows.append((destination, source, kind))
        else:
            for item in value:
                rows.extend(_toc_entries(item))
    return rows


def _needed_libraries(source: Path) -> set[str]:
    readelf = shutil.which("readelf")
    if not readelf:
        return set()
    data = _run([readelf, "-d", str(source)])
    return set(re.findall(r"\(NEEDED\).*?\[([^\]]+)\]", data or ""))


def _installed_runtime() -> list[metadata.Distribution]:
    # packaging 是 PyInstaller 的构建依赖，不成为 YesCC 运行依赖。
    from packaging.requirements import Requirement

    pending = ["yescc"]
    found: dict[str, metadata.Distribution] = {}
    while pending:
        name = pending.pop().lower().replace("_", "-")
        if name in found:
            continue
        dist = metadata.distribution(name)
        found[name] = dist
        for value in dist.requires or []:
            requirement = Requirement(value)
            if requirement.marker is None or requirement.marker.evaluate({"extra": ""}):
                pending.append(requirement.name)
    return [found[name] for name in sorted(found)]


def _source_artifacts(name: str, version: str) -> dict[str, Any]:
    lock = ROOT / "uv.lock"
    if not lock.exists():
        return {}
    for package in tomllib.loads(lock.read_text("utf-8")).get("package", []):
        if package["name"] == name and package["version"] == version:
            return {key: package[key] for key in ("source", "sdist") if key in package}
    return {}


def _notice_with_common(source: Path) -> list[Path]:
    notices = [source]
    for value in re.findall(
        r"/usr/share/common-licenses/([A-Za-z0-9.+-]+)", source.read_text("utf-8", errors="replace")
    ):
        common = Path("/usr/share/common-licenses") / value.rstrip(".")
        if common.is_file() and common not in notices:
            notices.append(common)
    return notices


def _system_notices(source: Path) -> tuple[str | None, list[Path]]:
    """只采集本机包管理器声明的原文，不从库名猜许可证。"""
    dpkg = shutil.which("dpkg-query")
    if dpkg:
        owners = _run([dpkg, "-S", str(source)])
        for line in (owners or "").splitlines():
            package, separator, _ = line.rpartition(": ")
            if not separator:
                continue
            name = package.split(":", 1)[0]
            copyright_file = Path("/usr/share/doc") / name / "copyright"
            if copyright_file.is_file():
                version = _run([dpkg, "-W", "-f=${Version}", package])
                return f"dpkg:{package}={version or 'unknown'}", _notice_with_common(copyright_file)
    pacman = shutil.which("pacman")
    if pacman:
        package = _run([pacman, "-Qoq", str(source)])
        if package and "\n" not in package:
            directory = Path("/usr/share/licenses") / package
            notices = [p for p in directory.rglob("*") if p.is_file()] if directory.exists() else []
            if notices:
                version = _run([pacman, "-Q", package])
                return f"pacman:{version or package}", notices
    return None, []


def collect(bundle: Path, workdir: Path, output: Path) -> dict[str, Any]:
    """写 bundle/licenses 原文和 output JSON；不把未识别库或静态依赖判成已覆盖。"""
    bundle, workdir, output = bundle.resolve(), workdir.resolve(), output.resolve()
    if not bundle.is_dir() or not workdir.is_dir():
        raise ValueError("bundle 与 workdir 必须是已有目录")
    notice_root = bundle / "licenses"
    notice_root.mkdir(exist_ok=True)
    unresolved: list[dict[str, str]] = []
    texts: list[dict[str, str]] = []
    owners: dict[Path, str] = {}
    components: list[dict[str, Any]] = []

    def copy_notice(source: Path, component: str) -> str:
        destination = notice_root / component / (_sha(source)[:12] + "-" + source.name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        relative = destination.relative_to(bundle).as_posix()
        texts.append({"component": component, "path": relative, "sha256": _sha(destination)})
        return relative

    try:
        runtime = _installed_runtime()
    except (metadata.PackageNotFoundError, ImportError) as exc:
        runtime = []
        unresolved.append({"component": "runtime-distributions", "reason": str(exc)})
    for dist in runtime:
        name = str(dist.metadata["Name"]).lower().replace("_", "-")
        notices = []
        for item in dist.files or []:
            source = Path(dist.locate_file(item)).resolve()
            if source.is_file():
                owners[source] = name
                if ".dist-info/licenses/" in str(item).replace("\\", "/"):
                    notices.append(copy_notice(source, name))
        components.append(
            {
                "name": name,
                "version": dist.version,
                "declared_license": dist.metadata.get("License-Expression")
                or dist.metadata.get("License"),
                "notices": notices,
                "project_urls": dist.metadata.get_all("Project-URL") or [],
                "source_artifacts": _source_artifacts(name, dist.version),
            }
        )
        if not notices:
            unresolved.append({"component": name, "reason": "installed license texts missing"})
        if name == "certifi":
            source = Path(dist.locate_file("certifi/cacert.pem"))
            if source.is_file():
                source_copy = copy_notice(source, "certifi-source")
                declaration = notice_root / "certifi-source" / "SOURCE.txt"
                declaration.write_text(
                    f"Certifi {dist.version}: the exact, unmodified covered CA source data is "
                    f"provided in this archive at {source_copy}.\n"
                    f"SHA-256: {_sha(source)}\n"
                    "Source data remains governed by MPL-2.0; see original certifi notice.\n"
                    f"License: {_REFERENCES['mpl']}\n"
                    f"Upstream: {_REFERENCES['certifi_source']}\n",
                    encoding="utf-8",
                )
                texts.append(
                    {
                        "component": "certifi-source",
                        "path": declaration.relative_to(bundle).as_posix(),
                        "sha256": _sha(declaration),
                    }
                )
            else:
                unresolved.append({"component": "certifi", "reason": "CA source data missing"})

    stdlib = Path(sysconfig.get_path("stdlib")).resolve()
    python_license = next(
        (
            p
            for p in (
                stdlib / "LICENSE.txt",
                Path(sys.base_prefix) / "LICENSE.txt",
                Path(sys.base_prefix) / "LICENSE",
            )
            if p.is_file()
        ),
        None,
    )
    python_notices = [copy_notice(python_license, "cpython")] if python_license else []
    interpreter_package, interpreter_notices = _system_notices(Path(sys.executable).resolve())
    if interpreter_package:
        python_notices.extend(copy_notice(p, "cpython-system") for p in interpreter_notices)
    components.append(
        {
            "name": "cpython",
            "version": platform.python_version(),
            "notices": python_notices,
            "system_package": interpreter_package,
            "references": [_REFERENCES["python_license"]],
        }
    )
    if not python_notices:
        unresolved.append({"component": "cpython", "reason": "actual interpreter LICENSE missing"})
    try:
        freezer = metadata.distribution("pyinstaller")
        freezer_notices = []
        for item in freezer.files or []:
            if item.name.upper() in {"COPYING.TXT", "LICENSE", "LICENSE.TXT"}:
                source = Path(freezer.locate_file(item))
                if source.is_file():
                    freezer_notices.append(copy_notice(source, "pyinstaller"))
        components.append(
            {
                "name": "pyinstaller",
                "version": freezer.version,
                "notices": freezer_notices,
                "license_basis": "GPL-2.0 with bootloader exception; some Apache-2.0 files",
                "references": [_REFERENCES["pyinstaller_license"]],
            }
        )
        if not freezer_notices:
            unresolved.append({"component": "pyinstaller", "reason": "actual COPYING text missing"})
    except metadata.PackageNotFoundError:
        unresolved.append(
            {"component": "pyinstaller", "reason": "build distribution not installed"}
        )

    mappings: dict[str, dict[str, Any]] = {}
    toc_records = []
    for toc in sorted(workdir.rglob("*.toc")):
        if not toc.name.startswith(("Analysis-", "COLLECT-")):
            continue
        try:
            entries = _toc_entries(ast.literal_eval(toc.read_text("utf-8")))
        except (ValueError, SyntaxError, UnicodeError) as exc:
            unresolved.append({"component": toc.name, "reason": f"cannot parse TOC: {exc}"})
            continue
        toc_records.append({"file": str(toc), "sha256": _sha(toc), "entries": len(entries)})
        for destination, raw_source, kind in entries:
            source = Path(raw_source)
            record: dict[str, Any] = {"source": raw_source, "kind": kind, "toc": str(toc)}
            if source.is_absolute() and source.is_file():
                record["source_sha256"] = _sha(source)
            mappings[destination.replace("\\", "/")] = record
    if not toc_records:
        unresolved.append({"component": "build-toc", "reason": "no Analysis/COLLECT TOC found"})

    files = []
    # PBS 等发行版把附带库编进 libpython；仅扫独立扩展文件会把这些漏成已覆盖。
    builtin_components = {
        component
        for extension, component in _EXTENSION_COMPONENTS.items()
        if extension in sys.builtin_module_names
    }
    native_components: set[str] = set(builtin_components)
    dynamic_requirements: dict[str, set[str]] = {}
    static_extension_components: set[str] = set()
    for path in sorted(bundle.rglob("*")):
        if not path.is_file() and not path.is_symlink():
            continue
        relative = path.relative_to(bundle).as_posix()
        if relative.startswith("licenses/") or path.resolve() == output:
            continue
        record: dict[str, Any] = {"path": relative}
        if path.is_symlink():
            record["symlink_target"] = str(path.readlink())
            if not path.exists():
                unresolved.append({"component": relative, "reason": "broken symlink"})
                files.append(record)
                continue
            if not path.resolve().is_relative_to(bundle):
                unresolved.append({"component": relative, "reason": "symlink escapes bundle"})
                files.append(record)
                continue
        if path.is_file():
            record.update({"sha256": _sha(path), "bytes": path.stat().st_size})
        logical = relative.removeprefix("_internal/")
        origin = mappings.get(relative) or mappings.get(logical)
        if origin:
            record["origin"] = origin
        is_native = (origin or {}).get("kind") in {"BINARY", "EXTENSION", "EXECUTABLE"}
        is_native = is_native or path.suffix.lower() in {".dll", ".pyd", ".so", ".exe"}
        if is_native:
            source = Path(origin["source"]).resolve() if origin else None
            owner = owners.get(source) if source else None
            if owner:
                record["license_component"] = owner
            elif source and source.is_relative_to(stdlib):
                record["license_component"] = "cpython"
                extension = logical.rsplit("/", 1)[-1].split(".", 1)[0]
                component = _EXTENSION_COMPONENTS.get(extension)
                if component:
                    native_components.add(component)
                    needed = {
                        name
                        for name in _needed_libraries(source)
                        if name.startswith(_DYNAMIC_PREFIXES[component])
                    }
                    if needed:
                        dynamic_requirements.setdefault(component, set()).update(needed)
                    else:
                        static_extension_components.add(component)
            elif path.name.startswith(("libpython", "python3")) and source:
                record["license_component"] = "cpython"
            elif (origin or {}).get("kind") == "EXECUTABLE":
                record["license_component"] = "yescc + PyInstaller bootloader"
            else:
                package, notices = (
                    _system_notices(source) if source and source.exists() else (None, [])
                )
                if package and notices:
                    safe_name = package.replace(":", "_").replace("/", "_")
                    record["license_component"] = package
                    record["notices"] = [copy_notice(p, safe_name) for p in notices]
                else:
                    unresolved.append(
                        {"component": relative, "reason": "native origin/license not resolved"}
                    )
        files.append(record)
    resolved_native_names = {
        Path(record["path"]).name for record in files if record.get("license_component")
    }
    dependency_review = []
    for component in sorted(native_components):
        needed = dynamic_requirements.get(component, set())
        covered = (
            component not in builtin_components
            and component not in static_extension_components
            and bool(needed)
            and needed <= resolved_native_names
        )
        dependency_review.append(
            {
                "component": component,
                "covered_by_dynamic_inventory": covered,
                "needed_libraries": sorted(needed),
            }
        )
        if not covered:
            unresolved.append(
                {
                    "component": component,
                    "reason": "CPython builtin/extension may statically include this library, or "
                    "its dynamic library lacks collected notices; exact build provenance and "
                    "original notices require review",
                }
            )
    report = {
        "schema_version": "1",
        "bundle": str(bundle),
        "python": sys.version,
        "platform": platform.platform(),
        "interpreter_builtin_modules": sorted(sys.builtin_module_names),
        "native_dependency_scope": {
            "builtin_components": sorted(builtin_components),
            "extension_components": sorted(native_components - builtin_components),
            "dynamic_dependency_review": dependency_review,
            "basis": "actual build interpreter builtins plus collected extension files; "
            "CPython LICENSE alone is not evidence for incorporated libraries",
        },
        "coverage_complete": not unresolved,
        "legal_conclusion": False,
        "components": components,
        "license_files": texts,
        "bundle_files": files,
        "build_tocs": toc_records,
        "unresolved": unresolved,
        "references": _REFERENCES,
        "scope": "Actual bundle inventory and source-license collection, not legal advice or "
        "a claim that upstream URLs alone satisfy every distribution obligation.",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report
