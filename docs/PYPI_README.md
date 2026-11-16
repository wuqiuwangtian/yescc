# YesCC（Yes Compiler Collection）

> **The compiler that never says no.**
> 语法是可选的，编译是必然的。

YesCC 是一个「永不扫兴」的 AI 编译器：正确代码交给原生工具链，错误 C 代码尽力修复；自然语言或胡言乱语也可以变成 C 程序，或者明确标注的创意兜底。

- Python、C、JavaScript 原生直通。原生构建成功就结束，不改源码、不调用模型。
- C 修复依次尝试有限规则和受限模型候选，每份候选都交真实工具链验证。
- 模型创意、固定梗和原文回显分别记录；编译或运行通过不代表实现了意图，也不保证作品有趣。
- 支持模型推理设置、输出额度、费用与请求上限、缓存及 JSON/explain 报告。推理正文不写入报告。

## 安装

以下命令针对正式 PyPI 版本 `0.1.0`；版本与资产状态以 [PyPI](https://pypi.org/project/yescc/) 和 [GitHub Releases](https://github.com/wuqiuwangtian/yescc/releases) 为准。

需要 Python ≥3.11，推荐使用独立工具环境：

```bash
pipx install yescc==0.1.0
# 或者：uv tool install yescc==0.1.0
yescc --version
```

C 程序需要 PATH 中的 GCC，JavaScript 需要 Node.js；Python 程序使用安装环境的解释器。Linux/Windows x86_64 免安装归档见 [0.1.0 Release](https://github.com/wuqiuwangtian/yescc/releases/tag/v0.1.0)：解压后保留整个目录，包括 `_internal`、`licenses` 和 `MANIFEST.json`；其用户程序仍需要外部工具链，冻结版 Python 从 PATH 选择外部 Python 3。

## 快速体验

解压原生归档时，将下列`yescc`换成Linux的`./yescc`或Windows的`.\yescc.exe`；归档自带`examples/`三例，`START-HERE.txt`给出直接运行命令。

把下面的 C 程序保存为 `hello.c`：

```c
#include <stdio.h>
int main(void) { puts("hello"); return 0; }
```

```bash
yescc run hello.c --offline
yescc hello.c --explain --offline
```

第一条输出 `hello`；第二条读取报告，正常代码应为 `native_ok`，没有模型请求。把 `return 0;` 中的分号改成全角 `；` 后再运行，可以体验有限规则修复；源文件保持用户保存的内容。

把一句自然语言保存为 UTF-8 的 `idea.txt`：

```bash
yescc run idea.txt --offline
yescc idea.txt --explain --offline
```

没有模型时，普通未知文本只会进入明确标注的 `print_input` 回显，不会实现自然语言意图。配置模型后可用 `yescc run idea.txt --play --budget 0.005` 体验模型创意；预算单位为 USD，云端必须先配置单价。配置与推理参数见 [使用与隐私指南](https://github.com/wuqiuwangtian/yescc/blob/main/docs/使用与隐私指南.md) 和 [完整 README](https://github.com/wuqiuwangtian/yescc/blob/main/README.md)。

## 边界

- AI 修复/生成目标仅 C；Python/JavaScript 只做原生直通。只支持单文件 UTF-8 输入，没有跨文件构建、macOS 或 MSVC 支持。
- `--strict` 关闭修复和兜底；`--no-fallback` 禁止创意兜底。原命令使用 `--play` 时先移除它，再选这两项之一。
- AI 运行使用尽力隔离：它不是阻止所有文件系统访问、联网或进程逃逸的安全边界。原生用户程序保留自身行为；`--offline` 只控制 YesCC 的模型请求。
- 模型创意 `run` 检查有限的输出、退出状态和超时。`--check-undefined` 默认关闭，显式启用时用当前 GCC 检查部分运行期错误，不保证内存安全或语义正确；`build` 不偷偷运行作品。
- Linux 归档在 Ubuntu 24.04 构建，不保证旧 glibc、musl/Alpine 或其他架构；Windows 归档内置 Python 3.14，要求 Windows 10+，验证范围以原生 CI 记录为准。

真实模型效果与安装证据见 [评测入口](https://github.com/wuqiuwangtian/yescc/blob/main/docs/评测/README.md)。自动回归、独立 AI 测试和维护者验收分别标注，历史模型成绩绑定采样版本。

问题请附版本、平台、命令、最小脱敏输入和报告，提交到 [Issues](https://github.com/wuqiuwangtian/yescc/issues/new/choose)；不要上传密钥或私密源码。YesCC 使用 [MIT](https://github.com/wuqiuwangtian/yescc/blob/main/LICENSE)，二进制捆绑组件分别适用其原许可证。
