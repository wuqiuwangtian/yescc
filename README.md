# YesCC · Yes Compiler Collection

**语法是可选的，编译是必然的。** 给 YesCC 一段能跑的代码，它就照常跑。给它一句离谱的设定，它会请模型顺着你的话，写个 C 程序跑给你看。

[![这也能编译？Hell world! 点开观看 YesCC 演示短片](docs/assets/hell-world-cover.jpg)](https://www.bilibili.com/video/BV14eyYzNEMF/)

[P1 · 演示短片](https://www.bilibili.com/video/BV14eyYzNEMF/) · [P2 · 真实终端实录](https://www.bilibili.com/video/BV14eyYzNEMF/?p=2)

## 安装

用 Python 3.11+ 和 pipx 安装命令行工具。编译 C 或把点子变成程序，还需要 PATH 里有 GCC。

```bash
pipx install yescc==0.2.0
yescc --version
```

<details>
<summary>免安装二进制（Windows / Linux x86_64）</summary>

从 [0.2.0 Release](https://github.com/wuqiuwangtian/yescc/releases/tag/v0.2.0) 下载对应压缩包，用同页的 `SHA256SUMS` 校验，解压后保留整个目录。可以先试包里的这三个例子。Linux 在解压目录运行以下命令，Windows 将 `./yescc` 换为 `.\yescc.exe`。

```bash
./yescc run examples/hello.c --offline    # 打印 hello
./yescc run examples/broken.c --offline   # 换掉全角分号，打印 repaired
./yescc run examples/idea.txt --offline   # 原样念出你的点子
```

</details>

## 试玩视频里的进度条

> 显示一根从 97% 走到 100% 的进度条。屏幕只有四行，每行写百分比和电脑此刻在做的事。

把这句话存成 UTF-8 的 `progress.txt`，配好模型后，在文件所在目录运行。第一条让模型照题发挥，第二条让它放飞一点。[模型配置](#模型配置)

```bash
yescc run progress.txt --budget 0.005
yescc run progress.txt --creativity wild --budget 0.005
```

录制时，放飞档走到 99% 正在「给缓存里的猫顺毛」。换你来跑，电脑可能又有别的事要忙。好奇它怎么理解了这道题？运行 `yescc progress.txt --explain` 看上一次的报告。

**想先离线试玩？** 在源码仓库根目录运行下面这条。它会把 `mian` 改成 `main`，补上漏掉的分号，打印 `value=7`，原文件里的错字还在。[示例源码](examples/multi_error.c)

```bash
uv run --locked yescc run examples/multi_error.c --offline
```

## 模型配置

原生运行和机械修复都在本机完成。想让模型接梗，再配置服务。`--budget 0.005` 表示本次模型预算为 0.005 美元，云端单价填全后才会发请求。

<details>
<summary>用 DeepSeek 试玩</summary>

下面以 `deepseek-flash` 为例。单价单位是美元／百万 token，按官方价格表的高峰价填写，输入取缓存未命中价。[DeepSeek 模型与价格](https://api-docs.deepseek.com/quick_start/pricing/)

**Bash** · 在当前终端粘贴，最后按提示输入密钥。

```bash
export YESCC_MODEL_L3_PROVIDER=deepseek YESCC_MODEL_L3_NAME=deepseek-flash
export YESCC_MODEL_L3_INPUT_PRICE_PER_MTOK=0.30
export YESCC_MODEL_L3_OUTPUT_PRICE_PER_MTOK=1.20
read -rsp 'DeepSeek API key: ' YESCC_MODEL_L3_API_KEY && export YESCC_MODEL_L3_API_KEY && echo
```

**PowerShell** · 在当前窗口粘贴，最后按提示输入密钥。

```powershell
$env:YESCC_MODEL_L3_PROVIDER = 'deepseek'
$env:YESCC_MODEL_L3_NAME = 'deepseek-flash'
$env:YESCC_MODEL_L3_INPUT_PRICE_PER_MTOK = '0.30'
$env:YESCC_MODEL_L3_OUTPUT_PRICE_PER_MTOK = '1.20'
$env:YESCC_MODEL_L3_API_KEY = (Get-Credential -UserName DeepSeek -Message '在密码框输入 API key').GetNetworkCredential().Password
```

</details>

[模型参数与输出额度](docs/使用与隐私指南.md#推理设置与创意运行检查) · [哪些内容会发给模型](docs/使用与隐私指南.md#数据流与清理)

## 能力矩阵 · 0.2.0

| 给它什么 | 先在本机做什么 | 模型可以做什么 |
| --- | --- | --- |
| C | GCC 编译。失败时先尝试补分号、改拼写等机械修复 | 尝试修复剩下的错误，或另写一个 C 程序 |
| Python / JavaScript | 用 Python 3 / Node.js 检查语法，通过后原样运行 | 语法检查失败时，可以另写一个 C 程序 |
| 一段话（`.txt`） | 识别内置彩蛋 | 把设定写成一个 C 程序 |

两个创意档位只影响模型。模型没能交付作品时，默认会尝试本地彩蛋或原文回显。`--explain` 会告诉你走了哪条路。

## 已知限制

一次处理一个 UTF-8 文件。模型写的 C 会交给 GCC 编译，是否接住了梗由你判断。生成的程序在本机运行，仍可访问宿主文件。处理陌生代码请用容器或虚拟机。`--offline` 只关闭 YesCC 的模型请求。[使用与隐私指南](docs/使用与隐私指南.md)

[0.2.0 发行说明](docs/releases/0.2.0.md) · [试玩与评测记录](docs/评测/README.md) · [贡献指南](CONTRIBUTING.md) · [MIT 许可](LICENSE)
