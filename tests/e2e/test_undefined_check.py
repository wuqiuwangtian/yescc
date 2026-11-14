"""可选 UB 检查的真实工具链回归；只使用本地假模型。"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import FakeModelServer
from yescc import pipeline
from yescc.adapters import BuildResult
from yescc.cli import main
from yescc.fix.engine import TempBuild
from yescc.pipeline import Invocation, execute

pytestmark = pytest.mark.e2e

TEXT = "一只会梦见彩虹的时钟"
GOOD = '#include <stdio.h>\nint main(void) { puts("GOOD"); return 0; }\n'
GOOD_OUTPUT = ("GOOD" + os.linesep).encode()
OVERFLOW = (
    '#include <stdio.h>\n#include <limits.h>\n'
    'int main(void) { printf("REJECTED\\n"); fflush(stdout); '
    'volatile int upper = INT_MAX; volatile int one = 1; '
    'volatile int sum = upper + one; (void)sum; return 0; }\n'
)


def _input(tmp_path: Path, text: str = TEXT, name: str = "idea.txt") -> Path:
    source = tmp_path / name
    source.write_text(text, encoding="utf-8")
    return source


def _run(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    source = _input(tmp_path)
    result = subprocess.run(
        [sys.executable, "-m", "yescc", "run", str(source), "--play", *args],
        cwd=tmp_path, capture_output=True, timeout=60, check=False,
    )
    assert source.read_text("utf-8") == TEXT
    return result


def _report(tmp_path: Path, stem: str = "idea") -> dict:
    return json.loads((tmp_path / ".yescc" / stem / "report.json").read_text("utf-8"))


def _model_attempts(saved: dict) -> list[dict]:
    return [attempt for attempt in saved["attempts"] if attempt["kind"] == "model"]


def _assert_checked(saved: dict) -> str:
    check = saved["undefined_behavior_check"]
    assert check["requested"] is True
    assert check["performed"] is True
    assert check["scope"] == "model_creative"
    assert check["mode"] in {"runtime", "trap"}
    generated = saved["result"]["generated"]["undefined_behavior_check"]
    assert generated["performed"] is True
    assert generated["mode"] == check["mode"]
    assert "-fsanitize=undefined" in generated["flags"]
    assert generated["ub_detected"] is None
    return check["mode"]


def _must_not_probe(*args: object, **kwargs: object) -> None:
    pytest.fail("当前路径不得探测或启用可选 UB 检查")


def test_default_creative_keeps_unchecked_behavior_and_source(
    tmp_path: Path, fake_model: FakeModelServer, monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(pipeline, "select_undefined_profile", _must_not_probe)
    source = _input(tmp_path)
    fake_model.responses.append({"source": OVERFLOW})
    assert execute([], Invocation(mode="run", source=source, play=True)) == 0
    output = capfd.readouterr()
    assert output.out == "REJECTED" + os.linesep
    saved = _report(tmp_path)
    assert "undefined_behavior_check" not in saved
    assert len(fake_model.requests) == 1
    assert saved["budget"]["attempts"] == 1
    attempt = _model_attempts(saved)[0]
    assert not any("sanitize" in flag for flag in attempt["command"])
    assert "undefined_behavior_check" not in attempt
    generated = saved["result"]["generated"]
    assert "undefined_behavior_check" not in generated
    assert (tmp_path / generated["source"]).read_text("utf-8") == OVERFLOW
    assert source.read_text("utf-8") == TEXT


@pytest.mark.parametrize("extra", [(), ("--unsafe-run",)], ids=["sandbox", "unsafe-run"])
def test_overflow_output_is_hidden_and_next_candidate_is_actually_checked(
    tmp_path: Path, fake_model: FakeModelServer, extra: tuple[str, ...],
) -> None:
    fake_model.responses.extend([{"source": OVERFLOW}, {"source": GOOD}])
    result = _run(tmp_path, "--check-undefined", *extra)
    assert result.returncode == 0, result.stderr
    assert result.stdout == GOOD_OUTPUT
    assert b"REJECTED" not in result.stderr
    saved = _report(tmp_path)
    mode = _assert_checked(saved)
    assert saved["model"]["calls"] == saved["budget"]["attempts"] == 2
    assert len(fake_model.requests) == 2
    first, second = _model_attempts(saved)
    assert first["validation"]["passed"] is False
    assert second["validation"]["passed"] is True
    for attempt in (first, second):
        check = attempt["undefined_behavior_check"]
        assert check["performed"] is True and check["mode"] == mode
        assert check["ub_detected"] is None
        assert all(flag in attempt["command"] for flag in check["flags"])
    if mode == "runtime":
        first_check = first["undefined_behavior_check"]
        assert first_check["diagnostic_observed"] is True
        assert "runtime error:" in first_check["stderr"]
    generated = saved["result"]["generated"]
    assert (tmp_path / generated["source"]).read_text("utf-8") == GOOD
    assert saved["result"]["fallback"]["kind"] == "model_creative"
    evidence_dir = os.environ.get("YESCC_TEST_EVIDENCE_DIR")
    if evidence_dir:
        directory = Path(evidence_dir)
        directory.mkdir(parents=True, exist_ok=True)
        name = "unsafe" if extra else "sandbox"
        (directory / f"undefined-{name}.json").write_text(
            json.dumps({
                "platform": sys.platform, "python": sys.version,
                "stdout_base64": base64.b64encode(result.stdout).decode("ascii"),
                "stderr_base64": base64.b64encode(result.stderr).decode("ascii"),
                "exit_code": result.returncode, "report": saved,
            }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )


def test_failed_checked_candidate_exhausts_original_limit_and_is_not_cached(
    tmp_path: Path, fake_model: FakeModelServer,
) -> None:
    fake_model.responses.extend([{"source": OVERFLOW}, {"source": GOOD}])
    first = _run(tmp_path, "--check-undefined", "--max-rounds", "1")
    assert first.returncode == 0, first.stderr
    assert first.stdout == (TEXT + os.linesep).encode()
    saved = _report(tmp_path)
    assert saved["budget"]["attempts"] == saved["model"]["calls"] == 1
    assert saved["budget"]["committed_usd"] <= saved["budget"]["limit_usd"]
    assert saved["result"]["fallback"]["kind"] == "print_input"
    assert saved["undefined_behavior_check"]["performed"] is True
    assert len(fake_model.requests) == 1
    second = _run(tmp_path, "--check-undefined", "--max-rounds", "1")
    assert second.returncode == 0 and second.stdout == GOOD_OUTPUT
    saved = _report(tmp_path)
    assert saved["budget"]["attempts"] == saved["model"]["calls"] == 1
    assert saved["model"]["cache_hits"] == 0
    assert len(fake_model.requests) == 2
    _assert_checked(saved)


def test_checked_compile_failure_does_not_claim_candidate_was_executed(
    tmp_path: Path, fake_model: FakeModelServer,
) -> None:
    fake_model.responses.append({"source": "int main(void) { return missing_symbol; }"})
    result = _run(tmp_path, "--check-undefined", "--max-rounds", "1")
    assert result.returncode == 0, result.stderr
    assert result.stdout == (TEXT + os.linesep).encode()
    saved = _report(tmp_path)
    assert saved["undefined_behavior_check"]["performed"] is False
    attempt = _model_attempts(saved)[0]
    assert attempt["exit_code"] != 0
    assert attempt["undefined_behavior_check"]["performed"] is False
    assert "validation" not in attempt
    assert saved["result"]["fallback"]["kind"] == "print_input"
    assert saved["model"]["calls"] == saved["budget"]["attempts"] == 1


def test_checked_artifact_executes_once_and_cache_profiles_are_separate(
    tmp_path: Path, fake_model: FakeModelServer,
) -> None:
    marker = tmp_path / "candidate executions.txt"
    source = (
        '#include <stdio.h>\nint main(void) { FILE *f = fopen('
        + json.dumps(marker.as_posix())
        + ', "a"); if (!f) return 9; fputs("1", f); fclose(f); '
        + 'puts("GOOD"); return 0; }\n'
    )
    fake_model.responses.extend([{"source": source}, {"source": source}])
    for flags, calls, cache_hits, count in [
        ((), 1, 0, 1),
        (("--check-undefined",), 1, 0, 2),
        (("--check-undefined",), 0, 1, 3),
        ((), 0, 1, 4),
    ]:
        completed = _run(tmp_path, *flags)
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout == GOOD_OUTPUT
        assert marker.read_text("utf-8") == "1" * count
        saved = _report(tmp_path)
        assert saved["model"]["calls"] == calls
        assert saved["model"]["cache_hits"] == cache_hits
        assert saved["budget"]["attempts"] == 1
        attempt = _model_attempts(saved)[0]
        assert (tmp_path / saved["result"]["artifact"]).is_file()
        if flags:
            _assert_checked(saved)
            check = saved["result"]["generated"]["undefined_behavior_check"]
            assert all(flag in attempt["command"] for flag in check["flags"])
        else:
            assert "undefined_behavior_check" not in saved
        assert (tmp_path / saved["result"]["generated"]["source"]).read_text("utf-8") == source
    assert len(fake_model.requests) == 2


def test_preexisting_default_cache_keeps_legacy_identity(
    tmp_path: Path, fake_model: FakeModelServer,
) -> None:
    from yescc.adapters import get_adapter
    from yescc.model.cache import cache_key, store
    from yescc.model.config import load_settings, resolve_controls
    from yescc.model.openai_compat import request_parameters
    from yescc.model.prompts import PROMPT_VERSION

    settings = load_settings("l3")
    assert settings is not None
    settings = resolve_controls(settings)
    info = get_adapter("c").toolchain()
    # The 07160fc cache identity has no validation-profile or user-argument field.
    legacy_key = cache_key({
        "kind": "creative",
        "prompt_version": PROMPT_VERSION,
        "provider": settings.provider,
        "base_url": settings.base_url,
        "model": settings.model,
        "generation_settings": request_parameters(
            settings, max_tokens=settings.max_output_tokens, temperature=0.4,
        ),
        "toolchain": {"adapter": info.adapter, "command": info.command, "version": info.version},
        "payload": {"text": TEXT, "creative": True, "feedback": ""},
    })
    store(legacy_key, {"source": GOOD})
    default = _run(tmp_path)
    assert default.returncode == 0 and default.stdout == GOOD_OUTPUT
    saved = _report(tmp_path)
    assert saved["model"]["calls"] == 0 and saved["model"]["cache_hits"] == 1
    assert not fake_model.requests
    assert "undefined_behavior_check" not in saved
    fake_model.responses.append({"source": GOOD})
    checked = _run(tmp_path, "--check-undefined")
    assert checked.returncode == 0 and checked.stdout == GOOD_OUTPUT
    saved = _report(tmp_path)
    assert saved["model"]["calls"] == 1 and saved["model"]["cache_hits"] == 0
    assert len(fake_model.requests) == 1
    _assert_checked(saved)


def test_checked_candidate_preserves_actual_user_flags(
    tmp_path: Path, fake_model: FakeModelServer, monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    source = _input(tmp_path)
    program = (
        '#include <stdio.h>\n'
        '#if __STDC_VERSION__ != 201112L\n#error wrong C standard\n#endif\n'
        '#if EXPECTED != 73\n#error missing user define\n#endif\n'
        '#ifndef __OPTIMIZE__\n#error user optimization was overridden\n#endif\n'
        'int main(void) { puts("GOOD"); return 0; }\n'
    )
    fake_model.responses.append({"source": program})
    flags = ["-std=c11", "-O1", "-D", "EXPECTED=73"]
    invocation = Invocation(mode="run", source=source, play=True, passthrough=flags,
                            check_undefined=True)
    assert execute([], invocation) == 0
    assert capfd.readouterr().out == "GOOD" + os.linesep
    saved = _report(tmp_path)
    _assert_checked(saved)
    command = _model_attempts(saved)[0]["command"]
    for flag in flags:
        assert flag in command
    assert "-O0" not in command
    assert [flag for flag in command if flag.startswith("-std=")] == ["-std=c11"]
    probes = saved["undefined_behavior_check"]["probes"]
    assert probes
    for probe in probes:
        for control_name in ("normal", "overflow"):
            control = probe.get(control_name)
            if control is None:
                continue
            probe_command = control["compile"]["command"]
            assert all(flag in probe_command for flag in flags)
            assert "-O0" not in probe_command
            assert [flag for flag in probe_command if flag.startswith("-std=")] == ["-std=c11"]
    assert (tmp_path / saved["result"]["generated"]["source"]).read_text("utf-8") == program
    # The same input with another actual compilation profile must not reuse this response.
    fake_model.responses.append({"source": program.replace("EXPECTED != 73", "EXPECTED != 74")})
    invocation.passthrough = ["-std=c11", "-O1", "-D", "EXPECTED=74"]
    for calls, hits in ((1, 0), (0, 1)):
        assert execute([], invocation) == 0
        assert capfd.readouterr().out == "GOOD" + os.linesep
        saved = _report(tmp_path)
        assert saved["model"]["calls"] == calls
        assert saved["model"]["cache_hits"] == hits
        assert saved["budget"]["attempts"] == 1
        _assert_checked(saved)
    assert len(fake_model.requests) == 2


@pytest.mark.parametrize("case", ["native", "native_bad_flags", "rule", "generated",
                                   "offline", "easter_egg"])
def test_other_paths_never_probe_or_claim_to_be_checked(
    tmp_path: Path, fake_model: FakeModelServer, monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str], case: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(pipeline, "select_undefined_profile", _must_not_probe)
    text, name = TEXT, "idea.txt"
    options: dict = {"check_undefined": True}
    expected_status, expected_exit = "creative_fallback", 0
    if case.startswith("native"):
        text, name = GOOD, "idea.c"
        expected_status = "native_ok"
        if case == "native_bad_flags":
            options.update(strict=True, passthrough=["-fsanitize=not-a-sanitizer"])
            expected_status, expected_exit = "native_failed", 102
    elif case == "rule":
        text, name = '#include <stdio.h>\nint main(void) { puts("GOOD") return 0; }', "idea.c"
        expected_status = "repaired"
    elif case == "generated":
        text = "写一个程序输出 GOOD"
        fake_model.responses.append({"source": GOOD})
        expected_status = "generated"
    elif case == "offline":
        options.update(play=True, offline=True)
    else:
        text = "天王盖地虎"
        options["play"] = True
    source = _input(tmp_path, text, name)
    assert execute([], Invocation(mode="run", source=source, **options)) == expected_exit
    capfd.readouterr()
    saved = _report(tmp_path)
    assert saved["result"]["status"] == expected_status
    check = saved["undefined_behavior_check"]
    assert check["requested"] is True and check["performed"] is False
    assert check["reason"] == "not_model_creative"
    assert check["mode"] is None and check["scope"] == "model_creative"
    assert all("undefined_behavior_check" not in attempt for attempt in saved["attempts"])
    assert len(fake_model.requests) == (1 if case == "generated" else 0)
    assert source.read_text("utf-8") == text


def test_build_rejects_flag_before_probe_model_or_program_execution(
    tmp_path: Path, fake_model: FakeModelServer, monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(pipeline, "select_undefined_profile", _must_not_probe)
    marker = tmp_path / "must not run"
    program = '#include <stdio.h>\nint main(void) { FILE *f = fopen(' + json.dumps(
        marker.as_posix()
    ) + ', "w"); if (f) fclose(f); puts("GOOD"); return 0; }'
    source = _input(tmp_path, program, "idea.c")
    assert main([str(source), "--check-undefined", "--json"]) == 100
    output = capfd.readouterr()
    assert json.loads(output.out)["report_kind"] == "command_error"
    assert not marker.exists() and not fake_model.requests
    assert not (tmp_path / ".yescc").exists()


@pytest.mark.parametrize("prior_budget_reason", [None, "prior repair budget blocked"])
def test_unavailable_profile_fails_before_model_without_deterministic_fallback(
    tmp_path: Path, fake_model: FakeModelServer, monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str], prior_budget_reason: str | None,
) -> None:
    from yescc.sandbox.undefined import UndefinedProfile

    monkeypatch.chdir(tmp_path)
    if prior_budget_reason is not None:
        original_session = pipeline.ModelSession

        def make_session(**kwargs: object) -> pipeline.ModelSession:
            session = original_session(**kwargs)
            session.budget_reason = prior_budget_reason
            return session

        monkeypatch.setattr(pipeline, "ModelSession", make_session)
    probes = [{"mode": "runtime", "exit_code": 1}, {"mode": "trap", "exit_code": 1}]
    profile = UndefinedProfile(False, None, (), "unavailable", probes)
    monkeypatch.setattr(pipeline, "select_undefined_profile", lambda *args, **kwargs: profile)
    source = _input(tmp_path)
    result = execute([], Invocation(mode="run", source=source, play=True, check_undefined=True))
    assert result == 102
    assert capfd.readouterr().out == ""
    assert not fake_model.requests
    saved = _report(tmp_path)
    check = saved["undefined_behavior_check"]
    assert check["requested"] is True and check["performed"] is False
    assert check["available"] is False and check["mode"] is None
    assert check["reason"] == "unavailable" and check["probes"] == probes
    assert saved["result"]["status"] == "failed"
    assert saved["result"]["fallback"] is None
    assert saved["budget"]["attempts"] == 0
    assert not saved["attempts"]


def test_check_option_keeps_run_json_disabled(
    tmp_path: Path, fake_model: FakeModelServer, monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(pipeline, "select_undefined_profile", _must_not_probe)
    source = _input(tmp_path)
    assert main(["run", str(source), "--check-undefined", "--json"]) == 100
    assert json.loads(capfd.readouterr().out)["report_kind"] == "command_error"
    assert not fake_model.requests and not (tmp_path / ".yescc").exists()


def test_archived_out_of_bounds_source_is_rejected_without_changing_its_bytes(
    tmp_path: Path, fake_model: FakeModelServer, monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    archive = Path(__file__).resolve().parents[2] / (
        "docs/评测/data/creative-generation-support-2026-11-13.json.gz"
    )
    archived = json.loads(gzip.decompress(archive.read_bytes()))["files"][
        "runs/fresh-d11__facts__r1/generated.c"
    ]
    raw_source = base64.b64decode(archived["base64"], validate=True)
    expected_sha = "86647c5f4ccb683dbaa79e81537a49595baf7e668f23593392723fa5a5cfa956"
    assert hashlib.sha256(raw_source).hexdigest() == archived["sha256"] == expected_sha
    fake_model.responses.extend([{"source": raw_source.decode("utf-8")}, {"source": GOOD}])
    compiled_sources: list[bytes] = []
    original_build = pipeline.build_in_temp

    def capture_build(*args: object, **kwargs: object) -> TempBuild:
        compiled_sources.append(str(args[1]).encode("utf-8"))
        return original_build(*args, **kwargs)

    monkeypatch.setattr(pipeline, "build_in_temp", capture_build)
    monkeypatch.chdir(tmp_path)
    source = _input(tmp_path)
    assert execute([], Invocation(mode="run", source=source, play=True, check_undefined=True)) == 0
    output = capfd.readouterr()
    assert output.out == "GOOD" + os.linesep
    assert compiled_sources == [raw_source, GOOD.encode("utf-8")]
    saved = _report(tmp_path)
    mode = _assert_checked(saved)
    first, second = _model_attempts(saved)
    assert first["validation"]["passed"] is False
    assert second["validation"]["passed"] is True
    if mode == "runtime":
        check = first["undefined_behavior_check"]
        assert check["diagnostic_observed"] is True
        assert "index 4 out of bounds" in check["stderr"]
    assert len(fake_model.requests) == saved["model"]["calls"] == 2
    assert saved["budget"]["attempts"] == 2
    assert source.read_bytes() == TEXT.encode("utf-8")


def test_checked_compile_cancellation_stops_requests_and_discards_cache(
    tmp_path: Path, fake_model: FakeModelServer, monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    from yescc.sandbox.undefined import UndefinedProfile

    monkeypatch.chdir(tmp_path)
    profile = UndefinedProfile(True, "runtime", ("-fsanitize=undefined",), "controls_passed", [])
    monkeypatch.setattr(pipeline, "select_undefined_profile", lambda *args, **kwargs: profile)

    def cancelled_build(*args: object, **kwargs: object) -> TempBuild:
        return TempBuild(BuildResult(
            command=["gcc", "generated.c", *profile.flags], returncode=130,
            stdout="", stderr="cancelled", artifact=None, duration_ms=1,
        ), None)

    monkeypatch.setattr(pipeline, "build_in_temp", cancelled_build)
    monkeypatch.setattr(pipeline, "run_creative_command", _must_not_probe)
    fake_model.responses.extend([{"source": GOOD}, {"source": GOOD}])
    source = _input(tmp_path)
    invocation = Invocation(mode="run", source=source, play=True, check_undefined=True)
    assert execute([], invocation) == 130
    assert capfd.readouterr().out == ""
    assert len(fake_model.requests) == 1 and len(fake_model.responses) == 1
    saved = _report(tmp_path)
    assert saved["budget"]["attempts"] == 1
    assert saved["undefined_behavior_check"]["performed"] is False
    assert _model_attempts(saved)[0]["exit_code"] == 130
    assert saved["result"]["fallback"] is None
    assert not list((tmp_path / "cache/model-v1").glob("*.json"))


def test_checked_program_cancellation_stops_requests_and_discards_cache(
    tmp_path: Path, fake_model: FakeModelServer,
) -> None:
    cancelled = '#include <stdio.h>\nint main(void) { puts("CANCELLED"); return 130; }\n'
    fake_model.responses.extend([{"source": cancelled}, {"source": GOOD}])
    result = _run(tmp_path, "--check-undefined")
    assert result.returncode == 130 and result.stdout == b""
    assert len(fake_model.requests) == 1 and len(fake_model.responses) == 1
    saved = _report(tmp_path)
    assert saved["budget"]["attempts"] == 1
    assert saved["undefined_behavior_check"]["performed"] is True
    assert len(_model_attempts(saved)) == 1
    assert not list((tmp_path / "cache/model-v1").glob("*.json"))
    retried = _run(tmp_path, "--check-undefined")
    assert retried.returncode == 0 and retried.stdout == GOOD_OUTPUT
    saved = _report(tmp_path)
    assert saved["model"]["calls"] == 1 and saved["model"]["cache_hits"] == 0
    assert len(fake_model.requests) == 2


def test_checked_candidate_does_not_run_after_build_exhausts_wall_clock(
    tmp_path: Path, fake_model: FakeModelServer, monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    sessions = []
    original_session = pipeline.ModelSession

    def make_session(**kwargs: object) -> pipeline.ModelSession:
        session = original_session(**kwargs)
        sessions.append(session)
        return session

    monkeypatch.setattr(pipeline, "ModelSession", make_session)
    original_build = pipeline.build_in_temp

    def expire_after_build(*args: object, **kwargs: object) -> TempBuild:
        built = original_build(*args, **kwargs)
        if kwargs.get("extra_flags"):
            assert built.build.returncode == 0
            sessions[0].budget.started -= sessions[0].budget.max_wall_s + 1
        return built

    monkeypatch.setattr(pipeline, "build_in_temp", expire_after_build)
    monkeypatch.setattr(pipeline, "run_creative_command", _must_not_probe)
    fake_model.responses.extend([{"source": GOOD}, {"source": GOOD}])
    source = _input(tmp_path)
    assert execute([], Invocation(mode="run", source=source, play=True, check_undefined=True)) == 0
    assert capfd.readouterr().out == TEXT + os.linesep
    saved = _report(tmp_path)
    assert saved["undefined_behavior_check"]["performed"] is False
    attempt = _model_attempts(saved)[0]
    assert attempt["exit_code"] == 0
    assert attempt["undefined_behavior_check"]["performed"] is False
    assert attempt["undefined_behavior_check"]["reason"] == "task_deadline"
    assert "validation" not in attempt
    assert saved["result"]["fallback"]["kind"] == "print_input"
    assert len(fake_model.requests) == saved["budget"]["attempts"] == 1
    assert not list((tmp_path / "cache/model-v1").glob("*.json"))


@pytest.mark.parametrize("checked", [False, True], ids=["default", "checked"])
def test_implicit_a_out_artifact_cannot_replace_the_original_input(
    tmp_path: Path, fake_model: FakeModelServer, checked: bool,
) -> None:
    source = tmp_path / "a.out"
    raw_source = TEXT.encode("utf-8")
    source.write_bytes(raw_source)
    fake_model.responses.append({"source": GOOD})
    flags = ["--check-undefined"] if checked else []
    result = subprocess.run(
        [sys.executable, "-m", "yescc", "run", str(source), "--play", *flags],
        cwd=tmp_path, capture_output=True, timeout=60, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == GOOD_OUTPUT
    assert source.read_bytes() == raw_source
    saved = _report(tmp_path, "a")
    artifact = tmp_path / saved["result"]["artifact"]
    assert artifact.resolve() != source.resolve()
    assert artifact.is_file()
    assert len(fake_model.requests) == 1
    if checked:
        _assert_checked(saved)
