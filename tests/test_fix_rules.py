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
    candidates = generate_candidates('int main(void) { prinf("a"); return 0; }')
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
    assert "#include <stdio.h>" in program
    assert '\\"hi\\"' in program
    assert "\\nnext" in program


def test_cpp_hint_ignores_strings_and_comments() -> None:
    from yescc.fallback.guidance import looks_like_cpp

    assert looks_like_cpp('int main(void){std::cout << "hello";}')
    assert not looks_like_cpp('puts("cout << hello"); // std::cin >> x\n')


def test_macro_and_control_headers_are_not_semicolon_targets() -> None:
    from yescc.fix.c_rules import insert_missing_semicolons
    from yescc.fix.feedback import semicolon_from_diagnostics

    macro = "#define SHOW(x) \\\n  puts(x)\n"
    assert insert_missing_semicolons(macro) == macro
    for line in ("for(int i=0;i<2;i++)", "#define X 1", '// a "', '/* a " */'):
        source = line + "\nnext();\n"
        diagnostic = "main.c:2:1: error: expected ';' before 'next'\n"
        assert semicolon_from_diagnostics(source, diagnostic, "main.c") is None
    assert semicolon_from_diagnostics("int x=2\nnext();\n", diagnostic, "other.c") is None


def test_smart_quote_contents_are_preserved() -> None:
    source = "int main(void){puts(“prinf，hello；”);return 0;}"
    assert any('"prinf，hello；"' in candidate.text for candidate in generate_candidates(source))


def test_rule_compile_budget_is_enforced(tmp_path, monkeypatch) -> None:
    from yescc.adapters.base import BuildResult
    from yescc.fix import engine
    from yescc.fix.c_rules import Candidate

    failed = engine.TempBuild(BuildResult([], 1, "", "error", None, 0), None)
    monkeypatch.setattr(
        engine,
        "generate_candidates",
        lambda text: [Candidate(f"bad {i}", (), 1) for i in range(100)],
    )
    monkeypatch.setattr(engine, "build_in_temp", lambda *args: failed)
    result = engine.attempt_repair(None, "source", "main.c", [], tmp_path, tmp_path)
    assert len(result.attempts) == engine.MAX_RULE_BUILDS == 24
    assert result.candidate is None and result.artifact is None


def test_fixit_uses_utf8_bytes_and_rejects_header_comment_or_other_file() -> None:
    from yescc.fix.feedback import semicolon_from_diagnostics

    source = 'puts("你好") return 0;\n'
    column = len('puts("你好") '.encode()) + 1
    diagnostic = f'fix-it:"main.c":{{1:{column}-1:{column}}}:";"\n'
    assert semicolon_from_diagnostics(source, diagnostic, "main.c") == 'puts("你好") ;return 0;\n'
    assert semicolon_from_diagnostics(source, diagnostic, "header.h") is None
    for text in ("for(int i=0 i<3;i++){}", "// comment"):
        diagnostic = 'fix-it:"main.c":{1:8-1:8}:";"\n'
        assert semicolon_from_diagnostics(text, diagnostic, "main.c") is None


def test_ast_does_not_insert_for_initializer_semicolon() -> None:
    from yescc.fix.c_rules import insert_missing_semicolons

    source = "int main(void){for(int i=0 i<3;i++){}return 0;}\n"
    assert insert_missing_semicolons(source) == source


def test_refinement_depth_is_bounded(tmp_path, monkeypatch) -> None:
    from yescc.adapters.base import BuildResult
    from yescc.fix import engine

    failed = engine.TempBuild(BuildResult([], 1, "", "error", None, 0), None)
    monkeypatch.setattr(engine, "generate_candidates", lambda text: [])
    monkeypatch.setattr(engine, "semicolon_from_diagnostics", lambda text, *args: text + " ")
    monkeypatch.setattr(engine, "insert_missing_semicolons", lambda text: text)
    monkeypatch.setattr(engine, "build_in_temp", lambda *args: failed)
    result = engine.attempt_repair(None, "source", "main.c", [], tmp_path, tmp_path)
    assert len(result.attempts) == engine.MAX_REFINEMENTS + 1


def test_length_changing_typos_do_not_shift_protected_text() -> None:
    source = 'int mian(void){prinf(0);prinf(0);prinf(0);prinf(0);puts("prinf");retrun 0;}'
    candidates = generate_candidates(source)
    assert any(candidate.text.count("printf(0)") == 4 for candidate in candidates)
    assert all('puts("prinf")' in candidate.text for candidate in candidates)
    assert any("return 0;" in candidate.text for candidate in candidates)
