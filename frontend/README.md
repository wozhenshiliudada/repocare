# RepoCare 工作台前端

React / TypeScript / Vite 薄界面，复用 Python 工作台与原 Pi 维护控制器。开发执行基线见 `docs/tasks/workbench-ui-relayout/APPROVED_V1_3.md`。

2026-10-22 完整工作台扩展：八个常驻页面与原控制层对照见 [设计](../docs/tasks/full-workbench-ui/DESIGN.md) 和 [功能覆盖](../docs/tasks/full-workbench-ui/COVERAGE.md)。原高级恢复/知识修订控件仍可使用；页面不替换原账本、Native或授权。

```powershell
npm ci --ignore-scripts --no-audit --no-fund
npm test
npm run build
```

`npm test` 包含请求不确定性、任务身份/状态、真实组件SSR与原连接适配器合同检查；SSR测试输出写入忽略的 `.demo/`。不监听端口或调用模型。

完整界面合成验收（使用新目录，开发依赖与Pi锁定依赖需已安装）：

```powershell
.venv/Scripts/python.exe -B scripts/full_workbench_preview.py --directory .demo/full-workbench-preview --port 28536
```

在仓库根目录执行。使用真实控制器/Pi SDK与本机合成SSE，GitHub资料为构造数据，远端认证和发布被显式拒绝。它可以验证Issue调查、人工审批与本地候选，不是收费模型效果或实网交付。私有启动链接只在本机输出，不写入Git；已有目录不会重置。

构建写入 `src/repo_maintainer/workbench_ui/`（不追踪生成物），由工作台固定白名单静态路由提供。先构建再打包 Python wheel。没有 CDN、外部字体、Vite 运行时服务器或放宽 CSP。

wheel 使用 setuptools≥77 保存第三方许可证。CI 在干净构建目录生成 wheel，并运行 `python scripts/verify_workbench_wheel.py .demo/ui-wheel` 比较当前资产字节、白名单和许可证。重复本地打包时，确认并清理自己上次的 `build/lib/repo_maintainer/workbench_ui` 生成副本，避免 setuptools 保留旧哈希资产；校验器会拒绝这类旧文件。

异步入口使用`AsyncButton`，由被点击的实例显示“处理中…”与`aria-busy`，图标按钮使用加载标记。直接异步回调应返回原Promise；通过`useOperation`提交后台任务时传`feedback={op.feedback}`，让按钮等待原job终态，而非HTTP响应一到就恢复。表单保留原`onSubmit`和浏览器提交行为。状态未知时显示“等待核验…”，不自动重试；原disabled、审批和请求幂等门保持。同一队列的相邻按钮不会自动显示成执行中。纯导航/展开控件继续使用原生按钮。

按钮等待的独立合成浏览器检查在`test/run-async-button-browser.mjs`，需从frontend目录执行并提供一个新的绝对证据目录；可通过`PI_UI_PLAYWRIGHT_PACKAGE`/`PI_UI_BROWSER_EXECUTABLE`指定已有Playwright包与浏览器。该夹具由测试放行Promise及后台回执，不发送真实API、GitHub或模型请求，未新增项目依赖。

工作台命令默认使用 React；`--ui legacy` 和 `/legacy` 保留原入口。缺少构建资产时会明确报出构建指令，不静默展示损坏页面。切换 UI 不更改原工作台元数据、维护范围、账本或恢复授权。Python 内部构造器保留 legacy 默认以兼容既有嵌入调用，CLI 明确传入所选 UI。

独立合成预览（必须使用新目录，不读取模型凭据）：

```powershell
.venv/Scripts/python.exe -B scripts/workbench_ui_preview.py --directory .demo/my-ui-preview --port 28314
```

使用启动日志中的完整本机链接。合成夹具采用 MockProvider，不代表真实模型修复效果。

依赖精确版本和发行完整性见 package-lock.json；运行时第三方许可证保存在 `third_party/frontend/`。本项目实现布局、任务事实投影、资料视图与桥接，不把 React/Pi 核心作为原创贡献。
