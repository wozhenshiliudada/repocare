# 可选 Pyright 来源与许可

- 上游：<https://github.com/microsoft/pyright>；实际 npm 包 `pyright@1.1.414`，不是另一个 Python wrapper 或 Pylance。安装目录 `runtime/pyright/node_modules/pyright` 不入 Git。
- 锁定发行包：<https://registry.npmjs.org/pyright/-/pyright-1.1.414.tgz>；npm integrity `sha512-FPZZb51jepDX4eP7TEYDeNFtmE3WgwkkEcJpvH3/QmUSsj0EAy3LXu+xB4T/FWDejtsXlCfFr/rbWFjwuwuXww==`，见独立 package-lock。npm 安装禁用脚本，Windows 没有安装可选 fsevents；锁中保留其 macOS 条目。复现安装建议同时 omit optional。
- 实际包内 `LICENSE.txt` 原样保留 [PYRIGHT-LICENSE.txt](PYRIGHT-LICENSE.txt)：MIT，SHA256 `f7c936bc43f132b08497ac952e9376cbc102e5eedb4bf6ec902ea8442bd9c68d`。
- 实际包内 `dist/typeshed-fallback/LICENSE` 原样保留 [TYPESHED-LICENSE](TYPESHED-LICENSE)：Apache-2.0 等原完整文本，SHA256 `295f8538c94ae5c3043301cf7cff1c852dab6a786a8ddee471e061b40d5ecabe`。完整 npm 工具及其自带声明通过原包安装，不将这些声明或第三方类型推断宣传为本项目原创。这里只保留已用包内许可，不宣称完成所有平台发行物的 SBOM。
- 本次 Windows 安装实际 5,423 文件、19,457,120 字节；Node24.16.0 执行。没有增加默认 Agent 依赖、复制解析器、网络查询第三方类型或运行目标源码。
- 类型和环境边界按实际1.1.414源码核查：[配置](https://github.com/microsoft/pyright/blob/1.1.414/docs/configuration.md)、[搜索路径](https://github.com/microsoft/pyright/blob/1.1.414/packages/pyright-internal/src/analyzer/pythonPathUtils.ts)、[解释器宿主](https://github.com/microsoft/pyright/blob/1.1.414/packages/pyright-internal/src/common/fullAccessHost.ts)。上游搜索路径可回落解释器，因此本项目使用新空site-packages、显式静态平台/版本和不可执行解释器位置，不沿目标项目配置自动发现运行环境。

适配器的显式文件视图、回执检查、固定命令接线由本项目实现；这不等于通用 Python 类型证明或所有动态 API 可解析。Jedi 只研究未安装/复制。
