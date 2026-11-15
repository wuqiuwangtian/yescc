"""显式准备 Windows CPython 精确源码标签和版权原文；不把 SBOM NOASSERTION 当许可证。"""

from __future__ import annotations

import ast
import hashlib
import importlib
import json
import platform
import re
import sys
import urllib.parse
from pathlib import Path, PureWindowsPath
from typing import Any

from bench.python_provenance import _get, _sha

RAW = "https://raw.githubusercontent.com/"
API = "https://api.github.com/repos/"
_MODULE_DEPS = {
    "_bz2": ("bzip2",),
    "_ctypes": ("libffi",),
    "_decimal": ("mpdecimal",),
    "_hashlib": ("openssl",),
    "_ssl": ("openssl",),
    "_lzma": ("xz",),
    "pyexpat": ("expat",),
    "_zstd": ("zstd",),
    "_sqlite3": ("sqlite",),
    "unicodedata": ("Unicode",),
}
_DLL_DEPS = {
    "libcrypto-3.dll": "openssl",
    "libcrypto-3-x64.dll": "openssl",
    "libssl-3.dll": "openssl",
    "libssl-3-x64.dll": "openssl",
    "libffi-8.dll": "libffi",
}
_CORE_DEPS = ("zlib-ng", "hacl-star", "mimalloc", "Unicode", "cpython-incorporated")


def _normalized(data: bytes) -> str:
    return " ".join(data.decode("utf-8-sig").split())


def _openssl_observation(module: Any) -> dict[str, Any]:
    # CPython keeps the historical major/minor/fix/patch/status tuple; OpenSSL 3
    # puts its semantic patch in the fourth field. Read the loaded library text.
    # https://docs.python.org/3.14/library/ssl.html#ssl.OPENSSL_VERSION
    text = getattr(module, "OPENSSL_VERSION", None)
    legacy = getattr(module, "OPENSSL_VERSION_INFO", None)
    match = (
        re.fullmatch(
            r"OpenSSL ([0-9]+\.[0-9]+\.[0-9]+[a-z]?)"
            r"(?:[ \t]+[0-9]{1,2}[ \t]+[A-Za-z]{3}[ \t]+[0-9]{4})?",
            text,
        )
        if isinstance(text, str)
        else None
    )
    return {
        "version": match[1] if match else None,
        "version_string": text,
        "legacy_version_info": list(legacy) if isinstance(legacy, tuple) else legacy,
        "basis": "exact OpenSSL release token from ssl.OPENSSL_VERSION",
    }


def _descriptor() -> dict[str, Any] | None:
    if sys.platform != "win32":
        return None
    git = getattr(sys, "_git", ())
    try:
        openssl = _openssl_observation(importlib.import_module("ssl"))
    except ImportError as exc:
        openssl = {"version": None, "error": f"{type(exc).__name__}: {exc}"}
    observed = {"openssl": openssl["version"]}
    for component, module_name, attribute in (
        ("expat", "pyexpat", "version_info"),
        ("mpdecimal", "_decimal", "__libmpdec_version__"),
        ("zstd", "compression.zstd", "zstd_version"),
    ):
        try:
            value = getattr(importlib.import_module(module_name), attribute)
        except (ImportError, AttributeError):
            continue
        observed[component] = (
            ".".join(str(v) for v in value[:3]) if isinstance(value, tuple) else str(value)
        )
    return {
        "observed_versions": observed,
        "observed_details": {"openssl": openssl},
        "version": platform.python_version(),
        "runtime_version_string": sys.version,
        "prefix": str(Path(sys.base_prefix).resolve()),
        "git_short": git[2] if len(git) == 3 else "",
        "context": "actual Windows build interpreter",
    }


def _notice_components(node: dict[str, Any]) -> set[str]:
    return set(node.get("dependencies", [])) | {
        node.get("owning_component", "cpython"),
        "Microsoft-CRT",
    }


