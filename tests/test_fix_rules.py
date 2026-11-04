"""规则护栏、候选生成与兜底输出的单元测试。"""

from __future__ import annotations

from yescc.fallback import fallback_output, generate_c_program
from yescc.fix import generate_candidates, parse_c
from yescc.fix.lexer import protected_spans, unterminated_quote


def test_string_content_is_protected() -> None:
    source = 'printf("你好，世界"); return 0;'
    spans = protected_spans(source)
    start = source.index("你好")
    assert any(span.start <= start < span.end for span in spans)


def test_fullwidth_fix_skips_strings_and_comments() -> None:
    source = 'printf("你好，世界"); // 注释（不要改）\nint x ＝ 1；'
    candidates = generate_candidates(source)
    assert candidates
    patched = candidates[0].text
    assert "你好，世界" in patched
    assert "注释（不要改）" in patched
    assert "int x = 1;" in patched


def test_missing_brace_candidate_appends_closer() -> None:
    source = "int main(void) { return 0;"
    candidates = generate_candidates(source)
    assert any(candidate.text.rstrip().endswith("}") for candidate in candidates)


def test_missing_quote_is_detected() -> None:
    assert unterminated_quote('puts("hi);') is not None
    assert unterminated_quote('puts("hi");') is None


def test_typo_candidate() -> None:
    candidates = generate_candidates("int main(void) { prinf(\"a\"); return 0; }")
    assert any("printf" in candidate.text for candidate in candidates)


def test_parse_c_reports_errors() -> None:
    assert parse_c("int main( {").has_error is True
    assert parse_c("int main(void) { return 0; }").has_error is False


def test_fallback_easter_egg() -> None:
    output = fallback_output("天王盖地虎")
    assert output.kind == "easter_egg"
    assert output.message == "宝塔镇河妖"


def test_fallback_program_escapes_text() -> None:
    output = fallback_output('say "hi"\nnext')
    program = generate_c_program(output.message)
    assert '#include <stdio.h>' in program
    assert '\\"hi\\"' in program
    assert "\\nnext" in program
