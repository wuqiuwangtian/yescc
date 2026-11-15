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


## 约束提取与独立评审研究

`creative_review.py`提供两条独立提示及严格响应校验；`creative_review_eval.py`只回放已捕获文本，不发模型请求、不执行C。它们未接入生产CLI，不增加默认模型调用、候选或预算。

```bash
uv run python -m bench.creative_review_eval \
  --items /path/to/materials.json --responses /path/to/model-responses \
  --output /tmp/creative-review-comparison.json
```

items为非空数组，包含安全文件名id/case_id、input/source/stdout、reference（pass/fail/contested），可带group和固定checks。同case_id必须对应同input。响应目录为`contracts/<case_id>.txt`与`reviews/<id>.txt`；合同每输入提取一次，输入以外的产物不交给提取模型；评审每产物独立，不提供reference或人工检查定义。

合同为`{"checks":[{"check":{...},"evidence":"input中的逐字引文"}],"uncheckable":[]}`，最多8条，用既有六类显式检查。来自模型的line_sequence只接受fullmatch=true、≤256字符、一个`(.*)`/`(\d+)`/`([0-9]+)`捕获及字面前后缀；禁止任意正则语法。评审为`{"verdict":"pass|fail|unknown","findings":[{"reason":"...","evidence":[{"origin":"input|stdout|source","quote":"..."}]}]}`，fail必须带实际stdout和input/source引文；pass/unknown不带findings。严格JSON、重复键、字段、长度和引文匹配均验证，不容忍围栏/臆造证据。

空合同/unknown为abstain，缺响应/坏格式/不在原文的引文为invalid，两者都不变相通过。引文存在只证明出处，不能证明约束忠实于输入或推理正确。reference来自冻结显式检查/既有双AI判断或独立合成正反例，不是人类真值。报告分既有/新探针、显式/开放，分别列覆盖、坏样本检出、好作品误拒绝及条件准确率；contested不进入准确率分母。CLI返回1表示存在invalid，0只表示回放完整，不代表评审质量达标；报告路径不能覆盖原始材料/响应，包含缺失响应路径与别名。

研究费用、协议、原始记录和未集成决定见[2026-11-13](../docs/评测/2026-11-13.md)。常规CI仍仅使用本地静态响应；真实研究调用按路线图授权、单价和费用预留独立约束，不把此回放命令当付费执行器。

逐项事实核对另由`creative_fact_review.py`提供：每条判断（包括pass）需2–4项可见事实，状态与最终verdict一致；每项都有stdout与input/source原文引文。引文只证明出处，不证明判断正确。`creative_fact_eval.py`离线比较basic、facts、facts_low三组；effort由外部受限研究调用器设置，不在回放时调用模型。

```bash
uv run python -m bench.creative_fact_eval \
  --items /path/to/materials.json --responses /path/to/model-responses \
  --output /tmp/creative-fact-comparison.json
```

响应布局为`<basic|facts|facts_low>/<id>.txt`；items与上一工具格式相同。缺失/坏证据为invalid，不因推理已付费而接受空判断；争议参考单列，分别报告旧难例诊断/开发澄清/独立新题材。三组共享单任务费用上限，thinking的推理token与正文共用输出额度，长度耗尽不等于逻辑能力结论。澄清题必须配新产物，不能把旧产物套新输入来回写成绩；协议与结果继续见同日评测。

## 生成侧短示例与本地事实研究

`creative_generation.py`仅构造研究消息，不调用模型或改生产默认：baseline逐字复用绑定产品版本的创意消息；examples追加`creative_generation_examples.json`中两个已编译核对的短示例；facts只追加原输入的codepoint/UTF8字节/行数与指定空白数量。codepoints不等于可见字符、字素或终端宽度，数据不是输出要求/意图；不能以统计报告代替创作。

三种消息在费用预留前构造。研究调用器必须`--no-cache`，因为生产缓存键不识别这些实验variant；不支持将辅助函数静默放进启用缓存的普通流程。`.gitattributes`固定示例JSON为LF，仅用于保留冻结字节，不改实际程序输出。

