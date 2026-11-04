# ADR-0011：命名与分发

- 状态：已接受（2026-11-04）；补充 [ADR-0007](0007-pypi-distribution-and-versioning.md)
- 相关：[路线图 §2/§5 M1.5/§8](../项目路线图与发布计划.md)

## 背景

项目需要一个有传播力、与「编译器」气质匹配的名字。`YesCC` 与 GCC 形成呼应，PyPI 的 `yescc` 与 `yes-cc` 均未被占用，GitHub 无显著同名项目。用户已确认采用 YesCC。

## 决策

- 名称：**YesCC**，命令 `yescc`，包名/模块 `yescc`。
- 改名范围：GitHub 远端仓库、`pyproject.toml`、`src/` 模块、CLI、文档与测试；**本地目录保留** `forgiving-compiler`，避免会话路径失效。
- 不保留 `fcomp` 别名；TestPyPI 上的 `fcomp 0.1.0.dev0` 作为历史记录，不再更新。
- 分发：PyPI 为必须项；同时提供免安装二进制（PyInstaller/Nuitka，Linux + Windows）。
- Rust 重写推迟：v0.1 保持 Python；是否重写由真实用户反馈决定，不在本期范围。
- 许可证与版权人不变（MIT，五秋望田 / wuqiuwangtian）。

## 后果

- 正面：命名统一，传播与安装路径清晰；改名一次性完成，成本低。
- 负面：旧链接、旧 TestPyPI 包与文档引用会短暂不一致；GitHub 重定向可缓解。
- 约束：改名必须在 M1.5 一次性完成并跑通双平台 CI，不允许长期双名并存。
