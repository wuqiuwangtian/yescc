<h1 align="center">YesCC</h1>

<p align="center"><strong>语法是可选的，编译是必然的。</strong></p>

<p align="center">
  代码写对了就跑，漏了分号就补。<br>
  给它一句离谱的设定，模型也能写个 C 程序跑给你看。
</p>

<p align="center">
  <a href="https://www.bilibili.com/video/BV14eyYzNEMF/"><img src="docs/assets/hell-world-cover.jpg" width="560" alt="这也能编译？点击观看 Hell world! 演示短片"></a>
</p>

<p align="center">
  <a href="https://www.bilibili.com/video/BV14eyYzNEMF/">▶ 看演示短片</a>
  &nbsp; · &nbsp;
  <a href="https://www.bilibili.com/video/BV14eyYzNEMF/?p=2">看真实终端实录</a>
</p>

## 快速开始

用 Python 3.11+ 和 pipx 安装 YesCC。

```sh
pipx install yescc==0.2.0
yescc --version
```

<details>
<summary>免安装二进制（Windows / Linux x86_64）</summary>

从 [0.2.0 Release](https://github.com/wuqiuwangtian/yescc/releases/tag/v0.2.0) 下载对应压缩包，按同页的 `SHA256SUMS` 校验后解压，保留整个目录。

在解压目录试试随包附带的例子。接下来的命令里，Linux 用 `./yescc` 启动，Windows PowerShell 用 `.\yescc.exe`。

```sh
./yescc run examples/hello.c --offline     # 输出 hello
./yescc run examples/broken.c --offline    # 修好后输出 repaired
./yescc run examples/idea.txt --offline    # 回显这份输入
```

</details>

接下来的两个例子都需要 GCC。运行 `gcc --version` 能看到版本号，就可以开始了。[Windows 配置 GCC](docs/使用与隐私指南.md#windows-原生安装与验收)

### 跑一个少了分号的程序

把这段代码存成 UTF-8 的 `hello.c`。让 `puts` 那行少一个分号，再看看会发生什么。

```c
#include <stdio.h>

int main(void) {
    puts("Hell world!")
    return 0;
}
```

打开终端，在文件所在目录运行这条命令。

```sh
yescc run hello.c --offline
```

终端会打印 **`Hell world!`**。YesCC 在副本里补上了分号。至于问候里少写的那个 `o`，它照单全收。

运行 `yescc hello.c --explain`，可以查看原来的报错和这次用到的修复规则。

## 让模型接个梗

视频里给电脑出了一道题。你也可以用同一句话比较两个档位，或者换成自己的设定。

> 显示一根从 97% 走到 100% 的进度条。屏幕只有四行，每行写百分比和电脑此刻在做的事。

### 模型配置

把题目存成 UTF-8 的 `progress.txt`。下面以 DeepSeek 为例。按终端选择一组配置命令，执行后在同一窗口继续试玩。密钥会单独提示输入。

<details>
<summary>Bash 配置</summary>

```bash
export YESCC_MODEL_L3_PROVIDER=deepseek
export YESCC_MODEL_L3_NAME=deepseek-flash
export YESCC_MODEL_L3_INPUT_PRICE_PER_MTOK=0.30
export YESCC_MODEL_L3_OUTPUT_PRICE_PER_MTOK=1.20
export YESCC_MODEL_L3_API_KEY
read -rsp 'DeepSeek API key: ' YESCC_MODEL_L3_API_KEY && echo
```

</details>

<details>
<summary>Windows PowerShell 配置</summary>

```powershell
$env:YESCC_MODEL_L3_PROVIDER = 'deepseek'
$env:YESCC_MODEL_L3_NAME = 'deepseek-flash'
$env:YESCC_MODEL_L3_INPUT_PRICE_PER_MTOK = '0.30'
$env:YESCC_MODEL_L3_OUTPUT_PRICE_PER_MTOK = '1.20'
$yesccCredential = Get-Credential -UserName DeepSeek -Message '在密码框输入 API key'
$env:YESCC_MODEL_L3_API_KEY = $yesccCredential.GetNetworkCredential().Password
```

</details>

云端请求需要输入和输出两项单价，单位是美元／百万 token。示例取 2026-11-21 的高峰价，输入按缓存未命中计。价格变动时请更新这两项。[DeepSeek 官方价格](https://api-docs.deepseek.com/quick_start/pricing/)

### 用同一道题试两次

先让模型照题发挥。每次运行独立处理一份输入，`--budget 0.005` 把这次运行的模型预算设为 0.005 美元。

```sh
yescc run progress.txt --budget 0.005
```

再加上 `--creativity wild`，给模型多一点改编空间。

```sh
yescc run progress.txt --creativity wild --budget 0.005
```

录制那次，放飞档走到 99% 时正在「给缓存里的猫顺毛」。如果你的电脑忙起了别的事，可以在运行后用 `yescc progress.txt --explain` 看它这次是怎么理解题目的。

## 0.2.0 能力矩阵

| 输入 | 本机先做什么 | 模型可以做什么 |
| --- | --- | --- |
| C | 用 GCC 编译，失败时尝试机械修复 | 修复剩余错误，或另写一个 C 程序 |
| Python / JavaScript | 用 Python 3 / Node.js 检查语法，通过后运行 | 语法检查失败时，另写一个 C 程序 |
| 自然语言（`.txt`） | 查找内置彩蛋 | 按设定生成 C 程序 |

原生成功或能在本机修好的程序，两个档位都不会调用模型。模型不可用或没能交付作品时，YesCC 会尝试用本地彩蛋或回显输入收尾。`--explain` 可以查看实际走了哪条路。

### 已知限制

- 一次处理一个 UTF-8 文件。模型写出的 C 要经过 GCC 编译，是否接住了梗由你判断。
- 生成的程序在本机运行，仍可访问宿主文件。处理陌生代码请用容器或虚拟机。`--offline` 只关闭 YesCC 的模型请求。

[使用与隐私指南](docs/使用与隐私指南.md) · [发行说明](docs/releases/0.2.0.md) · [试玩记录](docs/评测/README.md) · [贡献指南](CONTRIBUTING.md) · [MIT 许可](LICENSE)
