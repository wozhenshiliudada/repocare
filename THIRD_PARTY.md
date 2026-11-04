# 第三方组件及许可

本仓库的 RepoCare 项目代码采用 [AGPL-3.0-only](LICENSE)。以下第三方组件适用其原许可证。

| 组件 | 用途 | 来源和许可记录 |
| --- | --- | --- |
| Pi SDK | 模型请求、工具和会话 | [Pi 来源与 MIT 许可](third_party/pi/PROVENANCE.md) |
| React、Vite 及 Rolldown | WebUI 和构建产物中的辅助代码 | [前端来源与许可](third_party/frontend/PROVENANCE.md) |
| diff2html、diff、Hogan | 差异浏览和离线报告 | [内置资源来源](src/repo_maintainer/dashboard_assets/vendor/PROVENANCE.md) |
| filelock、psutil | 本地锁及进程识别 | [恢复组件来源与许可](third_party/recovery/PROVENANCE.md) |
| junitparser | JUnit XML 输出 | [报告组件来源与许可](third_party/comparison/PROVENANCE.md) |
| Starlette、Uvicorn、HTTPX 等 | 本机服务和测试 | [本机界面组件许可](third_party/local-ui/PROVENANCE.md) |
| CodeGraph、Pyright | 可选结构分析与静态检查 | [CodeGraph](third_party/codegraph/PROVENANCE.md) · [Pyright](third_party/pyright/PROVENANCE.md) |

Python 直接依赖见 `pyproject.toml`。Node.js 依赖见各自的 `package-lock.json`。Git 和 Git Credential Manager 使用本机安装版本。可选语义模型由对应准备脚本从其上游取得，并遵循各自的许可。
