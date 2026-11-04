# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

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
