"""合法程序样本：用于验证「合法输入零干扰」。

判定方式不是硬编码期望输出，而是把同一份源码分别交给原生工具链与本工具，
比较 stdout / stderr / 退出码，并检查源码未被修改、未调用模型。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class LegalCase:
    """一个合法程序样本。"""

    name: str
    lang: str
    source: str
    stdin: str = ""
    args: tuple[str, ...] = ()
    files: dict[str, str] = field(default_factory=dict)


CASES: tuple[LegalCase, ...] = (
    # ---------------- Python ----------------
    LegalCase("py_hello", "python", "print('hello')\n"),
    LegalCase("py_arithmetic", "python", "print(2 + 3 * 4)\n"),
    LegalCase("py_fstring", "python", "value = 1 + 1\nprint(f'value={value}')\n"),
    LegalCase("py_loop_sum", "python", "print(sum(range(10)))\n"),
    LegalCase(
        "py_function",
        "python",
        """\
def add(a, b):
    return a + b
print(add(2, 3))
""",
    ),
    LegalCase(
        "py_recursion",
        "python",
        """\
def factorial(n):
    return 1 if n <= 1 else n * factorial(n - 1)
print(factorial(5))
""",
    ),
    LegalCase("py_sorted", "python", "print(sorted([3, 1, 2]))\n"),
    LegalCase(
        "py_dict",
        "python",
        """\
values = {'a': 1, 'b': 2}
print(values['a'] + values['b'])
""",
    ),
    LegalCase(
        "py_class",
        "python",
        """\
class Greeter:
    def __init__(self, name):
        self.name = name

    def greet(self):
        return f'hi {self.name}'

print(Greeter('yescc').greet())
""",
    ),
    LegalCase(
        "py_args",
        "python",
        """\
import sys
print(' '.join(sys.argv[1:]))
""",
        args=("alpha", "beta"),
    ),
    LegalCase(
        "py_stdin_line",
        "python",
        """\
print(input().upper())
""",
        stdin="abc\n",
    ),
    LegalCase(
        "py_stdin_numbers",
        "python",
        """\
import sys
print(sum(int(value) for value in sys.stdin.read().split()))
""",
        stdin="1 2 3\n",
    ),
    LegalCase("py_unicode", "python", "print('你好，世界')\n"),
    LegalCase(
        "py_file_roundtrip",
        "python",
        """\
with open('data.txt', 'w', encoding='utf-8') as handle:
    handle.write('hello file\\n')
with open('data.txt', encoding='utf-8') as handle:
    print(handle.read().strip())
""",
    ),
    LegalCase(
        "py_read_file",
        "python",
        """\
with open('input.txt', encoding='utf-8') as handle:
    print(handle.read().strip())
""",
        files={"input.txt": "from file\n"},
    ),
    LegalCase(
        "py_exception",
        "python",
        """\
try:
    1 / 0
except ZeroDivisionError:
    print('caught')
""",
    ),
    LegalCase("py_comprehension", "python", "print([value * value for value in range(5)])\n"),
    LegalCase("py_slice", "python", "print('abcdef'[1:4])\n"),
    LegalCase("py_float_format", "python", "print(f'{3.14159:.2f}')\n"),
    LegalCase("py_bool", "python", "print(True and not False)\n"),
    LegalCase("py_tuple", "python", "a, b = (1, 2)\nprint(a + b)\n"),
    LegalCase("py_enumerate", "python", "print(list(enumerate('ab')))\n"),
    LegalCase("py_set", "python", "print(sorted({3, 1, 2}))\n"),
    LegalCase(
        "py_json",
        "python",
        """\
import json
print(json.dumps({'b': 1, 'a': 2}, sort_keys=True))
""",
    ),
    LegalCase(
        "py_math",
        "python",
        """\
import math
print(math.isqrt(17))
""",
    ),
    LegalCase("py_string", "python", "print('  Hi  '.strip().lower())\n"),
    LegalCase(
        "py_generator",
        "python",
        """\
def generate():
    yield from range(3)

print(list(generate()))
""",
    ),
    LegalCase("py_lambda", "python", "print(list(map(lambda value: value + 1, [1, 2])))\n"),
    LegalCase("py_exit_zero", "python", "print('done')\n"),
    LegalCase(
        "py_exit_three",
        "python",
        """\
import sys
print('bye')
sys.exit(3)
""",
    ),
    # ---------------- C ----------------
    LegalCase(
        "c_hello",
        "c",
        """\
