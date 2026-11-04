# YesCC（Yes Compiler Collection）

> **The compiler that never says no.**
> 语法是可选的，编译是必然的。

YesCC 是一个「永不扫兴」的 AI 编译器：正确代码原样编译；错误代码尽力修复；连自然语言或胡言乱语，也尽量变成一个能运行的程序。

- **正确代码绝不打扰**：原生工具链成功即结束，不改源码、不调模型、不注入参数。
- **错误代码尽力挽救**：规则 → 便宜/本地模型 → 强模型，每层都交真实工具链验证。
- **无意义输入也给出创造性解释**：确定性兜底、文化梗、模型创意解释；结果状态单独标注，绝不冒充原程序。

## 当前状态

**M1.5 已完成**：改名、TTY 状态行与 TestPyPI 演练通过；三语言原生直通、报告与退出码可用。容错修复、模型修复与创意兜底在 M2/M3。

首版范围：C 为 AI 修复/生成目标；Python / C / JavaScript 保留原生直通；MIT 许可，PyPI + 独立二进制发布。长期目标「所有语言」与「编辑器插件」不变。进度见[路线图](docs/项目路线图与发布计划.md)。

> 开发代号曾为 `fcomp`；仓库、包名与命令已统一为 `yescc`。

## 快速开始

需要 Python ≥3.11 与 [uv](https://docs.astral.sh/uv/)（尚未发布到 PyPI）：

```bash
uv sync --extra dev
uv run yescc hello.c -o hello       # 构建，参数透传给 gcc
uv run yescc run hello.c            # 构建后运行
uv run yescc hello.c --explain      # 查看最近一次报告
```

报告写入 `.yescc/<文件名>/report.json`；源文件不会被覆盖。

## 能力矩阵（v0.1 目标）

| 能力 | Python | C | JavaScript |
| --- | --- | --- | --- |
| 原生直通 | 是 | 是 | 是 |
| 规则修复 | 0.2 | 是 | 0.2 |
| 模型修复 | 0.2 | 是 | 0.2 |
| 自然语言生成 | 0.2 | 是 | 0.2 |
| 运行 | 是 | 是 | 是 |

## 已知限制

- 只支持单文件；跨文件与项目构建不支持，未识别语言返回退出码 101。
- 源码仅支持 UTF-8（可带 BOM）。
- Windows 上原生调用（非 WSL），C 工具链使用系统 GCC/MinGW；MSVC 不支持。
- 通过管道重定向时，用户程序自身的编码设置仍生效，YesCC 不替用户程序改写环境。

## 文档

- [初版需求报告](docs/初版需求报告.md)：完整愿景、兼容性边界、命令行体验、处理流程、性能成本、验收和分期。
- [技术参考与验证计划](docs/技术参考与验证计划.md)：程序修复研究、工具链与编辑器依据、实验设计。
- [GitHub 开源项目调研](docs/GitHub开源项目调研.md)：5 个相关候选，区分创意参照、修复组件与真实执行后端。
- [项目路线图与发布计划](docs/项目路线图与发布计划.md)：里程碑与验收门、技术契约、MIT 许可与 GitHub + PyPI 发布流程。
- [ADR 索引](docs/adr/README.md)：已确认的架构与产品决策。
- [评测记录](docs/评测/2026-11-03.md)：M1 原生一致性与包装开销 baseline。

## 开发

```bash
uv sync --extra dev
uv run ruff check .
uv run mypy
uv run pytest
```

真实模型评测不在 CI 中执行，记录写入 [docs/评测](docs/评测/README.md)。

## 许可

[MIT](LICENSE) © 2026 五秋望田（wuqiuwangtian）
