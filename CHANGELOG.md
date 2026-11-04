# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

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

## [0.0.1-alpha] - 未发布

M3 后发布的公开预览，见路线图 Alpha。

## [0.1.0] - 未发布

首个正式版本目标见路线图 M6。
