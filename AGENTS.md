# YesCC 协作入口

面向接手本仓库的人和 AI。本文件只保存稳定约束和导航；当前进度、剩余任务及验证状态统一维护在[路线图 §10](docs/项目路线图与发布计划.md#10-待决事项与下一步)。

## 开始工作

1. 读 [README](README.md)、路线图 §10，再按任务读 [ADR 索引](docs/adr/README.md)及相关源码。冲突时以最新有效 ADR 和用户最新指令为准。
2. 执行 `git status --short --branch`、`git log -3 --oneline`，识别基线、分支和已有改动。不要覆盖、清理或提交他人的未完成修改。
3. 启动与检查：`uv sync --extra dev --locked`；`uv run ruff check .`；`uv run mypy`；`uv run pytest`。CI 使用本地假模型；不要为了常规验证读取真实密钥或调用付费服务。

## 必须守住的契约

- 首版 AI 目标仅 C；Python/JS 保留原生直通。源文件不被 YesCC 改写；原生成功不调模型。成功构建的 stdout/stderr 原样转发，`--json` 保持单一 JSON 输出。
- 所有候选必须交真实工具链验证；编译通过不能表述为意图正确。`creative_fallback` 不得冒充修复成功。
- 模型候选尝试、请求次数、费用预留、时间分别受限；缓存命中也消耗一次候选尝试，无效补丁/无法编译的源码应失效缓存。显式模型/供应商覆盖不得被 L2 路由绕开。
- CLI 费用单位是 USD；维护者付费评测的授权约束见路线图 A3。云端缺单价时不发请求；费用未知时检查 `cost_known`，不能把费用小计当作总费用。
- 运行隔离是尽力隔离。POSIX 清理独立进程组，Windows 用 `taskkill /T` 尽力清理；不能据此宣称阻止文件系统访问或所有进程逃逸。
- 报告 schema v1 只增不改。隐私字段、运行限制和验证结论必须来自实际行为；变更前读 [ADR-0015](docs/adr/0015-alpha-reliability-contracts.md)。
- M4 的基准、对抗和平台边界见 [ADR-0016](docs/adr/0016-m4-evidence-and-platform-boundaries.md)；评测入口见 [bench/README](bench/README.md)。70 个模型样本在离线模式中必须标为未评测；固定数据/指纹的修改需单独说明，不能用改 oracle 提高已有模型得分。

## 协作与交付

- 若会话已授权多 agent 并行，分派前明确文件归属、依赖、基线与验收；共享工作区中避免两个 agent 同时写同一文件。主导者负责整合后检查，子任务“完成”不等于整个项目验收通过。
- 行为变更同步更新相关 ADR、README 和 CHANGELOG；任务状态只更新路线图，测试证据放 `docs/评测/`。不按会话新增重复的计划或交接文件。
- 交接说明已验证项、未验证平台、下一项可执行工作及 Git 状态。用 Git 查询实际提交；本地提交、推送、CI、发布分别表述，不把旧 alpha 发布算作新修复已发布。
- 使用任务分支和 Conventional Commits，提交前检查实际 diff。推送、PR、合并及发布按已有授权处理；用户只要求提交时完成本地提交。
