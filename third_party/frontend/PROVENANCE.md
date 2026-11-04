# 工作台前端依赖来源

2026-10-20，通过官方 npm registry 核对发行版本，禁安装脚本安装，精确锁定于 `frontend/package-lock.json`（包含发行完整性）。

- React / react-dom 19.3.1，MIT，Meta Platforms, Inc. and affiliates；源码 https://github.com/facebook/react ，对应许可完整保存为 React-LICENSE、ReactDOM-LICENSE。
- scheduler 为 react-dom 锁定的运行依赖，MIT，完整许可 Scheduler-LICENSE；实际版本以锁文件为准。
- 构建输出含 Vite 的 modulepreload 与 Rolldown 互操作辅助代码，故一并保存完整 Vite-LICENSE、Rolldown-LICENSE、Rolldown-THIRD-PARTY-LICENSE 并随 wheel 分发；它们不是新的 Agent 执行依赖。
- 开发构建：Vite 8.3.4 / @vitejs/plugin-react 6.1.2（MIT）、TypeScript 7.0.2（Apache-2.0）、@types/react 19.3.0 / @types/react-dom 19.3.0 / @types/node 22.19.21（MIT）。不进入 Python Agent 执行依赖，发行来源和传递依赖保留于 npm 锁文件。

参考 Vite 官方模板的构建组织方式，自写极小配置；未复制 gitgraph.js 核心或 Vibe-Trading 源码，不引入其 UI/字体依赖。拓扑视图遵守 Git log --topo-order。完整检索取舍见 docs/tasks/workbench-ui-relayout/APPROVED_V1_3.md。

实际生产构建约 249KB JavaScript / 20KB CSS（未压缩传输大小，随修改变化）；不得将 gzip 大小当安装大小或模型性能收益。依赖许可不代表本项目已选择整体 License。
