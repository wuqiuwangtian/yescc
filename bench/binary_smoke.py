"""在仓库外验收裸 YesCC 二进制；只使用外部工具链和本机假模型。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import signal
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tests.conftest import FakeModelServer

ROOT = Path(__file__).resolve().parents[1]
TIMEOUT_SECONDS = 60
DUMMY_KEY = "dummy-binary-smoke-secret"
MULTI_ERROR = (
    "include <stdio.h>\n\nint mian(void) {\n    int n = 7；\n"
    '    prinf("value=%d\\n", n)\n    retrun 0\n}\n'
)
MODEL_SOURCE = (
    '#include <stdio.h>\nint main(void){\nputs("model");\nreturn missing_binary_symbol;\n}\n'
)
MODEL_DIFF = (
    '@@ -1,5 +1,5 @@\n #include <stdio.h>\n int main(void){\n puts("model");\n'
    "-return missing_binary_symbol;\n+return 0;\n }\n"
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _environment(directory: Path) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("YESCC_", "FCOMP_", "PYTHON", "_PYI", "CONDA_"))
        and key != "VIRTUAL_ENV"
    }
    excluded = [ROOT]
    if os.environ.get("VIRTUAL_ENV"):
        excluded.append(Path(os.environ["VIRTUAL_ENV"]).resolve())
    path = []
    for value in env.get("PATH", "").split(os.pathsep):
        if value and not any(Path(value).resolve().is_relative_to(root) for root in excluded):
            path.append(value)
    env["PATH"] = os.pathsep.join(path)
    for name in ("home", "config", "cache", "tmp", "appdata", "localappdata"):
        (directory / name).mkdir()
    env.update(
        {
            "HOME": str(directory / "home"),
            "USERPROFILE": str(directory / "home"),
            "XDG_CONFIG_HOME": str(directory / "config"),
            "XDG_CACHE_HOME": str(directory / "cache"),
            "APPDATA": str(directory / "appdata"),
            "LOCALAPPDATA": str(directory / "localappdata"),
            "TMPDIR": str(directory / "tmp"),
            "TMP": str(directory / "tmp"),
            "TEMP": str(directory / "tmp"),
            "YESCC_CONFIG_DIR": str(directory / "config"),
            "YESCC_CACHE_DIR": str(directory / "cache"),
            "YESCC_SUPPRESS_NOTICE": "1",
            "PYTHONIOENCODING": "utf-8",
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
        }
    )
    return env


def _invoke(command: list[str], cwd: Path, env: dict[str, str]) -> dict[str, Any]:
    started = time.monotonic()
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=os.name != "nt",
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0,
    )
    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        timed_out = True
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                timeout=10,
                check=False,
            )
        else:
            os.killpg(process.pid, signal.SIGKILL)
        process.kill()
        stdout, stderr = process.communicate(timeout=10)
    return {
        "command": command,
        "cwd": str(cwd),
        "exit_code": process.returncode,
        "stdout": stdout.decode("utf-8", errors="replace").replace("\r\n", "\n"),
        "stderr": stderr.decode("utf-8", errors="replace").replace("\r\n", "\n"),
        "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
        "timed_out": timed_out,
        "duration_ms": round((time.monotonic() - started) * 1000),
    }


def _expect(row: dict[str, Any], name: str, condition: bool) -> None:
    row["checks"][name] = condition


class _Harness:
    def __init__(
        self, binary: Path, version: str, directory: Path, server: FakeModelServer
    ) -> None:
        self.binary = binary
        self.version = version
        self.directory = directory
        self.server = server
        self.env = _environment(directory)
        self.rows: list[dict[str, Any]] = []
        for tier in ("L2", "L3"):
            for setting, value in {
                "BASE_URL": server.base_url,
                "NAME": "binary-local-fake",
                "API_KEY": DUMMY_KEY,
                "INPUT_PRICE_PER_MTOK": "1",
                "OUTPUT_PRICE_PER_MTOK": "1",
            }.items():
                self.env[f"YESCC_MODEL_{tier}_{setting}"] = value

    def check(
        self,
        name: str,
        operation: Callable[[dict[str, Any], Path], None],
        expected_calls: int | None = 0,
    ) -> None:
        cwd = self.directory / name
        cwd.mkdir()
        before = len(self.server.requests)
        row: dict[str, Any] = {"id": name, "checks": {}, "invocations": []}
        try:
            operation(row, cwd)
        except Exception as exc:
            row["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            calls = len(self.server.requests) - before
            row["model_calls_received"] = calls
            required_calls = row.get("expected_model_calls", 0) if expected_calls is None else (
                expected_calls
            )
            _expect(row, "model_request_count", calls == required_calls)
            _expect(row, "dummy_key_not_exposed", DUMMY_KEY not in json.dumps(row))
            row["passed"] = "error" not in row and all(row["checks"].values())
            self.rows.append(row)
            self.server.responses.clear()

    def call(
        self,
        row: dict[str, Any],
        args: list[str],
        cwd: Path,
        env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        result = _invoke([str(self.binary), *args], cwd, self.env if env is None else env)
        row["invocations"].append(result)
        _expect(row, f"no_timeout_{len(row['invocations'])}", not result["timed_out"])
        return result

    def report(self, row: dict[str, Any], cwd: Path, stem: str) -> dict[str, Any]:
        path = cwd / ".yescc" / stem / "report.json"
        report = json.loads(path.read_text("utf-8"))
        row["report"] = report
        row["report_sha256"] = _sha(path)
        row["status"] = report["result"]["status"]
        _expect(row, "schema_v1", report["schema_version"] == "1")
        _expect(row, "report_version", report["tool"]["version"] == self.version)
        _expect(
            row,
            "reported_model_calls",
            (report.get("model") or {}).get("calls", 0) == row.get("expected_model_calls", 0),
        )
        return report

    def program(
        self,
        row: dict[str, Any],
        cwd: Path,
        suffix: str,
        source: str,
        status: str,
        stdout: str | None,
        flags: tuple[str, ...] = ("--offline",),
        exit_code: int = 0,
        response: object = None,
    ) -> dict[str, Any]:
        file = cwd / ("input" + suffix)
        file.write_text(source, encoding="utf-8")
        row["source_sha256_before"] = _sha(file)
        row["expected_model_calls"] = int(response is not None)
        if response is not None:
            self.server.responses.append(response)
        result = self.call(row, ["run", file.name, *flags], cwd)
        row["source_sha256_after"] = _sha(file)
        _expect(row, "source_unchanged", _sha(file) == row["source_sha256_before"])
        report = self.report(row, cwd, file.stem)
        row["exit_code"] = result["exit_code"]
        _expect(row, "status", report["result"]["status"] == status)
        _expect(row, "exit_code", result["exit_code"] == exit_code)
        if stdout is not None:
            _expect(row, "stdout", result["stdout"] == stdout)
        if status == "creative_fallback":
            fallback = report["result"].get("fallback")
            _expect(
                row,
                "fallback_marked",
                isinstance(fallback, dict) and isinstance(fallback.get("kind"), str),
            )
        return report

    def native(self, row: dict[str, Any], cwd: Path, language: str) -> None:
        sources = {
            "c": (
                ".c",
                "#include <stdio.h>\nint main(int argc,char **argv){"
                'printf("native:%s\\n",argv[1]);fputs("native stderr\\n",stderr);return 7;}\n',
            ),
            "python": (
                ".py",
                'import sys\nprint("native:"+sys.argv[1]);'
                'print("native stderr",file=sys.stderr);raise SystemExit(7)\n',
            ),
            "javascript": (
                ".js",
                'console.log("native:"+process.argv[2]);'
                'console.error("native stderr");process.exit(7);\n',
            ),
        }
        suffix, source = sources[language]
        file = cwd / ("input" + suffix)
        file.write_text(source, encoding="utf-8")
        before = _sha(file)
        actual = self.call(row, ["run", file.name, "--offline", "--", "参数"], cwd)
        row["source_sha256_before"] = before
        row["source_sha256_after"] = _sha(file)
        _expect(row, "source_unchanged", _sha(file) == before)
        report = self.report(row, cwd, "input")
        executable = report["toolchain"]["command"]
        _expect(row, "external_toolchain", Path(executable).resolve() != self.binary)
        if language == "c":
            target = cwd / ("native.exe" if os.name == "nt" else "native")
            compiled = _invoke([executable, file.name, "-o", str(target)], cwd, self.env)
            row["native_build"] = compiled
            _expect(row, "native_build", compiled["exit_code"] == 0)
            native = _invoke([str(target), "参数"], cwd, self.env)
        else:
            native = _invoke([executable, file.name, "参数"], cwd, self.env)
        row["native_comparison"] = native
        row["exit_code"] = actual["exit_code"]
        for field in ("stdout_sha256", "stderr_sha256", "exit_code"):
            _expect(row, "native_" + field, actual[field] == native[field])
        _expect(row, "native_expected_exit", actual["exit_code"] == 7)
        _expect(row, "status", report["result"]["status"] == "native_ok")

    def early_error(
        self,
        row: dict[str, Any],
        cwd: Path,
        args: list[str],
        code: int,
        source: str | None = None,
    ) -> None:
        file = cwd / args[0]
        if source is not None:
            file.write_text(source, encoding="utf-8")
        before = _sha(file) if file.exists() else None
        result = self.call(row, [*args, "--offline"], cwd)
        after = _sha(file) if file.exists() else None
        row["source_sha256_before"] = before
        row["source_sha256_after"] = after
        _expect(row, "source_unchanged", before == after)
        payload = json.loads(result["stdout"])
        row["status"] = payload.get("report_kind")
        row["exit_code"] = result["exit_code"]
        _expect(row, "exit_code", result["exit_code"] == code)
        _expect(row, "command_error", payload["report_kind"] == "command_error")
        _expect(row, "matching_error_code", payload["error"]["exit_code"] == code)
        _expect(row, "version", payload["tool"]["version"] == self.version)
        _expect(row, "stderr_empty", result["stderr"] == "")
        _expect(row, "no_report_written", not (cwd / ".yescc").exists())

    def invalid_language(self, row: dict[str, Any], cwd: Path, play: bool) -> None:
        file = cwd / "ok.c"
        file.write_text("int main(void){return 0;}\n", encoding="utf-8")
        before_source = _sha(file)
        first = self.call(row, [file.name, "--json", "--offline"], cwd)
        initial = self.report(row, cwd, "ok")
        _expect(row, "successful_baseline", first["exit_code"] == 0)
        _expect(row, "native_baseline", initial["result"]["status"] == "native_ok")
        report_path = cwd / ".yescc/ok/report.json"
        before_report = _sha(report_path)
        args = [file.name, "--lang", "nonesuch", "--json", "--offline"]
        if play:
            args.append("--play")
        result = self.call(row, args, cwd)
        payload = json.loads(result["stdout"])
        row["status"] = payload.get("report_kind")
        row["exit_code"] = result["exit_code"]
        row["source_sha256_before"] = before_source
        row["source_sha256_after"] = _sha(file)
        row["report_sha256_before"] = before_report
        row["report_sha256_after"] = _sha(report_path)
        _expect(row, "exit_code", result["exit_code"] == 101)
        _expect(row, "command_error", payload["report_kind"] == "command_error")
        _expect(row, "matching_error_code", payload["error"]["exit_code"] == 101)
        _expect(row, "stderr_empty", not result["stderr"])
        _expect(row, "report_unchanged", _sha(report_path) == before_report)
        _expect(row, "source_unchanged", _sha(file) == before_source)
        _expect(row, "no_fallback_source", not (report_path.parent / "fallback.c").exists())

    def missing_toolchain(self, row: dict[str, Any], cwd: Path, language: str) -> None:
        sources = {
            "c": (".c", "int main(void){return 0;}\n", "gcc"),
            "python": (".py", 'print("external Python required")\n', "python"),
            "javascript": (".js", 'console.log("external Node required");\n', "node"),
        }
        suffix, source, expected_tool = sources[language]
        file = cwd / ("input" + suffix)
        file.write_text(source, encoding="utf-8")
        before_source = _sha(file)
        empty_path = cwd / "empty-path"
        empty_path.mkdir()
        env = {**self.env, "PATH": str(empty_path)}
        row["toolchain_path_override"] = str(empty_path)
        result = self.call(row, [file.name, "--strict", "--json", "--offline"], cwd, env)
        report = self.report(row, cwd, "input")
        message = report["result"].get("failure_message")
        has_message = isinstance(message, str) and expected_tool in message.lower()
        _expect(row, "failed_with_102", result["exit_code"] == 102)
        _expect(row, "single_build_report", json.loads(result["stdout"]) == report)
        _expect(row, "not_command_error", "report_kind" not in report)
        _expect(row, "json_stderr_empty", not result["stderr"])
        _expect(row, "missing_tool_cause_saved", has_message)
        _expect(row, "no_invented_attempt", report["attempts"] == [])
        _expect(row, "no_invented_diagnostic", report["diagnostics"] == [])
        _expect(
            row,
            "no_invented_tool_command_or_version",
            not report["toolchain"]["command"] and not report["toolchain"]["version"],
        )
        _expect(row, "no_artifact", report["result"]["artifact"] is None)
        _expect(row, "no_model", report["model"] is None and not report["privacy"]["cloud_used"])
        saved_report = cwd / ".yescc/input/report.json"
        before_report = _sha(saved_report)
        explained_json = self.call(
            row, [file.name, "--explain", "--json", "--offline"], cwd, env
        )
        explained_text = self.call(row, [file.name, "--explain", "--offline"], cwd, env)
        _expect(
            row,
            "explain_json_uses_saved_report",
            explained_json["exit_code"] == 0
            and not explained_json["stderr"]
            and json.loads(explained_json["stdout"]) == report,
        )
        _expect(
            row,
            "explain_text_has_missing_tool_cause",
            explained_text["exit_code"] == 0
            and has_message
            and message in explained_text["stdout"],
        )
        _expect(row, "explain_preserves_report", _sha(saved_report) == before_report)
        plain = self.call(row, [file.name, "--strict", "--offline"], cwd, env)
        _expect(
            row,
            "plain_missing_tool_cause",
            plain["exit_code"] == 102
            and not plain["stdout"]
            and expected_tool in plain["stderr"].lower(),
        )
        row["source_sha256_before"] = before_source
        row["source_sha256_after"] = _sha(file)
        _expect(row, "source_unchanged", _sha(file) == before_source)
        _expect(row, "toolchain_path_stayed_empty", not list(empty_path.iterdir()))

    def creative_rejection(self, row: dict[str, Any], cwd: Path, checked: bool) -> None:
        file = cwd / "input.txt"
        text = "a stone begins to tell jokes\n"
        file.write_text(text, encoding="utf-8")
        before_source = _sha(file)
        marker = "FAILED-CANDIDATE-MUST-STAY-HIDDEN"
        if checked:
            source = (
                '#include <stdio.h>\n#include <limits.h>\nint main(void){\n'
                f'puts("{marker}"); fflush(stdout);\n'
                "volatile int upper=INT_MAX, one=1;\n"
                "volatile int sum=upper+one; (void)sum; return 0;\n}\n"
            )
        else:
            source = f'#include <stdio.h>\nint main(void){{puts("{marker}");return 7;}}\n'
        row["fake_model_source"] = source
        row["fake_model_endpoint"] = self.server.base_url
        self.server.responses.append({"source": source})
        args = ["run", file.name, "--play", "--no-cache", "--max-rounds", "1"]
        if checked:
            args.append("--check-undefined")
        actual = self.call(row, args, cwd)
        raw = json.loads((cwd / ".yescc/input/report.json").read_text("utf-8"))
        profile = raw.get("undefined_behavior_check")
        if checked and isinstance(profile, dict) and profile.get("available") is False:
            # This is a supported failure response, not evidence that a candidate ran.
            row["expected_model_calls"] = 0
            row["optional_check_coverage"] = "unavailable; candidate not requested or run"
            report = self.report(row, cwd, "input")
            message = report["result"].get("failure_message")
            _expect(row, "unavailable_fails_with_102", actual["exit_code"] == 102)
            _expect(row, "unavailable_report_status", report["result"]["status"] == "failed")
            _expect(row, "unavailable_not_performed", profile.get("performed") is False)
            _expect(row, "unavailable_probes_recorded", bool(profile.get("probes")))
            _expect(row, "unavailable_not_fallback", report["result"]["fallback"] is None)
            _expect(row, "unavailable_no_model_attempt", report["attempts"] == [])
            _expect(row, "unavailable_not_model_generation", report["result"]["generated"] is None)
            _expect(
                row,
                "unavailable_cause_is_visible",
                isinstance(message, str) and "不可用" in message and "不可用" in actual["stderr"],
            )
            _expect(row, "unavailable_no_candidate_output", not actual["stdout"])
        else:
            attempts = [item for item in raw["attempts"] if item["kind"] == "model"]
            assert len(attempts) == 1, "expected one generated candidate attempt"
            attempt = attempts[0]
            validation = attempt.get("validation")
            assert isinstance(validation, dict) and validation.get("performed") is True
            if checked:
                check = attempt.get("undefined_behavior_check")
                assert isinstance(check, dict) and check.get("performed") is True
                assert check.get("mode") in {"runtime", "trap"}
                row["optional_check_coverage"] = "candidate rejection verified"
                row["optional_check_mode"] = check["mode"]
                _expect(row, "reported_check_does_not_certify_ub", check.get("ub_detected") is None)
                _expect(row, "candidate_compile_has_check_flags", all(
                    flag in attempt["command"] for flag in check["flags"]
                ))
            row["expected_model_calls"] = 1
            report = self.report(row, cwd, "input")
            fallback = report["result"]["fallback"]
            guidance = fallback.get("guidance") if isinstance(fallback, dict) else None
            hints = "\n".join(str(hint) for hint in guidance) if isinstance(guidance, list) else ""
            _expect(row, "generated_candidate_compiled", attempt["exit_code"] == 0)
            _expect(row, "candidate_actual_run_failed", validation.get("passed") is False)
            _expect(row, "candidate_nonzero_exit", "exit_nonzero" in validation.get("reasons", []))
            _expect(row, "fallback_is_input_echo", isinstance(fallback, dict)
                    and fallback.get("kind") == "print_input")
            _expect(row, "failed_candidate_is_not_adopted", report["result"]["generated"] is None)
            _expect(row, "fallback_stdout_only", actual["stdout"] == text)
            _expect(row, "fallback_exit", actual["exit_code"] == 0)
            _expect(row, "rejection_reason_saved", "运行验证" in hints and "拒绝" in hints
                    and f"退出码 {validation.get('exit_code')}" in hints)
            if "creativity" in report["invocation"]:
                _expect(row, "quiet_fallback", not actual["stderr"])
            else:  # Published 0.1.0 archives use the original presentation.
                _expect(row, "rejection_reason_on_stderr", bool(guidance) and all(
                    str(hint) in actual["stderr"] for hint in guidance
                ))
            reason = report["budget"]["blocked_reason"]
            _expect(row, "original_attempt_stop_reason_preserved", bool(reason) and reason in hints)
            _expect(row, "original_attempt_limit", report["budget"]["attempts"] == 1)
            if checked:
                _expect(row, "actual_check_mode_visible", row["optional_check_mode"] in hints)
            before_report = _sha(cwd / ".yescc/input/report.json")
            explained = self.call(row, [file.name, "--explain", "--offline"], cwd)
            _expect(
                row,
                "explain_includes_saved_guidance",
                explained["exit_code"] == 0 and bool(guidance)
                and all(str(hint) in explained["stdout"] for hint in guidance),
            )
            _expect(row, "explain_preserves_report",
                    _sha(cwd / ".yescc/input/report.json") == before_report)
        _expect(row, "failed_candidate_output_hidden", all(
            marker not in actual[field] for field in ("stdout", "stderr")
        ))
        row["source_sha256_before"] = before_source
        row["source_sha256_after"] = _sha(file)
        _expect(row, "source_unchanged", _sha(file) == before_source)


def verify(binary: Path, expected_version: str) -> dict[str, Any]:
    """验收实际 ELF/PE 产物，返回包含失败在内的全部证据；不安装或导入被测包。"""
    binary = binary.resolve(strict=True)
    with binary.open("rb") as handle:
        signature = handle.read(4)
    if not (signature.startswith(b"\x7fELF") or signature.startswith(b"MZ")):
        raise ValueError("--binary 必须是构建后的 ELF/PE 可执行文件，不接受脚本或模块入口。")
    with tempfile.TemporaryDirectory(prefix="yescc-binary-") as raw:
        directory = Path(raw).resolve()
        if directory.is_relative_to(ROOT):
            raise ValueError("二进制验收临时目录必须位于仓库外。")
        server = FakeModelServer()
        server.start()
        harness = _Harness(binary, expected_version, directory, server)
        try:

            def version(row: dict[str, Any], cwd: Path, no_path: bool = False) -> None:
                env = dict(harness.env)
                if no_path:
                    env["PATH"] = ""
                result = harness.call(row, ["--version", "--offline"], cwd, env)
                _expect(row, "version", result["stdout"] == f"yescc {expected_version}\n")
                _expect(row, "exit_code", result["exit_code"] == 0)
                _expect(row, "stderr_empty", not result["stderr"])

            harness.check("version", version)
            harness.check("version_without_toolchain_path", lambda r, p: version(r, p, True))

            def help_text(row: dict[str, Any], cwd: Path) -> None:
                result = harness.call(row, ["--help", "--offline"], cwd)
                _expect(row, "exit_code", result["exit_code"] == 0)
                _expect(row, "help", all(s in result["stdout"] for s in ("--json", "--lang")))

            harness.check("help", help_text)

            def explain_identity(
                row: dict[str, Any], cwd: Path, cross_language: bool
            ) -> None:
                if cross_language:
                    first, second = cwd / "same.py", cwd / "same.c"
                    second_text = "int main(void){return 0;}\n"
                else:
                    (cwd / "first").mkdir()
                    (cwd / "second").mkdir()
                    first, second = cwd / "first/same.py", cwd / "second/same.py"
                    second_text = 'print("second")\n'
                first.write_text('print("first")\n', encoding="utf-8")
                second.write_text(second_text, encoding="utf-8")
                before_sources = [_sha(first), _sha(second)]
                initial = harness.call(row, ["run", str(first), "--offline"], cwd)
                _expect(row, "initial_program", initial["exit_code"] == 0
                        and initial["stdout"] == "first\n" and not initial["stderr"])
                built = harness.call(row, [str(second), "--json", "--offline"], cwd)
                expected = json.loads(built["stdout"])
                _expect(row, "second_build", built["exit_code"] == 0
                        and expected["result"]["status"] == "native_ok")
                report_file = cwd / ".yescc/same/report.json"
                before_report = _sha(report_file)
                row["report_sha256_before"] = before_report
                args = [str(first), "--explain", "--offline"]
                rejected = harness.call(row, [*args, "--json"], cwd)
                row["rejected_json"] = json.loads(rejected["stdout"])
                error = row["rejected_json"].get("error", {})
                _expect(row, "foreign_report_rejected", rejected["exit_code"] == 100
                        and not rejected["stderr"]
                        and row["rejected_json"].get("report_kind") == "command_error"
                        and error.get("exit_code") == 100 and error.get("phase") == "explain")
                text = harness.call(row, args, cwd)
                _expect(row, "text_rejected", text["exit_code"] == 100
                        and not text["stdout"] and bool(text["stderr"]))
                matched = harness.call(row, [str(second), "--explain", "--json", "--offline"], cwd)
                row["matching_json"] = json.loads(matched["stdout"])
                _expect(row, "matching_report", matched["exit_code"] == 0
                        and not matched["stderr"] and row["matching_json"] == expected)
                _expect(row, "source_unchanged", [_sha(first), _sha(second)] == before_sources)
                second.unlink()
                historical = harness.call(
                    row, [str(second), "--explain", "--json", "--offline"], cwd
                )
                _expect(row, "deleted_source_history", historical["exit_code"] == 0
                        and not historical["stderr"]
                        and json.loads(historical["stdout"]) == expected)
                row["report_sha256_after"] = _sha(report_file)
                row["report_preserved"] = row["report_sha256_after"] == before_report
                row["requested_source"] = str(first.relative_to(cwd))
                row["stored_source"] = str(second.relative_to(cwd))
                _expect(row, "report_preserved", row["report_preserved"])

            harness.check(
                "explain_cross_language_identity", lambda r, p: explain_identity(r, p, True)
            )
            harness.check(
                "explain_directory_identity", lambda r, p: explain_identity(r, p, False)
            )
            for language in ("c", "python", "javascript"):
                harness.check(
                    "native_" + language, lambda r, p, lang=language: harness.native(r, p, lang)
                )
                harness.check(
                    "missing_toolchain_" + language,
                    lambda r, p, lang=language: harness.missing_toolchain(r, p, lang),
                )
            harness.check(
                "rule_c",
                lambda r, p: harness.program(
                    r,
                    p,
                    ".c",
                    '#include <stdio.h>\nint main(void){puts("rule");return 0；}\n',
                    "repaired",
                    "rule\n",
                ),
            )
            harness.check(
                "multi_error",
                lambda r, p: harness.program(
                    r, p, ".c", MULTI_ERROR, "repaired", "value=7\n", ("--offline", "--no-fallback")
                ),
            )

            def offline_fallback(row: dict[str, Any], cwd: Path) -> None:
                source = "a stone begins to tell jokes\n"
                report = harness.program(row, cwd, ".xyz", source, "creative_fallback", source)
                guidance = report["result"]["fallback"].get("guidance")
                _expect(row, "guidance", isinstance(guidance, list) and bool(guidance))
                _expect(row, "print_input", report["result"]["fallback"]["kind"] == "print_input")

            harness.check("offline_fallback", offline_fallback)
            harness.check(
                "strict",
                lambda r, p: harness.program(
                    r,
                    p,
                    ".c",
                    "int main(void){return missing_binary_symbol;}\n",
                    "native_failed",
                    "",
                    ("--strict", "--offline"),
                    102,
                ),
            )
            harness.check(
                "model_repair",
                lambda r, p: harness.program(
                    r,
                    p,
                    ".c",
                    MODEL_SOURCE,
                    "repaired",
                    "model\n",
                    ("--no-cache", "--no-fallback"),
                    response={"diff": MODEL_DIFF},
                ),
                expected_calls=1,
            )
            harness.check(
                "model_generated",
                lambda r, p: harness.program(
                    r,
                    p,
                    ".txt",
                    "写一个程序输出 generated\n",
                    "generated",
                    "generated\n",
                    ("--no-cache",),
                    response={
                        "source": (
                            '#include <stdio.h>\nint main(void){puts("generated");return 0;}\n'
                        )
                    },
                ),
                expected_calls=1,
            )
            harness.check(
                "model_creative",
                lambda r, p: harness.program(
                    r,
                    p,
                    ".txt",
                    "a stone begins to tell jokes\n",
                    "creative_fallback",
                    "creative\n",
                    ("--play", "--no-cache"),
                    response={
                        "source": '#include <stdio.h>\nint main(void){puts("creative");return 0;}\n'
                    },
                ),
                expected_calls=1,
            )
            harness.check(
                "creative_rejection_guidance",
                lambda r, p: harness.creative_rejection(r, p, False),
                expected_calls=1,
            )
            harness.check(
                "checked_creative_rejection_guidance",
                lambda r, p: harness.creative_rejection(r, p, True),
                expected_calls=None,
            )

            def normal_json(row: dict[str, Any], cwd: Path) -> None:
                file = cwd / "ok.c"
                file.write_text("int main(void){return 0;}\n", encoding="utf-8")
                before = _sha(file)
                result = harness.call(row, [file.name, "--json", "--offline"], cwd)
                row["source_sha256_before"] = before
                row["source_sha256_after"] = _sha(file)
                report = harness.report(row, cwd, "ok")
                _expect(row, "exit_code", result["exit_code"] == 0)
                _expect(row, "single_json", json.loads(result["stdout"]) == report)
                _expect(row, "no_error_envelope", "report_kind" not in report)
                _expect(row, "source_unchanged", _sha(file) == before)

            harness.check("normal_json", normal_json)
            for name, args, code, source in (
                ("json_missing", ["missing.c", "--json"], 100, None),
                (
                    "json_budget",
                    ["ok.c", "--budget", "-1", "--json"],
                    100,
                    "int main(void){return 0;}\n",
                ),
                ("json_unsupported", ["unknown.xyz", "--strict", "--json"], 101, "hello\n"),
                (
                    "json_no_fallback",
                    ["unknown.txt", "--no-fallback", "--json"],
                    101,
                    "make time travel\n",
                ),
            ):
                harness.check(
                    name, lambda r, p, a=args, c=code, s=source: harness.early_error(r, p, a, c, s)
                )
            harness.check(
                "invalid_lang_preserves_report", lambda r, p: harness.invalid_language(r, p, False)
            )
            harness.check(
                "invalid_lang_play_preserves_report",
                lambda r, p: harness.invalid_language(r, p, True),
            )

            def language_json_value(row: dict[str, Any], cwd: Path) -> None:
                file = cwd / "ok.c"
                file.write_text("int main(void){return 0;}\n", encoding="utf-8")
                before = _sha(file)
                result = harness.call(row, [file.name, "--lang", "--json", "--offline"], cwd)
                row["source_sha256_before"] = before
                row["source_sha256_after"] = _sha(file)
                _expect(row, "exit_code", result["exit_code"] == 101)
                _expect(
                    row,
                    "plain_error",
                    not result["stdout"] and result["stderr"].startswith("yescc:"),
                )
                _expect(row, "no_report", not (cwd / ".yescc").exists())
                _expect(row, "source_unchanged", _sha(file) == before)

            harness.check("json_is_language_value", language_json_value)

            def program_json_argument(row: dict[str, Any], cwd: Path) -> None:
                file = cwd / "args.py"
                file.write_text("import sys;print(sys.argv[1])\n", encoding="utf-8")
                before = _sha(file)
                result = harness.call(row, ["run", file.name, "--offline", "--", "--json"], cwd)
                row["source_sha256_before"] = before
                row["source_sha256_after"] = _sha(file)
                harness.report(row, cwd, "args")
                _expect(row, "exit_code", result["exit_code"] == 0)
                _expect(row, "argument", result["stdout"] == "--json\n")
                _expect(row, "stderr_empty", not result["stderr"])
                _expect(row, "source_unchanged", _sha(file) == before)

            harness.check("program_json_argument", program_json_argument)
            return {
                "schema_version": "1",
                "classification": "developer_binary_smoke",
                "version": expected_version,
                "platform": platform.system(),
                "machine": platform.machine(),
                "binary": binary.name,
                "binary_sha256": _sha(binary),
                "outside_repository": True,
                "tested_without_repository_or_venv_on_path": True,
                "external_toolchains_required": ["gcc", "python3/python", "node"],
                "inherited_python_environment_removed": True,
                "model_mode": "local_fake_only",
                "paid_model_calls": 0,
                "model_requests_received": len(server.requests),
                "total": len(harness.rows),
                "passed": sum(row["passed"] for row in harness.rows),
                "rows": harness.rows,
            }
        finally:
            server.stop()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = verify(args.binary, args.expected_version)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({key: value for key, value in result.items() if key != "rows"}))
    return 0 if result["passed"] == result["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
