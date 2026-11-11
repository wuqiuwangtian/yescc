"""定位并校验 PBS 完整构建档案；版本相同或下载了许可证不等于运行时已匹配。"""

from __future__ import annotations

import hashlib
import json
import platform
import re
import shutil
import subprocess
import sys
import sysconfig
import tarfile
import tempfile
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Any

MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
API = "https://api.github.com/repos/astral-sh/python-build-standalone/releases/tags/"
SOURCE = "https://raw.githubusercontent.com/astral-sh/python-build-standalone/"


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "YesCC-release-license-audit"})
    with urllib.request.urlopen(request, timeout=30) as response:
        data = response.read(8 * 1024 * 1024 + 1)
    if len(data) > 8 * 1024 * 1024:
        raise ValueError("metadata response exceeds 8 MiB")
    return data


def _descriptor() -> dict[str, Any] | None:
    prefix = Path(sys.base_prefix).resolve()
    build = prefix / "BUILD"
    if not build.is_file():
        return None
    release = build.read_text("utf-8").strip()
    if not re.fullmatch(r"[0-9]{8}", release):
        raise ValueError("unrecognized PBS BUILD marker")
    machine = platform.machine().lower()
    architecture = {"amd64": "x86_64", "x86_64": "x86_64", "aarch64": "aarch64"}.get(machine)
    if not architecture:
        raise ValueError(f"unsupported PBS architecture: {machine}")
    # uv 的安装键保留 x86_64_vN；不能把提高指令集基线的构建当通用 x86_64。
    variant_arch = re.search(r"(?:^|-)(x86_64_v[234])(?:-|$)", prefix.name)
    if variant_arch:
        architecture = variant_arch.group(1)
    if sys.platform == "win32":
        triple = f"{architecture}-pc-windows-msvc"
    elif sys.platform.startswith("linux"):
        libc = "musl" if prefix.name.endswith("-musl") else "gnu"
        triple = f"{architecture}-unknown-linux-{libc}"
    else:
        raise ValueError(f"PBS provenance not implemented for {sys.platform}")
    return {
        "build": release,
        "version": platform.python_version(),
        "triple": triple,
        "free_threaded": bool(sysconfig.get_config_var("Py_GIL_DISABLED")),
        "debug": hasattr(sys, "gettotalrefcount"),
        "prefix": str(prefix),
        "build_marker_sha256": _sha(build),
    }


def _select_asset(release: dict[str, Any], descriptor: dict[str, Any]) -> dict[str, Any]:
    prefix = f"cpython-{descriptor['version']}+{descriptor['build']}-{descriptor['triple']}-"
    candidates = []
    for asset in release.get("assets", []):
        name = asset["name"]
        if not name.startswith(prefix) or not name.endswith("-full.tar.zst"):
            continue
        options = name[len(prefix) : -len("-full.tar.zst")].split("+")
        if ("freethreaded" in options) != descriptor["free_threaded"]:
            continue
        if ("debug" in options) != descriptor["debug"] or "static" in options:
            continue
        # 官方 install_only 从同版/同target可用最快构建生成。最终仍必须核对字节。
        rank = 0 if {"pgo", "lto"} <= set(options) else 1 if "pgo" in options else 2
        candidates.append((rank, name, asset))
    if not candidates:
        raise ValueError("no exact version/build/triple/variant full archive in official release")
    _, _, selected = min(candidates)
    digest = selected.get("digest") or ""
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("official asset has no SHA-256 digest; cannot verify archive")
    if selected["size"] > MAX_ARCHIVE_BYTES:
        raise ValueError(f"identified full archive exceeds planned {MAX_ARCHIVE_BYTES} byte limit")
    return {key: selected[key] for key in ("name", "size", "digest", "browser_download_url", "id")}


