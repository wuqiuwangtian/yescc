# ADR-0003：实现语言与工程基线

- 状态：已接受（2026-11-03）；命名与分发见 [ADR-0011](0011-naming-and-distribution.md)
- 相关：[路线图 §6.1](../项目路线图与发布计划.md)

## 背景

yescc 的核心工作是进程编排：调用原工具链、解析诊断、在预算内调用模型、验证结果、落盘报告。它不需要自己实现编译器后端，对运行时性能的要求主要是启动开销。

## 决策

- 语言：**Python ≥3.11**（`from __future__ import annotations` 可用，`tomllib` 内建）。
- 环境与锁：**uv**；提交 `uv.lock`。
- 构建：**hatchling**；`src/` 布局；`yescc` 控制台入口。
- 运行时依赖：仅 `httpx`（模型调用）与 `platformdirs`（配置/缓存路径）。
- 不引入 `typer`/`rich` 等重量级 CLI 框架，使用标准库 `argparse`。
- 质量门槛：`ruff` + `mypy --strict` + `pytest`，CI 覆盖 Linux/Windows 与 Python 3.11–3.14。

## 后果

- 正面：开发快、依赖面小、跨平台打包成熟（PyPI 即分发）。
- 负面：用户机器需要 Python 3.11+；冷启动开销必须实测（路线图 G1：包装开销 P95 ≤200 ms），若超标优先减少导入与依赖，而不是换语言重写。

## 复核条件

若实测启动开销或 Windows 兼容性无法满足 G1，再评估编译型实现；换语言需新 ADR，不静默迁移。
