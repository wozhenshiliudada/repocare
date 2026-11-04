# 可选语义检索本地环境

## 当前可用入口（2026-10-29）

固定MiniLM模型已下载到`runtime/semantic/.models/minilm-e8f8c211`，两份运行数据及README/Apache许可均按原固定SHA核验。真实48查询指标、长文本窗口、原Pi任务/原账/重启/新审批以及网页登记→新会话→明确启用→实际本地混合查询已验，详[本轮交付](../../docs/digital-media-contest/LOCAL_DELIVERY.md)。这里的真实编码器与本机合成语言模型分开记录，不代表真实修复收益。

```powershell
# 已有包只读核验，不联网：
.\runtime\semantic\.venv\Scripts\python.exe scripts/prepare-semantic-model.py --directory runtime/semantic/.models/minilm-e8f8c211
# 显式启动并登记，检索仍须在网页勾选保存：
.\runtime\semantic\.venv\Scripts\python.exe maintain.py web --semantic-model-manifest runtime/semantic/.models/minilm-e8f8c211/manifest.json
```

新机器需要下载时，给第一条增加`--download`并使用尚不存在的新目录。仅访问固定HTTPS来源、限定体积并核对SHA，失败保留现场且不登记；已有完整/部分目录拒绝覆盖。模型/依赖不会进入Git。源码开发环境使用`pip install -e ".[ui,test,semantic]"`，使Pi及样例仍按原源码布局查找；单独wheel不包含这些运行目录。

以下为上一阶段的安装记录；其中“未下载权重”描述已由本节新记录更新。

固定依赖来自根目录 `pyproject.toml` 的 `semantic` extra：`onnxruntime==1.20.1`、`tokenizers==0.23.1`、`numpy==2.3.2`。ONNX Runtime 1.20.1 的官方 Windows x64 wheel 只提供 cp310–cp313，没有 cp314；不要直接向 Python3.14 的原 `.venv` 强装或擅自升级这个固定版本。

本机2026-10-29已创建 `.venv`（Python3.13.13），安装项目及 `[ui,test,semantic]` 和传递依赖。为避免依赖旧测试目录，已有同版本解释器连同其许可复制到本目录 `.python`，通过标准 `venv --upgrade --copies` 固定为新环境的基础解释器。原根 `.venv` 的Python3.14保持；两者不会互相提供site-packages。

从仓库根使用这份环境：

```powershell
.\runtime\semantic\.venv\Scripts\python.exe -m pip check
.\runtime\semantic\.venv\Scripts\python.exe scripts/doctor.py --profile dev
.\runtime\semantic\.venv\Scripts\python.exe maintain.py web
```

最后一条仅为操作者的启动命令，本次安装没有启动/升级用户原服务或迁移旧账。需要语义检索时仍须按[原协议](../../docs/digital-media-contest/RETRIEVAL_EVALUATION.md)提供已核验的固定模型manifest，并在原页面明确启用。安装库不会下载模型权重或启用旧任务。真实编码器依然需要原固定ONNX/tokenizer数据；本次用ONNX Runtime随包sigmoid样例验证CPU推理、用本地WordLevel验证分词，没有冒充MiniLM检索评测。

新机器应先安装受支持的Python3.13、Git/Node及原Pi/前端依赖，再从仓库根创建环境：

```powershell
py -3.13 -m venv runtime/semantic/.venv
.\runtime\semantic\.venv\Scripts\python.exe -m pip install --index-url https://pypi.org/simple --only-binary=:all: ".[ui,test,semantic]"
```

虚拟环境/本地解释器不可跨机器直接搬用；`node_modules`、`.venv`、`.python`与缓存均被Git忽略，不作为源码提交或完整离线发行包。精确本机传递依赖、下载哈希、许可核对与实际检查见[安装证据](../../evidence/2026-10-29/optional-dependencies/SUMMARY.md)。这份freeze是本机快照，不是跨平台锁文件。
