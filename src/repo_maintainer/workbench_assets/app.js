'use strict';
window.mountWorkbenchOnboarding = function(root, options={}) {

// Only explicitly registered product messages are translated. Repository text,
// model output, commands, JSON, identifiers and editable values stay verbatim.
const i18n = (() => {
 let language=options.language==='en'?'en':'zh-CN';
 const dictionary={
 " · 进行中预留：": " · In-flight reservations: ",
 " · 已结束待核对：": " · Ended, unverified: ",
 " · 预留或待核对：": " · Reserved or unverified: ",
 "请求失败": "Request failed",
 " · 执行中": " · Running",
 "尚未连接仓库。": "No repository connected.",
 "会话已变化，请重新连接仓库": "The session changed. Reconnect the repository.",
 "维护界面加载失败": "Could not load the maintenance view.",
 "工作台地址无效，请使用当前统一入口": "Invalid workbench address. Use the current shared entry.",
 "仅将确认的文件提供给模型；最多 ": "Only confirmed files are provided to the model: up to ",
 " 个，单文件 ": " files, ",
 " KiB。测试默认只读。": " KiB per file. Tests are read-only by default.",
 "工作台会话绑定无效": "Invalid workbench session binding.",
 "尚未打开仓库。": "No repository opened.",
 "请在 GitHub 官方页面完成登录。": "Complete sign-in on the official GitHub page.",
 "正在处理…": "Working…",
 "操作记录不可用，请刷新检查，不要重复点击": "The operation record is unavailable. Refresh to check; do not submit again.",
 "操作完成。": "Operation complete.",
 "可修改：": "Editable: ",
 "尚未选择": "None selected",
 "只读：": "Read-only: ",
 "恢复已有检查与授权范围": "Restore saved checks and scope",
 "本机已确认维护配置": "Locally confirmed maintenance configuration",
 "沿用原配置与请求账本，历史审批不会自动执行。": "Keeps the original configuration and request ledger. Previous approvals are never replayed.",
 "本次不改变原超时、范围或额度。": "Keeps the original timeout, scope and budget.",
 "没有找到可靠的检查命令。可以先只读分析；需要修复时，再确认有效检查或在高级设置填写。": "No reliable check command found. Start with read-only analysis, then confirm valid checks or enter them in advanced settings before repair.",
 "出处：": "Source: ",
 "查看将执行的命令": "View the commands to run",
 "分支：": "Branch: ",
 "工作区干净，可确认维护范围。": "The working tree is clean. Confirm the maintenance scope.",
 "存在未提交修改。维护会话需先自行处理；只读分析不会修改这些文件。": "Uncommitted changes exist. Resolve them before opening maintenance; read-only analysis leaves them intact.",
 "第 {0} / {1} 页 · 匹配 {2} 项": "Page {0} / {1} · {2} matches",
 " 文件范围草稿": " file scope draft",
 "不读取": "Do not read",
 "可修改": "Editable",
 "只读": "Read-only",
 "尚未确认检查方式。请选择推荐检查、在高级设置填写，或先只读分析。": "No checks confirmed. Choose a recommendation, enter advanced checks, or start with read-only analysis.",
 "自定义命令格式有误，请使用 JSON 参数数组。": "Invalid custom command format. Use JSON argument arrays.",
 "至少需要一条有效检查命令；无测试时可先只读分析。": "At least one valid check command is required. Without tests, start with read-only analysis.",
 "请选择可修改源码；仅查看项目可先只读分析。": "Select editable source files. To inspect the project, start with read-only analysis.",
 "只读分析报告 · 尚未验证": "Read-only analysis · Unverified",
 "只读分析完成；未运行功能测试，项目仍尚未验证。": "Read-only analysis complete. No functional tests ran; the project remains unverified.",
 "模型请求：0 · 项目命令：0 · 文件修改：0": "Model requests: 0 · Project commands: 0 · File changes: 0",
 " 第": " line ",
 "行": "",
 "发现 Python 语法错误；未执行代码，未修改文件。": "Python syntax error found. No code executed and no files changed.",
 "语法解析未完成。": "Syntax parsing incomplete.",
 "语法可解析；不代表功能正确。": "Syntax parsed; this does not establish functional correctness.",
 "本轮未解析 Python 源码；其他语言只展示配置与验证缺口。": "No Python source was parsed. Other languages show configuration and verification gaps only.",
 "已登录：": "Signed in: ",
 " · 私有": " · Private",
 " · 公开": " · Public",
 " · 已归档": " · Archived",
 "已选择仓库，请点击连接并克隆。": "Repository selected. Click Connect and clone.",
 "此页没有可访问的仓库。": "No accessible repositories on this page.",
 "点击进入 ": "Open ",
 "仓库已连接，尚未调用模型。": "Repository connected. No model called.",
 "连接操作正在进行，请等待完成。": "Connection is in progress. Wait for it to finish.",
 "连接 ": "Connect ",
 "正在取得此仓库的本地副本；不会运行测试或调用模型。": "Getting a local copy of this repository. No tests or model calls are run.",
 "本地副本已就绪。首次维护只需确认下方文件范围和检查方式。": "The local copy is ready. Confirm the file scope and checks below before the first maintenance task.",
 "请先勾选确认检查方式和维护范围": "Confirm the checks and maintenance scope first.",
 "请先处理仓库中已有的修改，再重新检查": "Resolve existing repository changes, then inspect again.",
 "选择本地仓库，或连接 GitHub。新连接仓库默认请求上限为 ": "Choose a local repository or connect GitHub. The default request limit for new connections is ",
 " 次；已有仓库保留各自额度。": ". Existing repositories keep their own budgets.",
 "正在检测": "Checking",
 "正在诊断": "Diagnosing",
 "待审批": "Awaiting approval",
 "授权测试通过": "Authorized tests passed",
 "CI 需关注": "CI needs attention",
 "检查受阻": "Check blocked",
 "上次中断": "Previously interrupted",
 "旧方案失效": "Previous plan stale",
 "方案已处理，打开核验历史报告": "Plan handled; open and verify the historical report",
 "历史证据待核验": "Historical evidence needs verification",
 "候选已验证，等待交付决定": "Candidate verified; awaiting delivery decision",
 "修复失败，候选已回滚": "Repair failed; candidate rolled back",
 "候选已清理": "Candidate cleaned up",
 "已拒绝修改": "Changes rejected",
 "进入方案审阅与修复报告": "Open plan review and repair report",
 "监控中": "Monitoring",
 "已暂停": "Paused",
 " · 间隔 ": " · Interval: ",
 " 秒 · 模型剩余额度 ": " s · Remaining model requests: ",
 "维护进度：已观察 ": "Maintenance progress: observed ",
 "尚无提交": "No commit yet",
 "；保留 ": "; retained ",
 " 个完整本地检查记录。已观察不代表已检查；候选通过不代表已合并。": " complete local check records. Observed does not mean checked; a passing candidate does not mean merged.",
 "暂停": "Pause",
 "恢复": "Resume",
 "Pi 仓库维护": "Pi repository maintenance",
 "CI 信息未取得：": "CI information unavailable: ",
 "检测测试与退出码": "Detection tests and exit codes",
 "\n退出码 ": "\nExit code: ",
 " · 超时": " · Timed out",
 "只读查看历史方案、Diff与测试": "View historical plans, diffs and tests (read-only)",
 "历史记录 · 只读，不代表当前版本或执行授权": "Historical record · Read-only; does not establish the current version or execution permission",
 "审阅方案并决定是否修改": "Review the plan and decide on changes",
 "查看方案与运行报告": "View plan and run report",
 "运行结果：": "Run results: ",
 "维护仓库 ": "Repositories monitored: ",
 " 个 · 待关注 ": " · Needing attention: ",
 " 项": " items",
 "尚未启用自动维护。": "Automatic maintenance is not enabled.",
 "请先确认自动检测、测试和模型额度授权": "Confirm permission for automatic checks, tests and model requests first.",
 "此仓库没有可用的 GitHub HTTPS origin，请先通过 GitHub 连接": "No usable GitHub HTTPS origin. Connect through GitHub first.",
 "监控已启用，首次检测将自动开始。修改前仍需审批。": "Monitoring enabled. The first check starts automatically; changes still require approval.",
 "此浏览器不支持通知；请查看页面维护待办。": "This browser does not support notifications. Check the maintenance list on this page.",
 "已允许本页面的维护通知。": "Maintenance notifications are allowed for this page.",
 "浏览器通知未允许；页面待办仍可查看。": "Browser notifications are not allowed. The on-page list remains available.",
 "通知设置": "Notification settings",
 "Python 功能测试（unittest）": "Python functional tests (unittest)",
 "运行 {0} 中的测试，比较功能结果是否符合已有断言。": "Run tests in {0} and compare functional results with existing assertions.",
 "需要项目所需依赖；发现命令不代表环境就绪或已有测试通过。": "Project dependencies are required. Discovering a command does not establish environment readiness or passing tests.",
 "Python 功能测试（pytest）": "Python functional tests (pytest)",
 "运行项目的 pytest 测试；具体范围由项目配置决定。": "Run project pytest tests; project configuration determines their scope.",
 "需要 pytest 和项目依赖；插件及 conftest.py 也属于将执行的仓库代码。": "Requires pytest and project dependencies. Plugins and conftest.py are also repository code that will run.",
 "Node 功能测试": "Node functional tests",
 "项目构建检查": "Project build check",
 "代码规范检查": "Code style check",
 "执行项目声明的 {0} 脚本。": "Run the project’s declared {0} script.",
 "构建/规范通过不等于功能没有 bug。": "Passing build or style checks does not establish functional correctness.",
 "需要本机 npm 和已有依赖；显式脚本仍会执行仓库代码，不会自动安装依赖。": "Requires local npm and existing dependencies. Explicit scripts still execute repository code; dependencies are never installed automatically.",
 "Node 原生功能测试": "Native Node functional tests",
 "运行已识别的 node:test 测试文件（最多8个），不代表覆盖整个仓库。": "Run identified node:test files (up to 8); this does not establish full repository coverage.",
 "需要支持 node:test 的 Node 版本及项目依赖。": "Requires a Node version supporting node:test and project dependencies.",
 "检查线索较多，仅解析前40个文件；结果可能不完整。": "Many check clues found. Only the first 40 files were parsed; results may be incomplete.",
 "发现 GitHub CI 配置；复杂服务、矩阵及远程命令未自动转换，请结合 CI 手动确认。": "GitHub CI configuration found. Complex services, matrices and remote commands were not converted automatically; verify them against CI manually.",
 "发现 README，可用于确认环境说明；不会执行其中的自由文本命令。": "README found. Consult it for environment requirements; its free-form commands are not executed.",
 "发现 tox 配置；本轮不创建 tox 环境、不安装依赖，复杂环境需手动配置。": "tox configuration found. This step creates no tox environments and installs no dependencies; configure complex environments manually.",
 "建议范围只覆盖部分源码，优先核心包；不支持把“检查整个仓库所有问题”当作完整扫描。请按具体问题调整范围。": "The suggested scope covers selected source files, prioritizing core packages. A request to find every repository issue is not a full scan. Adjust scope for a specific problem.",
 " 不是 UTF-8 文本，未解析。": " is not UTF-8 text and was not parsed.",
 "{0} 不是 UTF-8 文本，未解析。": "{0} is not UTF-8 text and was not parsed.",
 " 无法解析，未从中推断命令。": " could not be parsed; no commands were inferred from it.",
 "{0} 无法解析，未从中推断命令。": "{0} could not be parsed; no commands were inferred from it.",
 "确认项目启动方式和依赖环境。": "Confirm project startup steps and dependencies.",
 "选一个具体问题，写下输入、预期结果与实际结果。": "Choose a specific issue and record inputs, expected results and actual results.",
 "为该问题补充可审阅的测试或确认人工验证步骤；本轮不会自动生成或修改测试。": "Add reviewable tests for that issue or confirm manual verification steps. This step does not automatically generate or change tests.",
 "确认有效检查和维护范围后，再进入原审批修复流程。": "Confirm valid checks and maintenance scope, then enter the original approval and repair process.",
 "Pi · 仓库维护工作台": "Pi · Repository maintenance workbench",
 "仓库维护工作台": "Repository maintenance workbench",
 "本机连接": "Local connection",
 "仓库": "Repositories",
 "连接仓库": "Connect repository",
 "切换仓库": "Switch repository",
 "切换视图保留任务、审批和消费记录。": "Switching views retains tasks, approvals and consumption records.",
 "从你的项目开始": "Start with your project",
 "选择一个仓库。": "Choose a repository.",
 "连接后确认维护范围，在独立会话中审阅方案、运行测试。": "Confirm the scope after connecting. Review plans and run tests in a dedicated session.",
 "正在连接本地工作台…": "Connecting to the local workbench…",
 "本地仓库": "Local repository",
 "选择已有 Git 仓库的根文件夹。": "Choose the root folder of an existing Git repository.",
 "文件夹路径": "Folder path",
 "选择文件夹": "Choose folder",
 "检查仓库": "Inspect repository",
 "GitHub 仓库": "GitHub repository",
 "点击检查登录，复用本机 Git 登录状态。": "Check sign-in to reuse the local Git session.",
 "检查登录": "Check sign-in",
 "登录 GitHub": "Sign in to GitHub",
 "查看我的仓库": "View my repositories",
 "在 GitHub 页面输入一次性授权码：": "Enter the one-time authorization code on GitHub:",
 "前往 GitHub 授权": "Authorize on GitHub",
 "仓库地址": "Repository URL",
 "连接并克隆": "Connect and clone",
 "上一页": "Previous page",
 "下一页": "Next page",
 "确认检查方式": "Confirm checks",
 "我们先找出这个项目已有的检查方法。下方建议尚未执行；确认后，修复仍需审批。": "First identify the checks this project already provides. Suggestions below have not run; repairs still need approval after confirmation.",
 "建议维护范围": "Suggested maintenance scope",
 "仅将确认的文件提供给模型；文件容量以当前服务配置为准。测试默认只读，建议可在高级设置调整。": "Only confirmed files are provided to the model. Capacity depends on current service settings. Tests are read-only by default; adjust suggestions in advanced settings.",
 "高级设置：自定义命令与文件范围": "Advanced settings: custom commands and file scope",
 "筛选文件": "Filter files",
 "输入文件名": "Enter a filename",
 "上一页文件": "Previous file page",
 "下一页文件": "Next file page",
 "自定义检查命令（JSON 参数数组）": "Custom checks (JSON argument arrays)",
 "内层数组是一条命令，每个参数单独填写。修改此处将采用自定义检查；不会自动安装依赖。": "Each inner array is one command, with separate arguments. Editing here selects custom checks; dependencies are never installed automatically.",
 "每条测试超时（秒）": "Timeout per test (seconds)",
 "允许在选定范围内自主修复": "Allow autonomous repair within the selected scope",
 "我确认检查方式和维护范围；后续生成方案会使用本地模型配置并可能计费": "I confirm the checks and maintenance scope. Later planning uses the local model configuration and may incur costs.",
 "确认并打开维护工作台": "Confirm and open maintenance workbench",
 "也可以先了解项目": "Explore the project first",
 "没有测试或暂时不准备修复？先做本地只读分析：查看检查线索、最多20个 Python 文件的语法和验证缺口。不会调用模型、执行项目或修改文件。": "No tests, or not ready for repair? Start with local read-only analysis: check clues, syntax in up to 20 Python files, and verification gaps. It calls no model, runs no project commands and changes no files.",
 "先只读分析": "Start read-only analysis",
 "自动维护：监控指定分支": "Automatic maintenance: monitor a branch",
 "检测提交并运行上述测试；发现失败后自动生成方案，修改前仍需你批准。检测使用独立快照，仅检查已提交的代码。": "Detect commits and run the selected tests. Failures trigger a plan, but changes still need your approval. Detection uses isolated snapshots of committed code only.",
 "监控来源": "Monitor source",
 "本地分支提交": "Local branch commits",
 "GitHub 远程分支（复用登录）": "GitHub remote branch (reuse sign-in)",
 "分支名": "Branch name",
 "检查间隔（秒，最少60）": "Check interval (seconds, minimum 60)",
 "允许持续检查此分支、执行已选测试，并按本地模型配置自动诊断；同一仓库共享监控请求额度，修改必须另外审批": "Allow continuous branch checks, selected tests and automatic diagnosis using the local model. Monitoring for this repository shares a request budget; changes need separate approval.",
 "启用自动维护": "Enable automatic maintenance",
 "程序保持运行时有效。暂停后不发起新检查，已开始的有界任务会完成。固定范围和额度不会随新提交或重启重置。": "Active while the program runs. Pausing prevents new checks; bounded tasks already started finish. New commits and restarts do not reset scope or budget.",
 "维护待办": "Maintenance follow-ups",
 "启用本浏览器通知": "Enable browser notifications",
 "页面中的待办始终可查看。浏览器通知需允许权限且保持页面打开；没有邮件、手机推送或云端后台。": "The on-page list remains available. Browser notifications require permission and an open page. There is no email, mobile push or cloud background service.",
 "已打开的仓库": "Opened repositories",
 "每个仓库保留自己的请求额度和运行记录。切换仓库不会继续执行旧审批，也不会自动合并或推送。": "Each repository keeps its request budget and run records. Switching repositories does not replay previous approvals or automatically merge or push.",
 "GitHub 登录在官方页面完成，凭据由本机 Git Credential Manager 管理。此页面不收集 GitHub 密码或 API Key。": "GitHub sign-in happens on its official page. Local Git Credential Manager manages credentials. This page collects no GitHub passwords or API keys.",
 "仓库维护": "Repository maintenance",
 "例如 [[\"{python}\",\"-m\",\"unittest\",\"discover\",\"-s\",\"tests\",\"-v\"]]": "For example [[\"{python}\",\"-m\",\"unittest\",\"discover\",\"-s\",\"tests\",\"-v\"]]"
};
 const bindings=new Set();
 const message=read=>({read,toString(){return String(read());},[Symbol.toPrimitive](){return String(read());}});
 const read=value=>value&&typeof value.read==='function'?read(value.read()):value;
 const t=key=>Object.assign(message(()=>language==='en'?(dictionary[key]??key):key),{source:key});
 const live=read=>message(read);
 function bind(target,value,attribute){
  const update=()=>{const result=String(read(value)??'');if(attribute)target.setAttribute(attribute,result);else target.textContent=result;};
  update();if(value&&typeof value.read==='function')bindings.add({target,update});
 }
 function setText(target,value){
  target.textContent=String(read(value)??'');
  // Register the text node, never its parent: labels can later contain inputs.
  bind(target.firstChild?.nodeType===3?target.firstChild:target,value);
 }
 function attribute(target,name,value){bind(target,value,name);}
 function prune(){for(const entry of bindings)if(entry.target.isConnected===false)bindings.delete(entry);}
 for(const target of root.querySelectorAll?.('[data-i18n]')||[]){
  const source=target.getAttribute('data-i18n');
  const text=[...(target.childNodes||[])].find(node=>node.nodeType===3&&node.textContent.trim()===source.trim());
  if(text){const prefix=text.textContent.match(/^\s*/)[0],suffix=text.textContent.match(/\s*$/)[0];bind(text,live(()=>prefix+read(t(source))+suffix));}
 }
 for(const name of ['aria-label','placeholder','title'])for(const target of root.querySelectorAll?.('[data-i18n-'+name+']')||[])attribute(target,name,t(target.getAttribute('data-i18n-'+name)));
 function textNode(value){const target=document.createTextNode('');bind(target,value);return target;}
 return {t,live,setText,attribute,prune,read,textNode,setLanguage(value){language=value==='en'?'en':'zh-CN';prune();for(const entry of bindings)entry.update();},getLanguage(){return language;},dispose(){bindings.clear();}};
})();
const {t,live,setText}=i18n;
const uiError=message=>Object.assign(new Error(String(i18n.read(message))),{displayMessage:message});
const format=(message,values)=>live(()=>String(i18n.read(message)).replace(/\{(\d+)\}/g,(_,index)=>String(i18n.read(values[index]))));
const access=window.RepoCareTabSession.open('workbench'),token=access.token;
const $=id=>root.querySelector('[id="'+id+'"]');
// Audited local drafts: these input/change handlers only update values, file choices,
// or clear confirmations. Repository inspection, connection and monitoring remain button actions.
for(const id of ['local-path','github-url','file-filter','test-command','test-timeout','monitor-source','monitor-branch','monitor-interval'])$(id)?.setAttribute?.('data-form-assist-draft','true');
let current=null,pending=false,page=1,hasNext=false;
const choices=new Map();
const intro=$('onboarding').querySelector('.intro'),sources=$('onboarding').querySelector('.sources');
const introTitle=t("选择一个仓库。"),introDescription=t("连接后确认维护范围，在独立会话中审阅方案、运行测试。");
function showPicker(){if(pending)return;sources.hidden=false;setText(intro.querySelector('h1'),live(()=>introTitle));setText(intro.querySelector('p'),live(()=>introDescription));}
function notice(text,error=false){setText($('notice'),live(()=>text));$('notice').classList.toggle('error',error);}
function element(tag,text){const el=document.createElement(tag);if(text!==undefined)setText(el,text);return el;}
async function api(path,body){const response=await fetch('/api/'+path,{method:body?'POST':'GET',headers:{Authorization:'Bearer '+token,...(body?{'Content-Type':'application/json'}:{})},...(body?{body:JSON.stringify(body)}:{})});const value=await response.json();if(response.status===401){access.expire();chooseRepository();notice(live(()=>value.error),true);}if(!response.ok)throw uiError(live(()=>value.error||t("请求失败")));return value;}
function setPending(value){pending=value;$('onboarding').querySelectorAll('button').forEach(b=>b.disabled=value);$('next').disabled=value||!hasNext;$('previous').disabled=value||page===1;}
const workspaceRecords=new Map(), workspaceViews=new Map(), workspaceLoads=new Map();
let selectedWorkspace=null,navigation=0;
function chooseRepository(){
 showPicker();
 if(options.onChooseRepository){options.onChooseRepository();return;}
 navigation++;
 access.saveView(null);
 selectedWorkspace=null;$('onboarding').hidden=false;
 for(const view of workspaceViews.values())view.root.hidden=true;
 renderRepositoryNavigation();
 window.scrollTo({top:0,behavior:'auto'});
}
function renderRepositoryNavigation(){
 const nav=$('repository-navigation');nav.replaceChildren();
 for(const record of workspaceRecords.values()){
  const button=element('button',live(()=>record.repository.split(/[\\/]/).at(-1)+(record.active?t(" · 执行中"):'')));
  button.title=record.repository;button.dataset.workspace=record.id;
  button.setAttribute('aria-pressed',String(selectedWorkspace===record.id));
  button.onclick=()=>showWorkspace(record.id).catch(error=>notice(live(()=>error.displayMessage||error.message),true));nav.append(button);
 }
 if(!workspaceRecords.size)nav.append(element('p',live(()=>t("尚未连接仓库。"))));
}
async function showWorkspace(id){
 if(options.onSelectWorkspace){options.onSelectWorkspace(id);return;}
 const version=++navigation;
 const record=workspaceRecords.get(id);if(!record)throw uiError(live(()=>t("会话已变化，请重新连接仓库")));
 if(!workspaceViews.has(id)||workspaceLoads.has(id)){
  if(!workspaceLoads.has(id))workspaceLoads.set(id,(async()=>{
  const response=await fetch('/maintenance.html');if(!response.ok)throw uiError(live(()=>t("维护界面加载失败")));
  const template=new DOMParser().parseFromString(await response.text(),'text/html');
  const root=element('div');root.className='maintenance-pane';root.dataset.workspace=id;root.hidden=true;
  for(const child of [...template.body.children])root.append(document.importNode(child,true));
  for(const node of root.querySelectorAll('[id]')){node.dataset.maintenanceId=node.id;node.id=id+'-'+node.id;}
  for(const node of root.querySelectorAll('[for]'))node.setAttribute('for',id+'-'+node.getAttribute('for'));
  $('maintenance-views').append(root);
  const controller=window.mountMaintenanceUI(root,{embedded:true,token:record.token,apiPrefix:record.api_prefix,
   language:i18n.getLanguage(),onChooseRepository:chooseRepository,onState:value=>{workspaceRecords.get(record.id).active=value.active;renderRepositoryNavigation();}});
  workspaceViews.set(id,{root,controller});
  await controller.ready;
  })());
  try{await workspaceLoads.get(id);}finally{workspaceLoads.delete(id);}
 }
 if(version!==navigation)return;
 selectedWorkspace=id;$('onboarding').hidden=true;
 access.saveView(id);
 for(const [key,view] of workspaceViews)view.root.hidden=key!==id;
 renderRepositoryNavigation();
 window.scrollTo({top:0,behavior:'auto'});
}
function launchLink(url,text){
 const parsed=new URL(url),match=/^#workspace=([a-f0-9]{32})$/.exec(parsed.hash);
 if(parsed.origin!==location.origin||!match)throw uiError(live(()=>t("工作台地址无效，请使用当前统一入口")));
 const button=element('button',live(()=>text));button.onclick=()=>showWorkspace(match[1]).catch(error=>notice(live(()=>error.displayMessage||error.message),true));return button;
}
function sessions(state){
 options.onState?.(state);
 if(state.resource_limits){
  $('test-timeout').max=String(state.resource_limits.test_timeout_max_seconds);
  if($('scope-capacity'))setText($('scope-capacity'),live(()=>t("仅将确认的文件提供给模型；最多 ")+state.resource_limits.source_context_max_files+t(" 个，单文件 ")+Math.round(state.resource_limits.source_file_max_bytes/1024)+t(" KiB。测试默认只读。")));
 }
 for(const record of state.workspaces||[]){
  if(!/^[a-f0-9]{32}$/.test(record.id)||! /^[a-f0-9]{64}$/.test(record.token)||record.api_prefix!=='/api/workspaces/'+record.id+'/')throw uiError(live(()=>t("工作台会话绑定无效")));
  workspaceRecords.set(record.id,record);
 }
 renderRepositoryNavigation();$('sessions').replaceChildren();
 for(const session of state.sessions)$('sessions').append(launchLink(session.launch_url,live(()=>session.repository)));
 if(!state.sessions.length)$('sessions').append(element('p',live(()=>t("尚未打开仓库。"))));
 $('auto-mode').disabled=!state.allow_auto;renderMonitors(state.monitors||[]);
}
$('add-repository').onclick=chooseRepository;
function showDevice(result){if(result?.user_code){$('device').hidden=false;setText($('device-code'),live(()=>result.user_code));notice(live(()=>t("请在 GitHub 官方页面完成登录。")));}}
async function action(name,body={}){if(pending)return;setPending(true);notice(live(()=>t("正在处理…")));try{let job=await api('actions/'+name,{...body,request_id:crypto.randomUUID().replaceAll('-','')});while(job.status==='running'){showDevice(job.result);await new Promise(resolve=>setTimeout(resolve,1000));const state=await api('state');sessions(state);job=state.jobs.find(j=>j.id===job.id);if(!job)throw uiError(live(()=>t("操作记录不可用，请刷新检查，不要重复点击")));}if(job.status==='failed')throw uiError(live(()=>job.result.error));notice(live(()=>t("操作完成。")));return job.result;}catch(error){notice(live(()=>error.displayMessage||error.message),true);return null;}finally{setPending(false);}}
function scopeSummary(){const edits=[...choices].filter(([p,v])=>v==='edit').map(([p])=>p),reads=[...choices].filter(([p,v])=>v==='read').map(([p])=>p);$('scope-summary').replaceChildren(element('p',live(()=>t("可修改：")+(edits.join('、')||t("尚未选择")))),element('p',live(()=>t("只读：")+(reads.join('、')||t("尚未选择")))));}
function resetConfirmations(){$('confirmed').checked=false;$('monitor-confirmed').checked=false;}
function recommendChecks(value){
 const box=$('check-recommendations');box.replaceChildren();$('check-warnings').replaceChildren();
 const saved=value.saved_scope?.policy,checks=value.checks||{recommendations:[],warnings:[],suggested_editable:[],suggested_readonly:[]};
 $('test-command').value=saved?JSON.stringify(saved.allowed_tests):'';$('test-timeout').value=saved?.timeout_seconds??60;
 $('auto-mode').checked=Boolean(value.saved_scope?.allow_auto);$('advanced-checks').open=false;
 for(const path of saved?.allowed_paths||checks.suggested_editable)choices.set(path,'edit');
 for(const path of saved?.readonly_paths||checks.suggested_readonly)choices.set(path,'read');
 const options=saved?[{id:'saved',title:t("恢复已有检查与授权范围"),argvs:saved.allowed_tests,sources:[t("本机已确认维护配置")],detail:t("沿用原配置与请求账本，历史审批不会自动执行。"),prerequisites:t("本次不改变原超时、范围或额度。")},...checks.recommendations]:checks.recommendations;
 if(!options.length)box.append(element('p',live(()=>t("没有找到可靠的检查命令。可以先只读分析；需要修复时，再确认有效检查或在高级设置填写。"))));
 for(const [index,originalCheck] of options.entries()){const check=checkPresentation(originalCheck);
  const card=element('div');card.className='check-option';const label=element('label'),radio=element('input');radio.type='radio';radio.name='check-choice';radio.value=check.id;radio.checked=index===0;label.append(radio,i18n.textNode(live(()=>' '+check.title)));
  const commands=check.argvs||[check.argv];if(index===0)$('test-command').value=JSON.stringify(commands);
  radio.onchange=()=>{if(radio.checked){$('test-command').value=JSON.stringify(commands);resetConfirmations();}};
  card.append(label,element('p',live(()=>check.detail)),element('p',live(()=>t("出处：")+check.sources.join('、'))),element('p',live(()=>check.prerequisites)));
  const detail=element('details');detail.append(element('summary',live(()=>t("查看将执行的命令"))),element('pre',live(()=>JSON.stringify(commands,null,2))));card.append(detail);box.append(card);
 }
 for(const warning of checks.warnings)$('check-warnings').append(element('p',live(()=>checkWarning(warning,checks.inputs||{}))));
 $('readonly-report').replaceChildren();scopeSummary();
}
function showRepository(value){if(!value||value.cancelled)return;current=value;filePage=1;choices.clear();$('scope').hidden=false;$('confirmed').checked=false;$('monitor-setup').hidden=false;$('monitor-confirmed').checked=false;$('monitor-branch').value=value.branch||'';$('monitor-source').value=value.github_repository?'github':'local';$('launch').replaceChildren();$('local-path').value=value.path;$('repository-summary').replaceChildren(element('p',live(()=>value.path)),element('p',live(()=>t("分支：")+(value.branch||'detached')+' · '+value.head.slice(0,12))),element('p',live(()=>value.clean?t("工作区干净，可确认维护范围。"):t("存在未提交修改。维护会话需先自行处理；只读分析不会修改这些文件。"))));recommendChecks(value);renderFiles();$('scope').scrollIntoView({behavior:'smooth',block:'start'});}
let filePage=1;
function renderFiles(){const query=$('file-filter').value.toLowerCase();$('files').replaceChildren();const matches=(current?.files||[]).filter(path=>path.toLowerCase().includes(query)),pages=Math.max(1,Math.ceil(matches.length/200));filePage=Math.min(filePage,pages);setText($('file-page'),live(()=>format(t("第 {0} / {1} 页 · 匹配 {2} 项"),[filePage,pages,matches.length])));$('file-prev').disabled=filePage<=1;$('file-next').disabled=filePage>=pages;for(const path of matches.slice((filePage-1)*200,filePage*200)){const row=element('div');row.className='file';row.append(element('span',live(()=>path)));const select=element('select');i18n.attribute(select,"aria-label",live(()=>path+t(" 文件范围草稿")));select.setAttribute('data-form-assist-draft','true');for(const [value,text] of [['none',t("不读取")],['edit',t("可修改")],['read',t("只读")]]){const option=element('option',live(()=>text));option.value=value;select.append(option);}select.value=choices.get(path)||'none';select.onchange=()=>{choices.set(path,select.value);resetConfirmations();scopeSummary();};row.append(select);$('files').append(row);}}
function selectedTests(){if(!$('test-command').value.trim())throw uiError(live(()=>t("尚未确认检查方式。请选择推荐检查、在高级设置填写，或先只读分析。")));let value;try{value=JSON.parse($('test-command').value);}catch{throw uiError(live(()=>t("自定义命令格式有误，请使用 JSON 参数数组。")));}if(!Array.isArray(value)||!value.length)throw uiError(live(()=>t("至少需要一条有效检查命令；无测试时可先只读分析。")));if(![...choices.values()].includes('edit'))throw uiError(live(()=>t("请选择可修改源码；仅查看项目可先只读分析。")));return value;}
$('test-command').oninput=()=>{for(const radio of document.querySelectorAll('[name=check-choice]'))radio.checked=false;resetConfirmations();};
$('test-timeout').oninput=resetConfirmations;
$('auto-mode').onchange=resetConfirmations;
$('analyze-repo').onclick=async()=>{if(!current)return;const result=await action('analyze_repo',{path:current.path,revision:current.revision});if(!result)return;const box=$('readonly-report');box.replaceChildren(element('h3',live(()=>t("只读分析报告 · 尚未验证"))),element('p',live(()=>result.mode==='local_readonly'?t("只读分析完成；未运行功能测试，项目仍尚未验证。"):result.summary)),element('p',live(()=>t("模型请求：0 · 项目命令：0 · 文件修改：0"))));for(const item of result.syntax_results)box.append(element('p',live(()=>item.file+(item.line?t(" 第")+item.line+t("行"):'')+'：'+(result.mode==='local_readonly'?({syntax_error:t("发现 Python 语法错误；未执行代码，未修改文件。"),unreadable:t("语法解析未完成。"),parsed:t("语法可解析；不代表功能正确。")}[item.status]||item.detail):item.detail))));if(!result.analyzed_files.length)box.append(element('p',live(()=>t("本轮未解析 Python 源码；其他语言只展示配置与验证缺口。"))));const list=element('ol');for(const step of result.next_steps)list.append(element('li',live(()=>result.mode==='local_readonly'?analysisStep(step):step)));box.append(list);};
$('pick').onclick=async()=>showRepository(await action('pick'));
$('inspect').onclick=async()=>showRepository(await action('local',{path:$('local-path').value.trim()}));
$('clone').onclick=async()=>showRepository(await action('clone',{url:$('github-url').value.trim()}));
$('check-account').onclick=async()=>{const value=await action('account');if(value)setText($('account'),live(()=>t("已登录：")+value.login));};
$('login').onclick=async()=>{const value=await action('login');if(value){setText($('account'),live(()=>t("已登录：")+value.login));$('device').hidden=true;setText($('device-code'),live(()=>''));}};
async function list(){const value=await action('repositories',{page});if(!value)return;hasNext=value.has_next;$('repos').replaceChildren();for(const repo of value.repositories){const button=element('button',live(()=>repo.name+(repo.private?t(" · 私有"):t(" · 公开"))+(repo.archived?t(" · 已归档"):'')));button.onclick=()=>{$('github-url').value=repo.url;notice(live(()=>t("已选择仓库，请点击连接并克隆。")));};$('repos').append(button);}if(!value.repositories.length)$('repos').append(element('p',live(()=>t("此页没有可访问的仓库。"))));$('pagination').hidden=false;setPending(false);}
$('list-repos').onclick=()=>{page=1;list();};$('next').onclick=()=>{page++;list();};$('previous').onclick=()=>{page=Math.max(1,page-1);list();};
$('file-filter').oninput=()=>{filePage=1;renderFiles();};$('file-prev').onclick=()=>{filePage=Math.max(1,filePage-1);renderFiles();};$('file-next').onclick=()=>{filePage++;renderFiles();};
async function openConnected(value){
 $('launch').replaceChildren(launchLink(value.launch_url,live(()=>t("点击进入 ")+value.repository)));
 sessions(await api('state'));await showWorkspace(new URL(value.launch_url).hash.slice('#workspace='.length));
 notice(live(()=>t("仓库已连接，尚未调用模型。")));
}
async function connectRepository(url){
 if(pending)throw uiError(live(()=>t("连接操作正在进行，请等待完成。")));
 current=null;choices.clear();resetConfirmations();$('scope').hidden=true;$('monitor-setup').hidden=true;
 $('github-url').value=url;sources.hidden=true;
 setText(intro.querySelector('h1'),live(()=>t("连接 ")+new URL(url).pathname.replace(/^\//,'').replace(/\.git$/,'')));
 setText(intro.querySelector('p'),live(()=>t("正在取得此仓库的本地副本；不会运行测试或调用模型。")));
 const value=await action('clone',{url});
 if(!value){sources.hidden=false;return;}
 showRepository(value);
 setText(intro.querySelector('p'),live(()=>t("本地副本已就绪。首次维护只需确认下方文件范围和检查方式。")));
 if(value.saved_scope&&value.clean){
  const policy=value.saved_scope.policy;
  const connected=await action('connect',{path:value.path,revision:value.revision,allowed_paths:policy.allowed_paths,
   readonly_paths:policy.readonly_paths,allowed_tests:policy.allowed_tests,timeout_seconds:policy.timeout_seconds,
   allow_auto:value.saved_scope.allow_auto,confirmed:true});
  if(connected)await openConnected(connected);
 }
}
$('connect').onclick=async()=>{if(!current)return;try{const allowed_tests=selectedTests();if(!$('confirmed').checked)throw uiError(live(()=>t("请先勾选确认检查方式和维护范围")));if(!current.clean)throw uiError(live(()=>t("请先处理仓库中已有的修改，再重新检查")));const value=await action('connect',{path:current.path,revision:current.revision,allowed_paths:[...choices].filter(([p,v])=>v==='edit').map(([p])=>p),readonly_paths:[...choices].filter(([p,v])=>v==='read').map(([p])=>p),allowed_tests,timeout_seconds:Number($('test-timeout').value),allow_auto:$('auto-mode').checked,confirmed:true});if(value){$('launch').replaceChildren(launchLink(value.launch_url,live(()=>t("点击进入 ")+value.repository)));sessions(await api('state'));await showWorkspace(new URL(value.launch_url).hash.slice('#workspace='.length));notice(live(()=>t("仓库已连接，尚未调用模型。")));}}catch(error){notice(live(()=>error.displayMessage||error.message),true);}};
api('state').then(async state=>{sessions(state);notice(live(()=>t("选择本地仓库，或连接 GitHub。新连接仓库默认请求上限为 ")+state.request_budget+t(" 次；已有仓库保留各自额度。")));if(options.onSelectWorkspace)return;const selected=access.selectedView();if(selected&&workspaceRecords.has(selected))await showWorkspace(selected);else if(selected)access.saveView(null);}).catch(error=>notice(live(()=>error.displayMessage||error.message),true));
const notified=new Set();let monitorSignature=null;
const monitorLabels={checking:t("正在检测"),diagnosing:t("正在诊断"),awaiting_approval:t("待审批"),healthy:t("授权测试通过"),ci_attention:t("CI 需关注"),blocked:t("检查受阻"),interrupted:t("上次中断"),stale:t("旧方案失效"),consumed:t("方案已处理，打开核验历史报告"),evidence_invalid:t("历史证据待核验"),candidate_ready:t("候选已验证，等待交付决定"),rolled_back:t("修复失败，候选已回滚"),discarded:t("候选已清理"),rejected:t("已拒绝修改")};
function monitorButton(text,name,payload){const button=element('button',live(()=>text));button.disabled=pending;button.onclick=async()=>{const result=await action(name,payload);if(result?.launch_url)$('monitor-launch').replaceChildren(launchLink(result.launch_url,live(()=>t("进入方案审阅与修复报告"))));sessions(await api('state'));};return button;}
function requestHoldText(budget){
 if(Number.isInteger(budget.requests_reserved_active)&&Number.isInteger(budget.requests_ended_unknown))
  return t(" · 进行中预留：")+budget.requests_reserved_active+t(" · 已结束待核对：")+budget.requests_ended_unknown;
 return t(" · 预留或待核对：")+(budget.requests_reserved_unknown??'—');
}
function renderMonitors(monitors){const signature=JSON.stringify(monitors);if(signature===monitorSignature)return;monitorSignature=signature;$('monitors').replaceChildren();let count=0;for(const monitor of monitors){const box=element('article');box.className='monitor';box.append(element('h3',live(()=>(monitor.spec.github_url||monitor.spec.path)+' · '+monitor.spec.branch)),element('p',live(()=>(monitor.enabled?t("监控中"):t("已暂停"))+t(" · 间隔 ")+monitor.spec.interval_seconds+t(" 秒 · 模型剩余额度 ")+monitor.requests_remaining+requestHoldText(monitor))));if(monitor.maintenance_memory?.cursors){const memory=monitor.maintenance_memory;box.append(element('p',live(()=>t("维护进度：已观察 ")+(memory.observations.head_commit?.slice(0,12)||t("尚无提交"))+t("；保留 ")+memory.cursors.completed_local_checks.length+t(" 个完整本地检查记录。已观察不代表已检查；候选通过不代表已合并。"))));}box.append(monitorButton(live(()=>monitor.enabled?t("暂停"):t("恢复")),monitor.enabled?'monitor_pause':'monitor_resume',{monitor_id:monitor.id}));if(monitor.error){box.append(element('p',live(()=>monitor.error)));if(!monitor.events.some(e=>e.message===monitor.error))count++;if(monitor.notification&&!notified.has(monitor.notification)){notified.add(monitor.notification);if(!options.unifiedNotifications&&'Notification'in window&&Notification.permission==='granted')try{new Notification(t("Pi 仓库维护"),{body:monitor.error,tag:monitor.id});}catch{}}}for(const event of [...monitor.events].reverse()){const row=element('div');row.className='maintenance-event';row.dataset.status=event.status;row.append(element('strong',live(()=>event.sha.slice(0,12)+' · '+(monitorLabels[event.display_status||event.status]||event.display_status||event.status))),element('p',live(()=>event.message)));if(event.ci_error)row.append(element('p',live(()=>t("CI 信息未取得：")+event.ci_error)));if(event.tests.length){const details=element('details');details.append(element('summary',live(()=>t("检测测试与退出码"))));details.append(element('pre',live(()=>event.tests.map(result=>result.command.join(' ')+t("\n退出码 ")+result.returncode+(result.timed_out?t(" · 超时"):'')+'\n'+result.stdout+'\n'+result.stderr).join('\n'))));row.append(details);}for(const failure of event.ci_failures||[]){const link=element('a',live(()=>failure.name+' · '+failure.conclusion));const url=new URL(failure.url);if(url.protocol==='https:'&&url.hostname==='github.com'){link.href=failure.url;link.target='_blank';link.rel='noopener noreferrer';row.append(link);}}const historyButton=element('button',live(()=>t("只读查看历史方案、Diff与测试")));historyButton.addEventListener('click',async()=>{historyButton.disabled=true;try{const value=await api('monitors/'+monitor.id+'/events/'+event.id+'/history');const details=element('details');details.open=true;details.append(element('summary',live(()=>t("历史记录 · 只读，不代表当前版本或执行授权"))),element('pre',live(()=>JSON.stringify(value,null,2))));row.append(details);}catch(error){row.append(element('p',live(()=>error.displayMessage||error.message)));}finally{historyButton.disabled=false;}});row.append(historyButton);if(event.can_open)row.append(monitorButton(live(()=>t("审阅方案并决定是否修改")),'monitor_open',{monitor_id:monitor.id,event_id:event.id}));if(event.launch_url)row.append(launchLink(event.launch_url,live(()=>t("查看方案与运行报告"))));if(event.runs?.length)row.append(element('p',live(()=>t("运行结果：")+event.runs.map(r=>r.status).join('，'))));box.append(row);if(['awaiting_approval','ci_attention','blocked','interrupted'].includes(event.display_status||event.status))count++;if(event.notification&&!notified.has(event.notification)){notified.add(event.notification);if(!options.unifiedNotifications&&'Notification'in window&&Notification.permission==='granted')try{new Notification(t("Pi 仓库维护"),{body:event.message,tag:event.id});}catch{};}}$('monitors').append(box);}$('monitors').append(Object.assign(element('div'),{id:'monitor-launch'}));setText($('maintenance-count'),live(()=>String(count)));setText($('maintenance-status'),live(()=>monitors.length?t("维护仓库 ")+monitors.length+t(" 个 · 待关注 ")+count+t(" 项"):t("尚未启用自动维护。")));}
$('monitor-start').onclick=async()=>{if(!current)return;try{if(!$('monitor-confirmed').checked)throw uiError(live(()=>t("请先确认自动检测、测试和模型额度授权")));const remote=$('monitor-source').value==='github';if(remote&&!current.github_url)throw uiError(live(()=>t("此仓库没有可用的 GitHub HTTPS origin，请先通过 GitHub 连接")));const result=await action('monitor_start',{path:current.path,revision:current.revision,allowed_paths:[...choices].filter(([p,v])=>v==='edit').map(([p])=>p),readonly_paths:[...choices].filter(([p,v])=>v==='read').map(([p])=>p),allowed_tests:selectedTests(),timeout_seconds:Number($('test-timeout').value),confirmed:true,branch:$('monitor-branch').value.trim(),github_url:remote?current.github_url:'',interval_seconds:Number($('monitor-interval').value)});if(result){sessions(await api('state'));notice(live(()=>t("监控已启用，首次检测将自动开始。修改前仍需审批。")));}}catch(error){notice(live(()=>error.displayMessage||error.message),true);}};
$('notify').onclick=async()=>{if(options.unifiedNotifications){options.onOpenNotifications?.();return;}if(!('Notification'in window)){notice(live(()=>t("此浏览器不支持通知；请查看页面维护待办。")));return;}const permission=await Notification.requestPermission();notice(live(()=>permission==='granted'?t("已允许本页面的维护通知。"):t("浏览器通知未允许；页面待办仍可查看。")));};
if(options.unifiedNotifications)setText($('notify'),live(()=>t("通知设置")));
const timer=setInterval(()=>{if(!pending)api('state').then(sessions).catch(()=>{});},3000);
return {setLanguage(language){i18n.setLanguage(language);for(const view of workspaceViews.values())view.controller.setLanguage?.(language);},showPicker,connectRepository,async inspectRepository(path){if(pending)throw uiError(live(()=>t("连接操作正在进行，请等待完成。")));current=null;choices.clear();resetConfirmations();$('scope').hidden=true;$('monitor-setup').hidden=true;$('local-path').value=path;showRepository(await action('local',{path}));},dispose(){clearInterval(timer);i18n.dispose();for(const view of workspaceViews.values())view.controller.dispose?.();}};

// Presentation only: IDs from the bounded check-discovery endpoint select
// product descriptions. Original command arrays, paths and report objects stay intact.
function checkPresentation(check){
 const id=check.id;
 if(/^unittest-\d+$/.test(id))return {...check,title:t("Python 功能测试（unittest）"),detail:format(t("运行 {0} 中的测试，比较功能结果是否符合已有断言。"),[(check.argv||check.argvs?.[0]||[])[5]||'']),prerequisites:t("需要项目所需依赖；发现命令不代表环境就绪或已有测试通过。")};
 if(id==='pytest')return {...check,title:t("Python 功能测试（pytest）"),detail:t("运行项目的 pytest 测试；具体范围由项目配置决定。"),prerequisites:t("需要 pytest 和项目依赖；插件及 conftest.py 也属于将执行的仓库代码。")};
 if(['npm-test','npm-build','npm-lint'].includes(id))return {...check,title:({'npm-test':t("Node 功能测试"),'npm-build':t("项目构建检查"),'npm-lint':t("代码规范检查")})[id],detail:live(()=>String(format(t("执行项目声明的 {0} 脚本。"),[id.slice(4)]))+(id==='npm-test'?'':String(t("构建/规范通过不等于功能没有 bug。")))),prerequisites:t("需要本机 npm 和已有依赖；显式脚本仍会执行仓库代码，不会自动安装依赖。")};
 if(id==='node-native')return {...check,title:t("Node 原生功能测试"),detail:t("运行已识别的 node:test 测试文件（最多8个），不代表覆盖整个仓库。"),prerequisites:t("需要支持 node:test 的 Node 版本及项目依赖。")};
 return check;
}
function checkWarning(warning,inputs){
 const fixed=[t("检查线索较多，仅解析前40个文件；结果可能不完整。"),t("发现 GitHub CI 配置；复杂服务、矩阵及远程命令未自动转换，请结合 CI 手动确认。"),t("发现 README，可用于确认环境说明；不会执行其中的自由文本命令。"),t("发现 tox 配置；本轮不创建 tox 环境、不安装依赖，复杂环境需手动配置。"),t("建议范围只覆盖部分源码，优先核心包；不支持把“检查整个仓库所有问题”当作完整扫描。请按具体问题调整范围。")];
 for(const message of fixed)if(message.source===warning)return message;
 for(const path of Object.keys(inputs)){
  if(warning===path+t(" 不是 UTF-8 文本，未解析。").source)return format(t("{0} 不是 UTF-8 文本，未解析。"),[path]);
  if(warning===path+t(" 无法解析，未从中推断命令。").source)return format(t("{0} 无法解析，未从中推断命令。"),[path]);
 }
 return warning;
}
function analysisStep(step){const messages=[t("确认项目启动方式和依赖环境。"),t("选一个具体问题，写下输入、预期结果与实际结果。"),t("为该问题补充可审阅的测试或确认人工验证步骤；本轮不会自动生成或修改测试。"),t("确认有效检查和维护范围后，再进入原审批修复流程。")];return messages.find(message=>message.source===step)||step;}
};
if(document.getElementById('onboarding'))window.mountWorkbenchOnboarding(document);
