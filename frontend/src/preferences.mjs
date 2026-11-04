export const PREFERENCES_KEY='repocare.preferences.v1';
export const NOTIFICATION_EVENTS=['approval','completion','failure','service'];
export const DEFAULT_PREFERENCES={theme:'system',fontSize:15,language:'zh-CN',notifications:{inApp:true,desktop:false,sound:false,
 events:{approval:true,completion:true,failure:true,service:true},quietHours:{enabled:false,start:'22:00',end:'08:00'}}};
const isObject=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const boolean=(value,fallback)=>typeof value==='boolean'?value:fallback;
const time=(value,fallback)=>typeof value==='string'&&/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(value)?value:fallback;
export function normalizePreferences(value){
 const source=isObject(value)?value:{},n=isObject(source.notifications)?source.notifications:{},events=isObject(n.events)?n.events:{},q=isObject(n.quietHours)?n.quietHours:{};
 return {theme:['system','light','dark'].includes(source.theme)?source.theme:'system',fontSize:[13,15,17].includes(source.fontSize)?source.fontSize:15,
  language:['zh-CN','en'].includes(source.language)?source.language:'zh-CN',notifications:{inApp:boolean(n.inApp,true),desktop:boolean(n.desktop,false),sound:boolean(n.sound,false),
   events:Object.fromEntries(NOTIFICATION_EVENTS.map(key=>[key,boolean(events[key],true)])),quietHours:{enabled:boolean(q.enabled,false),start:time(q.start,'22:00'),end:time(q.end,'08:00')}}};
}
export function readPreferences(storage){
 try{return normalizePreferences(JSON.parse((storage??globalThis.localStorage)?.getItem(PREFERENCES_KEY)||'null'));}catch{return normalizePreferences(null);}
}
export function writePreferences(value,storage){
 const safe=normalizePreferences(value);
 try{(storage??globalThis.localStorage)?.setItem(PREFERENCES_KEY,JSON.stringify(safe));return true;}catch{return false;}
}
export function applyPreferences(value,documentRoot){
 const safe=normalizePreferences(value),root=documentRoot??globalThis.document?.documentElement;
 if(root){root.dataset.theme=safe.theme;root.dataset.font=String(safe.fontSize);root.lang=safe.language;}
 return safe;
}
export function isQuietHours(value,now=new Date()){
 const q=normalizePreferences(value).notifications.quietHours;
 if(!q.enabled)return false;
 const minute=text=>{const [h,m]=text.split(':').map(Number);return h*60+m;};
 const current=now.getHours()*60+now.getMinutes(),start=minute(q.start),end=minute(q.end);
 return start===end|| (start<end?current>=start&&current<end:current>=start||current<end);
}
// This policy only chooses channels. It never requests browser permission or sends a notification.
export function notificationDecision(value,event,now=new Date(),permission='default'){
 const n=normalizePreferences(value).notifications,enabled=NOTIFICATION_EVENTS.includes(event)&&n.events[event]===true,quiet=isQuietHours(value,now);
 return {inApp:enabled&&n.inApp,desktop:enabled&&n.desktop&&permission==='granted'&&!quiet,sound:enabled&&n.sound&&!quiet,quiet};
}
export const translations={
 'zh-CN':{
  'app.settings':'设置','app.close':'关闭','app.back':'返回','app.newSession':'新会话','app.tutorial':'使用教程','app.workbench':'维护工作台','app.repositories':'仓库',
  'app.connectRepository':'连接仓库','app.searchRepositories':'搜索仓库','app.noRepository':'尚未连接仓库','app.taskDetails':'任务详情','app.newTask':'新维护任务','app.navigation':'工作台导航',
  'nav.overview':'工作概览','nav.tasks':'维护任务','nav.issues':'Issue 工作流','nav.events':'监控与待办','nav.memory':'记忆与知识','nav.delivery':'交付与验收','nav.configuration':'维护设置','nav.service':'服务管理',
  'tutorial.title':'开始使用 RepoCare','tutorial.intro':'连接仓库，选择模型，再开始一次有边界的维护。','tutorial.repository.title':'连接一个仓库','tutorial.repository.body':'选择本地仓库，或通过 GitHub 连接仓库。已有任务继续保留原配置。',
  'tutorial.model.title':'添加模型提供商','tutorial.model.body':'在设置的模型页添加服务地址、模型 ID 和密钥，再为新会话选择模型。','tutorial.task.title':'创建维护任务','tutorial.task.body':'描述问题或失败测试，先查看方案，再按当前权限审批执行。','tutorial.evidence.title':'核对结果与交付','tutorial.evidence.body':'查看测试、候选和交付证据；本地候选通过后仍需按流程验收。','tutorial.done':'开始使用',
  'settings.general':'通用','settings.models':'模型','settings.notifications':'通知','settings.categories':'设置分类','settings.close':'关闭设置','settings.general.description':'调整显示与语言，偏好保存在此浏览器。',
  'settings.theme':'主题','settings.light':'明亮','settings.dark':'暗色','settings.system':'跟随系统','settings.fontSize':'界面字号','settings.language':'语言','settings.persisted':'设置会自动保存在此浏览器，下次打开时继续使用。','settings.storageFailed':'无法保存浏览器偏好，本次打开仍可使用。',
  'notifications.description':'选择哪些事件需要提醒，以及提醒方式。','notifications.events':'事件类型','notifications.approval':'需要审批','notifications.completion':'任务或交付完成','notifications.failure':'执行失败或需要关注','notifications.service':'服务状态变化','notifications.channels':'提醒方式','notifications.inApp':'站内通知','notifications.desktop':'桌面通知','notifications.sound':'提示音','notifications.permission':'桌面通知权限','notifications.permission.granted':'已允许','notifications.permission.denied':'已阻止，请在浏览器设置中更改','notifications.permission.default':'尚未允许','notifications.permission.unsupported':'当前浏览器不支持','notifications.request':'允许桌面通知','notifications.requesting':'正在申请…','notifications.requestFailed':'未能申请权限，可稍后重试。','notifications.permissionHint':'只有点击“允许桌面通知”时才会申请浏览器权限。','notifications.quiet':'免打扰','notifications.quietEnabled':'按时段免打扰','notifications.start':'开始时间','notifications.end':'结束时间','notifications.quietHint':'按本机时间暂停桌面提醒和提示音，站内记录仍可查看。相同起止时间表示全天。',
  'models.description':'为新会话选择已保存的模型，或添加一个模型提供商。','models.saved':'已保存的模型','models.add':'添加模型提供商','models.refresh':'刷新模型列表','models.reconcile':'重新读取模型列表，核对保存结果','models.loading':'正在读取模型列表…','models.empty':'尚无模型配置，请添加模型提供商。','models.original':'原配置','models.default':'新会话默认','models.noModel':'尚未配置模型','models.noUrl':'尚未配置服务地址','models.keySaved':'密钥已保存','models.keyMissing':'尚未设置密钥','models.unavailable':'当前不可选','models.setDefault':'设为新会话默认','models.edit':'编辑并另存新版本','models.addTitle':'添加模型提供商','models.editTitle':'编辑模型 · 另存新版本','models.back':'返回模型列表','models.name':'配置名称','models.baseUrl':'Base URL','models.modelId':'模型 ID','models.apiKey':'API Key','models.context':'上下文长度','models.output':'最大输出长度','models.optional':'可选，使用模型默认值','models.save':'添加模型','models.saveVersion':'保存为新版本','models.saving':'正在保存…','models.newName':'{name} · 新版本','models.keyPlaceholder':'输入此配置的 API Key','models.modelPlaceholder':'填写服务商提供的模型 ID','models.namePlaceholder':'例如：开发模型','models.immutable':'另存会建立新的模型配置，旧会话、审批与原监控保持原绑定。请重新输入密钥；已保存密钥不会回显。','models.addHint':'密钥只保存在本机服务端，浏览器不持久保存。保存不调用模型。','models.limit':'已达到模型配置数量上限（最多添加 {count} 个，不含原配置）。','models.lengthError':'长度须为正整数，最大输出长度不能超过上下文长度。','models.readError':'无法读取模型列表，请稍后刷新核对。','models.saveError':'模型设置未保存，请检查填写内容或刷新列表后重试。','models.unknown':'保存结果未知，已暂停再次提交。请重新读取模型列表，核对后再操作。','models.savedMessage':'模型已保存到本机，未调用模型。旧任务保持原配置。','models.defaultMessage':'已设置新会话的默认模型，旧任务与原监控保持原绑定。','models.lengthSummary':'上下文 {context} · 最大输出 {output}','models.provider':'模型提供商'
 },
 en:{
  'app.settings':'Settings','app.close':'Close','app.back':'Back','app.newSession':'New session','app.tutorial':'Getting started','app.workbench':'Maintenance workbench','app.repositories':'Repositories',
  'app.connectRepository':'Connect repository','app.searchRepositories':'Search repositories','app.noRepository':'No repository connected','app.taskDetails':'Task details','app.newTask':'New maintenance task','app.navigation':'Workbench navigation',
  'nav.overview':'Overview','nav.tasks':'Maintenance tasks','nav.issues':'Issue workflow','nav.events':'Monitoring and follow-ups','nav.memory':'Memory and knowledge','nav.delivery':'Delivery and acceptance','nav.configuration':'Maintenance settings','nav.service':'Service management',
  'tutorial.title':'Get started with RepoCare','tutorial.intro':'Connect a repository, choose a model, and start a scoped maintenance task.','tutorial.repository.title':'Connect a repository','tutorial.repository.body':'Choose a local repository or connect through GitHub. Existing tasks keep their original configuration.',
  'tutorial.model.title':'Add a model provider','tutorial.model.body':'In Settings → Models, add the service URL, model ID, and API key, then choose a model for your new session.','tutorial.task.title':'Create a maintenance task','tutorial.task.body':'Describe a problem or failing test. Review the plan, then approve execution under the current permissions.','tutorial.evidence.title':'Review results and delivery','tutorial.evidence.body':'Review tests, candidates, and delivery evidence. A passing local candidate still requires acceptance.','tutorial.done':'Get started',
  'settings.general':'General','settings.models':'Models','settings.notifications':'Notifications','settings.categories':'Settings categories','settings.close':'Close settings','settings.general.description':'Adjust appearance and language. Preferences are saved in this browser.',
  'settings.theme':'Theme','settings.light':'Light','settings.dark':'Dark','settings.system':'System','settings.fontSize':'Font size','settings.language':'Language','settings.persisted':'Changes are saved automatically in this browser for your next visit.','settings.storageFailed':'Browser preferences could not be saved. Changes still apply for this visit.',
  'notifications.description':'Choose which events to receive and how to be notified.','notifications.events':'Event types','notifications.approval':'Approval required','notifications.completion':'Task or delivery completed','notifications.failure':'Failed or needs attention','notifications.service':'Service status changed','notifications.channels':'Notification channels','notifications.inApp':'In-app notifications','notifications.desktop':'Desktop notifications','notifications.sound':'Sound','notifications.permission':'Desktop permission','notifications.permission.granted':'Allowed','notifications.permission.denied':'Blocked. Change this in browser settings.','notifications.permission.default':'Not requested','notifications.permission.unsupported':'Not supported in this browser','notifications.request':'Allow desktop notifications','notifications.requesting':'Requesting…','notifications.requestFailed':'Could not request permission. Try again later.','notifications.permissionHint':'Browser permission is requested only when you click “Allow desktop notifications”.','notifications.quiet':'Do not disturb','notifications.quietEnabled':'Enable quiet hours','notifications.start':'Start time','notifications.end':'End time','notifications.quietHint':'Desktop alerts and sounds pause during these local hours. In-app records remain available. Equal start and end times mean all day.',
  'models.description':'Choose a saved model for new sessions, or add a model provider.','models.saved':'Saved models','models.add':'Add model provider','models.refresh':'Refresh models','models.reconcile':'Reload models to check the save result','models.loading':'Loading models…','models.empty':'No saved models. Add a model provider to begin.','models.original':'Original configuration','models.default':'Default for new sessions','models.noModel':'No model configured','models.noUrl':'No service URL configured','models.keySaved':'Key saved','models.keyMissing':'Key not configured','models.unavailable':'Currently unavailable','models.setDefault':'Set as new-session default','models.edit':'Edit and save a new version','models.addTitle':'Add model provider','models.editTitle':'Edit model · Save a new version','models.back':'Back to models','models.name':'Configuration name','models.baseUrl':'Base URL','models.modelId':'Model ID','models.apiKey':'API Key','models.context':'Context window','models.output':'Maximum output','models.optional':'Optional, use model default','models.save':'Add model','models.saveVersion':'Save new version','models.saving':'Saving…','models.newName':'{name} · New version','models.keyPlaceholder':'Enter the API key for this configuration','models.modelPlaceholder':'Enter the provider’s model ID','models.namePlaceholder':'For example: Development model','models.immutable':'Saving creates a new configuration. Existing sessions, approvals, and monitors keep their original binding. Enter a key again; saved keys are never shown.','models.addHint':'Keys are stored only on the local server, never persisted in the browser. Saving makes no model request.','models.limit':'Model configuration limit reached ({count} additional models, excluding the original).','models.lengthError':'Lengths must be positive integers. Maximum output cannot exceed the context window.','models.readError':'Could not load models. Refresh to check the current list.','models.saveError':'Model settings were not saved. Check the fields or refresh the list before retrying.','models.unknown':'The save result is unknown. Further writes are paused. Reload the models and check before continuing.','models.savedMessage':'Model saved locally. No model request was made. Existing tasks keep their configuration.','models.defaultMessage':'Default set for new sessions. Existing tasks and monitors keep their original binding.','models.lengthSummary':'Context {context} · Max output {output}','models.provider':'Model provider'
 }
};
export function t(language,key,variables={}){
 const dictionary=translations[language]||translations['zh-CN'];
 return Object.entries(variables).reduce((text,[name,value])=>text.replaceAll('{'+name+'}',String(value)),dictionary[key]||translations['zh-CN'][key]||key);
}
Object.assign(translations['zh-CN'],{'models.legacyLimits':'继承旧配置；供应商上限未验证','models.explicitLimits':'已声明长度；保存不会验证供应商容量','models.proposedLimits':'下方长度是供你确认的建议值，保存为新版本后生效；旧配置保持原样。','models.lengthError':'上下文须为 4096–2097152 的整数，最大输出须为 1–128000 的整数，且小于上下文长度。'});
Object.assign(translations.en,{'models.legacyLimits':'Inherited configuration; provider limits are unverified','models.explicitLimits':'Declared limits; saving does not verify provider capacity','models.proposedLimits':'The lengths below are suggestions for your review. They apply to the new version after saving; the original configuration stays unchanged.','models.lengthError':'Context must be an integer from 4096 to 2097152. Maximum output must be from 1 to 128000 and less than the context window.'});
Object.assign(translations['zh-CN'],{'models.reusedMessage':'完整模型配置未改变，已复用已有配置；未建立新版本或新增额度。'});
Object.assign(translations.en,{'models.reusedMessage':'The complete model configuration is unchanged, so the existing configuration was reused. No new version or allowance was created.'});

