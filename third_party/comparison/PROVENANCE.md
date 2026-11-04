# junitparser 报告依赖来源（2026-10-09）

- 官方项目：https://github.com/weiwei/junitparser ，版本5.0.3，Apache-2.0；固定在 pyproject.toml。
- 安装方式：pip从官方PyPI获取二进制wheel，--only-binary=:all: --no-deps，安装到工作区 recovery-python-deps，不更新全局Python。
- junitparser-LICENSE.txt 原样取自该wheel的 dist-info/licenses/LICENSE；版权2025 Weiwei Wang。SHA256：51bae38bd50369849f7006fa5fdde9ecbd81824ebb07667800818f63bc82c359。
- Apache-2.0.txt 为完整1–9条许可条款，从本仓库既有 dashboard_assets/vendor/hogan-LICENSE.txt 的 Apache正文复制；不复制Hogan版权或代码。SHA256：0cec06e0e55fbc3dc5cee4fca9b607f66cb8f4e4dbcf3b3c013594dd156732e9。
- 本记录只核验上述许可文件，不宣称已记录wheel校验值。代码通过固定依赖安装，不重新分发第三方Python实现。
- 实际用途为生成 contracts.xml；不是评分算法或Pi运行引擎。项目自身许可证未选择。
