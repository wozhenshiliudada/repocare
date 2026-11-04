# Pi 运行时第三方来源与许可边界

- 上游仓库：<https://github.com/earendil-works/pi>
- 锁定包：`@earendil-works/pi-coding-agent@1.0.4`，直接依赖另含 `typebox@1.3.27`；精确解析见 `runtime/pi/package-lock.json`。
- 上游声明：MIT；锁定包的 `package.json` / 锁文件 `license` 字段与上游 `LICENSE` 一致。
- `Pi-LICENSE` 保存上游真实许可原文，来源固定为 <https://raw.githubusercontent.com/earendil-works/pi/7c10bd4337495ee613f2224843ecdf349b80d1df/LICENSE>；SHA-256 为 `0457f5bcec3b3b211605dfb5d1a49042fd638f3686a410fe099c24a25af13c48`。版权年份/署名保持上游原文。
- 构建 wheel 时同时保存许可正文、来源与哈希；缺失或字节变化时检查明确报告。这里的清单是随包完整性校验，不是发布者签名或第三方许可法律审计。
- 依赖许可：构建时从锁文件生成 `LICENSES/DEPENDENCY-LICENSES.json`（名称、版本、SPDX 标识、integrity、来源）；在线安装取得的各依赖保留其包内许可。锁文件声明不替代所有传递依赖的完整许可审计。
- 在线发行无需把全部 npm tarball 放进 wheel；当前不声称离线安装已支持或已验收。可选 vendor 缓存完整性与包内运行资源完整性分别报告。
- 不进入发行包：`node_modules`、测试、用户配置、`.env*`、密钥。