#include <stdio.h>

int main(void) {
    printf("hello\\n");
    return 0;
}
""",
    ),
    LegalCase(
        "c_arithmetic",
        "c",
        """\
#include <stdio.h>

int main(void) {
    printf("%d\\n", 2 + 3 * 4);
    return 0;
}
""",
    ),
    LegalCase(
        "c_loop_sum",
        "c",
        """\
#include <stdio.h>

int main(void) {
    int total = 0;
    for (int value = 0; value < 10; value++) {
        total += value;
    }
    printf("%d\\n", total);
    return 0;
}
""",
    ),
    LegalCase(
        "c_function",
        "c",
        """\
#include <stdio.h>

static int add(int a, int b) {
    return a + b;
}

int main(void) {
    printf("%d\\n", add(2, 3));
    return 0;
}
""",
    ),
    LegalCase(
        "c_recursion",
        "c",
        """\
#include <stdio.h>

static int factorial(int n) {
    return n <= 1 ? 1 : n * factorial(n - 1);
}

int main(void) {
    printf("%d\\n", factorial(5));
    return 0;
}
""",
    ),
    LegalCase(
        "c_array_sum",
        "c",
        """\
#include <stdio.h>

int main(void) {
    int values[] = {1, 2, 3, 4};
    int total = 0;
    for (int index = 0; index < 4; index++) {
        total += values[index];
    }
    printf("%d\\n", total);
    return 0;
}
""",
    ),
    LegalCase(
        "c_struct",
        "c",
        """\
#include <stdio.h>

struct Point {
    int x;
    int y;
};

int main(void) {
    struct Point point = {2, 3};
    printf("%d\\n", point.x + point.y);
    return 0;
}
""",
    ),
    LegalCase(
        "c_pointer",
        "c",
        """\
#include <stdio.h>

int main(void) {
    int value = 7;
    int *pointer = &value;
    *pointer += 1;
    printf("%d\\n", value);
    return 0;
}
""",
    ),
    LegalCase(
        "c_strlen",
        "c",
        """\
#include <stdio.h>
#include <string.h>

int main(void) {
    printf("%zu\\n", strlen("hello"));
    return 0;
}
""",
    ),
    LegalCase(
        "c_args",
        "c",
        """\
#include <stdio.h>

int main(int argc, char **argv) {
    for (int index = 1; index < argc; index++) {
        printf("%s%s", argv[index], index + 1 < argc ? " " : "\\n");
    }
    return 0;
}
""",
        args=("alpha", "beta"),
    ),
    LegalCase(
        "c_stdin_scan",
        "c",
        """\
#include <stdio.h>

int main(void) {
    int value;
    int total = 0;
    while (scanf("%d", &value) == 1) {
        total += value;
    }
    printf("%d\\n", total);
    return 0;
}
""",
        stdin="1 2 3\n",
    ),
    LegalCase(
        "c_stdin_chars",
        "c",
        """\
#include <stdio.h>

int main(void) {
    int value;
    int count = 0;
    while ((value = getchar()) != EOF) {
        count++;
    }
    printf("%d\\n", count);
    return 0;
}
""",
        stdin="abc\ndef\n",
    ),
    LegalCase(
        "c_unicode",
        "c",
        """\
#include <stdio.h>

int main(void) {
    printf("你好，世界\\n");
    return 0;
}
""",
    ),
    LegalCase(
        "c_file_roundtrip",
        "c",
        """\
#include <stdio.h>

int main(void) {
    FILE *handle = fopen("data.txt", "w");
    if (handle == NULL) {
        return 1;
    }
    fputs("hello file\\n", handle);
    fclose(handle);

    char buffer[64];
    handle = fopen("data.txt", "r");
    if (handle == NULL) {
        return 1;
    }
    if (fgets(buffer, sizeof buffer, handle) == NULL) {
        return 1;
    }
    fclose(handle);
    printf("%s", buffer);
    return 0;
}
""",
    ),
    LegalCase(
        "c_enum",
        "c",
        """\
#include <stdio.h>

enum Color {
    RED,
    GREEN = 5,
    BLUE
};

int main(void) {
    printf("%d\\n", GREEN + BLUE);
    return 0;
}
""",
    ),
    LegalCase(
        "c_switch",
        "c",
        """\
#include <stdio.h>

