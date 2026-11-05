# YesCC（Yes Compiler Collection）

[![CI](https://github.com/wuqiuwangtian/yescc/actions/workflows/ci.yml/badge.svg)](https://github.com/wuqiuwangtian/yescc/actions/workflows/ci.yml)

> **The compiler that never says no.**
> 语法是可选的，编译是必然的。

YesCC 是一个「永不扫兴」的 AI 编译器：正确代码原样编译；错误代码尽力修复；连自然语言或胡言乱语，也尽量变成一个能运行的程序。

- **正确代码绝不打扰**：原生工具链成功即结束，不改源码、不调模型、不注入参数。
- **错误代码尽力挽救**：规则 → 便宜/本地模型 → 强模型，每层都交真实工具链验证。
- **无意义输入也给出创造性解释**：确定性兜底、文化梗、模型创意解释；结果状态单独标注，绝不冒充原程序。

## 当前状态

**v0.0.1-alpha（0.0.1a2）已发布**（[release](https://github.com/wuqiuwangtian/yescc/releases/tag/v0.0.1-alpha)）：三语言原生直通、C 规则修复、模型修复、自然语言生成与创意兜底可用；AI 产物默认在最小沙箱中运行。这是 alpha，接口与行为仍可能变化。

仓库中的后续修复不代表已更新 TestPyPI 安装包；当前开发状态、未发布变更和接续任务统一见[路线图 §10](docs/项目路线图与发布计划.md#10-待决事项与下一步)。

M4 已通过双平台 Gate；固定基准、已知失败与费用见 [M4 验证记录](docs/评测/2026-11-05.md)。该记录不代表新的稳定版本发布。

首版范围：C 为 AI 修复/生成目标；Python / C / JavaScript 保留原生直通；MIT 许可，PyPI + 独立二进制发布。长期目标「所有语言」与「编辑器插件」不变。进度见[路线图](docs/项目路线图与发布计划.md)。

> 开发代号曾为 `fcomp`；仓库、包名与命令已统一为 `yescc`。

## 安装（alpha）

TestPyPI 预览版，依赖仍从 PyPI 拉取：

```bash
pipx install --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ yescc==0.0.1a2
yescc --version
```

也可以 `uv tool install` 或从源码运行（见下）。

## 快速开始

需要 Python ≥3.11 与 [uv](https://docs.astral.sh/uv/)（尚未发布到 PyPI）：

```bash
uv sync --extra dev
uv run yescc hello.c -o hello       # 构建，参数透传给 gcc
uv run yescc run hello.c            # 构建后运行
uv run yescc broken.c -o fixed      # 规则修复（不覆盖源文件）
uv run yescc idea.txt               # 无法识别时走确定性兜底
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
| 创意兜底 | 是 | 是 | 是 |
| 运行 | 是 | 是 | 是 |

## 模型配置

默认读取环境变量；密钥只放在环境变量或本地 env 文件里，不要写进配置或仓库：

```bash
export YESCC_MODEL_L3_BASE_URL=https://api.deepseek.com/v1
export YESCC_MODEL_L3_NAME=deepseek-chat
export YESCC_MODEL_L3_API_KEY=...
export YESCC_MODEL_L3_INPUT_PRICE_PER_MTOK=...   # 当前输入单价，USD/百万 token
export YESCC_MODEL_L3_OUTPUT_PRICE_PER_MTOK=...  # 当前输出单价，USD/百万 token
export YESCC_MODEL_L2_BASE_URL=http://localhost:11434/v1   # 可选，本地小模型
export YESCC_MODEL_L2_NAME=llama3.2
```

常用开关：`--offline`（不发模型请求）、`--budget 0.05`（USD）、`--max-rounds 2`、`--no-cache`、`--trace`、`--emit-source`。没有可用密钥时自动降级为规则 + 确定性兜底。

云端模型必须配置两项单价，未知价格时不发请求；请求前保守估算并预留费用，剩余预算限制输出 token。缺 usage 或请求失败会保留预留，`model.cost_known=false` 时费用总额未知，`cost_usd` 只表示已知部分小计。回环本地端点可不填价格，但不能声称受到金额限制。`--max-rounds` 同时限制候选尝试（含缓存）与实际请求；L2 修复失败后有 L3 就升级，显式 `--model` / `--provider` 跳过自动 L2。详见 [ADR-0015](docs/adr/0015-alpha-reliability-contracts.md)。

## 已知限制

- 只支持单文件；跨文件与项目构建不支持，未识别语言返回退出码 101。
- 源码仅支持 UTF-8（可带 BOM）。
- Windows 上原生调用（非 WSL），C 工具链使用系统 GCC/MinGW；MSVC 不支持。
- 部分 MinGW 链接器不支持含非 ASCII 字符的绝对输出路径；可在目标工作目录使用相对输出文件名。此类原生工具链失败会如实保留。
- 通过管道重定向时，用户程序自身的编码设置仍生效，YesCC 不替用户程序改写环境。
- 沙箱是「尽力隔离」：独立临时目录、超时、环境变量白名单、POSIX 资源限额；**不是安全边界**。Linux 上 `unshare -n` 无权限时不会断网。POSIX 清理独立进程组；Windows 以 `taskkill /T` 尽力清理超时/中断的进程树，没有 Job Object，不保证清理已脱离任务树的后代。
- AI 产物默认使用临时 cwd，相对路径不再指向原目录；绝对路径仍可访问宿主文件。需要真实 cwd 与环境时使用 `--unsafe-run`，仍保留超时和进程清理。
- `--offline` 控制 YesCC 的模型请求，不改变原生用户程序的网络行为。费用估算依赖配置单价和服务遵守 token 限额，不能约束供应商隐藏计费；HTTP 分阶段超时也不是操作系统级的任务硬截止。

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

固定 200 例基准和离线/真实/暖缓存模式见 [bench/README](bench/README.md)。离线执行明确区分 130 个已评测样本和 70 个模型任务未评测；Linux/Windows CI 保留各自原始 JSON。

接手开发先读 [AGENTS.md](AGENTS.md) 和[路线图 §10](docs/项目路线图与发布计划.md#10-待决事项与下一步)；贡献流程见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 许可

[MIT](LICENSE) © 2026 五秋望田（wuqiuwangtian）
