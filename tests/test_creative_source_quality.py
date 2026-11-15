"""创意源码护栏只检查有限的词法风险，不代替工具链或运行检查。"""

from __future__ import annotations

import pytest

from yescc.fallback.quality import creative_source_issues

CHARACTER_REASON = ["non_ascii_character_literal"]


@pytest.mark.parametrize("literal", [
    "'☀'", "'🦆'", "'é'", "'a☀'", r"'\u2600'", r"'\U0001F986'",
    r"'\u0080'", r"'\U00000080'",
])
def test_non_ascii_ordinary_character_literals_are_rejected(literal: str) -> None:
    assert creative_source_issues(f"int main(void) {{ char c = {literal}; }}") == CHARACTER_REASON


@pytest.mark.parametrize("source", [
    'const char *frames[] = {"☀", "🦆", "雨"};',
    'const char frame[] = u8"☀";',
    'puts("the character \'☀\' is printed as a string");',
    'puts("escaped quote: \\\" \'☀\'");',
    "/* char c = '☀'; */ char c = 'a';",
    "// char c = '🦆';\nchar c = 'a';",
    r"char c = '\''; /* '☀' */",
    r"char c = '\\'; /* '☀' */",
    r'''char c = '"'; const char *s = "'☀'";''',
    r"char c = '\\u2600';",
    r"char c = '\\U0001F986';",
])
def test_comments_strings_and_escaped_quotes_do_not_create_character_issues(source: str) -> None:
    assert creative_source_issues(source) == []


@pytest.mark.parametrize("literal", [
    "'a'", "' '", "'~'", "'\x7f'", r"'\n'", r"'\0'", r"'\177'",
    r"'\200'", r"'\377'", r"'\x80'", r"'\xFF'", r"'\u007f'", r"'\U0000007F'",
])
def test_ascii_and_explicit_byte_escapes_remain_allowed(literal: str) -> None:
    assert creative_source_issues(f"char c = {literal};") == []


@pytest.mark.parametrize("prefix", ["L", "u", "U", "u8"])
@pytest.mark.parametrize("literal", ["'☀'", "'🦆'", r"'\u2600'", r"'\U0001F986'"])
def test_encoding_prefixed_character_literals_are_not_ordinary(prefix: str, literal: str) -> None:
    assert creative_source_issues(f"int c = {prefix}{literal};") == []


@pytest.mark.parametrize("prefix", [
    "fooL", "_L", "foou", "fooU", "foou8", "0L", "字L", "a\u0301L", "a²L",
    "L ", "u/*gap*/", "U\n", "u8 ",
])
def test_only_an_adjacent_complete_prefix_exempts_a_literal(prefix: str) -> None:
    # Identifier suffixes and separated tokens cannot become encoding prefixes.
    assert creative_source_issues(f"{prefix}'☀'") == CHARACTER_REASON


def test_escaped_quote_does_not_hide_a_later_ordinary_character() -> None:
    source = r"char quote = '\''; char slash = '\\'; char sun = '\u2600';"
    assert creative_source_issues(source) == CHARACTER_REASON


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_character_guard_respects_c_line_splicing(newline: str) -> None:
    assert creative_source_issues("// continued comment \\" + newline + "'☀'") == []
    assert creative_source_issues("/\\" + newline + "* '☀' */") == []
    assert creative_source_issues("char c = '\\" + newline + "☀';") == CHARACTER_REASON
    assert creative_source_issues("int c = u\\" + newline + "8'☀';") == []
    assert creative_source_issues("char c = '\\u26\\" + newline + "00';") == CHARACTER_REASON
    assert creative_source_issues('puts("\\' + newline + "'☀'\");") == []


def test_mixed_stream_guard_remains_independent_and_reasons_are_stable_and_unique() -> None:
    source = "char a = '☀', b = '🦆'; puts(\"title\"); wprintf(L\"body\"); putchar(a);"
    assert creative_source_issues(source) == [
        "mixed_stdout_orientation", "non_ascii_character_literal",
    ]
    assert creative_source_issues('puts("title"); wprintf(L"body");') == [
        "mixed_stdout_orientation",
    ]
    assert creative_source_issues('/* wprintf( */ puts("wprintf( \'☀\'");') == []
