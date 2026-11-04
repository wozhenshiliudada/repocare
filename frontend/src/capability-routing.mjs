import {ui,uiLabels} from './ui-language.mjs';
// Routing declarations distinguish existing operations from future capabilities.
export const toolWorkflows=['git-compare','preflight','python-binding','archive-import','backup','source-build',
 'case-export','source-compose','relations','structure-build','semantic-package','memory-migration','report-export','source-bind'];
export const nativeWorkflows=uiLabels({
 provider:{page:'service',label:'已接模型设置与原会话单次探测',button:'打开实际模型设置',detail:'API Key、URL、Model 可在本机保存，保存只检查格式；服务页可检查本地准备情况，单独预览和确认后使用原会话、原额度发送一次模型请求。旧任务不自动换绑。',provider:true},
 'auto-intake':{page:'issues',label:'已接有期持续接单',button:'打开实际 Issue 接单控制',detail:'按原维护规则预览并确认有期接单，保留原额度；没有默认赋予自主修复或无限期权限。'},
 'issue-comment':{page:'issues',label:'已接阶段进度评论 · 自由评论待实现',button:'打开实际 Issue 进度评论',detail:'原调查/执行可预览并明确发送阶段评论；自由评论与标签编辑没有接入。'},
 'last-success':{workflow:'ci-investigation',page:'tasks',label:'已接可比基线读取 · 需可信回执',button:'打开实际 CI 证据与基线',detail:'沿原 CI 证据接口核对准确版本；没有核准的命令/环境/依赖回执时保持无法确认，不把旧绿当可比。'},
 'unified-queue':{page:'events',label:'已接事件与待办 · 全来源自动排程待实现',button:'打开实际事件与待办',detail:'监控、原 Issue 状态与维护待办已有入口；全来源统一自动调度尚未实现。'},
 'activity-stream':{page:'tasks',label:'已接任务活动/usage · 完整订阅待实现',button:'打开原任务活动与用量',detail:'选中原维护任务查看已有阶段/工具/usage回执；未提供字段保持未知，完整流式订阅尚未实现。'},
 'service-upgrade':{page:'service',label:'已接安全停止 · 自动升级待实现',button:'打开实际服务控制',detail:'现有版本、活动与停止核验可操作；停止后的重启和升级仍由本机终端明确启动。'},
 'crash-recovery':{page:'tasks',label:'已接原恢复操作 · 通用诊断待实现',button:'打开原任务与恢复证据',detail:'选中原任务使用已实现的认领、核验、中止与接续；无法核验的现场保持，不自动重放历史。'},
 'scope-evolution':{page:'configuration',label:'已接目录规则 · 任意任务换范围待实现',button:'打开实际维护规则',detail:'原目录规则与新任务使用现有确认流程；旧任务固定范围、批准与原账不自动迁移。'},
 roles:{page:'configuration',label:'已接准入名单 · 通用角色管理待实现',button:'打开实际 Issue 准入设置',detail:'首次 Issue 规则可设置可信标签操作者；角色权限平台和旧规则名单热变更未实现。'},
});
export function capabilityRoute(id){
 if(toolWorkflows.includes(id))return {kind:'tool',label:ui("已接原 CLI / 本地工具")};
 if(nativeWorkflows[id])return {kind:'native',...nativeWorkflows[id]};
 if(id==='ci-investigation')return {kind:'ci',label:ui("已接只读 CI 证据")};
 if(['recent-projects','large-repository','dashboard','comparison'].includes(id))return {kind:'existing'};
 return {kind:'preview',label:ui("尚无专用执行接口")};
}
