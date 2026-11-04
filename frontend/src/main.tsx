import {usageText} from './request-usage.mjs';
import {AsyncButton} from './AsyncButton';
import React,{useState,useEffect,useRef} from 'react';
import {createRoot} from 'react-dom/client';
import {taskRows,operationTask,graphLanes,loadPreferences,fileTree} from './model.mjs';
import './style.css';
import {IssueManager} from './IssueManager';
import {WorkbenchPages} from './WorkbenchPages';
import {useDeliveryFeed} from './DeliveryInbox';
import {pages,statusLabel} from './workbench-model.mjs';
import {apiRequest} from './api.mjs';
import './workspace.css';
import {TaskReport} from './TaskReport';
import {CapabilityPanel,CapabilityDraftProvider} from './CapabilityPanel';
import {CiInvestigation} from './CiInvestigation';
import {createCiReadEvidence} from './ci-api.mjs';
import {SettingsWindow} from './SettingsWindow';
import {AssistantPane} from './AssistantPane';
import {HelpDialog} from './HelpDialog';
import {readPreferences,writePreferences,applyPreferences,t} from './preferences.mjs';
import {setUiLanguage,ui as displayUi} from './ui-language.mjs';
import {invalidateForm} from './form-assist.mjs';
import {createNotificationTracker,dispatchNotification} from './notifications.mjs';
import {ModelTaskSelector} from './ModelTaskSelector';
import {downloadArtifact} from './existing-capability-model.mjs';
import {gatewayRecoveryText} from './service-readiness-model.mjs';

type Data=Record<string,any>;
const labels:Record<string,string>={awaiting_approval:'等待审批',running:'执行中',candidate_ready:'候选已验证',rolled_back:'失败已回滚',discarded:'候选已清理',rejected:'已拒绝',consumed:'方案已处理',interrupted:'上次中断',published:'已发布本地分支',integrated:'已发布本地分支',failed:'操作失败',blocked:'需要核验',prepared:'等待发布审批',finished:'已结束'};
const label=(value:string)=>labels[value]||statusLabel(value);
const basename=(path:string)=>path.split(/[\\/]/).at(-1)||path;
const access=window.RepoCareTabSession.open('workbench');
async function request(path:string,token=access.token,body?:Data){
 return apiRequest(path,token,body,{expire:()=>{if(token===access.token)access.expire();}});
}
const readCiEvidence=createCiReadEvidence(request);
const readCiRuns=(url:string)=>request('/api/github/actions?url='+encodeURIComponent(url));
function Icon({name}:{name:string}){
 const paths:Record<string,React.ReactNode>={
  code:<><path d="m8 7-5 5 5 5m8-10 5 5-5 5m-3-13-2 16"/></>,
  issues:<><circle cx="12" cy="12" r="8"/><path d="M12 7v6m0 3v1"/></>,
  pulls:<><circle cx="6" cy="5" r="2"/><circle cx="6" cy="19" r="2"/><circle cx="18" cy="19" r="2"/><path d="M6 7v10m12 0V9a4 4 0 0 0-4-4h-2m3-3-3 3 3 3"/></>,
  actions:<><circle cx="12" cy="12" r="8"/><path d="m10 8 6 4-6 4Z"/></>,
  folder:<><path d="M3 6h7l2 2h9v12H3Z"/></>,
  settings:<><path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="3"/><circle cx="15" cy="17" r="3"/></>,
  plus:<path d="M12 4v16M4 12h16"/>,
  branch:<><circle cx="6" cy="5" r="2"/><circle cx="6" cy="19" r="2"/><circle cx="18" cy="5" r="2"/><path d="M6 7v10m12-10v2a6 6 0 0 1-6 6H6"/></>,
  menu:<path d="M4 6h16M4 12h16M4 18h16"/>,
  overview:<><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></>,
  memory:<><path d="M4 4h7a3 3 0 0 1 3 3v14a4 4 0 0 0-4-2H4Zm10 3a3 3 0 0 1 3-3h4v15h-3a4 4 0 0 0-4 2"/></>,
  service:<><rect x="3" y="4" width="18" height="6" rx="2"/><rect x="3" y="14" width="18" height="6" rx="2"/><path d="M7 7h.01M7 17h.01m4-10h6m-6 10h6"/></>,
 };
 return <svg className="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]||paths.folder}</svg>;
}

