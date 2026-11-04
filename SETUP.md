# 安装和启动 RepoCare

需要 Git、Python 3.11 或更新版本，以及 Node.js 24 和 npm。建议使用 Python 3.13。默认工作台在本机运行。只有在你配置模型并发起调用后，才会使用相应服务商。

## Windows PowerShell

```powershell
git clone https://github.com/wozhenshiliudada/RepoCare.git
cd RepoCare
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[ui,test]"
npm.cmd --prefix runtime/pi ci --ignore-scripts --no-audit --no-fund
npm.cmd --prefix frontend ci --ignore-scripts --no-audit --no-fund
npm.cmd --prefix frontend run build
.\.venv\Scripts\python.exe scripts/doctor.py --profile dev
.\.venv\Scripts\repocare.exe setup
.\.venv\Scripts\repocare.exe gateway run
```

保持启动终端打开，在同目录的新终端运行 `.\.venv\Scripts\repocare.exe gateway open`。

## Linux

```bash
git clone https://github.com/wozhenshiliudada/RepoCare.git
cd RepoCare
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[ui,test]"
npm --prefix runtime/pi ci --ignore-scripts --no-audit --no-fund
npm --prefix frontend ci --ignore-scripts --no-audit --no-fund
npm --prefix frontend run build
.venv/bin/python scripts/doctor.py --profile dev
.venv/bin/repocare setup
.venv/bin/repocare gateway run
```

保持启动终端打开，在同目录的新终端运行 `.venv/bin/repocare gateway open`。若 Python 缺少 `venv`，按发行版安装对应的 `python3-venv` 包。

## 第一次操作

打开工作台后，先通过「连接仓库」选一个你信任的 Git 仓库。需要真实模型时，在「设置 → 模型」填服务商的 Base URL、模型 ID 和 Key。Key 只填入本机设置。手工维护与目录监控的操作步骤见[使用说明](docs/USER_GUIDE.md)。

想先在无模型费用的固定样例里验证安装，可以用虚拟环境的 Python 执行 `python maintain.py demo --directory .demo/first-run`。请选择新目录。可用 `repocare gateway status` 查看状态，使用 `repocare gateway stop` 安全停止。

## 从源码构建安装包

完成依赖安装和前端构建后，用虚拟环境的 Python 运行：

```text
python scripts/build-workbench-wheel.py --directory .demo/package
python scripts/verify_workbench_wheel.py .demo/package/wheels
```

wheel 带已构建的网页、Pi 适配脚本、锁文件及许可证。运行机器仍需要 Python、Git、Node.js 及 Pi 的锁定依赖。安装 wheel 后，用 `repocare runtime install --json` 预览 Pi 依赖安装，再用返回的确认值执行 `repocare runtime install --approve <confirmation_sha256> --json`。