本轮先比较16输入×3组和两输入固定重复，再仅在候选满足冻结条件时打开8条留出输入做baseline/候选配对。作品经真实公开CLI run，原候选/请求/费用上限保持；只上传本来用于生成的输入及示例/派生测量，不额外请求模型裁判。两位不继承开发历史的AI按匿名实际作品评价贴题、新意与有限自洽，AI意见非人工验收。具体协议、源版本、费用、选择和未采用结果见[同日评测](../docs/评测/2026-11-13.md)。 54任务已完成：两种补充均未通过冻结筛选，facts主集共同ready虽从6/16到10/16，但重复从2/2到0/2，未打开封存留出或改生产默认。

## 生成阶段推理设置对照准备

```bash
uv run python -m bench.creative_reasoning --output dist/creative-reasoning-preflight.json
uv run pytest tests/test_creative_reasoning.py -q
```

预检只核对冻结字节并复用产品费用/参数逻辑计算首请求，输出42项CLI计划；不加载用户模型配置/密钥，不联网、不执行C、不读取封存留出，也不启动付费实验。输出须为新文件，已有文件不覆盖。pytest另会通过loopback假服务和真实GCC验证三组公开CLI，不连接真实供应商；测试清理代理并校验假服务地址/端口。协议及源码使用LF保存以跨平台核对原字节。

权威协议为`creative_reasoning_protocol.json`，输入/量表为同前缀JSON。主集12新输入×default(disabled/1024)、tokens(disabled/4096)、low(enabled/low/4096)，再固定两输入每组重复一次，共42任务；单任务$0.005、两候选/请求、无cache、无提示补充、默认不开UBSan。4096仍会受费用裁剪，推理/可见正文共用额度，low不传temperature，因此这是用户配置的综合对照。先测low，high/max不在本轮范围。

主集、固定重复分开；共同creative_ready、全任务创意效用、明确矛盾、实际运行数、失败与费用/耗时同时报告。不可评分保持null，效用统计的无贡献不回填个人零分；两位匿名AI各自判断，保留分歧，不称人工金标。仅满足冻结门槛才选唯一候选，打开此前封存8题配对确认；否则保留封存。42任务预留$0.21，条件确认最多16任务$0.08，合计$0.29（阶段上限仍$0.50）；这是未来最大预留，不是本轮实际花费或新增授权。

本工具不是API runner。付费前须冻结实际执行器、匿名映射与快照，离线证明未知费用等异常能阻断同一任务内后续HTTP请求，复核官方价目/能力；失配先停，不看作品后调参数或重跑。通过两阶段也只形成供维护者选择的建议，不自动修改生产默认。冻结设计、预检证据及测试隔离首轮失误见[本日评测](../docs/评测/2026-11-14.md#生成阶段推理设置对照准备)。

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


## 运行期未定义行为离线研究

`ubsan_corpus`从已归档的54份模型源码逐字核对来源，加2份冻结示例和16份明确正反例，生成新的72件研究语料；`ubsan_study`对普通/插桩源码副本分别编译运行，支持runtime与trap两模式。研究不调用模型，不改变生产C适配器、run/build或默认提示。

```bash
uv run python -m bench.ubsan_corpus --output-dir dist/ubsan-local
uv run python -m bench.ubsan_study --cases dist/ubsan-local/cases.json --compiler gcc --mode runtime --output dist/ubsan-local/runtime.json
uv run python -m bench.ubsan_study --cases dist/ubsan-local/cases.json --compiler gcc --mode trap --output dist/ubsan-local/trap.json
```

目录和报告必须是新路径，已有输入/结果不会覆盖。需要真实C工具链，**上述命令会编译并运行保存的C**，按现有超时/输出和尽力隔离执行，不提供文件系统强隔离。runtime需要目标运行库，trap免运行库但异常退出本身不证明UB。报告区分实际compiler探针、未测/构建失败/超时/输出超限、普通及插桩结果；退出0只表示研究记录完成，不代表无UB、支持或语义正确。候选stderr可伪造，工具不认证UB；原字节和仅CRLF/LF兼容的严格UTF8文本比较分开。具体结果与平台版本见同日评测，当前任务仅维护路线图§10.16。
