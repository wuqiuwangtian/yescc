# M4 固定基准

入口：`uv run python -m bench.harness --output /tmp/m4-offline.json`。
需要 Python ≥3.11、GCC、Node.js 和 `uv sync --extra dev --locked`。

## 数据与判定

`dataset.py` 是样本定义的唯一来源，版本 `m4-v1`；执行器把数据 SHA-256 和源码指纹写入结果。测试固定数据指纹，修改样本、规格或 oracle 时必须显式更新版本/指纹，不能为了提高得分偷偷换分母。

| 类别 | 数量 | 内容与判定 |
| --- | --- | --- |
| native | 50 | Python 17 / C 17 / JS 16，从既有合法集固定选择；分别执行原生工具链和 YesCC，对照 stdout/stderr/退出码，检查源文件未改、模型调用为零 |
| low | 50 | 从既有损坏集固定选择；必须状态 repaired 且通过预定行为检查；其余规则样本及原有 90 例合法回归仍在 pytest 中运行 |
| complex | 30 | 规则不可解的受控 C 损坏，覆盖未知函数、未声明变量/类型、缺分号、缺声明和冲突类型；先检查原生/规则确实失败，再要求模型修复通过独立输入输出 |
| nl | 40 | 有明确输入输出和边界的中文短任务，至少 3 组事先编写的测试；含零、负数、重复值、空数组/空行等 |
| fun | 30 | `--play` 强制娱乐解释，必须 creative_fallback、退出码 0、输出非空；不以娱乐结果冒充意图正确 |

“复杂”在这里表示超出当前规则能力，不表示真实项目复杂度。数据是受控合成的小程序集，不能外推到新手作业或生产项目。

参考程序先用真实 GCC 编译并与预先固定的 oracle 交叉验证。样本、参考程序与 oracle 由协作助手整理，尚未逐题获得人类复核；“独立”指与被测模型生成过程分离，不等于人工标注或第三方基准。模型只收到损坏源码或任务描述和必要诊断，参考程序、预期输出与隐藏测试不上传。参考程序通过不代表模型通过。数字任务比较 token，要求保留文本内容的任务使用完整行比较。

## 模式与费用

- 默认离线：实际执行 50 native / 50 low / 30 fun；70 个需要模型的任务标记 `not_evaluated`，不记成功或失败。
- `--models`：显式开启配置好的 L3；冷运行使用新建的私有缓存。`--warm` 再执行同一集，单独记录缓存后的结果，不能把暖缓存成绩叫 pass@1。
- 每个阶段是独立 CLI 任务，最多 2 次候选；冷阶段失败的样本在暖阶段可能再次请求模型，不能把暖阶段成绩称作“同一次任务两轮后的成绩”。费用上限跨两个阶段累计，暖阶段请求与费用单列。
- 真实评测默认每任务 `$0.005`、整个冷/暖轮次 `$0.50`，均可显式调小；不足时不再派发任务，标记未评测。金额按配置单价/usage 估算，缺 usage 的预留不退款；不是供应商账单。
- 真实模型密钥由当前会话授权的仓库外环境提供，不上传 CI、不写入 JSON。单任务开支仍受路线图 A3 约束。
- 每个候选只构建一次，再执行多组行为检查，不为每个隐藏输入重复调用模型。暖运行继续交真实工具链验证。

```bash
# 不需要 API key，CI 用此模式
uv run python -m bench.harness --output /tmp/m4-offline.json

# 已按当前会话授权配置环境后执行；不要把密钥写在命令或仓库中
uv run python -m bench.harness --models --warm \
  --budget 0.005 --total-budget 0.50 --output /tmp/m4-models.json
```

## 结果与边界

JSON 按 cold/warm、类别及样本记录：执行/未评测、构建状态、行为测试、源码哈希是否变化、请求/缓存/token/费用、首候选正确性及任务耗时。summary 的 P50/P95 包括构建和全部行为检查；pass@1 只解释冷运行的 complex/nl 首候选通过独立测试的比例。

原始 JSON 中的 `limits` 来自 CLI 的构建报告，所以构建阶段 `sandboxed=false`；后续行为检查通过 `run_ai_command` 执行，不能把该构建字段误解为行为检查未隔离。真正的 CLI `run` 限制报告另由沙箱/平台端到端测试核对。退出码 1 表示至少一个已评测样本失败，完成评测不等于全量通过。

执行器 stdout/stderr 先落临时文件，只读取各 1 MiB，输出过量判失败；该限制属于评测器，不得写成 YesCC 运行输出已限流。程序在产品的尽力隔离路径运行；不是恶意代码安全认证。

CI 在 Linux/Windows × Python 3.11/3.14 运行离线集并保存 artifact `m4-<OS>-py<版本>`。真实结果和 CI 链接见 [docs/评测](../docs/评测/README.md)；当前任务状态只维护在[路线图 §10](../docs/项目路线图与发布计划.md#10-待决事项与下一步)。


## M5 独立审计（全部离线）

```bash
uv run python -m bench.failure_audit --output dist/m5-failure-audit.json
uv run python -m bench.oracle_checks --output dist/m5-oracle-crosscheck.json
uv run pytest tests/e2e/test_captured_patches.py -q
```

失败审计使用 24 个独立的错误输入和本地假供应商，检查退出码、状态、无误报产物、源文件不变与 stdout/落盘报告一致；不支持语言/编码在报告建立前返回，明确记录没有报告。第二实现使用 Python 内建与标准库，从规格计算 40 个任务的结果，再与真实 GCC 构建的参考程序核对 508 个输入；它不修改 M4 固定数据，不代表人工复核。捕获的 8 个真实响应离线重放，交原样冻结 oracle 验证；不能把重放结果称作模型新 pass@1。

四组原生 CI 同时运行审计并把结果作为 `m4-<OS>-py<版本>` artifact 中的 `m5-*.json` 保存。新增 oracle probe 只用于复核参考程序，不混入固定 200 例成绩。


## RC 安装包验收与许可证据

```bash
uv sync --extra dev --locked
uv build
uv run python -m bench.package_smoke --output dist/package-smoke.json
```

默认检查 pyproject 版本对应的 wheel，也可 `--wheel <路径>`。脚本在仓库外创建新环境，按 `uv.lock` 导出的运行时约束安装 wheel；以 `python -I -m yescc` 和 console 版本命令确认加载安装包，避免误用 editable 源码。12 条 CLI 路径使用真实 Python/GCC/Node，模型交互只到本地假供应商，检查源码不变、状态/退出/输出/请求次数和脱敏落盘。

结果包含 wheel/lock 哈希、每条路径结论、实际安装的 10 个运行时依赖与 YesCC 的原始许可文本/哈希。已复核标识变化、新依赖或缺失原始许可会令检查失败；不是自动判断任意许可兼容性的工具。范围不含开发工具、系统 GCC/Node/Python 的再分发许可，也不含尚未构建的独立二进制。第三方声明见 [THIRD_PARTY_NOTICES](../THIRD_PARTY_NOTICES.md)。CI 在 Linux/Windows 各保留 `rc-package-<OS>` artifact（wheel、sdist、证据 JSON）。


RC2 的失败审计版本为 `m5-failure-v2`：预算用例从漏分号改为未声明符号，因为 L1 新增分号规则后原输入会在进入模型前修好。仅改变这一个独立审计输入；旧原始记录不回写。模型流程测试也改用未知函数以维持模型路径覆盖。M4 数据、分母与 oracle 保持不变。新增 8 个多错误场景的 RC1/RC2 离线对照单列，不并入固定 200 例成绩。
