"""二进制许可覆盖必须包含内置扩展和未知原生文件，不能仅统计 pip 许可证。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from bench import binary_licenses


def _isolate_inventory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(binary_licenses, "_installed_runtime", list)
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


def test_dynamic_dependency_requires_actual_bundled_library_and_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_inventory(monkeypatch)
    monkeypatch.setattr(binary_licenses, "_builtin_components", set)
    stdlib = tmp_path / "stdlib"
    stdlib.mkdir()
    (stdlib / "LICENSE.txt").write_text("fixture CPython notice\n")
    extension = stdlib / "_ssl.so"
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
