# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Added

- M4 固定 200 例基准及独立行为检查、数据/源码指纹、冷暖缓存分列和离线未评测标记；真实模型默认单任务 $0.005、整轮 $0.50。
- 28 条沙箱对抗用例与跨平台路径/CRLF/BOM/Unicode/stdin/退出/中断检查；任务分支触发双平台 CI 并保留离线基准 artifact。
- 报告记录环境过滤、资源/进程清理模式、文件系统和输出流隔离的实际边界。

### Fixed

- AI 临时构建正确解析原 cwd 的 include/library 路径；非链接构建不再误报旧可执行文件为新产物；Windows CTRL_BREAK 进入清理与报告路径。
- Windows AI 进程等待改为短轮询，及时处理控制事件；公开 MinGW 绝对 Unicode 输出路径限制并分别测试原生成功/失败路径。
- `m4-1` 提示词要求严格复制 diff 物理行及 C 转义，创意程序不再被要求自行等待额外 stdin；固定样本上的首轮和复测结果分开保留。
- 限制包含缓存的模型候选尝试，清除无效补丁/编译失败候选缓存，避免循环不退出；缓存键补入工具链信息，重试反馈统一随机构建路径以支持稳定命中。
- 模型请求前保守估算并预留费用，按剩余额度限制输出 token；失败请求也计数，缺云端价格时阻止上传，缺 usage 时保留预留并标记费用未知。
- L2 修复失败后升级 L3；显式模型/供应商覆盖不再被 L2 路由绕过；本地端点按 hostname 判断。
- 转发原生构建 stdout/stderr，包括 `gcc -E/-M` 和完整 strict 失败诊断，JSON 模式仍输出单一报告。
- POSIX 通过独立进程组清理超时、中断和正常父进程退出后的子进程；Windows 增加 `taskkill /T` 尽力清理。
- 修正报告中的云端使用、上传内容字节数和实际沙箱运行状态；费用未知时摘要与评测脚本不再显示为免费。
- 增加协作入口 `AGENTS.md`、ADR-0015 与路线图接续信息；配置文档区分已实现的环境变量和未实现的 TOML。

## [0.0.1-alpha] - 2026-11-04

首个公开预览，TestPyPI `0.0.1a2`：[Release](https://github.com/wuqiuwangtian/yescc/releases/tag/v0.0.1-alpha)。以下为该版本已有能力；上方修复尚未发布。

### Added

- M2 确定性修复：Tree-sitter 容错解析；全角标点、缺失括号/引号、常见拼写规则；候选交真实 gcc 验证。
- 确定性创意兜底：打印原始输入，已知文化梗走本地映射；`--no-fallback` 可关闭。
- 结果状态机：`native_ok / repaired / creative_fallback / failed`；兜底在 stderr 明示。
- `--play` 强制娱乐解释；`--explain` 展示修复规则与兜底来源。
- 53 例受控低级错误样本与回归测试。

### Added

- M3 模型层：OpenAI 兼容 provider、L2/L3 路由、单任务预算、内容寻址缓存与隐私提示。
- C 模型修复：模型只返回 unified diff，本地严格应用并重新编译，拒绝整文件改写。
- 自然语言 → C 与模型创意兜底；无密钥或 `--offline` 时降级为确定性兜底。
- `--offline`、`--budget`、`--max-rounds`、`--provider`、`--model`、`--emit-source`、`--trace`。
- 报告记录模型、token、费用、耗时、缓存命中与假设。

### Added

- 最小运行沙箱：AI 产物在临时目录执行，环境变量白名单、超时、POSIX 资源限额与尽力断网；`--unsafe-run` 可覆盖。
- 报告记录 `sandboxed` / `network_isolated` / `timeout_s` 与 `timed_out`。

### Changed

- 项目更名为 YesCC：仓库、包名、模块与命令统一为 `yescc`；版本回到 `0.0.1.dev0`。

### Added

- M1 原生直通：`yescc <file>`、`yescc run <file> -- args`、`--lang`、`--strict`、`--json`、`--explain`、`--no-cache`。
- 三语言适配器（Python/C/JavaScript），合法输入原样交给真实工具链，不修改源码、不调用模型。
- 报告 schema v1：`.yescc/<stem>/report.json`，记录输入哈希、工具链、尝试、耗时与隐私字段。
- 退出码契约：0 成功、100 用法、101 不支持、102 构建失败、103 预算、104 内部错误。
- 合法样本 90 例（每语言 30），端到端对照原生工具链的 stdout/stderr/退出码。
- `bench/overhead.py`：包装开销基准（P50/P95）。
- ADR-0008：主流模型供应商与本地部署的覆盖范围。

- M0 工程基线：MIT 许可、`pyproject.toml`、`src/yescc` 骨架、退出码约定。
- CI：Linux/Windows × Python 3.11–3.14 的 lint、类型检查、测试与打包冒烟。
- ADR-0001 至 ADR-0007：首版语言范围、友好终止、实现语言、模型策略、退出码与报告契约、隔离边界、PyPI 分发。
- 路线图：[docs/项目路线图与发布计划.md](docs/项目路线图与发布计划.md)。

## [0.1.0] - 未发布

首个正式版本目标见路线图 M6。
