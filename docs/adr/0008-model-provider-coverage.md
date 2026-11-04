# ADR-0008：模型供应商覆盖范围与本地部署

- 状态：已接受（2026-11-03）；由 [ADR-0012](0012-model-tiers-and-providers.md) 修订
- 相关：[ADR-0004](0004-model-strategy-and-data-boundary.md)（数据边界与预算规则继续有效）、[路线图 §6.9](../项目路线图与发布计划.md)

## 背景

ADR-0004 把模型接口限定为 OpenAI 兼容的 `/chat/completions`。实际需求要求「支持主流模型供应商和本地部署的模型」：Anthropic、Google Gemini、Azure OpenAI 的协议与 OpenAI 并不相同，本地部署（Ollama / LM Studio / vLLM）则大多提供 OpenAI 兼容端点。只做一种协议会限制用户选择。

## 决策

引入 **provider 抽象层**，负责请求构造与响应解析；上层只依赖统一的 `complete(messages, ...) -> ModelResult` 接口。

v0.1.0 覆盖以下 provider：

| provider | 覆盖对象 | 说明 |
| --- | --- | --- |
| `openai-compatible` | OpenAI、DeepSeek、Qwen/DashScope、Moonshot、vLLM、Ollama、LM Studio、其他兼容端点 | 同一实现，靠 `base_url` / `model` 区分 |
| `anthropic` | Claude Messages API | 原生协议，`x-api-key` + `anthropic-version` |
| `gemini` | Google Gemini API | 原生 `generateContent` 协议 |
| `azure-openai` | Azure OpenAI | 部署路径 + `api-key` 头 + `api-version` |

配置项（敏感值只从环境变量读取）：

- `FCOMP_MODEL_PROVIDER`（默认 `openai-compatible`）
- `FCOMP_MODEL_BASE_URL`、`FCOMP_MODEL_NAME`、`FCOMP_MODEL_API_KEY`
- `FCOMP_MODEL_API_VERSION`（Azure 需要）
- 常用厂商提供别名（`deepseek`、`openai`、`qwen`、`moonshot`、`ollama`、`lmstudio`），别名只提供默认 base_url 与示例模型名，不内置密钥

本地模型通过 OpenAI 兼容端点接入；**不内置模型权重、不自动下载模型**。没有可用凭据时降级为「仅规则」并提示，不自动联网。

## 不变的部分

ADR-0004 的以下规则继续有效：只上传必要上下文、模型输出必须经真实工具链验证、最多 2 轮、预算硬上限、缓存本地且不跨用户、密钥不进配置/报告/日志、`--offline` 禁网。

## 后果

- 正面：用户可带自己的供应商与本地模型；同一套评测可跨 provider 比较。
- 负面：每家协议差异需要独立测试；供应商行为差异（限流、字段缺失、错误码）需要在报告中如实记录。
- 边界：v0.1.0 不承诺所有 provider 在发布时都经过同等强度的真实评测，能力矩阵逐项标注。