def _download(asset: dict[str, Any], directory: Path) -> Path:
    destination = directory / asset["name"]
    expected = asset["digest"].split(":", 1)[1]
    if (
        destination.is_file()
        and destination.stat().st_size == asset["size"]
        and _sha(destination) == expected
    ):
        return destination
    temporary = destination.with_suffix(destination.suffix + ".part")
    request = urllib.request.Request(
        asset["browser_download_url"], headers={"User-Agent": "YesCC-release-license-audit"}
    )
    digest = hashlib.sha256()
    count = 0
    try:
        with urllib.request.urlopen(request, timeout=30) as response, temporary.open("wb") as out:
            while data := response.read(1024 * 1024):
                count += len(data)
                if count > asset["size"] or count > MAX_ARCHIVE_BYTES:
                    raise ValueError("archive exceeds recorded asset size")
                digest.update(data)
                out.write(data)
        if count != asset["size"] or digest.hexdigest() != expected:
            raise ValueError("archive SHA-256/size does not match official release asset")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def _extract(archive: Path, directory: Path) -> Path:
    # 构建依赖，不会捆绑进 YesCC。常规 dev-only 单元测试不需要 zstandard。
    import zstandard

    payload = directory / "payload"
    payload.mkdir(exist_ok=True)
    executable_names = {
        "python",
        "python.exe",
        f"python{sys.version_info.major}.{sys.version_info.minor}",
    }
    with (
        archive.open("rb") as compressed,
        zstandard.ZstdDecompressor().stream_reader(compressed) as decompressed,
        tarfile.open(fileobj=decompressed, mode="r|") as members,
    ):
        for member in members:
            path = PurePosixPath(member.name)
            if not member.isfile() or path.is_absolute() or ".." in path.parts:
                continue
            lower = path.as_posix().lower()
            wanted = path.as_posix() == "python/PYTHON.json" or lower.startswith("python/licenses/")
            native = lower.startswith("python/install/") and (
                path.name in executable_names
                or lower.endswith((".dll", ".pyd", ".so"))
                or ".so." in path.name
            )
            if not wanted and not native:
                continue
            if member.size > MAX_ARCHIVE_BYTES:
                raise ValueError("individual archive member exceeds extraction limit")
            destination = payload.joinpath(*path.parts)
            if not destination.resolve().is_relative_to(payload.resolve()):
                raise ValueError("archive member escapes provenance directory")
            destination.parent.mkdir(parents=True, exist_ok=True)
            source = members.extractfile(member)
            if source is None:
                raise ValueError("missing tar member data")
            with source, destination.open("wb") as out:
                shutil.copyfileobj(source, out)
    metadata = payload / "python/PYTHON.json"
    if not metadata.is_file():
        raise ValueError("full archive does not contain PYTHON.json")
    return payload / "python"


def _license_paths(value: object) -> set[str]:
    result: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"license_path", "license_paths"}:
                if isinstance(child, str):
                    result.add(child)
                elif isinstance(child, list):
                    result.update(p for p in child if isinstance(p, str))
            else:
                result.update(_license_paths(child))
    elif isinstance(value, list):
        for child in value:
            result.update(_license_paths(child))
    return result


def _archive_path(payload: Path, value: str) -> Path:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("unsafe path in PYTHON.json")
    result = payload.joinpath(*path.parts)
    if not result.resolve().is_relative_to(payload.resolve()):
        raise ValueError("PYTHON.json path escapes archive payload")
    return result


def _stripped_match(
    source: Path, target_digest: str, recipe: dict[str, str] | None
) -> dict[str, Any]:
    if not recipe:
        return {"matched": False, "reason": "raw hash differs; no verified stripping recipe"}
    for candidate in ("llvm-strip", "strip"):
        tool = shutil.which(candidate)
        if not tool:
            continue
        with tempfile.TemporaryDirectory(prefix="yescc-provenance-strip-") as raw:
            copy = Path(raw) / source.name
            shutil.copy2(source, copy)
            try:
                result = subprocess.run(
                    [tool, "--strip-debug", str(copy)], capture_output=True, timeout=60, check=False
                )
                version = subprocess.run(
                    [tool, "--version"], capture_output=True, text=True, timeout=10, check=False
                )
            except (OSError, subprocess.TimeoutExpired):
                continue
            normalized = _sha(copy)
            if result.returncode == 0 and normalized == target_digest:
                return {
                    "matched": True,
                    "method": "exact bytes after --strip-debug",
                    "tool": tool,
                    "tool_version": version.stdout.strip(),
                    "sha256": normalized,
                    "upstream_recipe": recipe,
                }
    return {"matched": False, "reason": "raw hash and available --strip-debug output differ"}


def _verify_runtime(
    prefix: Path, payload: Path, data: dict[str, Any], recipe: dict[str, str] | None
) -> dict[str, Any]:
    core = data.get("build_info", {}).get("core", {})
    paths = {data.get("python_exe"), core.get("shared_lib")}
    required = {p for p in paths if isinstance(p, str)}
    if not isinstance(core.get("shared_lib"), str) or not isinstance(data.get("python_exe"), str):
        return {
            "verified": False,
            "binaries": [],
            "reason": "required interpreter/shared libpython path missing",
        }
    extensions = data.get("build_info", {}).get("extensions", {})
    for definitions in extensions.values():
        for definition in definitions:
            if definition.get("shared_lib"):
                paths.add(definition["shared_lib"])
    binaries = []
    for name in sorted(p for p in paths if isinstance(p, str)):
        archived = _archive_path(payload, name)
        relative = PurePosixPath(name)
        if not relative.parts or relative.parts[0] != "install":
            continue
        installed = prefix.joinpath(*relative.parts[1:]).resolve()
        if not installed.is_file():
            if name in required:
                binaries.append(
                    {"path": name, "matched": False, "reason": "installed core missing"}
                )
            continue
        if not archived.is_file():
            binaries.append({"path": name, "matched": False, "reason": "archive binary missing"})
            continue
        actual = _sha(installed)
        original = _sha(archived)
        match = (
            {"matched": True, "method": "exact raw SHA-256"}
            if actual == original
            else (_stripped_match(archived, actual, recipe))
        )
        binaries.append(
            {
                "path": name,
                "installed": str(installed),
                "installed_sha256": actual,
                "archive_sha256": original,
                **match,
            }
        )
    core_matches = {entry["path"] for entry in binaries if entry["matched"]}
    verified = (
        bool(required) and required <= core_matches and all(row["matched"] for row in binaries)
    )
    return {"verified": verified, "required_core_paths": sorted(required), "binaries": binaries}


