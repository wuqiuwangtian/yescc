# 架构决策记录（ADR）索引

跨模块或对外契约的决策记录在此。新增 ADR 时复制现有格式并在此登记；已接受的 ADR 不直接改写结论，需要变更时新增 ADR 并标注取代关系。

| 编号 | 标题 | 状态 |
| --- | --- | --- |
| [0001](0001-first-release-language-scope.md) | 首版语言范围 | 已接受（AI 范围由 0010 修订） |
| [0002](0002-friendly-failure-semantics.md) | 「不报错」的产品语义 | 已被 0009 取代 |
| [0003](0003-implementation-language.md) | 实现语言与工程基线 | 已接受（分发见 0011） |
| [0004](0004-model-strategy-and-data-boundary.md) | 模型策略与数据边界 | 已接受（由 0012 修订） |
| [0005](0005-exit-codes-and-report-contract.md) | 退出码与报告契约 | 已接受（兜底见 0009） |
| [0006](0006-isolation-boundary.md) | 执行与隔离边界 | 已接受（由 0013 修订） |
| [0007](0007-pypi-distribution-and-versioning.md) | PyPI 分发与版本策略 | 已接受（补充见 0011） |
| [0008](0008-model-provider-coverage.md) | 模型供应商覆盖范围与本地部署 | 已接受（由 0012 修订） |
| [0009](0009-product-promise-and-status-machine.md) | 产品承诺与结果状态机 | 已接受 |
| [0010](0010-ai-target-language-and-capabilities.md) | AI 目标语言与能力矩阵 | 已接受 |
| [0011](0011-naming-and-distribution.md) | 命名与分发 | 已接受 |
| [0012](0012-model-tiers-and-providers.md) | 模型分级与供应商 | 已接受 |
| [0013](0013-sandbox-and-run-semantics.md) | 沙箱与运行语义 | 已接受 |
| [0014](0014-tolerant-parsing-and-rule-repair.md) | 容错解析与规则修复 | 已接受 |
| [0015](0015-alpha-reliability-contracts.md) | Alpha 可靠性修补契约 | 已实施 |
| [0016](0016-m4-evidence-and-platform-boundaries.md) | M4 基准、对抗验收与跨平台边界 | 已实施，G4 已通过 |

相关文档：[路线图](../项目路线图与发布计划.md)、[需求报告](../初版需求报告.md)、[技术参考](../技术参考与验证计划.md)。

[ADR-0017](0017-exact-context-patch-normalization.md)：模型 diff 元数据重算和唯一精确上下文定位（已实施；用户确认）。
