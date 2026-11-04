"""受控低级错误样本：规则修复后必须通过真实编译并保持意图。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BrokenCase:
    name: str
    source: str
    expected_stdout: str
    args: tuple[str, ...] = ()
    stdin: str = ""


CASES: tuple[BrokenCase, ...] = (
    # ---------------- 全角标点 ----------------
    BrokenCase(
        "fw_paren_semicolon",
        """\
#include <stdio.h>
int main（）{ printf（"a\\n"）； return 0； }
""",
        "a\n",
    ),
    BrokenCase(
        "fw_comma_args",
        """\
#include <stdio.h>
int main(void) { printf("%d\\n"， 5); return 0; }
""",
        "5\n",
    ),
    BrokenCase(
        "fw_ternary",
        """\
#include <stdio.h>
int main(void) { printf("%d\\n", 1 ？ 2 ： 3); return 0; }
""",
        "2\n",
    ),
    BrokenCase(
        "fw_assign",
        """\
#include <stdio.h>
int main(void) { int x ＝ 4; printf("%d\\n", x); return 0; }
""",
        "4\n",
    ),
    BrokenCase(
        "fw_plus",
        """\
#include <stdio.h>
int main(void) { printf("%d\\n", 1 ＋ 2); return 0; }
""",
        "3\n",
    ),
    BrokenCase(
        "fw_minus",
        """\
#include <stdio.h>
int main(void) { printf("%d\\n", 5 － 2); return 0; }
""",
        "3\n",
    ),
    BrokenCase(
        "fw_star",
        """\
#include <stdio.h>
int main(void) { printf("%d\\n", 2 ＊ 3); return 0; }
""",
        "6\n",
    ),
    BrokenCase(
        "fw_slash",
        """\
#include <stdio.h>
int main(void) { printf("%d\\n", 6 ／ 2); return 0; }
""",
        "3\n",
    ),
    BrokenCase(
        "fw_less",
        """\
#include <stdio.h>
int main(void) { printf("%d\\n", 1 ＜ 2); return 0; }
""",
        "1\n",
    ),
    BrokenCase(
        "fw_greater",
        """\
#include <stdio.h>
int main(void) { printf("%d\\n", 2 ＞ 1); return 0; }
""",
        "1\n",
    ),
    BrokenCase(
        "fw_amp",
        """\
#include <stdio.h>
int main(void) { printf("%d\\n", 1 ＆ 3); return 0; }
""",
        "1\n",
    ),
    BrokenCase(
        "fw_pipe",
        """\
#include <stdio.h>
int main(void) { printf("%d\\n", 1 ｜ 2); return 0; }
""",
        "3\n",
    ),
    BrokenCase(
        "fw_percent",
        """\
#include <stdio.h>
int main(void) { printf("%d\\n", 5 ％ 2); return 0; }
""",
        "1\n",
    ),
    BrokenCase(
        "fw_bang",
        """\
#include <stdio.h>
int main(void) { printf("%d\\n", ！0); return 0; }
""",
        "1\n",
    ),
    BrokenCase(
        "fw_brackets",
        """\
#include <stdio.h>
int main(void) { int a［2］ = {1, 2}; printf("%d\\n", a[0] + a[1]); return 0; }
""",
        "3\n",
    ),
    BrokenCase(
        "fw_braces",
        """\
#include <stdio.h>
int main(void) { if (1) ｛ printf("x\\n"); ｝ return 0; }
""",
        "x\n",
    ),
    BrokenCase(
        "fw_quotes_string",
        """\
#include <stdio.h>
int main(void) { printf（“a\\n”）； return 0; }
""",
        "a\n",
    ),
    BrokenCase(
        "fw_char_lit",
        """\
#include <stdio.h>
int main(void) { char c ＝ ‘a’; printf("%c\\n", c); return 0; }
""",
        "a\n",
    ),
    BrokenCase(
        "fw_include",
        """\
＃include <stdio.h>
int main(void) { printf("ok\\n"); return 0; }
""",
        "ok\n",
    ),
    BrokenCase(
        "fw_float",
        """\
#include <stdio.h>
int main(void) { printf("%.2f\\n", 3。14); return 0; }
""",
        "3.14\n",
    ),
    BrokenCase(
        "fw_for",
        """\
#include <stdio.h>
int main(void) {
    for （int i = 0; i < 2; i++） printf（"%d\\n", i）；
    return 0;
}
""",
        "0\n1\n",
    ),
    BrokenCase(
        "fw_while",
        """\
#include <stdio.h>
int main(void) {
    int i = 0;
    while （i < 2） ｛ printf（"%d\\n", i）； i++； ｝
    return 0;
}
""",
        "0\n1\n",
    ),
    BrokenCase(
        "fw_array_index",
        """\
#include <stdio.h>
int main(void) { int a[2] = {1, 2}; printf("%d\\n", a［1］); return 0; }
""",
        "2\n",
    ),
    BrokenCase(
        "fw_string_preserved",
        """\
#include <stdio.h>
int main(void) { printf("你好，世界\\n")； return 0; }
""",
        "你好，世界\n",
    ),
    BrokenCase(
        "fw_mixed",
        """\
#include <stdio.h>
int main（void）｛ printf（"%d\\n"， （1 ＋ 2））； return 0； ｝
""",
        "3\n",
    ),
    # ---------------- 缺失右花括号 ----------------
    BrokenCase(
        "mb_return",
        """\