def ensure_python_provenance(cache_dir: Path) -> Path | None:
    """返回含真实匹配结论的JSON；非PBS返回None，匹配失败同样保留false报告。"""
    descriptor = _descriptor()
    if descriptor is None:
        return None
    key = hashlib.sha256(json.dumps(descriptor, sort_keys=True).encode()).hexdigest()[:20]
    directory = cache_dir.resolve() / key
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / "python-provenance.json"
    report: dict[str, Any] = {
        "schema_version": "1",
        "descriptor": descriptor,
        "verified": False,
        "legal_conclusion": False,
    }
    try:
        release_url = API + descriptor["build"]
        release_bytes = _get(release_url)
        release = json.loads(release_bytes)
        (directory / "release.json").write_bytes(release_bytes)
        asset = _select_asset(release, descriptor)
        report.update(
            {
                "release_url": release_url,
                "release_metadata_sha256": hashlib.sha256(release_bytes).hexdigest(),
                "asset": asset,
            }
        )
        archive = _download(asset, directory)
        report["archive_sha256"] = _sha(archive)
        payload = _extract(archive, directory)
        metadata_path = payload / "PYTHON.json"
        data = json.loads(metadata_path.read_text("utf-8"))
        if (
            data["python_version"] != descriptor["version"]
            or data["target_triple"] != descriptor["triple"]
        ):
            raise ValueError("full archive interpreter version/target mismatch")
        report.update(
            {
                "metadata_path": str(metadata_path),
                "metadata_sha256": _sha(metadata_path),
                "payload": str(payload),
                "python_metadata": data,
            }
        )
        recipe = None
        recipe_url = SOURCE + descriptor["build"] + "/src/release.rs"
        try:
            recipe_data = _get(recipe_url)
            source = recipe_data.decode("utf-8")
            if re.search(
                r'fn llvm_strip\(.*?\.arg\("--strip-debug"\)\s*\.arg\("-"\)', source, re.S
            ):
                (directory / "upstream-release.rs").write_bytes(recipe_data)
                recipe = {"url": recipe_url, "sha256": hashlib.sha256(recipe_data).hexdigest()}
        except (OSError, ValueError):
            pass
        matched = _verify_runtime(Path(descriptor["prefix"]), payload, data, recipe)
        report["runtime_match"] = matched
        notices = []
        missing = []
        for name in sorted(_license_paths(data)):
            path = _archive_path(payload, name)
            if not path.is_file():
                missing.append(name)
                continue
            notices.append({"archive_path": name, "file": str(path), "sha256": _sha(path)})
        report["license_files"] = notices
        report["missing_license_paths"] = missing
        report["verified"] = matched["verified"] and bool(notices) and not missing
    except (OSError, ValueError, KeyError, TypeError, ImportError, tarfile.TarError) as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output


def read_verified_python_provenance(path: Path) -> dict[str, Any]:
    """只读复核准备好的报告，不联网；来源/运行时/原文任一不匹配则保持false。"""
    report = json.loads(path.read_text("utf-8"))
    if not report.get("verified"):
        return report
    try:
        descriptor = _descriptor()
        if descriptor != report["descriptor"]:
            raise ValueError("prepared provenance belongs to another actual runtime")
        asset = report["asset"]
        if Path(asset["name"]).name != asset["name"]:
            raise ValueError("unsafe asset filename")
        archive = path.parent / asset["name"]
        if _sha(archive) != asset["digest"].removeprefix("sha256:"):
            raise ValueError("cached full archive hash no longer matches official asset")
        payload = Path(report["payload"]).resolve()
        if not payload.is_relative_to(path.parent.resolve()):
            raise ValueError("payload outside provenance cache")
        metadata_path = payload / "PYTHON.json"
        if _sha(metadata_path) != report["metadata_sha256"]:
            raise ValueError("PYTHON.json changed after preparation")
        data = json.loads(metadata_path.read_text("utf-8"))
        if data != report["python_metadata"]:
            raise ValueError("copied Python metadata differs from original")
        recipe = None
        recipe_file = path.parent / "upstream-release.rs"
        if recipe_file.is_file():
            recipe = {
                "url": SOURCE + descriptor["build"] + "/src/release.rs",
                "sha256": _sha(recipe_file),
            }
        current = _verify_runtime(Path(descriptor["prefix"]), payload, data, recipe)
        if not current["verified"]:
            raise ValueError("prepared original binaries do not match current runtime")
        for row in report["license_files"]:
            source = _archive_path(payload, row["archive_path"])
            if source.resolve() != Path(row["file"]).resolve() or _sha(source) != row["sha256"]:
                raise ValueError("original license file changed after preparation")
        report["runtime_match"] = current
    except (OSError, ValueError, KeyError, TypeError) as exc:
        report["verified"] = False
        report["revalidation_error"] = f"{type(exc).__name__}: {exc}"
    return report
