# RepoCare

RepoCare 是在本机运行的 Git 仓库维护工作台。它能调查提交、Issue 与 CI 失败，在授权范围内准备隔离修复候选，并把测试、改动和交付记录留给维护者审阅。

当前为公开开发版。正式版本将在 Windows 和 Ubuntu 的四组 CI 检查通过后发布。

- **发现问题。** 连接本地仓库或 GitHub 仓库后，可主动发起维护任务，也可设置分支监控和目录维护规则。
- **修复和验证。** 通过 Pi SDK 调用你选择的模型，绑定已知源码版本，在隔离候选中复现和复测。默认由维护者审核，授权后可自动准备候选。
- **审阅和交付。** 查看修改前后的检查、差异和请求用量。候选发布和交付有各自的确认入口。

[安装与启动](SETUP.md) · [中文使用说明](docs/USER_GUIDE.md) · [图文快速上手](docs/tutorials/click-guide-20261031/README.md) · [参与开发](CONTRIBUTING.md)

## 开源许可

Copyright © 2026 RepoCare contributors. RepoCare 的项目代码以 **GNU Affero General Public License version 3 only** 发布，SPDX 标识为 `AGPL-3.0-only`。许可证正文见 [LICENSE](LICENSE)。第三方组件保留各自的许可及版权声明，见 [THIRD_PARTY.md](THIRD_PARTY.md)。

GitHub 上与发布提交对应的源码和构建文件可用于复现该版本。托管修改版时，请按许可证第 13 节向远程使用者提供实际运行版本的对应源码。
