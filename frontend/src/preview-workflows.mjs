// Local product-design workflows. This module has no transport or execution capability.
import {ui} from './ui-language.mjs';
import {previewDisplayText} from './situation-preview-language.mjs';
export {previewDisplayText,previewChoiceLabel} from './situation-preview-language.mjs';
// Keep schema titles, labels and options in their original form. Captions are
// localized explicitly at display boundaries, never in choices or preview data.
const text=(key,label,extra={})=>({key,label,type:'text',required:true,...extra});
const lines=(key,label,extra={})=>text(key,label,{type:'textarea',...extra});
const choice=(key,label,options)=>text(key,label,{type:'select',options});
const number=(key,label,min=1,max=100)=>text(key,label,{type:'number',min,max});
const repository=text('repository','仓库引用');
const target=text('target','目标版本或分支');
const scope=lines('scope','读取或操作范围（每行一项）');
const acceptance=lines('acceptance','验收标准与所需证据');
const rationale=lines('rationale','目标、原因与停止条件');
/** @type {Array<[string,string,string,'cli'|'planned',any[]]>} */
const entries=[
 ['conversation','tasks','连续会话交互预览','planned',[text('topic','会话主题'),lines('messages','拟议多轮消息'),lines('context','上下文来源与任务边界'),rationale]],
 ['model-switch','configuration','任务模型切换影响预览','planned',[text('task','原任务与Provider绑定'),text('provider','拟议Provider与模型'),text('credential','凭据配置引用（不填写密钥）'),lines('impact','原协议、历史、预算与审批影响')]],
 ['recent-projects','overview','最近项目与显式重连','planned',[lines('projects','项目路径或引用（每行一项）'),choice('order','排列方式',['最近访问','按名称']),scope]],
 ['large-repository','overview','大仓库按需连接','planned',[repository,target,scope,number('pageSize','每批文件数',1,1000)]],
 ['issue-create','issues','新建问题或想法','planned',[repository,choice('type','问题类型',['Bug','想法','明确功能','文档']),text('title','Issue标题'),lines('body','现象、目标与复现步骤'),acceptance]],
 ['issue-comment','issues','Issue评论与进度回写','planned',[repository,number('issue','Issue编号',1,9999999999),lines('body','拟发布内容'),lines('evidence','关联执行与证据引用')]],
 ['issue-labels','issues','Issue标签与准入管理','planned',[repository,number('issue','Issue编号',1,9999999999),lines('add','拟添加标签'),lines('remove','拟移除标签',{required:false}),text('actor','维护者身份引用'),rationale]],
 ['auto-intake','events','持续Issue自动接单','planned',[repository,target,lines('labels','准入标签'),lines('actors','可信维护者'),number('interval','轮询秒数',30,86400),choice('mode','执行方式',['只读分诊','按已批准规则处理'])]],
 ['unified-queue','events','统一事件队列与自动排程','planned',[repository,choice('sources','事件来源',['提交、CI与Issue','仅提交','仅CI','仅Issue']),number('capacity','队列容量',1,1000),number('cooldown','冷却秒数',0,86400),lines('dedup','同源关联与过期规则')]],
 ['version-replacement','issues','新版本替换与未知人工处理','planned',[text('execution','原执行引用'),text('original','原Issue版本/提交'),text('replacement','拟替换版本'),lines('impact','旧候选、批准与额度影响'),rationale]],
 ['webhook','configuration','Webhook与GitHub App接入','planned',[repository,text('app','应用配置引用'),text('callback','回调地址',{format:'https'}),lines('events','事件订阅范围'),text('credential','凭据配置引用（不填写密钥）')]],
 ['notifications','events','邮件、手机与跨设备通知','planned',[choice('channel','通知渠道',['邮件','手机','跨设备']),text('recipient','接收目标引用'),lines('events','需通知的事件'),text('quiet','免打扰时段'),rationale]],
 ['git-compare','tasks','Git固定版本比较','cli',[repository,text('base','基线版本'),text('head','目标版本'),choice('mode','比较方式',['单提交','两版本','分支merge-base']),scope]],
 ['last-success','tasks','可比的最后成功检查','planned',[repository,target,text('check','检查或工作流引用'),text('lastSuccess','候选成功记录'),lines('comparability','环境、版本与检查可比依据')]],
 ['ci-investigation','tasks','CI精确日志与失败步骤调查','planned',[repository,text('run','Run ID'),number('attempt','Attempt',1,10000),text('job','Job或失败步骤'),lines('logs','日志来源引用与已知线索'),acceptance]],
 ['dynamic-investigation','tasks','任务内追加调查范围','planned',[text('task','原任务引用'),scope,lines('query','新增搜索或历史线索'),number('bytes','拟议读取字节上限',1,2000000),rationale]],
 ['blame','tasks','Git blame辅助调查','planned',[repository,target,text('path','文件相对路径'),text('range','行号范围'),rationale]],
 ['bisect','tasks','有界bisect调查','planned',[repository,text('good','已核验成功版本'),text('bad','已复现失败版本'),lines('predicate','可靠判定与环境要求'),number('budget','检查次数上限',1,100),acceptance]],
 ['root-evidence','tasks','根因证据核对','planned',[text('claim','待核对根因假设'),lines('reproduction','复现证据'),lines('locations','代码位置与版本依据'),lines('unknown','仍未覆盖的替代解释'),acceptance]],
 ['preflight','tasks','规划前只测与复现','cli',[repository,target,scope,lines('checks','拟批准的精确检查argv（每行JSON数组）',{format:'argv'}),acceptance]],
 ['validation-design','tasks','无测试与新增功能验收方案','planned',[repository,text('requirement','预期业务行为'),lines('reproduction','输入、复现和当前结果'),lines('checks','拟议检查与覆盖'),acceptance]],
 ['python-binding','configuration','Python绑定静态检查','cli',[repository,target,scope,text('pythonVersion','Python版本'),text('platform','目标平台'),lines('declarations','可信只读声明与基线依据')]],
 ['sandbox','configuration','执行隔离与网络策略','planned',[repository,choice('backend','拟议隔离后端',['容器','独立账号','其他受控后端']),scope,lines('network','网络允许范围'),lines('resources','资源限制与回收策略'),rationale]],
 ['provider','configuration','模型配置与连通性检查','planned',[text('provider','Provider名称'),text('baseUrl','API Base URL',{format:'https'}),text('model','Model ID'),text('credential','凭据配置引用（不填写密钥）'),number('timeout','连接超时秒数',1,1800)]],
 ['model-routing','configuration','多Provider路由与fallback','planned',[lines('providers','候选Provider与模型配置引用'),choice('policy','路由目标',['质量优先','成本优先','延迟优先']),number('budget','请求预算',1,100),lines('stop','切换约束与停止条件')]],
 ['shell','tasks','受控命令与文件工具','planned',[repository,scope,lines('argv','拟议精确argv（每行JSON数组）',{format:'argv'}),lines('impact','读写、网络与环境影响'),acceptance]],
 ['scope-evolution','configuration','维护范围变更与任务续接','planned',[text('task','原任务或规则引用'),lines('original','原固定范围'),scope,lines('budget','原账本和Native衔接要求'),rationale]],
 ['roles','configuration','可信维护者变更','planned',[repository,lines('current','当前名单引用'),lines('actors','拟议维护者名单'),lines('evidence','角色与权限证据'),rationale]],
 ['merge','delivery','合并或部署提案','planned',[repository,text('source','来源分支或PR'),text('destination','目标分支或环境'),lines('checks','交付检查与回执要求'),lines('rollback','失败恢复要求'),acceptance]],
 ['conflicts','delivery','冲突解决与协同集成','planned',[repository,lines('branches','参与分支与固定版本'),scope,lines('strategy','拟议解决方式'),acceptance]],
 ['archive-import','tasks','旧运行只读导入与核验','cli',[text('archive','归档来源路径或引用'),text('destination','目标只读资料位置'),lines('selection','所选运行与证据'),choice('verify','核验方式',['原字节完整性','完整性及明确源对照'])]],
 ['backup','memory','原历史备份与完整性验证','cli',[text('journal','原日志引用'),text('destination','新备份位置'),lines('selection','Native与元数据选择'),choice('operation','操作',['备份预览','已有备份验证'])]],
 ['service-upgrade','service','服务重启与闲置升级','planned',[text('service','服务引用'),text('version','目标构建版本'),text('journal','原状态目录引用'),lines('activity','活动任务、监控与停止条件'),rationale]],
 ['crash-recovery','tasks','异常退出与恢复条件诊断','planned',[text('task','原任务与服务引用'),text('checkpoint','原检查点引用'),lines('process','退出、进程与环境证据'),lines('unknown','未知消费与未提交尾部'),rationale]],
 ['costs','configuration','金额预算与供应商账单核对','planned',[text('provider','Provider配置引用'),text('currency','币种'),number('amount','金额上限',1,1000000),choice('window','预算窗口',['单任务','每日','每月']),text('billing','账单来源引用')]],
 ['source-build','memory','登记Markdown与固定来源索引','cli',[lines('sources','Markdown来源路径（每行一项）'),text('corpus','新资料集位置'),text('index','新索引位置'),choice('classification','内容分类',['confirmed','proposed','pending','superseded','reference']),text('repository','目标仓库引用')]],
 ['source-bind','memory','新会话来源与结构固定绑定','cli',[repository,text('source','已有来源引用'),scope]],
 ['case-export','memory','运行报告导出案例','cli',[text('run','原运行/报告引用'),lines('range','明确报告范围'),text('corpus','新案例资料集位置'),text('index','新索引位置'),rationale]],
 ['source-compose','memory','组合文档与案例来源','cli',[lines('members','现有资料集引用（每行一项）'),text('destination','新组合清单位置'),text('index','目标索引位置'),lines('classification','各成员原分类与保留规则')]],
 ['relations','memory','决策替代声明登记与核验','cli',[text('newer','新条目ID'),text('older','旧条目ID'),text('statement','原声明文件与行号'),text('manifest','新关系清单位置'),rationale]],
 ['structure-build','memory','结构建图与显式绑定','cli',[repository,target,scope,choice('languages','语言范围',['Python','JavaScript/TypeScript','Python与JavaScript/TypeScript']),text('index','新结构索引位置')]],
 ['semantic-package','memory','本地语义模型包登记','cli',[text('package','本地模型包引用'),text('manifest','模型来源与校验清单'),text('license','许可依据'),number('bytes','拟议模型包字节上限',1,2000000000)]],
 ['semantic-dedup','memory','相似资料去重预览','planned',[lines('sources','资料来源与版本'),lines('examples','待比较条目引用'),choice('strategy','拟议保留方式',['保留并列出处','人工选择保留项','提出合并候选']),rationale]],
 ['memory-migration','memory','历史资料迁移与目标绑定','cli',[text('source','原日志或备份来源'),lines('selection','明确所选资料'),text('destination','新目标资料集位置'),text('repository','目标仓库引用'),acceptance]],
 ['memory-capacity','memory','记忆容量与归档治理','planned',[text('journal','原日志引用'),number('items','条数上限',1,1000000),number('bytes','正文总字节上限',1,2000000000),choice('policy','超限策略',['保留现场并停止','提出归档候选']),rationale]],
 ['memory-sharing','memory','跨仓库只读知识共享','planned',[lines('sources','来源仓库与固定版本'),lines('destinations','目标仓库引用'),lines('selection','可共享只读资料'),lines('boundary','来源、审阅和执行权边界')]],
 ['memory-sync','memory','跨机器与应用记忆同步','planned',[text('source','来源设备或应用'),text('destination','目标设备或应用'),lines('selection','明确同步资料'),lines('conflicts','版本碰撞与来源处理'),rationale]],
 ['report-export','tasks','运行报告导出','cli',[lines('runs','原运行记录引用'),choice('format','导出格式',['JSON','Markdown']),lines('fields','所选证据字段'),text('destination','拟议导出位置')]],
 ['dashboard','overview','离线运行仪表盘','cli',[lines('runs','原运行记录引用'),text('destination','新HTML导出位置'),lines('scope','覆盖范围与未覆盖说明')]],
 ['comparison','overview','对照与展示结果导出','cli',[lines('groups','对照组与运行引用'),lines('metrics','指标及分母定义'),text('destination','拟议导出位置'),lines('limits','失败、跳过及归因限制')]],
 ['global-metrics','overview','跨仓库维护效果与成本','planned',[lines('repositories','仓库引用'),text('window','统计时间范围'),lines('metrics','指标与分母'),lines('billing','真实用量及账单来源')]],
 ['code-review','tasks','变化语义审查','planned',[repository,text('base','Base版本'),text('head','Head版本'),scope,lines('rules','审查规则与必要上下文'),acceptance]],
 ['review-findings','tasks','结构化审查发现与转修复','planned',[text('change','变化或PR引用'),text('location','文件与行号'),choice('severity','严重程度',['低','中','高','紧急']),choice('evidence','证据状态',['静态推断','已复现','原有问题','本次引入']),lines('trigger','触发、影响与证据'),acceptance]],
 ['patch-review','tasks','候选补丁独立复审','planned',[text('issue','原问题引用'),text('candidate','候选提交'),lines('diff','差异来源引用'),lines('tests','实际检查证据'),acceptance]],
 ['pr-review','tasks','PR详情、Diff与专用审查','planned',[repository,number('pr','PR编号',1,9999999999),text('version','固定Head SHA'),scope,acceptance]],
 ['dependency-analysis','tasks','依赖分析与兼容风险','planned',[repository,target,lines('packages','所选依赖与版本'),lines('impact','调用影响与许可依据'),acceptance]],
 ['dependency-upgrade','tasks','依赖升级维护','planned',[repository,lines('packages','依赖与原/目标版本'),lines('checks','回归与兼容检查'),lines('policy','发布及停止策略'),acceptance]],
 ['documentation','tasks','文档维护任务','planned',[repository,text('requirement','文档目标'),lines('sources','事实来源'),scope,acceptance]],
 ['health-report','tasks','仓库健康与周期报告','planned',[repository,target,lines('checks','已有检查与证据来源'),text('schedule','拟议周期'),lines('coverage','覆盖与未覆盖范围')]],
 ['security','tasks','安全告警与漏洞修补','planned',[repository,text('advisory','告警来源引用'),text('versions','受影响版本'),lines('evidence','复现、缓解与权限要求'),acceptance]],
 ['architecture','tasks','架构治理与技术债','planned',[repository,text('goal','可验证目标'),scope,lines('evidence','问题与约束依据'),acceptance]],
 ['worktree-collaboration','configuration','多worktree与多写者协作','planned',[repository,lines('writers','执行者与职责'),lines('worktrees','独立工作区引用'),lines('conflicts','冲突、锁和接管规则'),acceptance]],
 ['mcp','configuration','MCP工具接入','planned',[text('server','服务配置引用'),lines('tools','所选工具及版本'),lines('permissions','数据与操作范围'),lines('lifecycle','启动、回执与停止要求')]],
 ['agents','configuration','多Agent分工编排','planned',[lines('roles','角色与职责'),lines('scope','独立读写范围'),lines('coordination','交接、冲突与停止规则'),number('budget','共享请求总上限',1,100)]],
 ['external-cli','configuration','外部CLI协作','planned',[text('tool','工具与版本引用'),text('task','单项委派目标'),scope,acceptance]],
 ['skills','configuration','受控Skill与插件装载','planned',[text('skill','Skill/插件来源与版本'),text('license','许可与审阅依据'),lines('permissions','工具、脚本与数据范围'),lines('stop','停止与撤销条件')]],
 ['editor','tasks','代码本地编辑预览','planned',[text('path','文件相对路径'),lines('before','明确原文本'),lines('after','拟议新文本'),acceptance]],
 ['git-operations','delivery','额外Git操作预览','planned',[repository,choice('operation','操作类型',['切换分支','创建提交','其他受控操作']),target,scope,lines('impact','原工作区及未提交修改影响')]],
 ['activity-stream','tasks','完整活动订阅与用量时间线','planned',[text('task','原任务或Issue执行引用'),lines('events','希望订阅的阶段、工具与路径字段'),lines('usage','请求、token及时间字段与原账关联'),lines('limits','断线续读、截断与未知记录处理')]],
 ['memory-history','memory','资料时序与版本对照','planned',[lines('entries','同主题条目与版本引用'),text('window','对照时间范围'),lines('sources','各版本原始出处与指纹'),lines('changes','分类、审阅、新鲜度与冲突核对要求')]],
];
/** @type {import('./capability-types').WorkflowDefinition[]} */
export const workflows=entries.map(([id,page,title,connection,fields])=>({id,page,title,connection,fields}));
export function validateDraft(workflow,values){
 const errors={};
 for(const field of workflow.fields){
  const value=String(values[field.key]??'').trim();
  if(field.required&&!value){errors[field.key]=previewDisplayText('请填写')+previewDisplayText(field.label);continue;}
  if(!value)continue;
  if(value.length>4000||/[\u0000-\u0008\u000b-\u001f]/.test(value)){errors[field.key]=previewDisplayText('内容超长或包含控制字符');continue;}
  if(field.type==='select'&&!field.options.includes(value))errors[field.key]=previewDisplayText('请选择列出的选项');
  if(field.type==='number'&&(!Number.isSafeInteger(Number(value))||Number(value)<field.min||Number(value)>field.max))errors[field.key]=ui(`请输入${field.min}至${field.max}之间的整数`,`Enter an integer between ${field.min} and ${field.max}`);
  if(field.format==='https'){try{const url=new URL(value);if(url.protocol!=='https:'||url.username||url.password)throw Error();}catch{errors[field.key]=previewDisplayText('请填写不含凭据的HTTPS地址');}}
  if(field.format==='argv'){try{for(const row of value.split('\n')){const argv=JSON.parse(row);if(!Array.isArray(argv)||!argv.length||!argv.every(v=>typeof v==='string'&&v.trim()))throw Error();}}catch{errors[field.key]=previewDisplayText('每行应为非空字符串组成的JSON argv数组；不会执行命令');}}
  if(/(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{16,}|(?:api[_ -]?key|password|secret|token)\s*[:=]\s*\S{8,})/i.test(value))errors[field.key]=previewDisplayText('请使用配置引用，勿填写密钥或令牌');
 }
 return errors;
}
export function previewDraft(workflow,values){
 const errors=validateDraft(workflow,values);if(Object.keys(errors).length)return {errors,preview:null};
 return {errors,preview:{workflow:workflow.id,title:workflow.title,status:'local_preview_only',executed:false,
  connection:workflow.connection,inputs:Object.fromEntries(workflow.fields.map(f=>[f.key,String(values[f.key]??'').trim()])),
  blocking_dependency:workflow.connection==='cli'?'现有CLI能力尚无网页执行接口':'此扩展尚无专用执行接口；已有部分能力以实际操作入口为准'}};
}