function App(){
 const [deliveryFocus,setDeliveryFocus]=useState<Data|null>(null);
 const [prefs,setPrefs]=useState<Data>(()=>({...loadPreferences({getItem:(key:string)=>window.sessionStorage.getItem(key)}),...readPreferences()}));
 setUiLanguage(prefs.language);
 const tr=(key:string)=>t(prefs.language,key),ui=(zh:string,en:string)=>prefs.language==='en'?en:zh;
 const [assistantOpen,setAssistantOpen]=useState(false),[helpOpen,setHelpOpen]=useState(false),[notifications,setNotifications]=useState<Data[]>([]);
 const assistantMemory=useRef({model:'',selected:'',text:''});
 const prefsRef=useRef(prefs);prefsRef.current=prefs;
 const notificationTracker=useRef<ReturnType<typeof createNotificationTracker>|null>(null),notificationOpener=useRef<(event:Data)=>void>(()=>{});
 if(!notificationTracker.current)notificationTracker.current=createNotificationTracker();
 const notify=(events:Data[])=>{for(const event of events)dispatchNotification(event,prefsRef.current,{onInApp:(notice:Data)=>setNotifications(previous=>[...previous.filter(n=>n.id!==notice.id),notice].slice(-20)),onOpen:(notice:Data)=>notificationOpener.current(notice)});};
 const delivery=useDeliveryFeed(request,{preferences:prefs,onNotifications:(events:Data[])=>notify(events.map(row=>({id:'delivery:'+row.notice_id,type:row.status==='candidate_review'?'completion':'failure',kind:'delivery',entity_id:row.id,status:row.status,workspace_id:row.origin?.workspace_id,repository:row.origin?.repository,origin:row.origin})))});
 const [page,setPage]=useState(prefs.page||'overview');
 const [hub,setHub]=useState<Data>({workspaces:[],monitors:[]});
 const [selected,setSelected]=useState<string|null>(null),[remote,setRemote]=useState<Data|null>(null);
 const [states,setStates]=useState<Record<string,Data>>({}),[task,setTask]=useState<string|null>(prefs.task);
 const [panel,setPanel]=useState<string|null>(prefs.panel),[onboarding,setOnboarding]=useState(false),[settings,setSettings]=useState(false);
 const [settingsCategory,setSettingsCategory]=useState('general');
 const [sidebar,setSidebar]=useState(false),[detail,setDetail]=useState(false),[error,setError]=useState('');
 const [serviceConnectionError,setServiceConnectionError]=useState('');
 const [allDetails,setAllDetails]=useState(false);
 const [drafts,setDrafts]=useState<Record<string,string>>({}),[submitting,setSubmitting]=useState(false);
 const [profiles,setProfiles]=useState<Data|null>(null),[newProfile,setNewProfile]=useState('');
 const profileDefault=useRef(''),profilesRef=useRef<Data|null>(null);
 const selectionContext=useRef({key:'',epoch:0}),selectionKey=JSON.stringify([selected,remote?.url,page,settings,onboarding]);
 if(selectionContext.current.key!==selectionKey)selectionContext.current={key:selectionKey,epoch:selectionContext.current.epoch+1};
 const selectionEpoch=selectionContext.current.epoch;
 const variantIntent=useRef<{epoch:number;phase:'inflight'|'unknown'}|null>(null),[variantState,setVariantState]=useState<'inflight'|'unknown'|null>(null);
 const expireModelSelection=()=>{selectionContext.current.epoch++;};
 const changedModelContext=()=>new Error(ui('模型选择页面已变化；已保存的配置可在目录中核对，请在当前页面重新选择。','The model selection context changed. Check any saved configuration in the catalog and choose again on the current page.'));
 const [modelBusy,setModelBusy]=useState(false),[modelUnknown,setModelUnknown]=useState(false),[modelChoicePending,setModelChoicePending]=useState(false);
 const pendingModel=useRef<{id:string;profile:string;repository:string}|null>(null),modelGate=useRef(false);
 const [remotes,setRemotes]=useState<Data[]>([]),[repoPage,setRepoPage]=useState(1),[repoNext,setRepoNext]=useState(false),[repoBusy,setRepoBusy]=useState(false),[account,setAccount]=useState('GitHub');
 const [search,setSearch]=useState(''),[repositoriesExpanded,setRepositoriesExpanded]=useState(true);
 const [connecting,setConnecting]=useState(false),[onboardingReady,setOnboardingReady]=useState(false);
 const onboardingController=useRef<OnboardingController|null>(null),connectionPending=useRef(false);
 const oldHost=useRef<HTMLDivElement>(null),onboardHost=useRef<HTMLDivElement>(null),workspaceHost=useRef<HTMLElement>(null),lastFormRoot=useRef<HTMLElement|null>(null);
 const getFormRoot=()=>{const root=settings?(document.querySelector?.('.settings-main') as HTMLElement|null):workspaceHost.current;lastFormRoot.current=root;return root;};
 const expireForm=()=>{if(lastFormRoot.current)invalidateForm(lastFormRoot.current);};
 const openSettings=()=>{expireForm();expireModelSelection();setSettingsCategory('general');setSettings(true);};
 const openNotificationSettings=()=>{expireForm();expireModelSelection();setSettingsCategory('notifications');setSettings(true);};
 const closeSettings=()=>{expireForm();setSettings(false);};
 const openAssistant=()=>{setAssistantOpen(true);setSidebar(false);};
 const receiveProfiles=(value:Data)=>{profileDefault.current=value.default_profile_id||'';profilesRef.current=value;setProfiles(value);setNewProfile(previous=>previous||value.default_profile_id||'');};
 async function refreshProfiles(){
  const pending=variantIntent.current;if(pending?.phase==='inflight')throw new Error(ui('思考配置仍在保存，请等待原结果。','The thinking configuration is still being saved. Wait for the original result.'));
  const value=await request('/api/model-profiles');if(!Array.isArray(value.profiles)||typeof value.revision!=='string')throw new Error(ui('模型目录回执待核对。','The model catalog response needs review.'));
  receiveProfiles(value);if(pending&&pending===variantIntent.current&&pending.phase==='unknown'){variantIntent.current=null;setVariantState(null);}
 }
 useEffect(()=>{let alive=true;request('/api/model-profiles').then(value=>{if(alive){receiveProfiles(value);if(!value.profiles?.some((p:Data)=>p.selectable))setSettings(true);}}).catch(()=>{});return()=>{alive=false;};},[]);
 const controllers=useRef(new Map<string,{root:HTMLElement;controller:Controller}>());
 const loadingControllers=useRef(new Map<string,Promise<{root:HTMLElement;controller:Controller}>>());
 const activeRepository=useRef(selected);activeRepository.current=selected;
 const initial=useRef(true), navRef=useRef<(id:string)=>void>(()=>{});
 const selectedRecord=(hub.workspaces||[]).find((w:Data)=>w.id===selected);
 const selectedModelContext=useRef('');
 useEffect(()=>{if(selectedRecord&&selectedModelContext.current!==selectedRecord.id){selectedModelContext.current=selectedRecord.id;if(selectedRecord.model_selectable!==false)setNewProfile(selectedRecord.model_profile_id||'legacy');}},[selectedRecord?.id,selectedRecord?.model_profile_id]);
 const state=selected?states[selected]:null;
 const repositoryKey=selectedRecord?.repository_id||remote?.url||'';
 const rows=taskRows(state),current=rows.find((t:Data)=>t.id===task)||null;
 const choose=(id:string)=>{expireForm();expireModelSelection();const record=(hub.workspaces||[]).find((w:Data)=>w.id===id);if(record&&record.model_selectable!==false)setNewProfile(record.model_profile_id||'legacy');setDeliveryFocus(null);setSelected(id);setRemote(null);setOnboarding(false);setSidebar(false);setTask(null);setDetail(false);setPage('overview');access.saveView(id);};
 const navigate=(next:string)=>{expireForm();expireModelSelection();setPage(next);setOnboarding(false);setSidebar(false);setDetail(false);setPanel(null);};
 const selectTask=(id:string)=>{setDeliveryFocus(null);const row=rows.find((r:Data)=>r.id===id);setTask(id);navigate(row?.selection.kind==='issue'?'issues':'tasks');};
 const reviewDelivery=(row:Data)=>{const origin=row.origin;if(!hub.workspaces.some((w:Data)=>w.id===origin.workspace_id)){setError(ui("原任务尚未连接，请明确连接原仓库；不会新建替代任务。","The original task is not connected. Connect its repository explicitly; no replacement task will be created."));return;}choose(origin.workspace_id);setDeliveryFocus(origin);navigate(origin.kind==='issue'?'delivery':'tasks');};
 useEffect(()=>{if(!deliveryFocus||selected!==deliveryFocus.workspace_id||!state)return;
  const found=rows.find((r:Data)=>deliveryFocus.kind==='issue'?r.selection?.kind==='issue'&&r.selection.id===deliveryFocus.execution_id:r.runs?.some((run:Data)=>run.run_id===deliveryFocus.run_id));
  if(found){setTask(found.id);if(deliveryFocus.kind==='monitor'){setAllDetails(true);setDetail(true);}}
 },[deliveryFocus,selected,state]);
 navRef.current=choose;
 const receiveHub=(value:Data)=>{notify(notificationTracker.current!.hub(value));setHub(value);if(value.model_profiles)receiveProfiles(value.model_profiles);if(initial.current){initial.current=false;const restored=value.workspaces.find((w:Data)=>w.id===access.selectedView())||value.workspaces.find((w:Data)=>w.repository_id===prefs.repository);if(restored){setSelected(restored.id);setTask(prefs.task);}else if(prefs.repository)setError(ui("服务重启或会话已变化，请明确重新连接原仓库以读取原任务和额度。","The service restarted or the session changed. Explicitly reconnect the original repository to read its tasks and budget."));}};
 useEffect(()=>{let stopped=false;
  const poll=()=>request('/api/state').then(value=>{
   if(stopped)return;
   setServiceConnectionError('');notify(notificationTracker.current!.service(true));receiveHub(value);
  }).catch(e=>{
   if(stopped)return;
   notify(notificationTracker.current!.service(false));
   const connectionLost=e.code==='transport_unknown'||e.status===401;
   setServiceConnectionError(connectionLost?e.message:'');
   if(!connectionLost)setError(e.message);
  });
  poll();const timer=setInterval(poll,3000);return()=>{stopped=true;clearInterval(timer);};
 },[]);
 useEffect(()=>{applyPreferences(prefs);writePreferences(prefs);
  try{sessionStorage.setItem('repocare.ui.v1',JSON.stringify({...prefs,repository:repositoryKey||prefs.repository,task,panel,page}));}catch{}
 },[prefs,repositoryKey,task,panel,page]);
 useEffect(()=>{
  expireForm();
  onboardingController.current?.setLanguage?.(prefs.language);
  for(const {controller} of controllers.current.values())controller.setLanguage?.(prefs.language);
 },[prefs.language,onboardingReady]);
 useEffect(()=>{if(!onboarding||!onboardHost.current||typeof MutationObserver==='undefined')return;
  const containsDraft=(node:Node)=>node instanceof Element&&(node.matches('[data-form-assist-draft]')||Boolean(node.querySelector('[data-form-assist-draft]')));
  const observer=new MutationObserver(records=>{if(records.some(record=>record.type==='attributes'?containsDraft(record.target):[...record.addedNodes,...record.removedNodes].some(containsDraft)))expireForm();});
  observer.observe(onboardHost.current,{subtree:true,childList:true,attributes:true,attributeFilter:['hidden','aria-hidden']});return()=>observer.disconnect();
 },[onboarding]);
 useEffect(()=>{
  if(!onboardHost.current)return;let disposed=false,adapter:OnboardingController|undefined;
  fetch('/onboarding.html').then(r=>{if(!r.ok)throw new Error(ui("连接界面加载失败","Could not load the repository connection view."));return r.text();}).then(html=>{
   if(disposed||!onboardHost.current)return;const parsed=new DOMParser().parseFromString(html,'text/html');
   onboardHost.current.replaceChildren(...[...parsed.body.children].map(n=>document.importNode(n,true)));
   adapter=window.mountWorkbenchOnboarding(onboardHost.current,{language:prefsRef.current.language,unifiedNotifications:true,onOpenNotifications:openNotificationSettings,onState:receiveHub,onSelectWorkspace:(id:string)=>navRef.current(id),onChooseRepository:()=>{expireForm();setOnboarding(true);}});
   onboardingController.current=adapter;setOnboardingReady(true);
  }).catch(e=>setError(e.message));return()=>{disposed=true;onboardingController.current=null;adapter?.dispose();};
 },[]);
 async function ensureController(record:Data){
  const id=record.id,existing=controllers.current.get(id);if(existing)return existing;
  const loading=loadingControllers.current.get(id);if(loading)return loading;
  const pending=fetch('/maintenance.html').then(r=>{if(!r.ok)throw new Error(ui("任务详情加载失败","Could not load the task details."));return r.text();}).then(html=>{
   const parsed=new DOMParser().parseFromString(html,'text/html'),root=document.createElement('div');root.className='maintenance-pane';
   for(const n of [...parsed.body.children])root.append(document.importNode(n,true));
   for(const n of root.querySelectorAll<HTMLElement>('[id]')){n.dataset.maintenanceId=n.id;n.id=id+'-'+n.id;}
   for(const n of root.querySelectorAll('[for]'))n.setAttribute('for',id+'-'+n.getAttribute('for'));
   const controller=window.mountMaintenanceUI(root,{language:prefsRef.current.language,embedded:true,token:record.token,apiPrefix:record.api_prefix,onChooseRepository:()=>{expireForm();setOnboarding(true);},onState:(value:Data)=>{notify(notificationTracker.current!.workspace(id,value).map(event=>({...event,repository:record.repository})));setStates(old=>({...old,[id]:value}));}});
   const entry={root,controller};controllers.current.set(id,entry);return entry;
  }).finally(()=>loadingControllers.current.delete(id));
  loadingControllers.current.set(id,pending);return pending;
 }
 useEffect(()=>{
  if(!selectedRecord||!oldHost.current)return;let stopped=false;
  ensureController(selectedRecord).then(entry=>{if(!stopped&&oldHost.current)oldHost.current.replaceChildren(entry.root);}).catch(e=>setError(e.message));return()=>{stopped=true;};
 },[selected]);
 useEffect(()=>{if(selected&&current&&current.selection.kind!=='issue')controllers.current.get(selected)?.controller.select(current.selection);},[selected,current?.selection.kind,current?.selection.id]);
 useEffect(()=>()=>{for(const v of controllers.current.values())v.controller.dispose();},[]);
 useEffect(()=>{const entry=selected?controllers.current.get(selected):null;if(entry&&selectedRecord?.active!==state?.active)entry.controller.refresh().catch(e=>setError(e.message));},[selectedRecord?.active]);
 async function listRepos(page=1){setRepoBusy(true);setError('');try{
  const value=await request('/api/github/repositories?page='+page);setRemotes(value.repositories);setRepoPage(page);setRepoNext(value.has_next);
 }catch(e){setError((e as Error).message);}finally{setRepoBusy(false);}}
 async function checkAccount(){try{const value=await request('/api/github/account');setAccount(value.login);}catch(e){setError((e as Error).message);}}
 const linkedWorkspace=(repository:Data)=>(hub.workspaces||[]).find((w:Data)=>w.github_url?.toLowerCase().replace(/\.git$/,'')===repository.url.toLowerCase().replace(/\.git$/,''));
 async function openConnection(){
  if(connectionPending.current||!onboardingController.current)return;
  expireForm();
  const existing=remote&&linkedWorkspace(remote);if(existing){choose(existing.id);return;}
  setSidebar(false);setError('');setOnboarding(true);
  if(!remote){onboardingController.current.showPicker();return;}
  connectionPending.current=true;setConnecting(true);
  try{await onboardingController.current.connectRepository(remote.url);}catch(e){setError((e as Error).message);}
  finally{connectionPending.current=false;setConnecting(false);}
 }
 async function inspectSavedProject(path:string){
  if(connectionPending.current||!onboardingController.current)throw new Error(ui("连接界面正在准备或已有操作，请稍后重试。","The connection view is preparing or an operation is in progress. Try again shortly."));
  expireForm();
  connectionPending.current=true;setConnecting(true);setSidebar(false);setRemote(null);setError('');setOnboarding(true);
  try{await onboardingController.current.inspectRepository(path);}
  finally{connectionPending.current=false;setConnecting(false);}
 }
 const existingCapabilities={hub,record:selectedRecord,openProvider:openSettings,request,refresh:async()=>receiveHub(await request('/api/state')),
  inspect:inspectSavedProject,connect:openConnection,download:(artifact:Data)=>downloadArtifact(artifact,access.token)};
 notificationOpener.current=(event:Data)=>{
  if(event.kind==='service'||!event.workspace_id){navigate('service');return;}
  if(event.kind==='delivery'&&event.origin){reviewDelivery({origin:event.origin});return;}
  const record=(hub.workspaces||[]).find((workspace:Data)=>workspace.id===event.workspace_id);
  if(!record){setError(ui('原仓库当前未连接，请明确连接后查看原记录。','The original repository is not connected. Connect it explicitly to review the record.'));return;}
  const originalState=states[record.id],row=taskRows(originalState).find((item:Data)=>event.kind==='plan'?item.plans?.some((plan:Data)=>plan.id===event.entity_id):event.kind==='run'?item.runs?.some((run:Data)=>run.run_id===event.entity_id):item.selection?.kind==='issue'&&item.selection.id===event.execution_id);
  choose(record.id);setTask(row?.id||null);setPage(event.kind==='issue-approval'?'issues':'tasks');
  if(['plan','run','job'].includes(event.kind)){setDetail(true);void ensureController(record).then(({controller})=>{if(event.kind!=='job')controller.select({kind:event.kind,id:event.entity_id});}).catch(e=>setError((e as Error).message));}
 };
 function modelResult(snapshot:Data){
  receiveHub(snapshot);const pending=pendingModel.current;if(!pending)return null;
  const job=snapshot.jobs?.find((j:Data)=>j.id===pending.id);if(!job||job.status==='running')return null;
  if(job.status==='failed'){pendingModel.current=null;setModelUnknown(false);throw new Error(job.result?.error||ui("模型会话准备失败；原任务保留。","Model session preparation failed; the original task is retained."));}
  const record=job.result?.workspace;
  if(job.status!=='finished'||!record||record.repository!==pending.repository||record.model_profile_id!==pending.profile||!snapshot.workspaces?.some((w:Data)=>w.id===record.id))throw new Error(ui("模型会话回执不完整，请核对上次操作；草稿保留。","The model session receipt is incomplete. Check the previous operation; the draft is retained."));
  pendingModel.current=null;setModelUnknown(false);return record;
 }
 async function reasoningVariant(profileId:string,effort:string){
  if(selectionEpoch!==selectionContext.current.epoch)throw changedModelContext();
  if(variantIntent.current){const error=new Error(ui('思考配置仍待核对，请先刷新可选模型，不会重复保存。','The thinking configuration needs review. Refresh models first; it will not be saved again.')) as Error&{uncertain?:boolean};error.uncertain=true;throw error;}
  const catalog=profilesRef.current;
  const original=catalog?.profiles?.find((p:Data)=>p.id===profileId&&p.selectable);
  if(!catalog||!original)throw new Error(ui('请先刷新并选择可用模型。','Refresh and choose an available model first.'));
  const intent={epoch:selectionEpoch,phase:'inflight' as const};variantIntent.current=intent;setVariantState('inflight');
  try{
   const value=await request('/api/model-profiles',undefined,{action:'reasoning_variant',revision:catalog.revision,profile_id:profileId,reasoning_effort:effort});
   const id=value.created_profile_id,derived=value.profiles?.find((p:Data)=>p.id===id);
   if(typeof id!=='string'||!derived?.selectable||derived.model!==original.model||derived.base_url!==original.base_url||derived.reasoning_effort!==effort||derived.reasoning_protocol!==original.reasoning_protocol||JSON.stringify(derived.reasoning_levels??null)!==JSON.stringify(original.reasoning_levels??null)||derived.context_window_tokens!==original.context_window_tokens||derived.max_output_tokens!==original.max_output_tokens){
    const error=new Error(ui('思考档位配置回执待核对；请刷新模型列表，不会自动执行。','Check the reasoning configuration receipt by refreshing models. Nothing runs automatically.')) as Error&{uncertain?:boolean};error.uncertain=true;throw error;
   }
   receiveProfiles(value);variantIntent.current=null;setVariantState(null);
   if(selectionContext.current.epoch!==intent.epoch)throw changedModelContext();return id;
  }catch(e){
   if(variantIntent.current===intent){if((e as Data)?.uncertain){variantIntent.current={...intent,phase:'unknown'};setVariantState('unknown');}else{variantIntent.current=null;setVariantState(null);}}
   throw e;
  }
 }
 async function activateProfile(profileId=newProfile){
  if(variantIntent.current)throw new Error(ui('思考配置仍待核对，请先刷新可选模型。','The thinking configuration needs review. Refresh models first.'));
  if(!selectedRecord||selectedRecord.model_selectable===false)throw new Error(ui("此监控任务保持原模型；请连接项目后再为新任务选模型。","This monitor retains its original model. Connect the project before choosing a model for a new task."));
  if(pendingModel.current)throw new Error(ui("上次模型会话准备结果待核对，请先点击“核对上次模型切换”。","The previous model session result needs verification. Click “Check previous model switch” first."));
  if(!profilesRef.current?.profiles?.some((p:Data)=>p.id===profileId&&p.selectable))throw new Error(ui("请选择可用的模型配置。","Choose an available model configuration."));
  if(profileId===(selectedRecord.model_profile_id||'legacy'))return selectedRecord;
  const origin=selectedRecord,id=crypto.randomUUID().replaceAll('-',''),activationEpoch=selectionContext.current.epoch;
  pendingModel.current={id,profile:profileId,repository:origin.repository};setModelUnknown(true);
  try{await request('/api/actions/task_model_workspace',access.token,{request_id:id,workspace_id:origin.id,profile_id:profileId,confirmed:true});}
  catch(e){if(!(e as any).uncertain){pendingModel.current=null;setModelUnknown(false);}throw e;}
  for(let n=0;n<60;n++){
   const record=modelResult(await request('/api/state'));
   if(record){if(activeRepository.current===origin.id&&selectionContext.current.epoch===activationEpoch){choose(record.id);setNewProfile(profileId);setPage('tasks');}await ensureController(record);return record;}
   await new Promise(resolve=>setTimeout(resolve,500));
  }
  throw new Error(ui("模型会话准备结果待核对；不会重复提交，草稿已保留。","Model session preparation needs verification. It will not be resubmitted; the draft is retained."));
 }
 async function applyProfile(profileId?:string){if(modelGate.current||submitting)return;modelGate.current=true;setModelBusy(true);setError('');try{await activateProfile(profileId);}catch(e){setError((e as Error).message);}finally{modelGate.current=false;setModelBusy(false);}}
 async function reconcileModel(){if(modelGate.current)return;modelGate.current=true;setModelBusy(true);setError('');try{const record=modelResult(await request('/api/state'));if(record){choose(record.id);setPage('tasks');await ensureController(record);}else setError(ui("上次模型会话操作仍待核对；不会重发。","The previous model session operation still needs verification. It will not be resubmitted."));}catch(e){setError((e as Error).message);}finally{modelGate.current=false;setModelBusy(false);}}
 async function submit(){
  const submittedDraft=drafts[repositoryKey]||'',issue=submittedDraft.trim();if(!issue||!selected||submitting||modelGate.current||state?.active||variantIntent.current||modelChoicePending)return;
  modelGate.current=true;setSubmitting(true);setError('');try{
   const record=selectedRecord?.model_selectable===false?selectedRecord:await activateProfile();
   const {controller}=await ensureController(record);const result=await controller.createTask(issue);
   if(result?.status==='finished'){setDrafts(old=>old[repositoryKey]===submittedDraft?{...old,[repositoryKey]:''}:old);if(activeRepository.current===selected||activeRepository.current===record.id){const next=operationTask(controller.getState(),result.result);setTask(next?.id||null);setDetail(true);}}
   else {setDetail(true);setError(ui("生成未完成，请查看原任务记录；草稿已保留。","Generation did not finish. Review the original task record; the draft is retained."));}
  }catch(e){setError((e as Error).message);}finally{modelGate.current=false;setSubmitting(false);}
 }
 const englishStatus:Data={awaiting_approval:'Awaiting approval',running:'Running',candidate_ready:'Candidate verified',rolled_back:'Failed and rolled back',discarded:'Candidate removed',rejected:'Rejected',consumed:'Plan handled',interrupted:'Interrupted',published:'Local branch published',integrated:'Local branch published',failed:'Operation failed',blocked:'Needs review',prepared:'Awaiting publication approval',finished:'Finished',starting:'Starting',planning:'Planning',baseline_testing:'Running baseline checks',candidate_testing:'Checking candidate',editing:'Applying approved changes',reviewing_plan:'Reviewing plan'};
 const appLabel=(value:string)=>prefs.language==='en'?(englishStatus[value]||value||'No record'):label(value);
 const pendingCount=(hub.monitors||[]).reduce((n:number,m:Data)=>n+(m.events||[]).filter((e:Data)=>['awaiting_approval','blocked','interrupted','ci_attention'].includes(e.display_status||e.status)).length,0);
 const title=selectedRecord?basename(selectedRecord.repository):remote?.name||ui('选择仓库','Choose repository');
 const assistant=assistantOpen?<AssistantPane memory={assistantMemory} request={request} profiles={profiles} language={prefs.language} getFormRoot={getFormRoot} onOpenHelp={()=>setHelpOpen(true)} onClose={()=>setAssistantOpen(false)}/>:undefined;
 return <div className={'app '+(panel?'has-inspector ':'')+(sidebar?'sidebar-open ':'')+(onboarding?'onboarding-open ':'')+(assistantOpen&&!settings?'has-assistant ':'')}>
  {sidebar&&<button className="sidebar-scrim" aria-label={ui('关闭仓库抽屉','Close repository drawer')} onClick={()=>setSidebar(false)}/>}
  <aside className="sidebar" aria-label={ui('仓库与任务','Repositories and tasks')}>
   <div className="brand"><span className="brand-mark">R</span><strong>RepoCare</strong><span className="caption">Pi</span><button className="mobile-only icon-button" aria-label={ui('关闭侧栏','Close sidebar')} onClick={()=>setSidebar(false)}>×</button></div>
   <div className="section-label assistant-sidebar-label">{ui('会话','Sessions')}</div><button type="button" className="nav-button assistant-launch" onClick={openAssistant}><Icon name="plus"/>{tr('app.newSession')}</button>
   <AsyncButton className="nav-button" disabled={connecting||!onboardingReady} onClick={openConnection}><Icon name="folder"/>{tr('app.connectRepository')}</AsyncButton>
   <nav className="primary-nav" aria-label={tr('app.navigation')}>{pages.map(([id,_text,icon])=><button key={id} aria-current={page===id&&!onboarding?'page':undefined} onClick={()=>navigate(id)}><Icon name={icon}/><span>{tr('nav.'+id)}</span>{id==='events'&&pendingCount>0&&<span className="count">{pendingCount}</span>}{id==='issues'&&(state?.issue_workbench?.approvals||[]).some((a:Data)=>a.status==='pending')&&<span className="nav-alert"/>}</button>)}</nav>
   <label className="search"><span>⌕</span><input aria-label={ui('搜索仓库和任务','Search repositories and tasks')} placeholder={ui('搜索仓库和任务…','Search repositories and tasks…')} value={search} onChange={e=>setSearch(e.target.value)}/></label>
   <div className="sidebar-scroll"><div className="section-label">{tr('app.repositories')}<AsyncButton className="icon-button" aria-label={ui('刷新 GitHub 仓库','Refresh GitHub repositories')} disabled={repoBusy} onClick={()=>listRepos(repoPage)}>↻</AsyncButton></div>
    <AsyncButton className="account-row" aria-expanded={repositoriesExpanded} aria-controls="account-repositories" onClick={()=>{setRepositoriesExpanded(open=>!open);if(account==='GitHub')return checkAccount();}}>◉ {account}<span aria-hidden="true">{repositoriesExpanded?'⌄':'›'}</span></AsyncButton>
    <div id="account-repositories" hidden={!repositoriesExpanded}>
    {(hub.workspaces||[]).filter((w:Data)=>w.repository.toLowerCase().includes(search.toLowerCase())).map((w:Data)=><button key={w.id} disabled={connecting||modelBusy||submitting} className={'repository-row '+(selected===w.id&&!onboarding?'selected':'')} title={w.repository+' · '+(w.model_name||ui('原模型','Original model'))} onClick={()=>choose(w.id)}><Icon name="folder"/><span>{basename(w.repository)}<small className="caption"> · {w.model_name||ui('原模型','Original model')}</small></span><span className="connection-dot" aria-label={ui('已连接本地','Local repository connected')}/></button>)}
    {remotes.filter(r=>r.name.toLowerCase().includes(search.toLowerCase())).map(r=><button key={r.url} disabled={connecting} className={'repository-row '+(remote?.url===r.url?'selected':'')} onClick={()=>{const existing=linkedWorkspace(r);if(existing){choose(existing.id);return;}expireForm();setRemote(r);setSelected(null);setOnboarding(false);setSidebar(false);setTask(null);}}><Icon name="folder"/><span>{r.name}</span><span title={ui('GitHub 远端','GitHub remote')}>↗</span></button>)}
    <AsyncButton className="text-button" disabled={repoBusy} onClick={()=>listRepos(1)}>{ui('浏览 GitHub 仓库','Browse GitHub repositories')}</AsyncButton>
    {remotes.length>0&&<div className="pagination"><AsyncButton disabled={repoPage===1||repoBusy} onClick={()=>listRepos(repoPage-1)}>{ui('上一页','Previous')}</AsyncButton><span>{repoPage}</span><AsyncButton disabled={!repoNext||repoBusy} onClick={()=>listRepos(repoPage+1)}>{ui('下一页','Next')}</AsyncButton></div>}
    </div>
    <div className="section-label tasks-label">{tr('nav.tasks')}<button className="icon-button" aria-label={ui('创建新维护任务','Create maintenance task')} disabled={!selected} onClick={()=>{setTask(null);navigate('tasks');}}>＋</button></div>
    {rows.filter((r:Data)=>r.title.toLowerCase().includes(search.toLowerCase())).map((row:Data)=><button key={row.id} className={'task-row '+(task===row.id?'selected':'')} onClick={()=>selectTask(row.id)}><span className={'task-dot status-'+row.status}/><span>{row.title}</span><span className="task-short-status">{appLabel(row.status)}</span></button>)}
    {!rows.length&&<p className="sidebar-empty">{selected?ui('还没有维护任务','No maintenance tasks yet'):ui('连接仓库后查看原任务','Connect a repository to view its tasks')}</p>}
   </div>
   <div className="sidebar-settings-actions"><button className="settings-button" onClick={openSettings}><Icon name="settings"/>{tr('app.settings')}</button><button className="tutorial-button" type="button" onClick={()=>setHelpOpen(true)}>{tr('app.tutorial')}</button></div>
  </aside>
  <main className="workspace" ref={workspaceHost}>
   <header className="workspace-header"><button className="mobile-only icon-button" aria-label={ui('打开仓库侧栏','Open repository sidebar')} onClick={()=>setSidebar(true)}><Icon name="menu"/></button><span className="online-dot"/><strong>{pages.some(([id])=>id===page)?tr('nav.'+page):tr('app.workbench')}</strong><span className="breadcrumb">/ {title}</span><button className="header-detail" disabled={!selected||['issues','delivery'].includes(page)} onClick={()=>{setDetail(true);closeSettings();}}>{tr('app.taskDetails')}</button><button className="header-assistant" type="button" onClick={openAssistant}>{ui('询问助手','Ask assistant')}</button></header>
   {serviceConnectionError&&<div className="error-banner" role="status"><span>{serviceConnectionError} {gatewayRecoveryText()}</span></div>}
   {error&&<div className="error-banner" role="alert"><span>{error}</span><button aria-label={ui('关闭提示','Dismiss message')} onClick={()=>setError('')}>×</button></div>}
   {notifications.length>0&&<section className="workbench-notifications" aria-label={ui('站内通知','In-app notifications')} aria-live="polite">{notifications.slice(-5).map(notice=><article key={notice.id}><button type="button" className="notification-open" onClick={()=>notificationOpener.current(notice)}><strong>{notice.title}</strong><span>{notice.body}</span></button><button type="button" className="notification-dismiss" aria-label={ui('收起通知','Dismiss notification')+': '+notice.title} onClick={()=>setNotifications(previous=>previous.filter(item=>item.id!==notice.id))}>×</button></article>)}</section>}
   <div className="onboarding" ref={onboardHost} hidden={!onboarding}/>
   <div className="conversation" id="native-tasks" hidden={onboarding||page!=='tasks'}>
    {selectedRecord?<>
     <div className="task-heading"><span className="eyebrow">{ui('仓库维护','Repository maintenance')}</span><h1>{current?.title||ui('手动发起维护任务','Start a manual maintenance task')}</h1><p>{current?appLabel(current.status):ui('有问题线索时在下方填写。也可以不描述问题，设置目录维护，让 Agent 自己检查并发现失败。','Enter any problem clues below. You can also configure directory maintenance without describing a problem and let the agent check for failures.')}</p>{!current&&<button onClick={()=>navigate('events')}>{ui('不描述问题，设置自动检查 →','Configure automatic checks without a problem description →')}</button>}</div>
     {modelUnknown&&<p role="status">{ui('上次模型会话操作尚待核对，草稿和原任务保留。','The previous model operation needs review. Your draft and original tasks are retained.')}<AsyncButton disabled={modelBusy||submitting} onClick={reconcileModel}>{ui('核对上次模型切换','Check previous model change')}</AsyncButton></p>}
     {current&&<><div className="message user-message"><span className="avatar">{ui('你','You')}</span><div>{current.title}</div></div>
      {current.plans.map((plan:Data)=><article className="message agent-message" key={plan.id}><span className="avatar agent-avatar">Pi</span><div className="message-body"><div className="message-meta">Pi <span>{appLabel(plan.status)}</span></div><p>{plan.plan?.diagnosis}</p><details className="activity-row"><summary>{ui('方案依据与验证范围','Plan evidence and validation scope')}</summary><p>{plan.plan?.risk}</p><p>{ui('基线','Baseline')} {plan.plan?.base_sha?.slice(0,12)}</p><pre>{JSON.stringify(plan.plan?.tests||[],null,2)}</pre></details><div className="proposal"><div className="proposal-title"><Icon name="code"/><strong>{ui('修改方案','Proposed changes')}</strong><span>{plan.plan?.edits?.length||0} {ui('个文件','files')}</span></div><pre className="diff-preview">{(plan.diff||ui('打开详情查看原方案与差异','Open details to review the original plan and diff')).split('\n').map((line:string,index:number)=><span key={index} className={line.startsWith('+')&&!line.startsWith('+++')?'diff-add':line.startsWith('-')&&!line.startsWith('---')?'diff-delete':'diff-context'}>{line||' '}{'\n'}</span>)}</pre><button className="primary" onClick={()=>{controllers.current.get(selected!)?.controller.select({kind:'plan',id:plan.id});setDetail(true);}}>{ui('审阅方案与审批','Review plan and approval')}</button><span className="caption">{ui('执行前将再次核验原始证据','Original evidence is checked again before execution')}</span></div></div></article>)}
      {current.runs.map((run:Data)=><article className="message" key={run.run_id}><span className="avatar agent-avatar">Pi</span><div className="message-body"><div className="message-meta">{ui('执行报告','Run report')} <span>{appLabel(run.status)}</span></div><p>{ui('运行','Run')} {run.run_id.slice(0,12)}</p><p>{run.error||ui('测试、候选与恢复操作请查看原始运行详情。','Open the original run details for checks, candidates, and recovery actions.')}</p><button onClick={()=>{controllers.current.get(selected!)?.controller.select({kind:'run',id:run.run_id});setDetail(true);}}>{ui('查看报告与恢复操作','View report and recovery actions')}</button></div></article>)}</>}
     {state&&<TaskReport key={current?.id||'new'} record={selectedRecord} state={state} current={current} request={request} refresh={async()=>{if(selected)await controllers.current.get(selected)?.controller.refresh();}} openOriginal={()=>setDetail(true)}/>}
     {state?.active&&<div className="active-card" role="status"><span className="online-dot"/>{ui('正在执行','Running')} · {appLabel(state.jobs?.find((j:Data)=>j.id===state.active)?.progress?.phase||'running')}<button onClick={()=>setDetail(true)}>{ui('查看活动与停止控件','View activity and stop controls')}</button></div>}
     {!current&&!state?.active&&<div className="empty-workspace"><Icon name="branch"/><h2>{ui('每一步，都留下证据。','Evidence at every step.')}</h2><p>{ui('问题、方案、审批、测试与恢复，保存在同一条维护任务中。','Problems, plans, approvals, checks, and recovery stay together in one maintenance task.')}</p><button onClick={()=>setDetail(true)}>{ui('查看维护范围与原始记录','View maintenance scope and original records')}</button></div>}
    </>:<div className="empty-workspace"><span className="brand-mark large">R</span><h1>{remote?ui('浏览仓库资料','Browse repository information'):ui('让仓库维护，有迹可循。','Keep repository maintenance traceable.')}</h1><p>{remote?ui('右侧查看 Issues、PR 与 Actions。点击连接将取得此仓库的本地副本，并沿用已有范围或确认首次维护范围。','Browse Issues, PRs, and Actions on the right. Connect to get a local copy and review the maintenance scope.'):ui('连接一个项目，确认维护范围，从问题诊断开始。','Connect a project, confirm its maintenance scope, and start with a diagnosis.')}</p><AsyncButton className="primary" disabled={connecting||!onboardingReady} onClick={openConnection}>{tr('app.connectRepository')}</AsyncButton></div>}
   <CapabilityPanel key={repositoryKey+'tasks'} page="tasks" repositoryKey={repositoryKey||'unconnected'} navigate={navigate} existing={existingCapabilities} ci={(selectedRecord?.github_url||remote?.url)?{repositoryUrl:selectedRecord?.github_url||remote!.url,readEvidence:readCiEvidence,readRuns:readCiRuns}:undefined}/>
   </div>
   {!onboarding&&page!=='tasks'&&<div className="workspace-pages" id={'native-'+page}><WorkbenchPages key={selected||remote?.url||'unconnected'} page={page} record={selectedRecord} remoteUrl={remote?.url} state={state} hub={hub} delivery={delivery.snapshot} deliveryError={delivery.error} refreshDelivery={delivery.refresh} reviewDelivery={reviewDelivery} deliveryFocus={deliveryFocus} notificationPreferences={prefs} onOpenNotifications={openNotificationSettings} request={request} refresh={async()=>{if(selected)await controllers.current.get(selected)?.controller.refresh();receiveHub(await request('/api/state'));}} navigate={navigate} openOriginal={()=>{setAllDetails(true);setDetail(true);}} connect={openConnection} selectTask={selectTask} selectWorkspace={choose} rows={rows} taskFocus={current?.selection.kind==='issue'?{workspace_id:selected,execution_id:current.selection.id}:null} focus={current?.selection.kind==='issue'?current.selection.number:null}/><CapabilityPanel key={repositoryKey+page} page={page} repositoryKey={repositoryKey||'unconnected'} navigate={navigate} existing={existingCapabilities}/></div>}
   <div className={'original-details '+(allDetails?'all-details':'review-details')} hidden={!detail||onboarding}><div className="detail-toolbar"><strong>{allDetails?ui('维护设置与原始控件','Maintenance settings and original controls'):ui('任务详情与审批','Task details and approvals')}</strong><button onClick={()=>setAllDetails(!allDetails)}>{allDetails?ui('收起设置','Hide settings'):tr('nav.configuration')}</button><button aria-label={ui('关闭任务详情','Close task details')} onClick={()=>setDetail(false)}>×</button></div><div ref={oldHost}/></div>
   {selectedRecord&&!onboarding&&page==='tasks'&&<div className="composer-wrap"><div className="composer"><div className="composer-context"><Icon name="branch"/><span>{state?.policy?.allowed_paths?.length||0} {ui('个可修改文件 · 人工审批','editable files · Approval required')}{state?.allow_auto?ui(' / 已授权自主模式',' / Autonomous mode authorized'):''}</span><button onClick={openSettings}>{tr('app.settings')}</button></div><textarea data-form-assist-draft="true" aria-label={ui('新维护任务的问题描述','New maintenance task description')} placeholder={ui('描述需要维护的问题，或粘贴失败测试…','Describe a maintenance problem, or paste a failing test…')} value={drafts[repositoryKey]||''} onChange={e=>setDrafts(old=>({...old,[repositoryKey]:e.target.value}))} onKeyDown={e=>{if((e.ctrlKey||e.metaKey)&&e.key==='Enter'){e.preventDefault();submit();}}} maxLength={2000}/><div className="composer-bottom"><div className="composer-model-choice">{selectedRecord.model_selectable!==false&&<ModelTaskSelector language={prefs.language} profiles={profiles} value={newProfile} currentId={selectedRecord.model_profile_id||'legacy'} contextKey={selectionEpoch} variantState={variantState} busy={modelBusy||submitting||modelUnknown||Boolean(state?.active)} budget={hub.request_budget} onChange={setNewProfile} onApply={applyProfile} onReasoningVariant={reasoningVariant} onRefresh={refreshProfiles} onPendingChange={setModelChoicePending}/>}</div><AsyncButton className="send primary" aria-label={ui('生成新任务方案','Generate maintenance plan')} disabled={!drafts[repositoryKey]?.trim()||submitting||modelBusy||modelUnknown||modelChoicePending||Boolean(variantState)||Boolean(state?.active)||!state} onClick={submit}>{submitting?'…':'↑'}</AsyncButton></div></div><div className="budget-line"><span>{usageText(state)}</span><span>{state?.budget_monitor_id?ui('监控共享请求账','Shared monitor ledger'):ui('手工任务 · 当前模型会话','Manual tasks · Current model session')}</span></div></div>}
  </main>
  {panel&&<Inspector key={(selected||remote?.url||'')+panel} panel={panel} record={selectedRecord} state={state} issueFocus={current?.selection.kind==='issue'?current.selection.number:null} refresh={async()=>{if(selected)await controllers.current.get(selected)?.controller.refresh();}} remote={remote} close={()=>setPanel(null)} onConnect={openConnection} connecting={connecting||!onboardingReady}/>}
  <nav className="rail" aria-label={ui('仓库资料','Repository information')}>{[['code',ui('代码','Code')],['issues','Issues'],['pulls','PR'],['actions','Actions']].map(([id,text])=><button key={id} className={panel===id?'active':''} aria-pressed={panel===id} aria-label={text} title={text} onClick={()=>{if(id==='issues'){navigate('issues');return;}setPanel(panel===id?null:id);}}><Icon name={id}/><span>{text}</span></button>)}</nav>
  <footer className="statusbar"><span>⌁ {ui('本机连接','Local connection')}</span><span>{title}</span><span className="status-right">{ui('Pi SDK · 原生任务与审批','Pi SDK · Native tasks and approvals')}</span></footer>
  {assistantOpen&&!settings&&<div className="assistant-dock">{assistant}</div>}
  {settings&&<SettingsWindow prefs={prefs} onPrefsChange={setPrefs} request={request} initialProfiles={profiles||undefined} onProfilesChange={receiveProfiles} onClose={closeSettings} initialCategory={settingsCategory} assistant={assistant} onOpenAssistant={openAssistant}/>}
  {helpOpen&&<HelpDialog request={request} language={prefs.language} onClose={()=>setHelpOpen(false)} navigate={navigate}/>}
 </div>;
}

