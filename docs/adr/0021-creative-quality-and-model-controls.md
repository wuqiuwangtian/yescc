# ADR-0021：创意输出检查与模型推理控制

- 状态：已实施（2026-11-12）。补充ADR-0012/0013/0015/0020，不修改首版语言范围或发布授权。
- 来源：维护者在创意专项结果后要求按建议顺序改进，并增加可调整的推理设置；数据库/联网检索本轮不实施。

## 决策

### 生成C与原生工具链

- 只有完整AI生成的C候选（自然语言/创意）通过`build_in_temp(..., link_math=True)`显式链接`-lm`，真实命令进入attempt。合法原生、规则、模型补丁修复均不暗加库。
- `-c/-S/-E/-M/-MM/-fsyntax-only/-nostdlib/-nodefaultlibs`不追加；显式`-lm`或`-l m`不重复。没有增加其他外部库，也不猜测源码依赖。

### 推理设置、预算与兼容性

- 新CLI：`--thinking auto|enabled|disabled`、`--reasoning-effort auto|none|minimal|low|medium|high|xhigh|max`、`--max-output-tokens 1..16384`。同名环境项为`YESCC_MODEL_[L2_|L3_]THINKING/REASONING_EFFORT/MAX_OUTPUT_TOKENS`，tier优先于通用项，CLI优先于环境。
- CLI显式auto清除该项环境控制，返回项目供应商默认，并非强制采用服务端默认。DeepSeek普通模型默认显式禁用thinking；`deepseek-reasoner`默认启用。用户选择推理等级时DeepSeek启用thinking，规范化minimal→low、medium/xhigh→high；none禁用；disabled与有效推理等级冲突时拒绝。
- OpenAI发送Chat Completions的`reasoning_effort`；显式effort时用`max_completion_tokens`，有效推理时不传temperature。OpenAI不接受DeepSeek式thinking开关；未声明能力的兼容供应商拒绝显式推理设置。模型具体是否支持某个API枚举仍由模型/供应商决定，不保证所有OpenAI模型都支持所有等级。
- 默认输出上限仍1024；可调上限不会扩张费用预算。推理与可见正文共享供应商的输出额度，仍先按消息字节/配置单价预留，并按剩余费用下调实际上限。费用未知、单价缺失、候选/请求/时间限制均沿用ADR-0015。
- 配置在模型路径、缓存/预留之前校验，native成功和offline不因模型环境错误失败。修复路径首次请求前预检所有潜在L2/L3控制，避免先收费再发现后级配置错而丢失费用；生成只校验选中层。已知配置错作为UsageError/100、JSON为command_error，不写造构建费用或覆盖旧报告。
- 不支持的具体模型/远端参数可能在请求后返回HTTP错误：此类请求仍计次数和预留，费用未知则明确未知。不会自动删除用户设置后再付费重试。
- `length`、过滤、空正文先保存可靠usage、结算预算，再拒绝响应；不把已知费用误标未知。模型响应/JSON/源码不可用可在原候选上限内重试，没有新增长轮数或费用。
- 缓存键加入有效推理、输出上限和提示版本；坏构建/坏运行清缓存。缓存命中不伪造served model/finish reason/新请求参数；`configured_settings`只表示配置，`request_settings`表示本次实际HTTP参数。

### 创意检查的范围

