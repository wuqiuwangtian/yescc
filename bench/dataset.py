"""M4 固定 200 例受控样本；参考程序与独立测试不上传模型。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field

from tests.fixtures.broken_c_cases import CASES as BROKEN
from tests.fixtures.legal_cases import CASES as LEGAL

VERSION = "m4-v1"


@dataclass(frozen=True)
class Check:
    stdin: str = ""
    stdout: str | None = None
    args: tuple[str, ...] = ()
    exit_code: int = 0
    comparison: str = "tokens"


@dataclass(frozen=True)
class Case:
    id: str
    category: str
    lang: str
    source: str
    checks: tuple[Check, ...]
    reference: str = ""
    files: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Task:
    name: str
    spec: str
    body: str
    examples: tuple[tuple[str, str], ...]

    @property
    def reference(self) -> str:
        return (
            "#include <stdio.h>\n#include <string.h>\n#include <ctype.h>\n"
            f"/* {self.spec} */\nint main(void) {{\n{self.body}\nreturn 0;\n}}\n"
        )


def scalar(name: str, spec: str, expression: str, examples: tuple[tuple[str, str], ...]) -> Task:
    return Task(
        name,
        spec,
        'long long n; if (scanf("%lld", &n) != 1) return 0;\n'
        f'printf("%lld\\n", (long long)({expression}));',
        examples,
    )


def pair(name: str, spec: str, expression: str, examples: tuple[tuple[str, str], ...]) -> Task:
    return Task(
        name,
        spec,
        'long long a,b; if (scanf("%lld%lld", &a,&b) != 2) return 0;\n'
        f'printf("%lld\\n", (long long)({expression}));',
        examples,
    )


ARRAY = (
    'long long n,a[100]; if(scanf("%lld",&n)!=1)return 0; for(int i=0;'
    'i<n;i++)scanf("%lld",&a[i]);\n'
)
TEXT = 'char s[1024]=""; fgets(s,sizeof s,stdin); s[strcspn(s,"\\r\\n")]=0;\n'
SORT = (
    "for(int i=0;i<n;i++)for(int j=i+1;j<n;j++)if(a[i]>a[j]){long long t=a[i];a[i]=a[j];a[j]=t;}\n"
)

# 输入输出在被测模型调用前独立固定，至少 3 组；不是被测模型生成的自测。
TASKS: tuple[Task, ...] = (
    pair("sum", "读入两个整数，输出和", "a+b", (("2 3\n", "5"), ("-4 4\n", "0"), ("0 0\n", "0"))),
    pair(
        "difference",
        "读入 a b，输出 a 减 b",
        "a-b",
        (("3 8\n", "-5"), ("-2 -3\n", "1"), ("0 0\n", "0")),
    ),
    pair(
        "product",
        "读入两个整数，输出乘积",
        "a*b",
        (("-3 4\n", "-12"), ("0 7\n", "0"), ("6 6\n", "36")),
    ),
    pair(
        "maximum",
        "读入两个整数，输出最大值",
        "a>b?a:b",
        (("-3 -5\n", "-3"), ("2 9\n", "9"), ("4 4\n", "4")),
    ),
    pair(
        "minimum",
        "读入两个整数，输出最小值",
        "a<b?a:b",
        (("-3 -5\n", "-5"), ("2 9\n", "2"), ("4 4\n", "4")),
    ),
    scalar(
        "absolute",
        "读入绝对值不超过十亿的整数，输出绝对值",
        "n<0?-n:n",
        (("-8\n", "8"), ("0\n", "0"), ("9\n", "9")),
    ),
    scalar(
        "sign",
        "读入整数，负数输出 -1，零输出 0，正数输出 1",
        "(n>0)-(n<0)",
        (("-8\n", "-1"), ("0\n", "0"), ("9\n", "1")),
    ),
    Task(
        "parity",
        "读入整数，偶数输出 EVEN，奇数输出 ODD",
        'long long n;scanf("%lld",&n);printf("%s\\n",n%2?"ODD":"EVEN");',
        (("-3\n", "ODD"), ("0\n", "EVEN"), ("8\n", "EVEN")),
    ),
    scalar(
        "sum_to_n",
        "读入 0 到 10000 的 n，输出 1 到 n 的和",
        "n*(n+1)/2",
        (("0\n", "0"), ("1\n", "1"), ("100\n", "5050")),
    ),
    Task(
        "factorial",
        "读入 0 到 20 的 n，输出 n 的阶乘，0 的阶乘为 1",
        'long long n,r=1;scanf("%lld",&n);for(int i=2;i<=n;i++)r*=i;printf("%lld\\n",r);',
        (("0\n", "1"), ("1\n", "1"), ("10\n", "3628800")),
    ),
    Task(
        "fibonacci",
        "读入 0 到 40 的 n，输出 F(n)，F(0)=0、F(1)=1",
        (
            'long long n,a=0,b=1;scanf("%lld",&n);for(int i=0;i<n;i++){long lo'
            'ng t=a+b;a=b;b=t;}printf("%lld\\n",a);'
        ),
        (("0\n", "0"), ("1\n", "1"), ("10\n", "55")),
    ),
    Task(
        "gcd",
        "读入两个非负整数，输出最大公约数，gcd(0,0)=0",
        (
            'long long a,b;scanf("%lld%lld",&a,&b);while(b){long long t=a%b;a='
            'b;b=t;}printf("%lld\\n",a);'
        ),
        (("12 18\n", "6"), ("0 0\n", "0"), ("0 9\n", "9")),
    ),
    Task(
        "lcm",
        "读入两个非负整数，输出最小公倍数，有零则输出零",
        (
            'long long a,b,x,y;scanf("%lld%lld",&a,&b);x=a;y=b;while(y){long l'
            'ong t=x%y;x=y;y=t;}printf("%lld\\n",x?a/x*b:0);'
        ),
        (("12 18\n", "36"), ("0 9\n", "0"), ("0 0\n", "0")),
    ),
    Task(
        "power",
        "读入整数 a 和 0 到 8 的 b，输出 a 的 b 次方，0 的 0 次方取 1",
        'long long a,b,r=1;scanf("%lld%lld",&a,&b);while(b-->0)r*=a;printf("%lld\\n",r);',
        (("2 8\n", "256"), ("0 0\n", "1"), ("-3 3\n", "-27")),
    ),
    Task(
        "digit_sum",
        "读入绝对值不超过十亿的整数，输出绝对值各位数字之和",
        (
            'long long n,r=0;scanf("%lld",&n);if(n<0)n=-n;do{r+=n%10;n/=10;}wh'
            'ile(n);printf("%lld\\n",r);'
        ),
        (("0\n", "0"), ("-1023\n", "6"), ("999\n", "27")),
    ),
    Task(
        "digit_count",
        "读入绝对值不超过十亿的整数，输出绝对值的十进制位数，0 为一位",
        (
            'long long n,r=0;scanf("%lld",&n);if(n<0)n=-n;do{r++;n/=10;}while('
            'n);printf("%lld\\n",r);'
        ),
        (("0\n", "1"), ("-1023\n", "4"), ("9\n", "1")),
    ),
    Task(
        "reverse_number",
        "读入 0 到十亿的整数，倒转十进制数字并作为整数输出",
        ('long long n,r=0;scanf("%lld",&n);do{r=r*10+n%10;n/=10;}while(n);printf("%lld\\n",r);'),
        (("0\n", "0"), ("1200\n", "21"), ("123\n", "321")),
    ),
    Task(
        "palindrome_number",
        "读入非负整数，是十进制回文输出 1，否则 0",
        (
            'long long n,x,r=0;scanf("%lld",&n);x=n;do{r=r*10+x%10;x/=10;}whil'
            'e(x);printf("%d\\n",r==n);'
        ),
        (("0\n", "1"), ("1221\n", "1"), ("120\n", "0")),
    ),
    Task(
        "prime",
        "读入不超过一百万的整数，是素数输出 1，否则 0",
        (
            'long long n,r=1;scanf("%lld",&n);if(n<2)r=0;for(int d=2;d*d<=n;d+'
            '+)if(n%d==0)r=0;printf("%lld\\n",r);'
        ),
        (("1\n", "0"), ("2\n", "1"), ("49\n", "0"), ("17\n", "1")),
    ),
    Task(
        "divisor_count",
        "读入 1 到 10000 的 n，输出正因数数量",
        (
            'long long n,r=0;scanf("%lld",&n);for(int d=1;d<=n;d++)if(n%d==0)r'
            '++;printf("%lld\\n",r);'
        ),
        (("1\n", "1"), ("12\n", "6"), ("17\n", "2")),
    ),
    scalar(
        "leap_year",
        "读入正整数年份，是公历闰年输出 1，否则 0",
        "n%400==0||(n%4==0&&n%100!=0)",
        (("1900\n", "0"), ("2000\n", "1"), ("2024\n", "1")),
    ),
    Task(
        "triangle",
        "读入三个整数边长，能构成非退化三角形输出 YES，否则 NO",
        (
            'long long a,b,c;scanf("%lld%lld%lld",&a,&b,&c);printf("%s\\n",a>0&'
            '&b>0&&c>0&&a+b>c&&a+c>b&&b+c>a?"YES":"NO");'
        ),
        (("3 4 5\n", "YES"), ("1 2 3\n", "NO"), ("0 5 5\n", "NO")),
    ),
    Task(
        "median_three",
        "读入三个整数，输出中位数",
        'long long n=3,a[3];for(int i=0;i<3;i++)scanf("%lld",&a[i]);'
        + SORT
        + 'printf("%lld\\n",a[1]);',
        (("3 1 2\n", "2"), ("-3 -1 -2\n", "-2"), ("7 7 1\n", "7")),
    ),
    Task(
        "sort_three",
        "读入三个整数，按升序输出，用空格分隔",
        'long long n=3,a[3];for(int i=0;i<3;i++)scanf("%lld",&a[i]);'
        + SORT
        + 'printf("%lld %lld %lld\\n",a[0],a[1],a[2]);',
        (("3 1 2\n", "1 2 3"), ("-3 0 -2\n", "-3 -2 0"), ("7 7 1\n", "1 7 7")),
    ),
    Task(
        "array_sum",
        "读入 n（0到100），再读 n 个整数，输出和，空数组为 0",
        ARRAY + 'long long r=0;for(int i=0;i<n;i++)r+=a[i];printf("%lld\\n",r);',
        (("0\n", "0"), ("4\n-2 0 5 1\n", "4"), ("1\n-8\n", "-8")),
    ),
    Task(
        "array_max",
        "读入 n（1到100），再读 n 个整数，输出最大值",
        ARRAY + 'long long r=a[0];for(int i=1;i<n;i++)if(a[i]>r)r=a[i];printf("%lld\\n",r);',
        (("3\n-5 -2 -9\n", "-2"), ("1\n0\n", "0"), ("4\n1 9 9 2\n", "9")),
    ),
    Task(
        "array_min",
        "读入 n（1到100），再读 n 个整数，输出最小值",
        ARRAY + 'long long r=a[0];for(int i=1;i<n;i++)if(a[i]<r)r=a[i];printf("%lld\\n",r);',
        (("3\n-5 -2 -9\n", "-9"), ("1\n0\n", "0"), ("4\n1 9 9 2\n", "1")),
    ),
    Task(
        "positive_count",
        "读入 n（0到100），再读 n 个整数，输出严格正数个数",
        ARRAY + 'long long r=0;for(int i=0;i<n;i++)if(a[i]>0)r++;printf("%lld\\n",r);',
        (("0\n", "0"), ("4\n-2 0 5 1\n", "2"), ("3\n-1 -2 -3\n", "0")),
    ),
    Task(
        "even_count",
        "读入 n（0到100），再读 n 个整数，输出偶数个数，0 算偶数",
        ARRAY + 'long long r=0;for(int i=0;i<n;i++)if(a[i]%2==0)r++;printf("%lld\\n",r);',
        (("0\n", "0"), ("4\n-2 0 5 1\n", "2"), ("3\n1 3 5\n", "0")),
    ),
    Task(
        "linear_search",
        "读入 n（0到100）、n 个整数、目标值，输出目标首次出现的零基下标，未找到输出 -1",
        ARRAY
        + (
            'long long t,r=-1;scanf("%lld",&t);for(int i=0;i<n;i++)if(a[i]==t)'
            '{r=i;break;}printf("%lld\\n",r);'
        ),
        (("0\n3\n", "-1"), ("4\n2 7 7 1\n7\n", "1"), ("2\n1 2\n9\n", "-1")),
    ),
    Task(
        "reverse_array",
        "读入 n（0到100）及 n 个整数，按逆序输出，空数组输出空行",
        ARRAY + 'for(int i=n-1;i>=0;i--)printf("%lld ",a[i]);puts("");',
        (("0\n", ""), ("1\n4\n", "4"), ("4\n1 -2 3 0\n", "0 3 -2 1")),
    ),
    Task(
        "sort_array",
        "读入 n（0到100）及 n 个整数，升序输出，保留重复值，空数组输出空行",
        ARRAY + SORT + 'for(int i=0;i<n;i++)printf("%lld ",a[i]);puts("");',
        (("0\n", ""), ("1\n4\n", "4"), ("5\n3 -2 3 0 1\n", "-2 0 1 3 3")),
    ),
    Task(
        "unique_count",
        "读入 n（0到100）及 n 个整数，输出不同整数的数量",
        ARRAY
        + SORT
        + 'long long r=0;for(int i=0;i<n;i++)if(i==0||a[i]!=a[i-1])r++;printf("%lld\\n",r);',
        (("0\n", "0"), ("4\n2 2 2 2\n", "1"), ("5\n3 -2 3 0 1\n", "4")),
    ),
    Task(
        "second_largest",
        "读入 n（0到100）及 n 个整数，输出第二大的不同整数，不存在输出 NONE",
        ARRAY
        + SORT
        + (
            'int i=n-2;while(i>=0&&a[i]==a[n-1])i--;if(i<0)puts("NONE");else p'
            'rintf("%lld\\n",a[i]);'
        ),
        (("0\n", "NONE"), ("3\n2 2 2\n", "NONE"), ("5\n3 -2 3 0 1\n", "1")),
    ),
    Task(
        "text_length",
        "读入一行 ASCII 文本（不超过1000字符），输出除换行符外的长度",
        TEXT + 'printf("%d\\n",(int)strlen(s));',
        (("\n", "0"), ("hello\n", "5"), ("a b!\n", "4")),
    ),
    Task(
        "uppercase",
        "读入一行 ASCII 文本，将小写英文字母转成大写，其他字符保留",
        TEXT + "for(int i=0;s[i];i++)s[i]=toupper((unsigned char)s[i]);puts(s);",
        (("\n", ""), ("Abc 12!\n", "ABC 12!"), ("already-UP\n", "ALREADY-UP")),
    ),
    Task(
        "lowercase",
        "读入一行 ASCII 文本，将大写英文字母转成小写，其他字符保留",
        TEXT + "for(int i=0;s[i];i++)s[i]=tolower((unsigned char)s[i]);puts(s);",
        (("\n", ""), ("AbC 12!\n", "abc 12!"), ("already-low\n", "already-low")),
    ),
    Task(
        "vowel_count",
        "读入一行 ASCII 文本，输出英文字母元音 aeiou 的数量，不区分大小写",
        TEXT
        + (
            'long long r=0;for(int i=0;s[i];i++)if(strchr("aeiou",tolower((uns'
            'igned char)s[i])))r++;printf("%lld\\n",r);'
        ),
        (("\n", "0"), ("AEiou xyz\n", "5"), ("Hello World\n", "3")),
    ),
    Task(
        "text_palindrome",
        "读入一行 ASCII 文本，严格按字符比较，回文输出 1，否则 0，空串是回文",
        TEXT + 'int r=1,n=strlen(s);for(int i=0;i<n/2;i++)if(s[i]!=s[n-1-i])r=0;printf("%d\\n",r);',
        (("\n", "1"), ("abba\n", "1"), ("Abba\n", "0")),
    ),
    Task(
        "reverse_text",
        "读入一行 ASCII 文本，倒序输出全部字符，保留空格，空串输出空行",
        TEXT + "for(int i=(int)strlen(s)-1;i>=0;i--)putchar(s[i]);putchar(10);",
        (("\n", ""), ("abc\n", "cba"), ("a b!\n", "!b a")),
    ),
)

FUN = (
    "天王盖地虎",
    "宫廷玉液酒",
    "奇变偶不变",
    "你好世界",
    "今天不想上班",
    "一只会写代码的猫",
    "月亮正在摸鱼",
    "让计算机感受到孤独",
    "sudo make me a sandwich",
    "此处应该有一个程序",
    "一颗赛博土豆",
    "鸭子统治互联网",
    "星期八的日落",
    "蓝色的风跑过键盘",
    "我的显卡想退休",
    "1234567890",
    "喵喵喵",
    "汪汪汪",
    "😀😃😄",
    "☕🍵",
    "春眠不觉晓",
    "床前明月光",
    "纸飞机与宇宙",
    "程序员的白日梦",
    "二进制泡泡糖",
    "键盘上的小恐龙",
    "逻辑在跳探戈",
    "永不说不的编译器",
    "风扇唱起了摇篮曲",
    "这只是一段没有需求的胡言乱语",
)


def damage(source: str, index: int) -> str:
    mode = index % 6
    if mode == 0:
        return source.replace("printf(", "emit_result(", 1)
    if mode == 1:
        return source.replace("return 0;", "return result_code;", 1)
    if mode == 2:
        return source.replace("long long", "integer_t", 1)
    if mode == 3:
        return source.replace(";", "", 1)
    if mode == 4:
        # stdout 未声明在旧 GCC 上也是硬错误，避免把仅有警告的程序标成损坏。
        return source.replace("#include <stdio.h>\n", "", 1).replace(
            "printf(", "fprintf(stdout, ", 1
        )
    return source.replace(
        "int main(void) {", "int main(void) { long long duplicate=0; char *duplicate=0;", 1
    )


def cases() -> tuple[Case, ...]:
    result: list[Case] = []
    # 三语言均覆盖，90 例回归不替代这 50 例基准。
    for lang, count in (("python", 17), ("c", 17), ("javascript", 16)):
        for item in [item for item in LEGAL if item.lang == lang][:count]:
            result.append(
                Case(
                    "native_" + item.name,
                    "native",
                    lang,
                    item.source,
                    (Check(item.stdin, None, item.args, comparison="native"),),
                    files=item.files,
                )
            )
    for item in BROKEN[:50]:
        result.append(
            Case(
                "low_" + item.name,
                "low",
                "c",
                item.source,
                (Check(item.stdin, item.expected_stdout, item.args),),
            )
        )
    for index, task in enumerate(TASKS):
        exact = task.name in {"uppercase", "lowercase", "reverse_text"}
        checks = tuple(
            Check(
                stdin, stdout + "\n" if exact else stdout, comparison="exact" if exact else "tokens"
            )
            for stdin, stdout in task.examples
        )
        if index < 30:
            result.append(
                Case(
                    "complex_" + task.name,
                    "complex",
                    "c",
                    damage(task.reference, index),
                    checks,
                    task.reference,
                )
            )
        result.append(
            Case(
                "nl_" + task.name,
                "nl",
                "c",
                task.spec + "。使用标准输入输出，不打印提示；数值保证不溢出有符号64位整数。\n",
                checks,
                task.reference,
            )
        )
    result.extend(
        Case(f"fun_{index:02}", "fun", "c", text, (Check(),)) for index, text in enumerate(FUN)
    )
    return tuple(result)


def fingerprint() -> str:
    payload = {"version": VERSION, "cases": [asdict(case) for case in cases()]}
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
