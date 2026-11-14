"""Freeze integrity and public-CLI feasibility, without real model requests."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from bench.creative_reasoning import PROTOCOL, ROOT, main, preflight

from tests.conftest import FakeModelServer
from yescc.cli import main as cli
from yescc.model.config import DEFAULT_BASE_URLS


def test_preflight_uses_no_network_configuration_or_candidate_execution() -> None:
    with (
        patch("httpx.Client.send", side_effect=AssertionError("network forbidden")),
        patch("yescc.model.config._env", side_effect=AssertionError("config forbidden")),
        patch("subprocess.Popen", side_effect=AssertionError("execution forbidden")),
    ):
        report = preflight()
    assert report["discovery_tasks"] == 42
    assert report["discovery_reservation_usd"] == pytest.approx(0.21)
    assert report["maximum_planned_reservation_usd"] == pytest.approx(0.29)
    assert report["real_model_calls"] == report["candidate_executions"] == 0
    assert report["sealed_inputs_opened"] is False
    plans = report["plans"]
    for case in {p["case_id"] for p in plans}:
        first = [p for p in plans if p["case_id"] == case and p["repeat"] == 1]
        assert len(first) == 3
        assert len({p["first_messages_sha256"] for p in first}) == 1
    assert all(p["first_request_reservation_usd"] <= 0.005 for p in plans)
    low = [p for p in plans if p["profile"] == "low"]
    tokens = [p for p in plans if p["profile"] == "tokens"]
    assert all("temperature" not in p["first_request_parameters"] for p in low)
    # 4096 is requested, but the real budget makes every first request smaller.
    assert all(1024 < p["first_request_parameters"]["max_tokens"] < 4096 for p in low + tokens)


@pytest.fixture
def frozen_copy(tmp_path: Path) -> Path:
    protocol = json.loads((ROOT / PROTOCOL).read_text("utf-8"))
    for relative in [PROTOCOL, *protocol["frozen_files"]]:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    return tmp_path


@pytest.mark.parametrize("relative", [
    "bench/creative_reasoning_inputs.json", "src/yescc/model/prompts.py",
])
def test_changed_frozen_bytes_stop_preflight(frozen_copy: Path, relative: str) -> None:
    with (frozen_copy / relative).open("ab") as stream:
        stream.write(b"\n")
    with pytest.raises(ValueError, match="frozen file changed"):
        preflight(frozen_copy)


def test_unbalanced_task_plan_is_rejected(frozen_copy: Path) -> None:
    path = frozen_copy / PROTOCOL
    protocol = json.loads(path.read_text("utf-8"))
    protocol["tasks"][0]["profile"] = "missing-profile"
    path.write_text(json.dumps(protocol), encoding="utf-8")
    with pytest.raises(ValueError, match="unbalanced"):
        preflight(frozen_copy)


def test_output_never_replaces_existing_file_or_symlink(tmp_path: Path) -> None:
    target = tmp_path / "existing.json"
    target.write_bytes(b"original")
    with pytest.raises(FileExistsError):
        main(["--output", str(target)])
    assert target.read_bytes() == b"original"
    link = tmp_path / "alias.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation unavailable")
    with pytest.raises(FileExistsError):
        main(["--output", str(link)])
    assert target.read_bytes() == b"original"


@pytest.mark.e2e
@pytest.mark.parametrize("profile_id", ["default", "tokens", "low"])
def test_planned_settings_match_actual_public_cli_request(
    profile_id: str, tmp_path: Path, fake_model: FakeModelServer,
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    # --provider intentionally selects its registered URL over BASE_URL env.
    # Override that registry for the local server and reject any external send.
    monkeypatch.setitem(DEFAULT_BASE_URLS, "deepseek", fake_model.base_url)
    for key in os.environ:
        if key.upper().endswith("_PROXY"):
            monkeypatch.delenv(key)
    import httpx

    original_send = httpx.Client.send

    def local_only(client, request, **kwargs):
        assert request.url.host == "127.0.0.1", "external request forbidden"
        assert request.url.port == httpx.URL(fake_model.base_url).port
        return original_send(client, request, **kwargs)

    monkeypatch.setattr(httpx.Client, "send", local_only)
    for key, value in {
        "YESCC_MODEL_L3_PROVIDER": "deepseek",
        "YESCC_MODEL_L3_INPUT_PRICE_PER_MTOK": "0.3",
        "YESCC_MODEL_L3_OUTPUT_PRICE_PER_MTOK": "1.2",
        "YESCC_MODEL_L3_TIMEOUT_S": "45",
        "YESCC_RUN_TIMEOUT_S": "3",
    }.items():
        monkeypatch.setenv(key, value)
    cases = json.loads((ROOT / "bench/creative_reasoning_inputs.json").read_text("utf-8"))
    case = next(c for c in cases["cases"] if c["id"] == "effort-d03")
    plan = next(p for p in preflight()["plans"] if p["profile"] == profile_id
                and p["case_id"] == case["id"] and p["repeat"] == 1)
    source = tmp_path / "input.txt"
    original = case["input"].encode("utf-8")
    source.write_bytes(original)
    fake_model.responses.append({
        "source": '#include <stdio.h>\nint main(void) { puts("MOCK"); return 0; }\n',
    })
    assert cli(plan["cli_arguments"]) == 0
    output = capfd.readouterr()
    assert output.out.strip() == "MOCK"
    assert source.read_bytes() == original
    assert len(fake_model.requests) == 1
    request = fake_model.requests[0]
    for name, value in plan["first_request_parameters"].items():
        assert request[name] == value
    if profile_id == "low":
        assert "temperature" not in request
    else:
        assert "reasoning_effort" not in request
    report = json.loads((tmp_path / ".yescc/input/report.json").read_text("utf-8"))
    assert report["result"]["fallback"]["kind"] == "model_creative"
    assert report["budget"]["attempts"] == report["model"]["calls"] == 1
    assert "undefined_behavior_check" not in report
    (tmp_path / "request.json").write_text(json.dumps(request), encoding="utf-8")
    (tmp_path / "stdout.txt").write_text(output.out, encoding="utf-8")
    (tmp_path / "stderr.txt").write_text(output.err, encoding="utf-8")
