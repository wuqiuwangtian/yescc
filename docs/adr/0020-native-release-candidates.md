# ADR-0020：原生发布候选与外部工具链

- 状态：已实施，待正式发布（2026-11-11）；落实 ADR-0011 的二进制分发，不扩大语言范围。
- 来源：维护者确认 G5 后继续 M6；发布资产准备与实际发布分别授权。

## 决策

- 使用锁定版本的 PyInstaller 构建 Linux/Windows x86_64 的 onedir 候选。Linux 用 tar.gz 保留符号链接，Windows 用 zip；整个目录是安装单元，不能只复制可执行文件。release extra 只用于构建，不加入 wheel 的运行时依赖。冻结使用同一基础解释器的独立venv，只安装已锁定的wheel运行依赖与PyInstaller；验证用pytest/Twine等不得污染冻结导入图。Windows构建进程的DLL搜索PATH只包含该解释器、builder和系统目录，避免从Java等SDK目录复制无关CRT；不改变CLI用户程序的PATH。
- CLI 自带 Python 运行时和既有运行依赖；C/Python/JavaScript 用户代码仍交外部 GCC/Python 3/Node。冻结版从 PATH 验证可运行的外部 Python 3，报告其实际版本与缓存产物；源码/wheel 继续使用当前解释器。不能把冻结后的 sys.executable 当 Python，也不能把内置版本/缓存标签写成外部工具链事实。
- 按 PyInstaller 文档在冻结版调用外部工具链/产物时恢复动态库搜索环境；普通源码/wheel 不改用户环境。Windows 暂时恢复 DLL 搜索目录后还原主进程；Linux 恢复 LD_LIBRARY_PATH_ORIG，没有则移除引导器注入值。
- 发布 Linux 候选在 Ubuntu 24.04 的系统 Python 上构建，记录实际 glibc 基线；不能声称兼容所有 Linux。本机 Arch/PBS 打包仅作为开发验证。Windows 以原生 CI 实测为依据，不扩称所有旧 Windows 或 ARM 可用。
- 构建器记录源码提交与工作区是否有改动、打包器、解释器、系统、逐文件哈希和 symlink。验收器必须在仓库外解包并验证 manifest，清除源码/venv/PYTHONPATH 等依赖，再检查真实 CLI；只用本机假模型，不读取真实凭据。
- 许可审计以实际 bundle 文件及 TOC 来源为分母，保留运行依赖、CPython、打包器、证书源的原始声明；额外原生库和静态链接部分不能由一个 CPython LICENSE 代替。coverage_complete 仅表示采集范围已查，不是法律意见；有未知项必须列 unresolved 并阻止正式发布验收。
- certifi 的实际 PEM 与许可原文、锁定 sdist URL/哈希及源获取说明一起留存；不把网页链接当作已经镜像整个上游源码。GNU GPL 的 PyInstaller bootloader exception 不能误写成 LGPL，也不能覆盖第三方依赖的许可义务。
- 候选工作流只构建、验证和保存 Actions artifact，无上传索引、tag 或 Release 步骤。0.1.0 源码版本号不表示已经发布；PyPI 实际安装、正式资产及观察窗仍是 G6 必须项。

## 验证与边界

现有 wheel 回归、冻结模式专属回归、双平台构建及解包 CLI 检查分别留证据。独立 AI 首跑从新的历史和用户材料开始，不能导入开发源码，也不冒充人工测试。冻结脚手架不增强源程序的安全隔离承诺；native 程序仍拥有既有本机执行权限。

实际结果、许可缺口与后续动作只维护在路线图 §10 及对应日期评测记录。

## 依据

- [PyInstaller onedir 与打包选项](https://pyinstaller.org/en/stable/usage.html)
- [冻结进程启动外部程序的环境边界](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html)
- [打包器许可证与 bootloader exception](https://pyinstaller.org/en/stable/license.html)
- [Python Build Standalone 完整分发与许可元数据](https://docs.astral.sh/python-build-standalone/distributions/)
- [PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/using-a-publisher/)
