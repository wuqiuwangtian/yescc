# 贡献指南

感谢你考虑为 yescc（包容你的编译器）做贡献。本项目仍处于早期阶段，先把「合法代码原生一致、失败不伪造成功」守住，再谈扩展。

## 开发环境

需要 Python ≥3.11 与 [uv](https://docs.astral.sh/uv/)：

```bash
git clone https://github.com/wuqiuwangtian/forgiving-compiler.git
cd forgiving-compiler
uv sync --extra dev
uv run yescc --version
```

## 提交前检查

```bash
uv run ruff check .
uv run mypy
uv run pytest
```

CI 会在 Linux 与 Windows 的 Python 3.11–3.14 上重复这些检查；本地至少跑一遍。真实模型调用不在 CI 中执行，涉及模型行为的变化请附本地评测记录。

## 提交与分支

- 提交信息遵循 [Conventional Commits](https://www.conventionalcommits.org/zh-hans/)，例如 `fix: 修正全角分号在字符串内被替换`。
- 一个 PR 只做一件事；行为变化请写明影响了路线图中的哪条验收门。
- `main` 受保护，通过 PR 合并（squash）。

## 设计约束（不接受「先跑通再说」的绕过）

1. **合法输入零干扰**：原工具链成功时，不得改源码、不得调模型、不得注入参数。
2. **真实执行作证**：生成的「成功」必须有真实产物或真实运行；未验证结果必须单独标注。
3. **不伪造成功**：失败就返回失败状态与准确退出码，不允许静默返回 0。
4. **预算与隐私**：模型调用受轮数、token、时间上限约束；密钥只从环境变量读取，不进日志与报告。
5. **诊断保留**：界面可以不展示原始诊断，但内部必须保留，并作为修复依据。

## 架构与决策

跨模块或对外契约的改动，先在 `docs/adr/` 新增 ADR 并在 PR 中讨论。现有决策见 [ADR 索引](docs/adr/README.md)；路线图与验收门见 [docs/项目路线图与发布计划.md](docs/项目路线图与发布计划.md)。

## 许可

提交即表示你同意以 [MIT](LICENSE) 许可贡献你的代码。