function Inspector({panel,record,state,issueFocus,refresh,remote,close,onConnect,connecting}:{panel:string;record?:Data;state?:Data|null;issueFocus?:number|null;refresh:()=>Promise<unknown>;remote:Data|null;close:()=>void;onConnect:()=>void;connecting:boolean}){
 const ui=displayUi;
 const [value,setValue]=useState<Data|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[page,setPage]=useState(1),[file,setFile]=useState<Data|null>(null),[filter,setFilter]=useState(''),[branches,setBranches]=useState<string[]|null>(null),[graph,setGraph]=useState<Data|null>(null),[job,setJob]=useState<Data|null>(null);
 const revision=useRef(0),fileRevision=useRef(0);
 const url=record?.github_url||remote?.url;
 const load=async()=>{const rev=++revision.current;setBusy(true);setError('');try{
  const data=panel==='code'?(record?await request('/api/browse/'+record.id+'/tree?page='+page+'&query='+encodeURIComponent(filter)):null):url?await request('/api/github/'+panel+'?url='+encodeURIComponent(url)+'&page='+page):null;
  if(rev===revision.current)setValue(data);
 }catch(e){if(rev===revision.current)setError((e as Error).message);}finally{if(rev===revision.current)setBusy(false);}};
 useEffect(()=>{load();return()=>{revision.current++;fileRevision.current++;};},[page,filter]);
 async function openFile(path:string){const rev=++fileRevision.current;setFile(null);try{const data=await request('/api/browse/'+record!.id+'/file?path='+encodeURIComponent(path));if(rev===fileRevision.current)setFile(data);}catch(e){if(rev===fileRevision.current)setError((e as Error).message);}}
 async function openGraph(){setBusy(true);setError('');try{const local=await request('/api/browse/'+record!.id+'/graph');setGraph(local);if(url){const options=await request('/api/github/branches?url='+encodeURIComponent(url));setGraph({...local,branches:[...new Set([local.branch,...options.branches].filter(Boolean))],branch_has_next:options.has_next,branch_page:1});}}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 async function moreBranches(){if(!graph||!url)return;setBusy(true);try{const options=await request('/api/github/branches?url='+encodeURIComponent(url)+'&page='+(graph.branch_page+1));setGraph({...graph,branches:[...new Set([...graph.branches,...options.branches])],branch_has_next:options.has_next,branch_page:options.page});}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 async function fetchHistory(){if(!record)return;setBusy(true);setError('');try{
  const result=await request('/api/history',access.token,{request_id:crypto.randomUUID().replaceAll('-',''),workspace_id:record.id,branches:branches??[graph?.branch].filter(Boolean),confirmed:true});setJob(result);
 }catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 useEffect(()=>{if(!job||job.status!=='running')return;let stopped=false;const timer=setInterval(()=>request('/api/history/'+job.id).then(data=>{if(!stopped){setJob(data);if(data.status==='finished')setGraph(data.result);}}).catch(e=>{if(!stopped){setError(e.message);setJob({...job,status:'unknown'});}}),1000);return()=>{stopped=true;clearInterval(timer);};},[job?.id,job?.status]);
 const titles:Record<string,string>={code:displayUi("代码与提交","Code and commits"),issues:'Issues',pulls:'Pull Requests',actions:'Actions'};
 return <aside className="inspector" aria-label={titles[panel]}><div className="inspector-header"><Icon name={panel}/><strong>{titles[panel]}</strong><AsyncButton feedback={{pending:busy}} className="icon-button" disabled={busy} aria-label={displayUi("刷新资料","Refresh information")} onClick={load}>↻</AsyncButton><button className="icon-button" aria-label={displayUi("关闭资料栏","Close information panel")} onClick={close}>×</button></div><div className="inspector-content">
  {error&&<p role="alert" className="error-text">{error}</p>}{busy&&<p role="status">{displayUi("正在读取…","Loading…")}</p>}
  {panel==='code'?record?<>
   <div className="inspector-path"><Icon name="folder"/>{basename(record.repository)}<span className="caption">{displayUi("只读","Read-only")}</span></div><input className="file-search" aria-label={displayUi("筛选已追踪文件","Filter tracked files")} placeholder={displayUi("查找文件…","Find files…")} value={filter} onChange={e=>{setFilter(e.target.value);setPage(1);}}/>
   <div className="file-tree"><FileTree entries={fileTree(value?.files||[])} selected={file?.path} expand={Boolean(filter)} open={openFile}/></div>
   <div className="button-row"><AsyncButton disabled={busy||page<=1} onClick={()=>setPage(page-1)}>{displayUi("上一页文件","Previous files")}</AsyncButton><span className="caption">{displayUi('第','Page')} {value?.page||page} {displayUi('页 · 匹配','· matches')} {value?.total??'—'} {displayUi('项','items')}</span><AsyncButton disabled={busy||!value?.has_next} onClick={()=>setPage(page+1)}>{displayUi("下一页文件","Next files")}</AsyncButton></div>
   {file&&<section className="file-preview"><div className="file-preview-title">{file.path}<span>{file.bytes} B</span></div><pre>{file.content.split('\n').map((line:string,index:number)=><span className="code-line" key={index}><span className="line-number">{index+1}</span>{line||' '}</span>)}</pre></section>}
   <div className="graph-heading"><strong>{displayUi("分支与提交","Branches and commits")}</strong><AsyncButton disabled={busy} onClick={openGraph}>{displayUi("读取本地历史","Read local history")}</AsyncButton></div>
   {graph&&<><p className="caption">{graph.cached?displayUi("独立缓存","Isolated cache"):displayUi("本地历史","Local history")} · {graph.branch||'detached HEAD'} · {displayUi('最多 300 条','up to 300 commits')}</p><CommitGraph commits={graph.commits||[]}/><details className="history-options"><summary>{displayUi("补取远端历史","Fetch remote history")}</summary><p className="caption">{displayUi("独立缓存，最多 8 分支，每分支深度 300。","Isolated cache, up to 8 branches and 300 commits per branch.")}</p>{(graph.branches||[]).map((b:string)=><label key={b}><input type="checkbox" checked={branches?branches.includes(b):b===graph.branch} disabled={Boolean(job?.status==='running')} onChange={e=>{const previous=branches??[graph.branch].filter(Boolean);if(e.target.checked&&previous.length>=8){setError(displayUi("最多选择 8 个分支","Select at most 8 branches"));return;}setBranches(e.target.checked?[...previous,b]:previous.filter(v=>v!==b));}}/>{b}</label>)}{graph.branch_has_next&&<AsyncButton disabled={busy} onClick={moreBranches}>{displayUi("更多远端分支","More remote branches")}</AsyncButton>}<AsyncButton feedback={{pending:busy||job?.status==='running'}} className="primary" disabled={!url||job?.status==='running'||busy||branches?.length===0} onClick={fetchHistory}>{displayUi("确认补取选定分支","Confirm fetching selected branches")}</AsyncButton>{!url&&<p>{displayUi("此本地仓库没有支持的 GitHub HTTPS origin。","This local repository has no supported GitHub HTTPS origin.")}</p>}{job&&<p role="status">{label(job.status)} {job.error||''}</p>}</details></>}
  </>:<div className="inspector-empty"><Icon name="folder"/><h3>{displayUi("连接本地副本后查看代码","Connect a local copy to view code")}</h3><p>{remote?displayUi("连接当前选中的仓库，自动取得本地副本。","Connect the selected repository to obtain a local copy."):displayUi("先选择一个仓库。","Choose a repository first.")}</p><AsyncButton onClick={onConnect} disabled={connecting}>{displayUi("连接本地副本","Connect local copy")}</AsyncButton></div>:!url?<div className="inspector-empty"><h3>{displayUi("选择 GitHub 仓库","Choose a GitHub repository")}</h3><p>{displayUi("从左侧浏览可访问仓库，或连接有 GitHub origin 的本地项目。","Browse accessible repositories on the left, or connect a local project with a GitHub origin.")}</p></div>:<>
   <div className="inspector-path">{remote?.name||basename(record?.repository||'')}</div>
   {panel==='actions'&&<CiInvestigation key={url} repositoryUrl={url} runs={value?.items||[]} readEvidence={readCiEvidence}/>}
   {panel==='issues'?<IssueManager record={record} state={state} focus={issueFocus} items={value?.items||[]} request={request} refresh={refresh}/>:<>{(value?.items||[]).map((item:Data)=><a className="github-card" href={item.url} target="_blank" rel="noopener noreferrer" key={item.id}><span className={'task-dot status-'+item.state}/><div><strong>{item.title}</strong><p>#{item.number||item.id} · {item.state}{item.author?' · '+item.author:''}</p><span className="caption">{item.updated_at}</span></div><span>↗</span></a>)}</>}
   {value&&!value.items.length&&<p>{displayUi('本页暂无条目。','No items on this page.')} {value.has_next?displayUi("还有后续页面，可继续浏览。","More pages are available."):''}</p>}
   {value&&<div className="pagination"><AsyncButton feedback={{pending:busy}} disabled={page===1||busy} onClick={()=>setPage(page-1)}>{displayUi("上一页","Previous")}</AsyncButton><span>{displayUi('第','Page')} {page} {displayUi('页','')}</span><AsyncButton feedback={{pending:busy}} disabled={!value.has_next||busy} onClick={()=>setPage(page+1)}>{displayUi("下一页","Next")}</AsyncButton></div>}
  </>}
 </div><footer className="inspector-footer">{displayUi("只读资料 · 不授予修改或审批权限","Read-only information · grants no editing or approval authority")}</footer></aside>;
}
function FileTree({entries,selected,expand,open}:{entries:Map<string,any>;selected?:string;expand:boolean;open:(path:string)=>void}){
 return <ul className="tree-branch">{[...entries.values()].sort((a,b)=>Number(Boolean(a.file))-Number(Boolean(b.file))||a.name.localeCompare(b.name)).map(entry=><li key={entry.path}>{entry.file?<AsyncButton className={selected===entry.path?'selected':''} title={entry.path} onClick={()=>open(entry.path)}><span>◇</span>{entry.name}</AsyncButton>:<details open={expand}><summary><Icon name="folder"/>{entry.name}</summary><FileTree entries={entry.children} selected={selected} expand={expand} open={open}/></details>}</li>)}</ul>;
}
function CommitGraph({commits}:{commits:Data[]}){
 const rows=graphLanes(commits),width=Math.max(120,...rows.map((r:Data)=>(Math.max(r.before.length,r.after.length)+1)*20));
 return <div className="commit-scroll"><div className="commit-graph"><svg width={width} height={rows.length*36+12} aria-label={displayUi("Git 提交拓扑图","Git commit graph")} role="img">{rows.map((r:Data,index:number)=><g key={r.sha}>{r.before.map((sha:string|null,lane:number)=>sha&&lane!==r.lane?<path className={'lane lane-'+lane%6} key={lane} d={`M${lane*20+12} ${index*36}V${(index+1)*36}`}/>:null)}{r.edges.map((edge:Data)=><path className={'lane lane-'+r.lane%6} key={edge.parent} strokeDasharray={r.missing_parents?.includes(edge.parent)?'3 3':undefined} d={`M${edge.from*20+12} ${index*36+12}C${edge.from*20+12} ${index*36+30},${edge.to*20+12} ${index*36+24},${edge.to*20+12} ${(index+1)*36+12}`}/>)}<circle className={'commit-node lane-'+r.lane%6} cx={r.lane*20+12} cy={index*36+12} r="4"/></g>)}</svg><div className="commit-labels">{rows.map((r:Data)=><div key={r.sha} title={r.sha+' '+r.subject}><code>{r.sha.slice(0,7)}</code><span>{r.subject}</span>{r.shallow&&<span className="caption">{displayUi("浅边界","Shallow boundary")}</span>}</div>)}</div></div>{rows.some((r:Data)=>r.missing_parents?.length)&&<p className="caption">{displayUi("虚线表示尚未装载的父提交或浅克隆边界。","Dashed lines indicate unloaded parent commits or shallow-clone boundaries.")}</p>}</div>;
}
createRoot(document.getElementById('root')!).render(<CapabilityDraftProvider><App/></CapabilityDraftProvider>);