Object.assign(translations['zh-CN'],{'models.units':'上下文长度和最大输出长度均以 tokens 计量。'});
Object.assign(translations.en,{'models.units':'Context window and maximum output are measured in tokens.'});

// These are reminder preferences, not permission to approve or execute a task.
Object.assign(translations['zh-CN'],{'notifications.approval':'待审事项提醒','notifications.failure':'失败或需要关注'});
Object.assign(translations.en,{'notifications.approval':'Pending review reminders','notifications.quietEnabled':'Quiet hours'});

Object.assign(translations['zh-CN'],{
 'models.description':'按服务地址管理模型；每个模型单独设置长度、思考档位和新会话默认值。',
 'models.service':'模型服务','models.addToService':'添加此服务的模型','models.chooseModels':'选择要添加的模型（可多选）','models.selectModel':'选择模型',
 'models.fetchDirectory':'获取可用模型','models.directoryLoading':'正在获取模型…','models.directoryReady':'已获取 {count} 个模型。选择要添加的模型后，分别核对参数。',
 'models.directoryEmpty':'服务没有返回可选模型，可以手动填写模型 ID。','models.directoryError':'未能获取模型目录；服务商可能不支持 /models。可以手动填写模型 ID，或明确点击按钮重试。',
 'models.directoryHint':'填写服务地址和密钥后，离开输入框会查询一次模型目录；不会运行模型或保存配置。',
 'models.metadataHint':'目录和长度仅为服务商返回的信息，不证明该账号支持这些能力；思考档位表由你确认，不会按目录静默删减。',
 'models.advertisedEfforts':'目录声明的思考档位：{values}','models.manualModel':'手动添加未列出的模型','models.modelDraft':'模型草稿 {number}','models.removeDraft':'移除此草稿',
 'models.idHint':'这是实际请求使用的 API 模型 ID，不能为空或含空白。配置名称可以含空格，留空时使用模型 ID。','models.idError':'请填写实际 API 模型 ID，不能包含空白或控制字符。',
 'models.connectionRequired':'请填写服务地址和密钥。同一服务可明确复用已保存密钥；更改地址必须重新输入密钥。','models.reuseKey':'已保存；相同服务地址留空复用密钥',
 'models.immutable':'另存会建立新的模型配置，旧会话、审批与原监控保持原绑定。相同服务地址可留空复用已保存密钥，密钥不会回显。',
 'models.apiProtocol':'连接协议：OpenAI Chat Completions 兼容接口（DeepSeek 官方也推荐此接口）。其他协议不在本页支持范围内。',
 'models.deepseekDefaults':'检测到官方 DeepSeek HTTPS 地址：新模型默认使用 DeepSeek 思考格式，off 会关闭 thinking。你手动选择的格式会保留。',
 'models.units':'长度以 tokens 计量，支持 K / M 十进制写法，例如 128K、1M、1.5M；不会把保存当作供应商容量验证。',
 'models.lengthError':'上下文至少 4096，输出为正整数且小于上下文长度；可用 K/M，结果须为不超过 9007199254740991 的整数。',
 'models.reasoningProtocol':'思考参数格式','models.reasoningEffort':'新会话的默认思考档位','models.protocol.inherit':'继承原行为（不新增思考设置）',
 'models.protocol.none':'无思考参数','models.protocol.openai':'OpenAI 思考参数','models.protocol.deepseek':'DeepSeek 思考参数',
 'models.reasoningHint':'这里只声明参数，不验证服务商是否支持。OpenAI 的 off 映射为 none；若留空则使用服务商默认，不保证关闭思考。DeepSeek 的 off 关闭 thinking。',
 'models.reasoningError':'请保留 1–7 个唯一档位，默认档位须在表中。API 值不能含空白且最多 64 字符；off 的映射须符合所选格式。',
 'models.reasoningSummary':'思考格式 {protocol} · 默认 {effort}','models.reasoningInherited':'思考设置继承原行为','models.levelsTitle':'支持的思考档位与 API 值',
 'models.levelsHint':'可删除或加回固定七档；非 off 行的 API 值留空表示使用服务商默认，off 的含义以所选格式说明为准。删除默认档位后会选择表内首档，请核对。',
 'models.thinkingDisabled':'thinking 已关闭','models.noReasoningParameters':'不发送思考参数',
 'models.apiValue':'API 值','models.apiDefault':'留空：服务商默认','models.removeLevel':'删除档位','models.addLevel':'加回档位',
 'models.saveSelected':'保存所选 {count} 个模型','models.batchSaved':'已保存 {count} 个模型，未运行模型；旧任务保持原绑定。',
 'models.savedThisBatch':'本次已保存的模型','models.checkSavedList':'核对当前已保存列表','models.reviewUnknown':'上次此项保存结果未知。先重新读取列表，移除已存在的项，或人工核对后明确保留此草稿；不会自动重放。',
 'models.keepCheckedDraft':'已核对，将此项保留为未保存草稿'
});
Object.assign(translations.en,{
 'models.description':'Manage models by service URL. Set each model’s limits, thinking levels, and new-session default separately.',
 'models.service':'Model service','models.addToService':'Add a model from this service','models.chooseModels':'Choose models to add (multiple allowed)','models.selectModel':'Select model',
 'models.fetchDirectory':'Fetch available models','models.directoryLoading':'Fetching models…','models.directoryReady':'Found {count} models. Select models to add, then review each model’s settings.',
 'models.directoryEmpty':'The service returned no models. You can enter a model ID manually.','models.directoryError':'Could not fetch the directory. This provider may not support /models. Enter a model ID manually, or click the button to retry.',
 'models.directoryHint':'After entering the URL and key, leaving the input queries the model directory once. This runs no inference and saves no configuration.',
 'models.metadataHint':'The directory and limits are provider declarations, not proof of account capabilities. Review thinking levels yourself; directory results never silently remove them.',
 'models.advertisedEfforts':'Advertised thinking levels: {values}','models.manualModel':'Add an unlisted model manually','models.modelDraft':'Model draft {number}','models.removeDraft':'Remove this draft',
 'models.idHint':'The API model ID is used in requests and must contain no whitespace. Display names may contain spaces; a blank name uses the model ID.','models.idError':'Enter the actual API model ID without whitespace or control characters.',
 'models.connectionRequired':'Enter the service URL and key. A saved key can be explicitly reused for the exact same URL; changing the URL requires a new key.','models.reuseKey':'Saved key; leave blank to reuse it at the same URL',
 'models.immutable':'Saving creates a new configuration. Existing sessions, approvals, and monitors keep their original binding. Leave the key blank to reuse it at the exact same service URL; saved keys are never shown.',
 'models.apiProtocol':'Connection protocol: an OpenAI Chat Completions compatible API (also recommended by DeepSeek). Other protocols are not supported on this page.',
 'models.deepseekDefaults':'Official DeepSeek HTTPS URL detected: new models default to DeepSeek thinking parameters, where off disables thinking. Your manual format choices are retained.',
 'models.units':'Lengths are measured in tokens. Decimal K / M forms such as 128K, 1M, and 1.5M are supported. Saving does not verify provider capacity.',
 'models.lengthError':'Context must be at least 4096 and output positive and below context. K/M is supported; each result must be a whole number no greater than 9007199254740991.',
 'models.reasoningProtocol':'Thinking parameter format','models.reasoningEffort':'Default thinking level for new sessions','models.protocol.inherit':'Inherit original behavior (no new thinking settings)',
 'models.protocol.none':'No thinking parameters','models.protocol.openai':'OpenAI thinking parameters','models.protocol.deepseek':'DeepSeek thinking parameters',
 'models.reasoningHint':'These settings declare parameters; they do not verify provider support. OpenAI off maps to none; a blank API value uses the provider default and does not guarantee thinking is disabled. DeepSeek off disables thinking.',
 'models.reasoningError':'Keep 1–7 unique levels and select a default from the table. API values must have no whitespace and at most 64 characters. The off mapping must match the selected format.',
 'models.reasoningSummary':'Thinking format {protocol} · default {effort}','models.reasoningInherited':'Thinking settings inherit original behavior','models.levelsTitle':'Supported thinking levels and API values',
 'models.levelsHint':'Remove or restore any of the seven fixed levels. Blank API values on non-off rows use the provider default; off follows the selected format’s explanation. Removing the default selects the first remaining level; review it.',
 'models.thinkingDisabled':'Thinking disabled','models.noReasoningParameters':'No thinking parameters sent',
 'models.apiValue':'API value','models.apiDefault':'Blank: provider default','models.removeLevel':'Remove level','models.addLevel':'Restore level',
 'models.saveSelected':'Save {count} selected models','models.batchSaved':'Saved {count} models without running inference. Existing tasks keep their original binding.',
 'models.savedThisBatch':'Models saved in this batch','models.checkSavedList':'Check the current saved models','models.reviewUnknown':'The previous save result for this item is unknown. Reload the list, remove an existing item, or explicitly keep this draft after checking. It is never replayed automatically.',
 'models.keepCheckedDraft':'Checked; keep this as an unsaved draft'
});
