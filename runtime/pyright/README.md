# 可选 Python 名称与调用检查

锁定 Pyright **1.1.414**。这是另外安装的检查工具，不进入默认 Pi/Python 安装依赖。原 AgentSession、测试命令权限、人工审批和回滚仍由现有系统负责。

安装：在本目录运行 `npm ci --ignore-scripts --omit=optional --no-audit --no-fund`。不运行目标仓库的安装脚本。Windows 本次实际安装 5,423 个文件、19,457,120 字节（约 18.56 MiB），没有安装可选的 macOS fsevents；这不是所有平台的最终打包大小。需要可信 Node 可执行文件。

从**目标仓库/隔离候选根目录**执行下面的参数数组；绝对路径换为自己的已安装路径：

```json
["{python}","C:/path/to/repocare/scripts/python_binding_check.py","--source","calculator.py","--check","calculator.py","--node","C:/Program Files/nodejs/node.exe","--python-version","3.11","--python-platform","Linux"]
```

`--source` 明确复制的 Python 源码或类型声明，最多 50 份、每份 100000 字节；需要依赖本地模块时逐份提供。`--check` 是其中要求出诊断的文件，最多 20 份。`--python-version`/`--python-platform` 指**静态分析目标**，不表示已经在该版本/平台运行过程序。支持静态版本 3.11–3.14、Linux/Windows/Darwin；本次实际宿主验收仅 Windows/Python3.13.3/Node24.16.0，静态目标 Linux/3.11。

先在干净基线上运行、审阅缺失导入/Unknown 警告，确认适用，再在**新任务**的 Policy 中作为原运行测试之外的附加固定命令，保留原运行测试。只读辅助源/`.pyi` 要固定并放在可编辑范围外。不要为使门变绿虚构声明或删错误，也不要临时修改旧任务/冻结单。尚无自动 UI 选项或 SWE-Bench-CL freeze 选项。

它只分析临时源码副本＋Pyright 自带 typeshed；不扫描整个仓库，不使用目标仓库配置、已安装第三方包或主机 Python 搜索路径。目标文件不被导入/执行；空静态环境和不存在的固定解释器路径防止查询目标 Python，Node 子进程不继承密钥或 NODE_OPTIONS。它不是操作系统安全沙箱，工具安装本身仍须可信。禁止源内 `# pyright:` 配置，原样保留但关闭 `# type: ignore` 的忽略效果。

返回 0：未发现所选静态错误；1：有静态错误；2：不支持/基础设施失败。有效诊断含名称/位置与源 SHA，JSON 控制在 16000 字节以内、诊断最多 40 条；缺失回执、诊断数/退出码冲突、源码中途变化、超时不算通过。启用四条错误规则：调用、属性、未定义名称和未绑定局部名称，Python 语法错误也会报告。缺失导入、Unknown 等仅作覆盖警告，**Any/Unknown 仍可能漏检**；0 不是语义正确或类型覆盖完整证明。

已知 SymPy 旧 C 的实测：四份原授权源码的基线有 4 错误/326 警告，C 第二轮虚构属性未被新识别，末轮增加 1 个布尔调用错误。这个不完整静态视图尚不适合直接作为该库硬门。详情见 [PYTHON_BINDING_CHECK](../../docs/digital-media-contest/PYTHON_BINDING_CHECK.md)。