int main(void) {
    int value = 2;
    int result = 0;
    switch (value) {
        case 1:
            result = 10;
            break;
        case 2:
            result = 20;
            break;
        default:
            result = 30;
    }
    printf("%d\\n", result);
    return 0;
}
""",
    ),
    LegalCase(
        "c_malloc",
        "c",
        """\
#include <stdio.h>
#include <stdlib.h>

int main(void) {
    int *values = malloc(3 * sizeof(int));
    if (values == NULL) {
        return 1;
    }
    for (int index = 0; index < 3; index++) {
        values[index] = index * index;
    }
    printf("%d\\n", values[0] + values[1] + values[2]);
    free(values);
    return 0;
}
""",
    ),
    LegalCase(
        "c_typedef",
        "c",
        """\
#include <stdio.h>

typedef struct {
    int x;
    int y;
} Point;

int main(void) {
    Point point = {2, 3};
    printf("%d\\n", point.x + point.y);
    return 0;
}
""",
    ),
    LegalCase(
        "c_bitwise",
        "c",
        """\
#include <stdio.h>

int main(void) {
    printf("%d\\n", (5 & 3) | (8 >> 2));
    return 0;
}
""",
    ),
    LegalCase(
        "c_char_compare",
        "c",
        """\
#include <stdio.h>

int main(void) {
    char value = 'a';
    printf("%d\\n", value == 'a');
    return 0;
}
""",
    ),
    LegalCase(
        "c_float_format",
        "c",
        """\
#include <stdio.h>

int main(void) {
    printf("%.2f\\n", 3.14159);
    return 0;
}
""",
    ),
    LegalCase(
        "c_sizeof",
        "c",
        """\
#include <stdio.h>

int main(void) {
    printf("%zu\\n", sizeof(int));
    return 0;
}
""",
    ),
    LegalCase(
        "c_qsort",
        "c",
        """\
#include <stdio.h>
#include <stdlib.h>

static int compare(const void *left, const void *right) {
    return (*(const int *)left) - (*(const int *)right);
}

int main(void) {
    int values[] = {3, 1, 2};
    qsort(values, 3, sizeof(int), compare);
    for (int index = 0; index < 3; index++) {
        printf("%d%s", values[index], index == 2 ? "\\n" : " ");
    }
    return 0;
}
""",
    ),
    LegalCase(
        "c_strcpy",
        "c",
        """\
#include <stdio.h>
#include <string.h>

int main(void) {
    char buffer[16];
    strcpy(buffer, "hi");
    strcat(buffer, "!");
    printf("%s\\n", buffer);
    return 0;
}
""",
    ),
    LegalCase(
        "c_static_fn",
        "c",
        """\
#include <stdio.h>

static int twice(int value) {
    return value * 2;
}

int main(void) {
    printf("%d\\n", twice(21));
    return 0;
}
""",
    ),
    LegalCase(
        "c_do_while",
        "c",
        """\
#include <stdio.h>

int main(void) {
    int value = 0;
    do {
        value++;
    } while (value < 3);
    printf("%d\\n", value);
    return 0;
}
""",
    ),
    LegalCase(
        "c_ternary",
        "c",
        """\
#include <stdio.h>

int main(void) {
    printf("%s\\n", 3 > 2 ? "yes" : "no");
    return 0;
}
""",
    ),
    LegalCase(
        "c_exit_three",
        "c",
        """\
#include <stdio.h>

int main(void) {
    printf("bye\\n");
    return 3;
}
""",
    ),
    LegalCase(
        "c_stderr",
        "c",
        """\
#include <stdio.h>

int main(void) {
    fprintf(stderr, "warn\\n");
    printf("ok\\n");
    return 0;
}
""",
    ),
    LegalCase(
        "c_abs",
        "c",
        """\
#include <stdio.h>
#include <stdlib.h>

int main(void) {
    printf("%d\\n", abs(-5));
    return 0;
}
""",
    ),
    # ---------------- JavaScript ----------------
    LegalCase("js_hello", "javascript", "console.log('hello');\n"),
    LegalCase("js_arithmetic", "javascript", "console.log(2 + 3 * 4);\n"),
    LegalCase(
        "js_template",
        "javascript",
        "const value = 1 + 1;\nconsole.log(`value=${value}`);\n",
    ),
    LegalCase(
        "js_loop_sum",
        "javascript",
        "let total = 0;\nfor (let i = 0; i < 10; i++) total += i;\nconsole.log(total);\n",
    ),
    LegalCase(
        "js_arrow",
        "javascript",
        "const add = (a, b) => a + b;\nconsole.log(add(2, 3));\n",
    ),
    LegalCase(
        "js_recursion",
        "javascript",
        """\
