"""独立测试入口不能继承维护者凭据；这些检查不是产品验收。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from bench.blackbox import launcher


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "tools.json").write_text('{"path": ["/usr/bin"]}', encoding="utf-8")
    monkeypatch.setattr(launcher, "ROOT", tmp_path)
    return tmp_path


def test_no_inherited_credentials(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "OPENAI_API_KEY",
        "YESCC_MODEL_L3_API_KEY",
        "FCOMP_MODEL_API_KEY",
        "AWS_SECRET_ACCESS_KEY",
    ):
        monkeypatch.setenv(name, "dummy-parent-secret")
    env = launcher.environment()
    assert not any(value == "dummy-parent-secret" for value in env.values())
    assert env["HOME"] == str(workspace / "home")
    assert env["YESCC_CACHE_DIR"] == str(workspace / "state/yescc-cache")
    assert not any(name.endswith("API_KEY") for name in env)


def test_local_fake_uses_only_dummy_key(workspace: Path) -> None:
    (workspace / "model-test.json").write_text(
        json.dumps(
            {
                "l3": {
                    "base_url": "http://127.0.0.1:18888/v1",
                    "name": "fake",
                    "input_price_per_mtok": 0,
                }
            }
        ),
        encoding="utf-8",
    )
    env = launcher.environment()
    assert env["YESCC_MODEL_L3_API_KEY"] == "blackbox-dummy-key"
    assert env["YESCC_MODEL_L3_INPUT_PRICE_PER_MTOK"] == "0"
    assert env["YESCC_MODEL_L3_PROVIDER"] == "openai-compatible"


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/v1",
        "http://localhost.example.com/v1",
        "file:///tmp/test",
        "http://secret@localhost:1234/v1",
        "http://127.0.0.1/v1?key=secret",
    ],
)
def test_remote_and_credential_urls_are_rejected(workspace: Path, url: str) -> None:
    (workspace / "model-test.json").write_text(
        json.dumps({"l3": {"base_url": url}}), encoding="utf-8"
    )
    with pytest.raises(ValueError):
        launcher.environment()


def test_real_key_field_is_not_accepted(workspace: Path) -> None:
    (workspace / "model-test.json").write_text(
        json.dumps(
            {
                "l3": {
                    "base_url": "http://localhost:1234/v1",
                    "api_key": "dummy-forbidden-key",
                }
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        launcher.environment()
