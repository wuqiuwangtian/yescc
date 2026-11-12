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

来源另存`fallback_kind`与`model_success`：后者要求实际模型来源、成功模型构建及既有行为检查通过。首候选不能仅按“最终退出0+一次预算尝试”推断；模型JSON失败后print_input不计模型成功。fun的原passed仍是可执行口径，包含确定性兜底；这些来源字段不把编译/非空输出升级成创意或语义质量评分，固定样本/oracle不改。

创意专项的冻结50输入、重复、匿名双AI评分和参数实验见[2026-11-12](../docs/评测/2026-11-12.md)。匿名材料需清除绝对路径中的条件标签；附加C11等可移植性探针与产品实际构建分别列证据，不把额外flags的失败混称默认构建失败。主集build+测量、生产run护栏、固定旧候选重放与新留出输入分别统计，不能用同一“通过”合并。

原始 JSON 中的 `limits` 来自 CLI 的构建报告，所以构建阶段 `sandboxed=false`；后续行为检查通过 `run_ai_command` 执行，不能把该构建字段误解为行为检查未隔离。真正的 CLI `run` 限制报告另由沙箱/平台端到端测试核对。退出码 1 表示至少一个已评测样本失败，完成评测不等于全量通过。

执行器 stdout/stderr 先落临时文件，只读取各 1 MiB，输出过量判失败；该限制属于评测器，不得写成 YesCC 运行输出已限流。程序在产品的尽力隔离路径运行；不是恶意代码安全认证。