def _check_observed_versions(
    components: dict[str, Any], observed_versions: dict[str, Any], errors: list[str]
) -> set[str]:
    available = set(components)
    for name, observed in observed_versions.items():
        component = components.get(name)
        if component is None:
            continue
        component["observed_runtime_version"] = observed
        component["version_matches_runtime"] = observed == component["version"]
        if not component["version_matches_runtime"]:
            available.discard(name)
            errors.append(
                f"{name}: actual runtime version {observed} differs from declared source "
                f"{component['version']}"
            )
    return available


def prepare_windows_sources(
    cache_dir: Path,
    descriptor: dict[str, Any],
    installed_license: bytes,
    native_sources: list[dict[str, str]],
) -> Path:
    """可用于实际Windows解释器或明确标记的CI快照；只下载源码元数据和版权文件。"""
    key = hashlib.sha256(json.dumps(descriptor, sort_keys=True).encode()).hexdigest()[:20]
    directory = cache_dir.resolve() / key
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / "windows-provenance.json"
    license_path = directory / "installed-LICENSE.txt"
    license_path.write_bytes(installed_license)
    report: dict[str, Any] = {
        "schema_version": "1",
        "descriptor": descriptor,
        "source_binding_verified": False,
        "legal_conclusion": False,
        "binary_reproducibility_verified": False,
        "installed_license": {"file": str(license_path), "sha256": _sha(license_path)},
        "source_files": [],
        "components": {},
        "native_sources": [],
        "errors": [],
        "scope": "Only positively mapped Python-distribution native files: installed release "
        "tag, original distribution LICENSE, official source build mapping and native hashes. "
        "Unrecognized/foreign files remain the general collector's responsibility. This is "
        "not a reproducible source-to-binary proof.",
    }

    def fetch(relative: str, url: str, role: str = "metadata") -> dict[str, str]:
        target = directory / relative
        receipt = target.with_name(target.name + ".source.json")
        if target.is_file() and receipt.is_file():
            old = json.loads(receipt.read_text("utf-8"))
            valid = old.get("url") == url and old.get("sha256") == _sha(target)
        else:
            valid = False
        if not valid:
            content = _get(url)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        row = {"file": str(target), "url": url, "sha256": _sha(target), "role": role}
        receipt.write_text(json.dumps(row, indent=2) + "\n", encoding="utf-8")
        report["source_files"].append(row)
        return row

    def add_component(
        name: str, version: str, notices: list[dict[str, str]], basis: str, **extra: object
    ) -> None:
        report["components"][name] = {
            "name": name,
            "version": version,
            "notices": notices,
            "basis": basis,
            **extra,
        }

    def source_notices(name: str, location: str) -> list[dict[str, str]]:
        match = re.fullmatch(
            r"https://github.com/([^/]+/[^/]+)/archive/(?:refs/tags/)?(.+)\.(?:tar\.gz|zip)",
            location,
        )
        if not match:
            raise ValueError(f"unsupported exact source location for {name}")
        repository, reference = match.groups()
        listing = fetch(
            f"metadata/{name}-source-files.json",
            API + repository + "/contents/?ref=" + urllib.parse.quote(reference, safe=""),
        )
        notices = []
        for item in json.loads(Path(listing["file"]).read_text("utf-8")):
            if item["type"] == "file" and item["name"].upper().startswith(
                ("LICENSE", "LICENCE", "COPYING", "COPYRIGHT", "NOTICE")
            ):
                notices.append(
                    fetch(f"notices/{name}/{item['name']}", item["download_url"], "notice")
                )
        if not notices:
            raise ValueError(f"no original copyright/license files found in exact {name} source")
        return notices

    try:
        version = descriptor["version"]
        if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
            raise ValueError("unsupported CPython release version")
        tag = "v" + version
        base = RAW + "python/cpython/" + tag + "/"
        commit = fetch("metadata/cpython-commit.json", API + "python/cpython/commits/" + tag)
        commit_sha = json.loads(Path(commit["file"]).read_text("utf-8"))["sha"]
        git_short = descriptor.get("git_short", "")
        if not re.fullmatch(r"[0-9a-f]{7,40}", git_short) or not commit_sha.startswith(git_short):
            raise ValueError(
                "actual interpreter git revision does not match the official release tag"
            )
        report["cpython_commit"] = commit_sha
        # 用已与实际解释器git revision对上的不可变commit取原文，避免标签移动。
        base = RAW + "python/cpython/" + commit_sha + "/"
        files = {}
        for name in (
            "LICENSE",
            "PC/crtlicense.txt",
            "PCbuild/regen.targets",
            "PC/layout/main.py",
            "PCbuild/pcbuild.proj",
            "PCbuild/python.props",
            "PCbuild/pythoncore.vcxproj",
            "Doc/license.rst",
            "Misc/externals.spdx.json",
            "Misc/sbom.spdx.json",
        ):
            role = (
                "notice"
                if name in {"LICENSE", "PC/crtlicense.txt", "Doc/license.rst"}
                else "metadata"
            )
            files[name] = fetch("cpython/" + name, base + name, role)
        actual = _normalized(installed_license)
        psf = files["LICENSE"]
        crt = files["PC/crtlicense.txt"]
        if (
            _normalized(Path(psf["file"]).read_bytes()) not in actual
            or _normalized(Path(crt["file"]).read_bytes()) not in actual
        ):
            raise ValueError(
                "actual Windows distribution LICENSE lacks exact PSF/CRT original terms"
            )
        report["source_binding_verified"] = True
        add_component(
            "cpython", version, [psf], "actual release revision and installed LICENSE match"
        )
        add_component(
            "Microsoft-CRT",
            "CPython-distribution-conditions",
            [crt],
            "actual installed Windows Python LICENSE includes the original CRT conditions",
            restrictions_apply=True,
        )
        doc = files["Doc/license.rst"]
        doc_text = Path(doc["file"]).read_text("utf-8")
        add_component(
            "cpython-incorporated",
            version,
            [doc],
            "unmodified exact-release incorporated-software notices; documented incomplete list",
        )
        for name, heading in (
            ("mimalloc", "mimalloc\n--------"),
            ("Unicode", "Unicode Character Database\n--------------------------"),
        ):
            if heading in doc_text:
                add_component(
                    name,
                    "vendored-in-" + tag,
                    [doc],
                    "exact CPython license section: " + heading.splitlines()[0],
                )
        internal = json.loads(Path(files["Misc/sbom.spdx.json"]["file"]).read_text("utf-8"))
        external = json.loads(Path(files["Misc/externals.spdx.json"]["file"]).read_text("utf-8"))
        packages = {p["name"]: p for p in internal["packages"]}
        # PCbuild使用externals里的mpdecimal 4，而不是内树的2.5；不混用两个版本。
        packages.update({p["name"]: p for p in external["packages"]})
        proj = Path(files["PCbuild/pcbuild.proj"]["file"]).read_text("utf-8")
        known_modules = set()
        for value in re.findall(
            r'<(?:ExtensionModules|ExternalModules|TestModules)\s+Include="([^"]+)"', proj
        ):
            known_modules.update(v for v in value.split(";") if re.fullmatch(r"[A-Za-z0-9_]+", v))
        prefix = PureWindowsPath(descriptor["prefix"])
        selected = []
        needed = set(_CORE_DEPS)
        for source in native_sources:
            original = PureWindowsPath(source["source"])
            if not original.is_relative_to(prefix):
                continue
            filename = original.name.lower()
            dependencies: tuple[str, ...] = ()
            if original.suffix.lower() == ".pyd" and original.stem in known_modules:
                dependencies = _MODULE_DEPS.get(original.stem, ())
                owning_component = "cpython"
                role = "CPython standard extension declared by exact PCbuild project"
            elif filename in _DLL_DEPS:
                dependencies = (_DLL_DEPS[filename],)
                owning_component = _DLL_DEPS[filename]
                role = "external native library declared by exact Windows CPython build"
            elif filename in {"vcruntime140.dll", "vcruntime140_1.dll"}:
                owning_component = "Microsoft-CRT"
                role = "Microsoft runtime from actual Python distribution; CRT restrictions apply"
            elif filename in {
                "python3.dll",
                f"python{version.split('.')[0]}{version.split('.')[1]}.dll",
            }:
                dependencies = _CORE_DEPS
                owning_component = "cpython"
                role = "CPython core/stable ABI library with incorporated dependencies"
            else:
                continue
            node = {
                **source,
                "role": role,
                "owning_component": owning_component,
                "dependencies": list(dependencies),
            }
            selected.append(node)
            needed.update(dependencies)
        report["known_modules"] = sorted(known_modules)
        for name in sorted(needed - {"mimalloc", "Unicode", "cpython-incorporated"}):
            package = packages.get(name)
            if not package:
                report["errors"].append(f"no exact source package declared for {name}")
                continue
            try:
                if name == "expat":
                    notices = [
                        fetch("notices/expat/COPYING", base + "Modules/expat/COPYING", "notice")
                    ]
                else:
                    notices = source_notices(name, package["downloadLocation"])
                add_component(
                    name,
                    package["versionInfo"],
                    notices,
                    "exact CPython source/build metadata plus original upstream notices",
                    source_location=package["downloadLocation"],
                    sbom_license_concluded=package.get("licenseConcluded"),
                )
            except (OSError, ValueError, TypeError, KeyError) as exc:
                report["errors"].append(f"{name}: {type(exc).__name__}: {exc}")
        available = _check_observed_versions(
            report["components"], descriptor.get("observed_versions", {}), report["errors"]
        )
        for node in selected:
            missing = sorted(_notice_components(node) - available)
            node["covered"] = not missing
            node["missing_components"] = missing
        report["native_sources"] = selected
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        report["errors"].append(f"{type(exc).__name__}: {exc}")
    report["recognized_python_subset_complete"] = (
        report["source_binding_verified"]
        and bool(report["native_sources"])
        and all(row["covered"] for row in report["native_sources"])
    )
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output


