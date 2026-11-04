# 参与 RepoCare 开发

先按 [SETUP.md](SETUP.md) 安装开发依赖。通过 Issue 描述实际行为、期望结果和最小复现。可以从独立分支提交 PR，并注明运行过的检查及其结果。

```text
npm --prefix frontend test
npm --prefix frontend run build
python -m unittest discover -s tests -v
npm --prefix runtime/pi test
```

默认测试使用固定样例和本机合成响应。请用合成凭据和可公开的仓库复现，不要提交真实 Key、原始会话链接、私有项目的运行日志或账本。两个只读复核私有历史评测档案的用例会在公开检出中明确跳过。

请确认你有权按 [AGPL-3.0-only](LICENSE) 提交你的贡献。引入第三方内容时，说明来源、版本和许可证，并保留原有声明。