function factorial(n) {
  return n <= 1 ? 1 : n * factorial(n - 1);
}
console.log(factorial(5));
""",
    ),
    LegalCase(
        "js_array_ops",
        "javascript",
        """\
const values = [1, 2, 3, 4];
console.log(values.filter((v) => v % 2 === 0).map((v) => v * 10).join(','));
""",
    ),
    LegalCase(
        "js_object_json",
        "javascript",
        "console.log(JSON.stringify({ b: 1, a: 2 }, ['a', 'b']));\n",
    ),
    LegalCase(
        "js_class",
        "javascript",
        """\
class Point {
  constructor(x, y) {
    this.x = x;
    this.y = y;
  }

  sum() {
    return this.x + this.y;
  }
}
console.log(new Point(2, 3).sum());
""",
    ),
    LegalCase(
        "js_args",
        "javascript",
        "console.log(process.argv.slice(2).join(' '));\n",
        args=("alpha", "beta"),
    ),
    LegalCase(
        "js_stdin",
        "javascript",
        """\
const fs = require('fs');
const text = fs.readFileSync(0, 'utf8').trim();
console.log(text.toUpperCase());
""",
        stdin="abc\n",
    ),
    LegalCase("js_unicode", "javascript", "console.log('你好，世界');\n"),
    LegalCase(
        "js_file_roundtrip",
        "javascript",
        """\
const fs = require('fs');
fs.writeFileSync('data.txt', 'hello file\\n');
console.log(fs.readFileSync('data.txt', 'utf8').trim());
""",
    ),
    LegalCase(
        "js_try",
        "javascript",
        """\
try {
  throw new Error('boom');
} catch (error) {
  console.log('caught');
}
""",
    ),
    LegalCase(
        "js_destructure",
        "javascript",
        "const { a, b } = { a: 1, b: 2 };\nconsole.log(a + b);\n",
    ),
    LegalCase(
        "js_spread",
        "javascript",
        "const values = [1, 2];\nconsole.log([...values, 3].join('-'));\n",
    ),
    LegalCase(
        "js_async",
        "javascript",
        """\
async function main() {
  const value = await Promise.resolve(42);
  console.log(value);
}
main();
""",
    ),
    LegalCase("js_number_format", "javascript", "console.log((3.14159).toFixed(2));\n"),
    LegalCase("js_string", "javascript", "console.log('  Hi  '.trim().toLowerCase());\n"),
    LegalCase(
        "js_map_set",
        "javascript",
        """\
const values = new Map([['a', 1], ['b', 2]]);
const unique = new Set([1, 1, 2]);
console.log(values.get('b') + unique.size);
""",
    ),
    LegalCase(
        "js_reduce",
        "javascript",
        "console.log([1, 2, 3].reduce((total, value) => total + value, 0));\n",
    ),
    LegalCase(
        "js_closure",
        "javascript",
        """\
function make(value) {
  return () => value * 2;
}
const double = make(4);
console.log(double());
""",
    ),
    LegalCase("js_ternary", "javascript", "console.log(3 > 2 ? 'yes' : 'no');\n"),
    LegalCase(
        "js_json_parse",
        "javascript",
        "const value = JSON.parse('{\"a\":1,\"b\":2}');\nconsole.log(value.a + value.b);\n",
    ),
    LegalCase("js_math", "javascript", "console.log(Math.max(1, 5, 2));\n"),
    LegalCase(
        "js_exit_three",
        "javascript",
        "console.log('bye');\nprocess.exit(3);\n",
    ),
    LegalCase(
        "js_stderr",
        "javascript",
        "console.error('warn');\nconsole.log('ok');\n",
    ),
    LegalCase(
        "js_sort",
        "javascript",
        "console.log([3, 1, 2].sort((a, b) => a - b).join(','));\n",
    ),
    LegalCase(
        "js_entries",
        "javascript",
        "console.log(Object.entries({ a: 1, b: 2 }).map(([k, v]) => `${k}${v}`).join(','));\n",
    ),
    LegalCase(
        "js_promise",
        "javascript",
        "Promise.resolve('ok').then((value) => console.log(value));\n",
    ),
)


def cases_for(language: str) -> list[LegalCase]:
    """返回指定语言的样本。"""
    return [case for case in CASES if case.lang == language]
