// Exact fixed copy from service_readiness.py only. Raw errors, paths and reports
// never enter this allowlist. Unknown identities/text remain original.
import {ui} from './ui-language.mjs';
const identities=new Set(['python','git','node','pi_lock','ui_index','pi_runtime','pi_resources','python_dependencies','pi_dependencies','ui_assets','provider_config','installation','installation_report','installation_extra']);
const copy=new Set([
  "程序安装与运行时",
  "本次未取得完整安装检查结果。",
  "使用当前源码目录运行安装自检。",
  "Python 版本",
  "使用 Python 3.11 或更新版本。",
  "Git 工具",
  "安装 Git 并确认当前程序可以找到它。",
  "Node.js 版本",
  "安装符合 SETUP.md 要求的 Node.js。",
  "Pi 依赖锁文件",
  "使用完整源码目录，并按 SETUP.md 安装 Pi 依赖。",
  "工作台网页入口",
  "按 SETUP.md 安装并构建前端。",
  "Pi 运行资源",
  "重新安装可信发行包。",
  "Pi 资源完整性",
  "重新安装包含完整运行资源与许可的发行包。",
  "Python 安装包未携带 Pi 运行目录；请按 SETUP.md 使用受支持的完整源码安装。",
  "Python 安装包未携带外部 Pi 运行目录；不能直接执行维护。",
  "缺失、版本不符或本地检查未完成。",
  "本地检查通过。",
  "Python 网页依赖",
  "在当前 Python 环境按 SETUP.md 安装网页依赖。",
  "Pi 安装依赖",
  "在当前源码目录按锁文件安装 Pi 依赖。",
  "网页脚本与样式",
  "按 SETUP.md 重新构建前端，然后重新检查。",
  "运行 repo-maintainer runtime install 查看锁定依赖的安装计划，再明确确认。",
  "Python 安装包不包含外部 Pi Node 依赖；请按 SETUP.md 使用完整源码安装。",
  "所需文件或依赖未全部通过检查。",
  "安装检查完整性",
  "安装检查结果不完整，不能认定本地条件齐备。",
  "重新运行当前版本的安装自检。",
  "其他安装条件",
  "安装检查仍有未通过项目。",
  "查看当前版本安装自检并补齐缺项。",
  "在本机编辑启动时指定的模型配置文件，然后重新检查。",
  "模型配置文件",
  "尚未明确指定模型配置；安装检查不会自动寻找密钥。",
  "选择自己的本地模型配置文件，并在启动或自检时明确指定。",
  "需要明确指定本地配置文件。",
  "配置路径包含链接目录或链接文件，不能安全读取。",
  "配置必须是大小受限的普通本地文件。",
  "示例内容尚未替换，请填写自己的密钥、地址和模型。",
  "指定的本地配置文件不存在。",
  "文件不可安全读取，或密钥、HTTPS 地址、模型字段格式无效。",
  "本地字段格式通过；尚未验证真实模型请求。"
]);
export function readinessDisplayText(row,field){
 const value=row?.[field];
 return identities.has(row?.id)&&['label','detail','next_action'].includes(field)&&copy.has(value)?ui(value):value;
}
