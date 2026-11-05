# 参与 RepoCare 开发

先按 [SETUP.md](SETUP.md) 安装开发依赖。通过 Issue 描述实际行为、期望结果和最小复现。可以从独立分支提交 PR，并注明运行过的检查及其结果。

```text
npm --prefix frontend test
npm --prefix frontend run build
python -m unittest discover -s tests -v
npm --prefix runtime/pi test
```

Windows CI 将每个 Python 版本的完整测试分成四批，分别使用独立 runner，避免单个作业超过六小时。同一模块的测试保持在同一批，原有断言、时限和重复发现的测试均保留。复现指定批次可运行：

```text
python scripts/ci_unittest.py --shard 1 --shards 4 --report .demo/ci-results/python-1.json
```

将 `--shard` 依次设为 1、2、3、4 才覆盖全量；单批通过不表示完整 Windows 检查通过。CI 保存各批的发现清单、执行结果和耗时。Linux 继续运行上面的完整 `unittest discover` 命令。

默认测试使用固定样例和本机合成响应。请用合成凭据和可公开的仓库复现，不要提交真实 Key、原始会话链接、私有项目的运行日志或账本。缺少私有历史评测档案时，只跳过直接复核原档案的用例。公开测试仍使用合成数据验证历史证据完整性和权限边界。

请确认你有权按 [AGPL-3.0-only](LICENSE) 提交你的贡献。引入第三方内容时，说明来源、版本和许可证，并保留原有声明。