CI 在 Linux/Windows × Python 3.11/3.14 运行离线集并保存 artifact `m4-<OS>-py<版本>`。真实结果和 CI 链接见 [docs/评测](../docs/评测/README.md)；当前任务状态只维护在[路线图 §10](../docs/项目路线图与发布计划.md#10-待决事项与下一步)。

## 创意一致性回归

`creative_consistency_cases.json`为与M4完全分开的冻结12题（8条显式约束、4条开放评审），SHA-256 `a16f8b397ad39f367ddc6ad312ae73a7f6d9c954c680626af8a9b9ab8cfbe2a7`。由独立AI在未读源码或旧结果的情况下设计，模型只收到input，不收到checks/合格或违规样例。修改题目或检查需说明新指纹，不回写旧成绩。

`.gitattributes`只为该冻结文件固定LF，避免Windows检出时自动CRLF转换改变原字节指纹；评测器读取已捕获程序输出时仍接受LF/CRLF，不改写输出。

```bash
# 不调用模型或执行候选；读取事先捕获的CC01.stdout等文件
uv run python -m bench.creative_consistency \
  --cases bench/creative_consistency_cases.json \
  --outputs /path/to/captured-stdout --output /tmp/creative-consistency.json
```

支持line_count（默认含空白行）、contains/not_contains、line_sequence、symbol_count、last_nonempty_line。line_sequence逐行search或fullmatch，仅取每行一次匹配的单个捕获组，按字符串序列精确比较；splitlines接受LF/CRLF，保留内部空行，末尾换行不增加一行。symbol_count按完整子串精确、不重叠计数，包含变体选择符，不做Unicode归一化；这不是终端显示宽度或通用字素计数。

无checks的合法UTF-8输出标passed=null/not_assessed，不算语义通过；缺文件或非法UTF-8失败，非法配置拒绝。报告保留数据/逐输出SHA与检查明细。CLI退出0仅表示已配置约束无失败，开放题仍须评审，输入要求中的叙事因果也不能只靠这些检查证明。它不读取构建报告、不能证明输出来自模型或已通过生产run，调用者须另核来源/实际运行。只作为离线评测，不改变生产运行/候选预算或M4 oracle。


## M5 独立审计（全部离线）

```bash
uv run python -m bench.failure_audit --output dist/m5-failure-audit.json
uv run python -m bench.oracle_checks --output dist/m5-oracle-crosscheck.json
uv run pytest tests/e2e/test_captured_patches.py -q
```

失败审计使用 24 个独立的错误输入和本地假供应商，检查退出码、状态、无误报产物、源文件不变与 stdout/落盘报告一致；不支持语言/编码在构建报告建立前返回；RC3 明确记录无落盘构建报告但收到 command_error JSON，不混称为 stdout 匹配落盘报告。第二实现使用 Python 内建与标准库，从规格计算 40 个任务的结果，再与真实 GCC 构建的参考程序核对 508 个输入；它不修改 M4 固定数据，不代表人工复核。捕获的 8 个真实响应离线重放，交原样冻结 oracle 验证；不能把重放结果称作模型新 pass@1。

四组原生 CI 同时运行审计并把结果作为 `m4-<OS>-py<版本>` artifact 中的 `m5-*.json` 保存。新增 oracle probe 只用于复核参考程序，不混入固定 200 例成绩。


## RC 安装包验收与许可证据

```bash
uv sync --extra dev --locked
uv build
uv run python -m bench.package_smoke --output dist/package-smoke.json
```

默认检查 pyproject 版本对应的 wheel，也可 `--wheel <路径>`。脚本在仓库外创建新环境，按 `uv.lock` 导出的运行时约束安装 wheel；以 `python -I -m yescc` 和 console 版本命令确认加载安装包，避免误用 editable 源码。16 条 CLI 路径使用真实 Python/GCC/Node，模型交互只到本地假供应商，检查源码不变、状态/退出/输出/请求次数和脱敏落盘。

结果包含 wheel/lock 哈希、每条路径结论、实际安装的 10 个运行时依赖与 YesCC 的原始许可文本/哈希。已复核标识变化、新依赖或缺失原始许可会令检查失败；不是自动判断任意许可兼容性的工具。范围不含开发工具、系统 GCC/Node/Python 的再分发许可，也不含尚未构建的独立二进制。第三方声明见 [THIRD_PARTY_NOTICES](../THIRD_PARTY_NOTICES.md)。CI 在 Linux/Windows 各保留 `rc-package-<OS>` artifact（wheel、sdist、证据 JSON）。


RC2 的失败审计版本为 `m5-failure-v2`：预算用例从漏分号改为未声明符号，因为 L1 新增分号规则后原输入会在进入模型前修好。仅改变这一个独立审计输入；旧原始记录不回写。模型流程测试也改用未知函数以维持模型路径覆盖。M4 数据、分母与 oracle 保持不变。新增 8 个多错误场景的 RC1/RC2 离线对照单列，不并入固定 200 例成绩。


## 独立 AI 黑盒工作区准备

`uv run python -m bench.prepare_blackbox --help` 提供准备入口。需给定仓库外全新 `--workspace`、与归档 CI 相符的 RC2 `--wheel`，以及工作区外的 `--receipt`。准备器拒绝覆盖已有目录；不复制项目源码、回归用例或成绩，不运行独立验收。模板位于 `bench/blackbox/`，仅包含用户功能说明、环境入口和测试协议。

凭据清理/本机假配置等脚手架自检不等于产品验收；外部 AI 自建测试证据单独记录。工作区只做上下文隔离，不能声称强系统沙箱。维护者用新会话交付提示词，报告回传后再复核材料哈希、实际结果和剩余风险。


## M6 原生候选（不发布）

```bash
uv sync --extra dev --extra release --locked
uv build
uv run twine check --strict dist/yescc-0.1.0*.whl dist/yescc-0.1.0*.tar.gz
uv run python -m bench.binary_build --output dist/native
uv run python -m bench.binary_archive_smoke --archive dist/native/yescc-0.1.0-linux-x86_64.tar.gz --expected-version 0.1.0 --output dist/native/binary-smoke.json
```

Windows归档后缀为zip、目标名windows-x86_64。构建只在当前平台原生执行；CLI包、实际外部工具链与许可覆盖分别验收，详见[ADR-0020](../docs/adr/0020-native-release-candidates.md)。`binary-licenses.json`的unresolved不为空时不可关闭正式许可门槛；工作流保持候选artifact，不创建tag/Release或上传索引。

README动图由实际三例输出生成，可选安装Pillow后运行 `python -m bench.render_demo --binary <冻结入口> --font <本机允许使用的中文字体> --output docs/assets/yescc-demo.gif --record dist/m6-demo.json`。记录来源、源文件保护和原始stdout/stderr；不要用编造的终端输出替代真实演示。


发布后索引验证：`uv run python -m bench.index_smoke --expected-version 0.1.0 --output dist/published-install.json` 从公共PyPI安装根包和依赖；手动工作流 `Published install verification` 在Linux/Windows × Python3.11/3.14执行。发布前可加 `--wheel <候选whl>` 演练同一套检查，根包来自本地、依赖按公开版本范围解析，报告明确为local_wheel_rehearsal，不算实际PyPI发布验收。两种模式均只离线运行产品，不使用真实模型。
