"""M5 独立负例：不同输入/环境/协议失败，不用冷暖重试凑样本数。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FailureCase:
    name: str
    suffix: str
    source: str | bytes
    exit_code: int = 102
    status: str | None = "native_failed"
    flags: tuple[str, ...] = ("--strict", "--offline")
    condition: str = "normal"
    response: object = None


CASES = (
    FailureCase("py_missing_colon", ".py", "if True\n    print(1)\n"),
    FailureCase("py_bad_indent", ".py", "if True:\n    print(1)\n  print(2)\n"),
    FailureCase("py_unclosed_string", ".py", "text = 'unterminated\n"),
    FailureCase("py_unclosed_bracket", ".py", "values = [1, 2\n"),
    FailureCase("py_invalid_assignment", ".py", "1 = 2\n"),
    FailureCase("js_missing_brace", ".js", "function greeting() {\n"),
    FailureCase("js_const_without_value", ".js", "const missing;\n"),
    FailureCase("js_incomplete_expression", ".js", "let value = ;\n"),
    FailureCase("js_unclosed_string", ".js", 'console.log("unterminated);\n'),
    FailureCase("c_missing_semicolon", ".c", "int main(void){return 0}\n"),
    FailureCase("c_incomplete_expression", ".c", "int main(void){return 1 + ;}\n"),
    FailureCase("c_unknown_type", ".c", "int main(void){unknown_type value;return 0;}\n"),
    FailureCase("c_undefined_symbol", ".c", "int main(void){return missing_symbol;}\n"),
    FailureCase("c_no_entrypoint", ".c", "int library_value = 1;\n"),
    FailureCase(
        "missing_header",
        ".c",
        '#include "yescc_missing_header.h"\nint main(void){return 0;}\n',
        status="failed",
        flags=("--offline",),
    ),
    FailureCase(
        "missing_library",
        ".c",
        "int main(void){return 11;}\n",
        status="failed",
        flags=("--offline", "-lyescc_missing_library"),
    ),
    FailureCase(
        "missing_compiler",
        ".c",
        "int main(void){return 12;}\n",
        status="failed",
        flags=("--offline",),
        condition="missing_compiler",
    ),
    FailureCase(
        "missing_output_directory",
        ".c",
        "int main(void){return 13;}\n",
        status="failed",
        flags=("--offline", "-o", "missing-parent/out.exe"),
    ),
    FailureCase("unsupported_encoding", ".py", b"\xff\xfeprint(14)\n", exit_code=101, status=None),
    FailureCase(
        "unsupported_language",
        ".rs",
        'fn main() { println!("audit"); }\n',
        exit_code=101,
        status=None,
    ),
    FailureCase(
        "invalid_model_json",
        ".c",
        "int main(void){return json_missing_symbol;}\n",
        exit_code=103,
        status="failed",
        flags=("--no-fallback", "--max-rounds", "1"),
        response="not a JSON response",
    ),
    FailureCase(
        "mismatched_model_patch",
        ".c",
        "int main(void){return patch_missing_symbol;}\n",
        exit_code=103,
        status="failed",
        flags=("--no-fallback", "--max-rounds", "1"),
        response={"diff": "@@ -1 +1 @@\n-not the source\n+int main(void){return 0;}\n"},
    ),
    FailureCase(
        "empty_model_patch",
        ".c",
        "int main(void){return empty_missing_symbol;}\n",
        exit_code=103,
        status="failed",
        flags=("--no-fallback", "--max-rounds", "1"),
        response={"diff": ""},
    ),
    FailureCase(
        "budget_blocked",
        ".c",
        "int main(void){int budget_probe=1 return budget_probe;}\n",
        exit_code=103,
        status="failed",
        flags=("--no-fallback", "--budget", "0.0000001"),
    ),
)
