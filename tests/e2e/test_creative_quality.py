"""创意路径的真实构建、有限重试、输出检查和控制参数。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import FakeModelServer
from yescc.cli import parse
from yescc.fallback.quality import creative_source_issues

pytestmark = pytest.mark.e2e

GOOD = '#include <stdio.h>\nint main(void) { puts("好的作品"); return 0; }\n'
TEXT = "一只会梦见彩虹的时钟"


def invoke(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    source = tmp_path / "idea.txt"
    source.write_text(TEXT, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "yescc", *args, str(source), "--play"],
        cwd=tmp_path, capture_output=True, timeout=60, check=False,
    )
    assert source.read_text("utf-8") == TEXT
    return result


def report(tmp_path: Path) -> dict:
    return json.loads((tmp_path / ".yescc/idea/report.json").read_text("utf-8"))


@pytest.mark.parametrize("bad,reason", [
    ('int main(void) { return 0; }', "stdout_empty"),
    ('#include <stdio.h>\nint main(void) { putchar(0xB2); return 0; }', "stdout_invalid_utf8"),
    ('#include <stdio.h>\nint main(void) { puts("REJECTED"); return 5; }', "exit_nonzero"),
    (
        '#include <stdio.h>\nint main(void) { for(int i=0;i<70000;i++) putchar(65); }',
        "output_limit",
    ),
])
def test_bad_creative_run_is_hidden_and_retried(
    tmp_path: Path, fake_model: FakeModelServer, bad: str, reason: str,
) -> None:
    fake_model.responses.extend([{"source": bad}, {"source": GOOD}])
    result = invoke(tmp_path, "run")
    assert result.returncode == 0, result.stderr
    assert result.stdout == "好的作品\n".encode()
    saved = report(tmp_path)
    assert saved["model"]["calls"] == saved["budget"]["attempts"] == 2
    attempts = [a for a in saved["attempts"] if a["kind"] == "model"]
    assert reason in attempts[0]["validation"]["reasons"]
    assert attempts[0]["validation"]["passed"] is False
    assert attempts[1]["validation"]["passed"] is True
    assert saved["result"]["generated"]["validation"]["passed"] is True
    assert saved["result"]["fallback"]["kind"] == "model_creative"


def test_creative_timeout_can_use_remaining_attempt(
    tmp_path: Path, fake_model: FakeModelServer, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("YESCC_RUN_TIMEOUT_S", "0.3")
    fake_model.responses.extend([{"source": "int main(void) { for(;;){} }"}, {"source": GOOD}])
    result = invoke(tmp_path, "run")
    assert result.returncode == 0 and result.stdout == "好的作品\n".encode()
    assert "timeout" in report(tmp_path)["attempts"][0]["validation"]["reasons"]


def test_creative_bad_cache_is_discarded_and_limit_is_not_increased(
    tmp_path: Path, fake_model: FakeModelServer,
) -> None:
    fake_model.responses.extend([
        {"source": '#include <stdio.h>\nint main(void) { putchar(0xB2); }'},
        {"source": GOOD},
    ])
    first = invoke(tmp_path, "run", "--max-rounds", "1")
    assert first.returncode == 0 and first.stdout == (TEXT + "\n").encode()
    assert report(tmp_path)["result"]["fallback"]["kind"] == "print_input"
    assert len(fake_model.requests) == 1
    second = invoke(tmp_path, "run", "--max-rounds", "1")
    assert second.stdout == "好的作品\n".encode() and len(fake_model.requests) == 2
    assert report(tmp_path)["model"]["cache_hits"] == 0


def test_build_does_not_execute_creative_program(
    tmp_path: Path, fake_model: FakeModelServer,
) -> None:
    marker = tmp_path / "must-not-be-created"
    # Deliberate controlled side effect would be visible even from a temporary cwd.
    source = '#include <stdio.h>\nint main(void) { FILE *f=fopen(' + json.dumps(
        marker.as_posix()
    ) + ',"w"); if(f) fclose(f); puts("executed"); return 0; }'
    fake_model.responses.append({"source": source})
    result = invoke(tmp_path, "--json")
    assert result.returncode == 0 and not marker.exists()
    saved = json.loads(result.stdout)
    assert saved["result"]["generated"]["validation"]["performed"] is False
    assert saved["limits"]["sandboxed"] is False
    assert "-lm" in saved["attempts"][0]["command"]


def test_mixed_streams_rejected_without_claiming_runtime_validation(
    tmp_path: Path, fake_model: FakeModelServer,
) -> None:
    bad = (
        '#include <stdio.h>\n#include <wchar.h>\n'
        'int main(void){ puts("title"); wprintf(L"lost"); }'
    )
    fake_model.responses.extend([{"source": bad}, {"source": GOOD}])
    result = invoke(tmp_path, "run")
    assert result.stdout == "好的作品\n".encode()
    first = report(tmp_path)["attempts"][0]
    assert first["exit_code"] == 0
    assert first["source_checks"]["reasons"] == ["mixed_stdout_orientation"]
    assert "validation" not in first
    assert not creative_source_issues('/* wprintf( */ puts("wprintf(");')


def test_invalid_json_retries_instead_of_counting_fallback_as_model(
    tmp_path: Path, fake_model: FakeModelServer,
) -> None:
    fake_model.responses.extend(["not JSON", {"source": GOOD}])
    result = invoke(tmp_path, "run")
    assert result.stdout == "好的作品\n".encode()
    saved = report(tmp_path)
    assert saved["model"]["calls"] == 2 and saved["budget"]["attempts"] == 2
    assert len(saved["attempts"]) == 1


def test_cancel_code_is_not_retried(tmp_path: Path, fake_model: FakeModelServer) -> None:
    fake_model.responses.extend([{"source": "int main(void) { return 130; }"}, {"source": GOOD}])
    result = invoke(tmp_path, "run")
    assert result.returncode == 130 and len(fake_model.requests) == 1


def test_cli_controls_reach_deepseek_request_and_report(
    tmp_path: Path, fake_model: FakeModelServer, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("YESCC_MODEL_L3_PROVIDER", "deepseek")
    fake_model.responses.append({"source": GOOD})
    result = invoke(tmp_path, "run", "--reasoning-effort", "low", "--max-output-tokens", "2048")
    assert result.returncode == 0, result.stderr
    request = fake_model.requests[0]
    assert request["thinking"] == {"type": "enabled"}
    assert request["reasoning_effort"] == "low" and request["max_tokens"] == 2048
    assert "temperature" not in request
    assert report(tmp_path)["model"]["request_settings"]["reasoning_effort"] == "low"


def test_bad_provider_controls_are_json_error_before_request(
    tmp_path: Path, fake_model: FakeModelServer,
) -> None:
    result = invoke(tmp_path, "--json", "--thinking", "enabled")
    assert result.returncode == 100 and not fake_model.requests
    assert json.loads(result.stdout)["report_kind"] == "command_error"


def test_native_success_ignores_invalid_model_environment(
    tmp_path: Path, fake_model: FakeModelServer, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("YESCC_MODEL_L3_MAX_OUTPUT_TOKENS", "invalid")
    source = tmp_path / "native.c"
    source.write_text(GOOD, encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, "-m", "yescc", "run", str(source)], cwd=tmp_path,
        capture_output=True, timeout=60, check=False,
    )
    assert completed.returncode == 0 and completed.stdout == "好的作品\n".encode()
    assert not fake_model.requests


@pytest.mark.parametrize("flag,value", [
    ("--reasoning-effort", "bogus"), ("--thinking", "maybe"),
    ("--max-output-tokens", "0"), ("--max-output-tokens", "16385"),
])
def test_bad_cli_values_have_single_json_error(tmp_path: Path, flag: str, value: str) -> None:
    completed = invoke(tmp_path, "--json", flag, value)
    assert completed.returncode == 100
    assert json.loads(completed.stdout)["report_kind"] == "command_error"


def test_control_value_is_not_json_switch() -> None:
    from yescc.cli import _requests_json

    for flag in ("--thinking", "--reasoning-effort", "--max-output-tokens"):
        assert not _requests_json(["idea.txt", flag, "--json"])
    assert parse(["idea.txt", "--thinking", "auto"]).invocation.thinking == "auto"


def test_later_tier_config_error_is_caught_before_paid_l2(
    tmp_path: Path, fake_model: FakeModelServer, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("YESCC_MODEL_L3_PROVIDER", "openai")
    monkeypatch.setenv("YESCC_MODEL_L3_THINKING", "enabled")
    source = tmp_path / "bad.c"
    source.write_text('#include <stdio.h>\nint main(void) { missing("hi"); }')
    fake_model.responses.append({"diff": ""})
    completed = subprocess.run(
        [sys.executable, "-m", "yescc", str(source), "--json"],
        cwd=tmp_path, capture_output=True, timeout=60, check=False,
    )
    assert completed.returncode == 100 and not fake_model.requests
    assert json.loads(completed.stdout)["report_kind"] == "command_error"


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_stream_guard_respects_c_line_splicing(newline: str) -> None:
    source = 'puts("works"); // comment continues \\' + newline + 'wprintf(L"comment");'
    assert not creative_source_issues(source)
    assert creative_source_issues('puts("works"); wp\\' + newline + 'rintf(L"actual");')
