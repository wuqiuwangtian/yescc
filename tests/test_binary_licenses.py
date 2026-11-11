"""二进制许可覆盖必须包含内置扩展和未知原生文件，不能仅统计 pip 许可证。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from bench import binary_licenses


def _isolate_inventory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(binary_licenses, "_installed_runtime", list)
    monkeypatch.setattr(binary_licenses, "_installed_owners", dict)
    monkeypatch.setattr(binary_licenses, "_system_notices", lambda _source: (None, []))
    monkeypatch.setattr(
        binary_licenses.metadata,
        "distribution",
        lambda _name: SimpleNamespace(files=[], version="fixture"),
    )


def test_builtin_third_party_code_cannot_be_covered_by_cpython_license_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_inventory(monkeypatch)
    monkeypatch.setattr(binary_licenses, "_builtin_components", lambda: {"OpenSSL", "zlib"})
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    source = tmp_path / "libpython3.11.so"
    source.write_bytes(b"fixture: statically linked TLS/compression code")
    (bundle / source.name).write_bytes(source.read_bytes())
    work = tmp_path / "work"
    work.mkdir()
    (work / "COLLECT-00.toc").write_text(repr(([source.name, str(source), "BINARY"],)))
    report = binary_licenses.collect(bundle, work, tmp_path / "audit.json")
    assert report["coverage_complete"] is False
    assert report["legal_conclusion"] is False
    missing = {row["component"] for row in report["unresolved"]}
    assert {"OpenSSL", "zlib"} <= missing
    assert report["native_dependency_scope"]["builtin_components"] == ["OpenSSL", "zlib"]
    assert report["bundle_files"][0]["license_component"] == "cpython"


def test_unknown_native_file_stays_unresolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_inventory(monkeypatch)
    monkeypatch.setattr(binary_licenses, "_builtin_components", set)
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "unknown.dll").write_bytes(b"unidentified binary fixture")
    work = tmp_path / "work"
    work.mkdir()
    (work / "Analysis-00.toc").write_text("()")
    report = binary_licenses.collect(bundle, work, tmp_path / "audit.json")
    assert report["coverage_complete"] is False
    assert any(row["component"] == "unknown.dll" for row in report["unresolved"])
    assert report["bundle_files"][0]["sha256"]


@pytest.mark.parametrize("builtin", [False, True])
def test_dynamic_dependency_requires_actual_bundled_library_and_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, builtin: bool
) -> None:
    _isolate_inventory(monkeypatch)
    monkeypatch.setattr(
        binary_licenses, "_builtin_components", lambda: {"OpenSSL"} if builtin else set()
    )
    stdlib = tmp_path / "stdlib"
    stdlib.mkdir()
    (stdlib / "LICENSE.txt").write_text("fixture CPython notice\n")
    extension = tmp_path / "libpython3.11.so" if builtin else stdlib / "_ssl.so"
    extension.write_bytes(b"fixture dynamically linked TLS extension")
    crypto = tmp_path / "libcrypto.so.3"
    crypto.write_bytes(b"fixture external TLS library")
    notice = tmp_path / "COPYING.openssl"
    notice.write_text("fixture OpenSSL original notice\n")
    monkeypatch.setattr(binary_licenses.sysconfig, "get_path", lambda _name: str(stdlib))
    monkeypatch.setattr(binary_licenses, "_needed_libraries", lambda _source: {crypto.name})
    monkeypatch.setattr(
        binary_licenses,
        "_system_notices",
        lambda source: ("fixture:openssl", [notice]) if source == crypto else (None, []),
    )
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    for source in (extension, crypto):
        (bundle / source.name).write_bytes(source.read_bytes())
    work = tmp_path / "work"
    work.mkdir()
    entries = [(extension.name, str(extension), "EXTENSION"), (crypto.name, str(crypto), "BINARY")]
    (work / "COLLECT-00.toc").write_text(repr((entries,)))
    report = binary_licenses.collect(bundle, work, tmp_path / "with-library.json")
    assert not any(row["component"] == "OpenSSL" for row in report["unresolved"])
    (bundle / crypto.name).unlink()
    report = binary_licenses.collect(bundle, work, tmp_path / "without-library.json")
    assert any(row["component"] == "OpenSSL" for row in report["unresolved"])


def test_python_provenance_requires_exact_core_bytes(tmp_path: Path) -> None:
    from bench.python_provenance import _verify_runtime

    prefix = tmp_path / "installed"
    payload = tmp_path / "archive"
    paths = ["bin/python3.11", "lib/libpython3.11.so.1.0"]
    for name in paths:
        for root in (prefix, payload / "install"):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"matching original Python fixture")
    data = {
        "python_exe": "install/" + paths[0],
        "build_info": {"core": {"shared_lib": "install/" + paths[1]}, "extensions": {}},
    }
    assert _verify_runtime(prefix, payload, data, None)["verified"] is True
    (prefix / paths[1]).write_bytes(b"different build of the same Python version")
    report = _verify_runtime(prefix, payload, data, None)
    assert report["verified"] is False
    assert any(not row["matched"] for row in report["binaries"])


def test_python_provenance_rejects_metadata_path_escape(tmp_path: Path) -> None:
    from bench.python_provenance import _archive_path

    with pytest.raises(ValueError, match="unsafe path"):
        _archive_path(tmp_path, "../outside/LICENSE.txt")


def test_unknown_pure_python_owner_blocks_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_inventory(monkeypatch)
    monkeypatch.setattr(binary_licenses, "_builtin_components", set)
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    source = tmp_path / "third_party_module.py"
    source.write_text("def vendor_function(): return 'unclassified'\n")
    (work / "PYZ-00.toc").write_text(repr((["unknown_vendor", str(source), "PYMODULE"],)))
    report = binary_licenses.collect(bundle, work, tmp_path / "audit.json")
    assert report["coverage_complete"] is False
    assert any(row["component"] == "unknown_vendor" for row in report["unresolved"])
    assert report["pure_python_modules"][0]["origin"]["source_sha256"]


def test_unverified_prepared_provenance_does_not_trigger_network_or_claim_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json
    import urllib.request

    _isolate_inventory(monkeypatch)
    monkeypatch.setattr(binary_licenses, "_builtin_components", lambda: {"OpenSSL"})

    def unexpected_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("ordinary collector must not prepare/download provenance")

    monkeypatch.setattr(urllib.request, "urlopen", unexpected_network)
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    prepared = tmp_path / "prepared.json"
    prepared.write_text(json.dumps({"verified": False, "error": "stripped bytes differ"}))
    report = binary_licenses.collect(bundle, work, tmp_path / "audit.json", provenance=prepared)
    assert report["coverage_complete"] is False
    assert any(row["component"] == "OpenSSL" for row in report["unresolved"])
    assert report["python_provenance"]["verified"] is False


def test_bundled_extra_distribution_keeps_legacy_license(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_inventory(monkeypatch)
    monkeypatch.setattr(binary_licenses, "_builtin_components", set)
    source = tmp_path / "legacy_module.py"
    source.write_text("EXTRA_OWNER = 'included by freezing, not runtime requirements'\n")
    original = tmp_path / "legacy-1.dist-info/LICENSE"
    original.parent.mkdir()
    original.write_text("Original legacy distribution notice\n")
    dist = SimpleNamespace(
        metadata={"Name": "legacy"},
        version="1",
        files=[original.relative_to(tmp_path)],
        locate_file=lambda item: tmp_path / item,
    )

    # email.Message 与dict的get_all接口不同，给此fixture仅补本测试所需的metadata行为。
    class Metadata(dict):
        def get_all(self, key: str) -> None:
            return None

    dist.metadata = Metadata(dist.metadata)
    monkeypatch.setattr(binary_licenses, "_installed_owners", lambda: {source: dist})
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    (work / "PYZ-00.toc").write_text(repr((["legacy_module", str(source), "PYMODULE"],)))
    report = binary_licenses.collect(bundle, work, tmp_path / "audit.json")
    component = next(row for row in report["components"] if row["name"] == "legacy")
    assert component["notices"]
    assert (bundle / component["notices"][0]).read_bytes() == original.read_bytes()
    assert report["pure_python_modules"][0]["license_component"] == "legacy"
    assert not any(row["component"] == "legacy" for row in report["unresolved"])


def test_windows_prepare_skip_does_not_create_cache_or_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bench import windows_provenance

    monkeypatch.setattr(windows_provenance, "_descriptor", lambda: None)

    def unexpected(*args: object) -> None:
        raise AssertionError("non-Windows build must not download Windows copyright sources")

    monkeypatch.setattr(windows_provenance, "_get", unexpected)
    cache = tmp_path / "unused-cache"
    assert windows_provenance.ensure_windows_provenance(cache, tmp_path) is None
    assert not cache.exists()


def test_windows_release_revision_mismatch_never_claims_bound_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from bench import windows_provenance

    monkeypatch.setattr(windows_provenance, "_get", lambda _url: b'{"sha":"abcdef0123456789"}')
    descriptor = {"version": "3.14.10", "prefix": "C:\\Python", "git_short": "1234567"}
    path = windows_provenance.prepare_windows_sources(tmp_path, descriptor, b"actual LICENSE", [])
    report = json.loads(path.read_text())
    assert report["source_binding_verified"] is False
    assert report["legal_conclusion"] is False
    assert any("revision" in reason for reason in report["errors"])


@pytest.mark.parametrize("covered", [True, False])
def test_windows_cpython_native_keeps_third_party_dependency_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, covered: bool
) -> None:
    from bench import windows_provenance

    _isolate_inventory(monkeypatch)
    monkeypatch.setattr(binary_licenses, "_builtin_components", set)
    original = tmp_path / "_ssl.pyd"
    original.write_bytes(b"native fixture with a TLS library dependency")
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / original.name).write_bytes(original.read_bytes())
    work = tmp_path / "work"
    work.mkdir()
    (work / "COLLECT-00.toc").write_text(repr(([original.name, str(original), "EXTENSION"],)))
    prepared_file = tmp_path / "prepared-windows.json"
    prepared_file.write_text("{}")
    components = {}
    for name in ("cpython", "Microsoft-CRT", "openssl"):
        notice = tmp_path / (name + "-LICENSE")
        notice.write_text("Original notice for " + name + "\n")
        components[name] = {
            "name": name,
            "version": "fixture",
            "basis": "explicit fixture evidence",
            "notices": [{"file": str(notice)}],
        }
    prepared = {
        "source_binding_verified": True,
        "installed_license": {"file": components["cpython"]["notices"][0]["file"]},
        "source_files": [],
        "components": components,
        "native_sources": [
            {
                "source": str(original),
                "role": "CPython standard extension",
                "dependencies": ["openssl"],
                "covered": covered,
                "missing_components": [] if covered else ["openssl"],
            }
        ],
    }
    monkeypatch.setattr(windows_provenance, "read_windows_provenance", lambda _path: prepared)
    report = binary_licenses.collect(
        bundle, work, tmp_path / "audit.json", windows_provenance=prepared_file
    )
    native = report["bundle_files"][0]
    if covered:
        assert "openssl" in native["windows_provenance"]["components"]
        dependency = next(
            r
            for r in report["native_dependency_scope"]["dynamic_dependency_review"]
            if r["component"] == "OpenSSL"
        )
        assert dependency["covered"] is True
        assert dependency["windows_source_notices"]
    else:
        assert report["coverage_complete"] is False
        assert any(r["component"] == "_ssl.pyd" for r in report["unresolved"])
        assert "own code only" in native["license_component"]