#include <stdio.h>
int main(void) { printf("a\\n"); return 0;
""",
        "a\n",
    ),
    BrokenCase(
        "mb_if",
        """\
#include <stdio.h>
int main(void) {
    if (1) { printf("a\\n");
    return 0;
}
""",
        "a\n",
    ),
    BrokenCase(
        "mb_for",
        """\
#include <stdio.h>
int main(void) {
    for (int i = 0; i < 2; i++) { printf("%d\\n", i); }
    return 0;
""",
        "0\n1\n",
    ),
    BrokenCase(
        "mb_two_braces",
        """\
#include <stdio.h>
int main(void) { if (1) { printf("a\\n");
""",
        "a\n",
    ),
    BrokenCase(
        "mb_else",
        """\
#include <stdio.h>
int main(void) {
    if (0) { printf("x\\n"); } else { printf("y\\n");
    return 0;
}
""",
        "y\n",
    ),
    BrokenCase(
        "mb_while",
        """\
#include <stdio.h>
int main(void) {
    int i = 0;
    while (i < 2) { printf("%d\\n", i); i++; }
    return 0;
""",
        "0\n1\n",
    ),
    BrokenCase(
        "mb_function_after",
        """\
#include <stdio.h>
static int f(void) { return 1; }
int main(void) { printf("%d\\n", f()); return 0;
""",
        "1\n",
    ),
    BrokenCase(
        "mb_nested_blocks",
        """\
#include <stdio.h>
int main(void) { { printf("a\\n");
""",
        "a\n",
    ),
    BrokenCase(
        "mb_switch",
        """\
#include <stdio.h>
int main(void) {
    int x = 1;
    switch (x) { case 1: printf("a\\n"); break;
    return 0;
}
""",
        "a\n",
    ),
    BrokenCase(
        "mb_do_while",
        """\
#include <stdio.h>
int main(void) {
    int i = 0;
    do { i++; } while (i < 2);
    if (i == 2) { printf("ok\\n");
    return 0;
}
""",
        "ok\n",
    ),
    # ---------------- 缺失右圆括号 ----------------
    BrokenCase(
        "mp_printf",
        """\
#include <stdio.h>
int main(void) { printf("a\\n"; return 0; }
""",
        "a\n",
    ),
    BrokenCase(
        "mp_expr",
        """\
#include <stdio.h>
int main(void) { int x = (1 + 2; printf("%d\\n", x); return 0; }
""",
        "3\n",
    ),
    BrokenCase(
        "mp_nested_paren",
        """\
#include <stdio.h>
int main(void) { printf("%d\\n", ((1 + 2); return 0; }
""",
        "3\n",
    ),
    BrokenCase(
        "mp_two_calls",
        """\
#include <stdio.h>
int main(void) { printf("%d\\n", 1; printf("%d\\n", 2; return 0; }
""",
        "1\n2\n",
    ),
    BrokenCase(
        "mp_math_group",
        """\
#include <stdio.h>
int main(void) { printf("%d\\n", (1 + 2) * 3; return 0; }
""",
        "9\n",
    ),
    BrokenCase(
        "mp_func_call",
        """\
#include <stdio.h>
static int sum(int a, int b) { return a + b; }
int main(void) { printf("%d\\n", sum(1, 2; return 0; }
""",
        "3\n",
    ),
    BrokenCase(
        "mp_double_group",
        """\
#include <stdio.h>
int main(void) { int x = ((1 + 2; printf("%d\\n", x); return 0; }
""",
        "3\n",
    ),
    BrokenCase(
        "mp_condition",
        """\
#include <stdio.h>
int main(void) { if (1; printf("a\\n"); return 0; }
""",
        "a\n",
    ),
    # ---------------- 缺失引号 ----------------
    BrokenCase(
        "mq_printf",
        """\
#include <stdio.h>
int main(void) { printf("a\\n); return 0; }
""",
        "a\n",
    ),
    BrokenCase(
        "mq_char",
        """\
#include <stdio.h>
int main(void) { char c = 'a; printf("%c\\n", c); return 0; }
""",
        "a\n",
    ),
    BrokenCase(
        "mq_string_assign",
        """\
#include <stdio.h>
int main(void) { char *s = "abc; puts(s); return 0; }
""",
        "abc\n",
    ),
    BrokenCase(
        "mq_puts",
        """\
#include <stdio.h>
int main(void) { puts("hi); return 0; }
""",
        "hi\n",
    ),
    BrokenCase(
        "mq_second_string",
        """\
#include <stdio.h>
int main(void) { printf("%s\\n", "x); return 0; }
""",
        "x\n",
    ),
    # ---------------- 常见拼写 ----------------
    BrokenCase(
        "typo_prinf",
        """\
#include <stdio.h>
int main(void) { prinf("a\\n"); return 0; }
""",
        "a\n",
    ),
    BrokenCase(
        "typo_retrun",
        """\
#include <stdio.h>
int main(void) { printf("a\\n"); retrun 0; }
""",
        "a\n",
    ),
    BrokenCase(
        "typo_whlie",
        """\
#include <stdio.h>
int main(void) {
    int i = 0;
    whlie (i < 2) { printf("%d\\n", i); i++; }
    return 0;
}
""",
        "0\n1\n",
    ),
    # ---------------- 组合 ----------------
    BrokenCase(
        "mixed_fw_missing_brace",
        """\
#include <stdio.h>
int main（）{ printf（"a\\n"）； return 0；
""",
        "a\n",
    ),
    BrokenCase(
        "mixed_typo_fw",
        """\
#include <stdio.h>
int main(void) { prinf（"a\\n"）； return 0； }
""",
        "a\n",
    ),
)
