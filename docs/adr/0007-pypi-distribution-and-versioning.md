# ADR-0007：PyPI 分发与版本策略

- 状态：已接受（2026-11-03）；命名与二进制分发见 [ADR-0011](0011-naming-and-distribution.md)
- 相关：[路线图 §8.4](../项目路线图与发布计划.md)

## 背景

安装体验是首版 MVP 的一部分：用户应能用一条命令装好，而不必从 GitHub 下载源码。包名 `fcomp` 已确认在 PyPI 未被占用。

## 决策

- 包名 `fcomp`；`pyproject.toml` 中的 `version` 是唯一版本来源。
- 首版目标版本 `0.1.0`；开发期使用 `0.1.0.dev0`，发布前改为 `0.1.0`。
- 版本遵循 SemVer；`0.1.x` 只修 bug，新能力进 `0.2`。
- 发布物：sdist + wheel；GitHub Release 必须，PyPI 必须（D7）。
- 发布方式：优先 PyPI Trusted Publishing（OIDC，无长期 token）；不可用时用维护者提供的 `PYPI_API_TOKEN`。
- 发布前：M0 起用 TestPyPI 演练构建与上传流程；发布前在干净环境验证 `pipx install fcomp==<版本>`。
- 发布后：在干净容器/机器上复核安装；CHANGELOG 与 tag 一致。

## 后果

- 正面：分发路径短，用户安装门槛低。
- 负面：包名一旦发布难以更改；发布前需确认名称与元数据。凭据由维护者持有，发布是显式动作，不自动化到每次 push。