def ensure_windows_provenance(cache_dir: Path, workdir: Path) -> Path | None:
    """Builder显式调用；只下载源码元数据/版权原文，非Windows不执行。"""
    descriptor = _descriptor()
    if descriptor is None:
        return None
    from bench.binary_licenses import _toc_entries

    entries = {}
    for toc in workdir.rglob("COLLECT-*.toc"):
        for destination, raw_source, kind in _toc_entries(ast.literal_eval(toc.read_text("utf-8"))):
            source = Path(raw_source)
            if kind in {"BINARY", "EXTENSION"} and source.is_file():
                entries[str(source.resolve())] = {
                    "source": str(source.resolve()),
                    "sha256": _sha(source),
                    "destination": destination,
                }
    license_file = Path(descriptor["prefix"]) / "LICENSE.txt"
    if not license_file.is_file():
        license_file = Path(descriptor["prefix"]) / "Lib/LICENSE.txt"
    return prepare_windows_sources(
        cache_dir, descriptor, license_file.read_bytes(), list(entries.values())
    )


def read_windows_provenance(path: Path) -> dict[str, Any]:
    """Collector只读复核；不下载、不执行被审计的DLL。"""
    report = json.loads(path.read_text("utf-8"))
    try:
        actual = _descriptor()
        if actual is None or any(
            actual[key] != report["descriptor"][key] for key in ("version", "prefix", "git_short")
        ):
            raise ValueError("prepared Windows provenance belongs to another interpreter")
        for source in [report["installed_license"], *report["source_files"]]:
            file = Path(source["file"]).resolve()
            if not file.is_relative_to(path.parent.resolve()) or _sha(file) != source["sha256"]:
                raise ValueError("prepared Windows original source/notice changed")
        original = Path(actual["prefix"]) / "LICENSE.txt"
        if not original.is_file():
            original = Path(actual["prefix"]) / "Lib/LICENSE.txt"
        if _sha(original) != report["installed_license"]["sha256"]:
            raise ValueError("actual installed Windows LICENSE changed")
        for row in report["native_sources"]:
            native = Path(row["source"]).resolve()
            if (
                not native.is_relative_to(Path(actual["prefix"]).resolve())
                or _sha(native) != row["sha256"]
            ):
                row["covered"] = False
                row["revalidation_error"] = "actual native source bytes changed"
    except (OSError, ValueError, KeyError, TypeError) as exc:
        report["source_binding_verified"] = False
        report["revalidation_error"] = f"{type(exc).__name__}: {exc}"
        for row in report.get("native_sources", []):
            row["covered"] = False
    return report