- 创意提示要求一个贴题构思、语言/情绪匹配和完整短作品，避免泛化字节统计/翻译/复述，保护UTF-8；不要求所有诗歌都必须有算法。当前`m6-creative-3`恢复`m6-creative-1`的原正文，仅更新缓存版本。实验`m6-creative-2`扩写数量、方向、首尾状态等要求后，冻结显式约束由6/8降为5/8，未达到预先约定条件，因此回退；不以更多提示要求代替效果证据。版本变化使旧缓存失效，不改历史成绩，完整实验记录保留。
- 编译通过后对创意源码做有限风格检查：直接`wprintf/vwprintf/putwchar`与`printf/vprintf/puts/putchar`混用则拒绝候选。先按C续行规则处理LF/CRLF，再忽略注释和字符串；真实源码字节不改。此保守规则即使两调用在互斥分支也拒绝，不覆盖所有stdio调用、宏和间接调用，不称完整C语义分析。
- 同一有限源码检查拒绝普通单引号字符常量中的直接非ASCII字符或非ASCII的`\u`/`\U`转义（`non_ascii_character_literal`），避免`'☀'`等被GCC仅警告接受、再截断为坏字节。字符串/注释、ASCII和十六进制/八进制字节转义、完整紧邻L/u/U/u8前缀的字符常量不受这条规则拒绝；不保证覆盖宏或间接字节处理。这是创意风格约束，即使普通非ASCII字符常量在某实现中合法也可能被拒绝；原生/修复/普通生成不适用。每种拒绝原因提供对应重试反馈，仍共享原候选/请求/费用上限。
- **build不执行程序**。只记录source_checks及`generated.validation.performed=false`，不把编译说成运行验证或作品正确。
- 仅model_creative的显式run，使用原有隔离/冻结环境路径执行一次，stdin为空、捕获stdout/stderr。检查退出0、未超时/超量、stdout非Unicode空白、stdout可解码UTF-8。普通生成/修复、确定性兜底和原生输出保持既有运行语义。
- stdout+stderr合计64KiB为创意run额度：轮询及退出后检测超量、清理进程组，最多保留额度内前缀；磁盘瞬间写入可超过额度，不称严格字节或文件系统隔离。截断stdout不能据前缀判完整UTF-8，记未知；该上限不应用于原生程序。
- 未通过的模型作品输出不转发；记录attempt.validation及实际validation_limits，失效缓存，并在原候选/请求/费用/时间上限内重试，耗尽后仍如实print_input。成功输出按原始字节转发，生成程序不重复执行。取消/130直接结束，不再请求模型。
- `--unsafe-run`只沿用既有放宽运行位置/环境的含义，不解除创意检查、超时或输出额度。检查不能撤销程序已产生的副作用，也不证明安全、完整、贴题或有趣。
- 修复与创意仍共享原有候选额度，不自动预留或加钱；明确创作需求使用`--play`。改变两者分配需另行决定。

### 报告与评测

- schema v1只增字段：模型的requested/served、finish_reason、reasoning_tokens、configured/request_settings和响应摘要；不记录推理正文。逐候选source_checks/validation/validation_limits与最终generated.validation分别来自实际阶段。
- 最终limits只代表最终实际运行；先前失败候选限制在attempt中，不能从编译limits推断已运行/断网。output_limited仅在确实观察到超额时为true。
- 固定M4数据/oracle/原passed口径不变；新增fallback_kind/model_success，以真实来源、成功模型构建和行为检查区分模型作品、固定梗、原文回显，修正历史fun_03式首候选污染。model_success仍不是创意评分。
- `bench.creative_consistency`仅对已捕获stdout做冻结输入的显式输出约束检查，不执行程序、不接入生产run、不做通用语义裁决。12条独立新题材与M4数据分开：8条有行数/顺序/符号数量等检查，4条开放题须另评审；空checks标not_assessed，不计语义通过。原始字节解码UTF-8，不做Unicode归一化或终端宽度推测；缺文件/非法UTF-8失败，非法配置拒绝，保存数据与输出SHA。题目/检查/样例在调用前冻结，不能根据模型输出放宽oracle。

## 依据与验证

生成侧的短示例/本地测量先作为独立bench研究，消息补充必须发生在费用预留之前，三个variant只在显式no-cache下比较。派生计数不声称字素/显示宽度或意图，不以统计报告替代创作。示例/事实进入模型消息的实际字节仍由原session费用/上传记录处理；若今后采用，须单独更新生产提示版本与缓存身份，不能把bench包装直接接入既有缓存。

约束提取/独立自检的研究工具只在bench内运行：合同提取只看输入，评审只看输入/源码/实际stdout，冻结参考不交给模型；引文匹配只证明出处，格式错误或空结论不算通过。模型提取正则另受小语法约束，不能当任意可信oracle执行。该研究不改变上述生产检查、默认提示或费用/候选规则；数据与是否值得进一步验证的结论见[2026-11-13评测](../评测/2026-11-13.md)。

同日逐项事实核对的研究协议要求2–4项事实及一致的verdict，合法unknown同样需要证据；普通判断与两种effort分开，low开启后不再发送不支持的temperature。比较在固定USD上限下进行，推理耗尽正文额度归无效，不擅自放大预算。旧歧义参考与旧结果保留，澄清输入配新源/输出，独立新题材另标，不将研究结论自动用于生产阻断。

- [创意专项](../评测/2026-11-12.md)的六份数学候选、非法UTF-8、宽窄流缺行、默认思考空正文和候选耗尽记录。
- [DeepSeek思考控制](https://api-docs.deepseek.com/guides/thinking_mode/)及[计价](https://api-docs.deepseek.com/quick_start/pricing/)。
- [OpenAI Chat Completions参数](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)及[推理说明](https://developers.openai.com/api/docs/guides/reasoning?api-mode=chat)。

离线回归、双平台CI、当前真实复测和未测边界按日期保存在docs/评测；状态只维护路线图§10。
