import React,{useEffect,useRef,useState} from 'react';
import {applyProposal,captureForm,describeProposal,formCaptureInfo} from './form-assist.mjs';
import {reasoningChoices,selectedReasoning} from './model-reasoning.mjs';
import {ModelReasoningPicker} from './ModelReasoningPicker';
import './assistant.css';
type Data=Record<string,any>;
export type AssistantMemory={model:string;selected:string;text:string};
export type AssistantPaneProps={request:(path:string,token?:string,body?:Data)=>Promise<Data>;profiles?:Data|null;language:string;getFormRoot:()=>HTMLElement|null;onOpenHelp:()=>void;onClose?:()=>void;memory?:{current:AssistantMemory}};
const PENDING_KEY='repocare.assistant.pending.v1';
const copies:Record<string,Record<string,string>>={
 'zh-CN':{title:'RepoCare 助手',description:'无需连接仓库，也可以聊天、查教程或整理当前表单。',help:'使用教程',close:'关闭助手',sessions:'会话',newSession:'创建新会话',model:'新会话的模型',chooseModel:'选择已保存的模型',noModels:'请先在设置 → 模型中添加模型提供商。',noSession:'选择模型并创建会话后，即可开始聊天。',selectSession:'选择会话',message:'发送给助手的消息',placeholder:'询问怎么使用，或请助手帮你整理当前表单…',send:'发送',sending:'正在处理…',attach:'附带当前表单的安全字段',readForm:'读取当前表单',formReady:'已读取 {count} 个安全字段。密码、密钥与权限确认不会发送。',noForm:'当前没有可读取的安全字段，可以继续聊天。',charge:'创建会话和查教程不调用模型；发送会调用所选模型，使用独立的助手额度。',budget:'助手请求：已用 {used} · 剩余 {remaining} · 未知占用 {unknown}',refresh:'刷新会话',reconcile:'重新核对操作结果',unknown:'未能确认操作结果。已保留请求标识并暂停发送，请只读核对，勿重复提交。',readError:'无法读取助手记录，请稍后刷新核对。',writeError:'助手操作未完成，请检查模型配置和请求条件后重试。',running:'正在等待这次操作的原始回执…',failed:'这次助手操作未完成。原记录保留，可以刷新核对。',you:'你',assistant:'助手',proposal:'表单草稿建议',before:'原值',after:'建议值',apply:'应用建议到草稿',applied:'已更新草稿。请核对后手动点击表单确认或下一步。',stale:'当前表单已变化，或此建议来自旧页面。请重新读取当前表单并发送请求。',invalid:'这份建议不符合当前安全字段，无法应用。',applyError:'未能更新完整草稿，请核对当前输入后重新读取表单。',draftOnly:'建议只会修改草稿，不会提交表单、确认权限或触发业务操作。',empty:'尚无消息。可以先问“我该如何开始？”',noPermission:'权限、费用批准、密码和密钥请自行填写。',request:'请求标识',true:'是',false:'否',untitled:'助手会话',helpTitle:'使用教程',search:'搜索教程与功能',download:'下载 Markdown 教程',helpLoading:'正在读取教程…',helpError:'无法读取教程，请稍后重试。',retry:'重新读取',catalog:'功能目录',noMatches:'没有匹配内容。',openPage:'打开页面',helpClose:'关闭教程',available:'已实现',partial:'部分实现',planned:'规划中',unverified:'待验证'},
 en:{title:'RepoCare assistant',description:'Chat, read the guide, or draft form values without connecting a repository.',help:'Getting started',close:'Close assistant',sessions:'Sessions',newSession:'Create new session',model:'Model for new session',chooseModel:'Choose a saved model',noModels:'Add a model provider in Settings → Models first.',noSession:'Choose a model and create a session to start chatting.',selectSession:'Select session',message:'Message to assistant',placeholder:'Ask how to use RepoCare, or ask for help drafting the current form…',send:'Send',sending:'Working…',attach:'Include safe fields from the current form',readForm:'Read current form',formReady:'Read {count} safe fields. Passwords, keys, and permission confirmations are excluded.',noForm:'No safe editable fields are available. You can still chat.',charge:'Creating sessions and reading the guide make no model request. Sending uses the selected model and a separate assistant allowance.',budget:'Assistant requests: {used} used · {remaining} remaining · {unknown} unknown',refresh:'Refresh sessions',reconcile:'Check this operation again',unknown:'The result is unknown. The request ID has been retained and sending is paused. Check the record without resubmitting.',readError:'Could not read assistant records. Refresh to check.',writeError:'The assistant operation did not complete. Check the model configuration and request before retrying.',running:'Waiting for the original receipt for this operation…',failed:'This assistant operation did not complete. The original record is retained; refresh to check.',you:'You',assistant:'Assistant',proposal:'Suggested form draft',before:'Current',after:'Suggested',apply:'Apply suggestion to draft',applied:'Draft updated. Review it, then click the form’s confirmation or next-step button yourself.',stale:'The form changed, or this suggestion belongs to an earlier page. Read the current form and send a new request.',invalid:'This suggestion does not match the current safe fields and cannot be applied.',applyError:'The complete draft could not be updated. Review current inputs, then read the form again.',draftOnly:'Applying changes draft values only. It never submits a form, confirms permission, or starts a business operation.',empty:'No messages yet. Try “How do I get started?”',noPermission:'Enter permissions, payment approvals, passwords, and API keys yourself.',request:'Request ID',true:'Yes',false:'No',untitled:'Assistant session',helpTitle:'Getting started',search:'Search the guide and features',download:'Download Markdown guide',helpLoading:'Loading guide…',helpError:'Could not load the guide. Try again later.',retry:'Reload guide',catalog:'Feature catalog',noMatches:'No matching content.',openPage:'Open page',helpClose:'Close guide',available:'Implemented',partial:'Partially implemented',planned:'Planned',unverified:'Unverified'}
};
copies['zh-CN'].noForm='此页没有已登记的安全草稿字段。助手可以解释步骤；未登记的控件请手动操作。';copies.en.noForm='No registered safe draft fields are available here. The assistant can explain the steps; use unregistered controls yourself.';
copies['zh-CN'].partial='已停止继续填写，部分草稿可能已更新。请核对当前输入并重新读取表单。';copies.en.partial='Filling stopped. Some draft values may have changed. Review the current inputs and read the form again.';
Object.assign(copies['zh-CN'],{catalogPartial:'部分实现',connected:'已接入',mixed:'部分接入',preview:'流程预览',internal:'内部机制',engineering:'工程验证'});
Object.assign(copies.en,{catalogPartial:'Partially implemented',connected:'Connected',mixed:'Partially connected',preview:'Preview',internal:'Internal mechanism',engineering:'Engineering verification'});
Object.assign(copies['zh-CN'],{currentModel:'当前会话模型',switchModel:'切换到模型',effort:'思考深度',switch:'切换模型 / 思考深度',switchNotice:'切换只建立关联的新会话，原记录保留；发送消息时才调用模型。',configureReasoning:'此模型未明确配置可选思考档位，请到设置 → 模型中选择协议和档位。',history:'关联历史（只读）',variantUnknown:'思考配置的保存结果尚未确认。请刷新模型目录核对后，再手动切换。',variantChecked:'模型目录已重新读取，请核对配置后手动切换。',invalid_model:'服务商未接受这个 API 模型 ID，请在模型设置中获取目录并核对。',auth:'服务商未接受身份验证，请在模型设置中检查密钥。',network:'模型连接未完成，请检查服务地址和网络后刷新核对。',rate_limit:'服务商限制了当前请求，请稍后重试。',unsupported_parameters:'服务商未接受这些模型参数，请核对思考协议、档位映射与长度。',provider_error:'服务商未完成请求，请检查模型设置和原操作记录。'});
Object.assign(copies.en,{currentModel:'Current session model',switchModel:'Switch to model',effort:'Thinking depth',switch:'Switch model / thinking depth',switchNotice:'Switching creates a linked conversation and keeps the original records. Only sending a message calls the model.',configureReasoning:'This model has no explicit thinking choices. Choose its protocol and levels in Settings → Models.',history:'Linked history (read only)',variantUnknown:'The thinking configuration result is unknown. Refresh the model directory to check, then switch manually.',variantChecked:'The model directory was read again. Review the configuration and switch manually.',invalid_model:'The provider did not accept this API model ID. Fetch the model directory in Settings and check it.',auth:'The provider did not accept authentication. Check the key in model settings.',network:'The model connection did not finish. Check the endpoint and network, then refresh the record.',rate_limit:'The provider limited this request. Try again later.',unsupported_parameters:'The provider did not accept these model parameters. Check the thinking protocol, mappings, and lengths.',provider_error:'The provider did not complete this request. Check the model settings and original operation record.'});
Object.assign(copies['zh-CN'],{
 timeout:'模型请求超时。草稿已保留，本次额度占用可在「会话与用量」查看。',
 budget_blocked:'这个模型的助手额度已用完或被未知请求占用。请查看「会话与用量」，或切换到仍有额度的模型。',
});
Object.assign(copies.en,{
 timeout:'The model request timed out. Your draft is retained. Check Session and usage for the reservation.',
 budget_blocked:'This model has no remaining assistant allowance. Check Session and usage, or switch to a model with remaining allowance.',
});
export function assistantCopy(language:string,key:string,variables:Data={}){const dictionary=copies[language]||copies['zh-CN'];return Object.entries(variables).reduce((text,[name,value])=>text.replaceAll('{'+name+'}',String(value)),dictionary[key]||key);}
type Pending={requestId:string;jobId:string;action:'create'|'send'|'switch_model';sessionId?:string;unknown:boolean;stage?:'variant'|'switch_model'};
function readPending():Pending|null{try{const value=JSON.parse(sessionStorage.getItem(PENDING_KEY)||'null');return value&&/^[a-f0-9]{32}$/.test(value.requestId)&&['create','send','switch_model'].includes(value.action)?{requestId:value.requestId,jobId:typeof value.jobId==='string'?value.jobId:value.requestId,action:value.action,sessionId:typeof value.sessionId==='string'?value.sessionId:undefined,unknown:true,...(['variant','switch_model'].includes(value.stage)?{stage:value.stage}:{})}:null;}catch{return null;}}
function requestId(){return crypto.randomUUID().replaceAll('-','');}
export function AssistantPane({request,profiles,language,getFormRoot,onOpenHelp,onClose,memory}:AssistantPaneProps){
 const tr=(key:string,variables:Data={})=>assistantCopy(language,key,variables);
 const [state,setState]=useState<Data>({sessions:[],jobs:[],budgets:{}}),[selected,setSelectedState]=useState(()=>memory?.current.selected||''),[model,setModelState]=useState(()=>memory?.current.model||profiles?.default_profile_id||'');
 const [text,setTextState]=useState(()=>memory?.current.text||''),[attach,setAttach]=useState(true),[snapshot,setSnapshot]=useState<Data|null>(null),[formStatus,setFormStatus]=useState('');
 const [pending,setPendingState]=useState<Pending|null>(readPending),[preparing,setPreparing]=useState(false),[error,setError]=useState('');
 const [applyStatus,setApplyStatus]=useState<Record<string,string>>({}),[applying,setApplying]=useState<string|null>(null);
 const [catalog,setCatalog]=useState<Data|null>(profiles||null),[switchModel,setSwitchModel]=useState(''),[effort,setEffort]=useState('off');
 const alive=useRef(true),sending=useRef(false),reading=useRef(false),readAgain=useRef(false),pendingRef=useRef<Pending|null>(pending),polls=useRef(0),snapshots=useRef(new Map<string,Data>()),applied=useRef(new Set<string>()),applyLock=useRef(false);
 const catalogNeedsRefresh=useRef(false);
 const selectedRef=useRef(selected);selectedRef.current=selected;
 // The App owns only these non-authoritative view drafts across sidebar/modal mounts.
 // Update the shared memory on every setter, never as an unmount-cleanup side effect.
 function setSelected(value:string){if(memory)memory.current.selected=value;selectedRef.current=value;setSelectedState(value);}
 function setModel(value:string){if(memory)memory.current.model=value;setModelState(value);}
 function setText(value:string){if(memory)memory.current.text=value;setTextState(value);}
 const sessions=Array.isArray(state.sessions)?state.sessions:[],current=sessions.find((session:Data)=>session.id===selected)||null;
 const profileRows=catalog?.profiles||profiles?.profiles||[],available=profileRows.filter((profile:Data)=>profile.selectable&&!profile.archived),busy=preparing||Boolean(pending);
 const target=available.find((profile:Data)=>profile.id===(current?switchModel:model)),choices=reasoningChoices(target);
 const currentProfile=profileRows.find((profile:Data)=>profile.id===current?.profile_id);
 useEffect(()=>{setCatalog(profiles||null);},[profiles]);
 useEffect(()=>{if(current){setSwitchModel(current.profile_id);setEffort(selectedReasoning((catalog?.profiles||profiles?.profiles||[]).find((profile:Data)=>profile.id===current.profile_id)));}},[current?.id,currentProfile?.id]);
 useEffect(()=>{if(!available.some((profile:Data)=>profile.id===model))setModel(available.some((profile:Data)=>profile.id===profiles?.default_profile_id)?profiles?.default_profile_id:available[0]?.id||'');},[profiles]);
 useEffect(()=>{if(!current)setEffort(selectedReasoning(available.find((profile:Data)=>profile.id===model)));},[current?.id,model]);
 function chooseModel(id:string){if(busy||catalogNeedsRefresh.current)return;const next=available.find((profile:Data)=>profile.id===id);if(!next)return;setModel(id);setSwitchModel(id);setEffort(selectedReasoning(next));}
 const modelChanged=Boolean(current&&target&&(target.id!==current.profile_id||choices.length&&effort!==target.reasoning_effort));
 function setPending(value:Pending|null){pendingRef.current=value;setPendingState(value);try{if(value)sessionStorage.setItem(PENDING_KEY,JSON.stringify(value));else sessionStorage.removeItem(PENDING_KEY);}catch{}}
 function jobResult(job:Data,p:Pending){
  if(['finished','completed','succeeded'].includes(job.status)){
   if(['create','switch_model'].includes(p.action)&&typeof job.result?.session_id==='string')setSelected(job.result.session_id);
   if(p.action==='send')setText('');setPending(null);polls.current=0;return;
  }
  if(['failed','cancelled','rejected'].includes(job.status)){setError(tr(['invalid_model','auth','network','rate_limit','unsupported_parameters','provider_error','timeout','budget_blocked'].includes(job.error_category)?job.error_category:'failed'));setPending(null);return;}
  const unknown=['unknown','interrupted'].includes(job.status)||polls.current>=80;
  setPending({...p,jobId:job.id||p.jobId,unknown});
 }
 async function refresh(explicit=false){
  if(reading.current){readAgain.current=true;return;}reading.current=true;
  try{const value=await request('/api/assistant/state');if(!alive.current)return;
   setState(value);const records=Array.isArray(value.sessions)?value.sessions:[];
   if(!selectedRef.current&&records.length)setSelected(typeof value.active==='string'&&records.some((session:Data)=>session.id===value.active)?value.active:records[0].id);
   const p=pendingRef.current;if(p?.stage==='variant'||catalogNeedsRefresh.current){
    if(!explicit)return;
    const refreshed=await request('/api/model-profiles');if(!alive.current)return;
    if(!Array.isArray(refreshed.profiles)||typeof refreshed.revision!=='string')throw Error('Invalid model catalog');
    setCatalog(refreshed);catalogNeedsRefresh.current=false;if(p?.stage==='variant')setPending(null);setError(tr('variantChecked'));
   }else if(p){const job=(value.jobs||[]).find((item:Data)=>item.id===p.jobId||item.id===p.requestId||item.request_id===p.requestId);
    if(job){polls.current++;jobResult(job,p);}else setPending({...p,unknown:true});}
  }catch{if(alive.current){setError(tr('readError'));if(pendingRef.current)setPending({...pendingRef.current,unknown:true});}}
  finally{reading.current=false;if(readAgain.current&&alive.current){readAgain.current=false;queueMicrotask(()=>void refresh());}}
 }
 async function switchConversation(){return prepareConversation('switch_model');}
 async function prepareConversation(action:'create'|'switch_model'){
  if(sending.current||pendingRef.current||catalogNeedsRefresh.current||action==='switch_model'&&(!current||!modelChanged)||!target?.selectable||target.archived||choices.length&&!choices.some(row=>row.level===effort))return;
  sending.current=true;setPreparing(true);setError('');polls.current=0;
  const id=requestId(),p:Pending={requestId:id,jobId:id,action,sessionId:current?.id,unknown:false};
  let sent=false,variant=false;
  try{
   let profileId=target.id;
   if(choices.length&&effort!==target.reasoning_effort){
    variant=true;
    setPending({...p,stage:'variant'});
    const result=await request('/api/model-profiles',undefined,{action:'reasoning_variant',revision:catalog?.revision||profiles?.revision,profile_id:target.id,reasoning_effort:effort});
    if(!alive.current)return;
    const resolved=result.profiles?.find((profile:Data)=>profile.id===result.created_profile_id);
    if(typeof resolved?.id!=='string'||!resolved.id||!resolved.selectable||resolved.archived||resolved.model!==target.model||resolved.base_url!==target.base_url||resolved.reasoning_effort!==effort||resolved.reasoning_protocol!==target.reasoning_protocol||JSON.stringify(resolved.reasoning_levels??null)!==JSON.stringify(target.reasoning_levels??null)||resolved.context_window_tokens!==target.context_window_tokens||resolved.max_output_tokens!==target.max_output_tokens){const invalid=Error('Invalid reasoning variant') as Error&{uncertain:boolean};invalid.uncertain=true;throw invalid;}
    setCatalog(result);profileId=resolved.id;variant=false;
   }
   if(!alive.current)return;
   setPending({...p,stage:'switch_model'});sent=true;
   const response=await request('/api/assistant/actions/'+action,undefined,{request_id:id,...(action==='switch_model'?{session_id:current.id}:{}),profile_id:profileId});
   if(!alive.current)return;
   const job=response.job||response;
   if(!job||typeof job.id!=='string'||job.id!==id&&job.request_id!==id){setPending({...p,unknown:true});return;}
   setPending({...p,jobId:job.id});jobResult(job,{...p,jobId:job.id});await refresh();
  }catch(e){if(alive.current){if((e as Data)?.uncertain)setPending({...p,unknown:true,...(variant&&!sent?{stage:'variant'}:{})});else{if(variant&&!sent)catalogNeedsRefresh.current=true;setPending(null);setError(tr('writeError'));}}}
  finally{sending.current=false;if(alive.current)setPreparing(false);}
 }
 useEffect(()=>{alive.current=true;void refresh();return()=>{alive.current=false;};},[]);
 useEffect(()=>{if(!pending||pending.unknown||pending.stage==='variant')return;const timer=setTimeout(()=>{void refresh();},800);return()=>clearTimeout(timer);},[pending]);
 async function readForm(){
  try{const value=await captureForm(getFormRoot());if(!alive.current)return null;
   setSnapshot(value);setFormStatus(value?'formReady':'noForm');if(value)snapshots.current.set(value.form_id+':'+value.revision,value);return value;
  }catch{if(alive.current){setSnapshot(null);setFormStatus('noForm');}return null;}
 }
 async function mutate(action:'create'|'send'){
  if(action==='create')return prepareConversation('create');
  if(sending.current||pendingRef.current||modelChanged||!current||!text.trim())return;
  sending.current=true;setPreparing(true);setError('');polls.current=0;
  const id=requestId(),p:Pending={requestId:id,jobId:id,action,sessionId:current.id,unknown:false};
  try{
   const context=attach?await readForm():null;
   if(!alive.current)return;
   const body:Data={request_id:id,session_id:current.id,text:text.trim(),form_context:context,language};
   setPending(p);const response=await request('/api/assistant/actions/'+action,undefined,body);
   if(!alive.current)return;
   const job=response.job||response;
   if(!job||typeof job.id!=='string'||job.id!==id&&job.request_id!==id){setPending({...p,unknown:true});return;}
   setPending({...p,jobId:job.id});jobResult(job,{...p,jobId:job.id});await refresh();
  }catch(e){if(alive.current){if((e as Data)?.uncertain)setPending({...p,unknown:true});else{setPending(null);setError(tr('writeError'));}}}
  finally{sending.current=false;if(alive.current)setPreparing(false);}
 }
 async function apply(message:Data,saved:Data,proposal:Data){
  if(applyLock.current||applied.current.has(message.id))return;
  applyLock.current=true;setApplying(message.id);
  try{const result=await applyProposal(getFormRoot(),saved,proposal);if(alive.current){setApplyStatus(old=>({...old,[message.id]:result.applied?'applied':result.updated?'partial':result.reason==='stale'?'stale':result.reason==='invalid'?'invalid':'applyError'}));if(result.applied)applied.current.add(message.id);}}
  catch{if(alive.current)setApplyStatus(old=>({...old,[message.id]:'applyError'}));}
  finally{applyLock.current=false;if(alive.current)setApplying(null);}
 }
 const budget=state.budgets?.[current?.profile_id||model];
 const display=(value:any,saved?:Data|null,fieldId?:string)=>{
  const field=saved?.fields?.find((row:Data)=>row.id===fieldId),index=field?.type==='select'?field.options?.indexOf(value):-1;
  if(index>=0&&typeof field.option_labels?.[index]==='string')return field.option_labels[index];
  return typeof value==='boolean'?tr(value?'true':'false'):String(value??'');
 };
 return <aside className="assistant-pane" aria-label={tr('title')} data-form-assist="exclude">
  <header className="assistant-header"><div><h2>{tr('title')}</h2><p>{tr('description')}</p></div><div><button type="button" onClick={onOpenHelp}>{tr('help')}</button>{onClose&&<button className="assistant-close" type="button" aria-label={tr('close')} onClick={onClose}>×</button>}</div></header>
  <div className="assistant-session-controls">
   {sessions.length>0&&<select aria-label={tr('selectSession')} value={selected} disabled={busy} onChange={event=>{setSelected(event.target.value);setSnapshot(null);setFormStatus('');}}><option value="">{tr('selectSession')}</option>{sessions.map((session:Data)=><option value={session.id} key={session.id}>{session.title||tr('untitled')} · {session.model}</option>)}</select>}
   <button className="assistant-new-chat" type="button" aria-label={tr('newSession')} title={tr('newSession')} disabled={busy||catalogNeedsRefresh.current||!target} onClick={()=>mutate('create')}>＋</button>
  </div>
  <div className="assistant-messages" aria-live="polite" aria-relevant="additions text">
   {!current&&<p className="assistant-empty">{tr('noSession')}</p>}{current&&!(current.messages||[]).length&&<p className="assistant-empty">{tr('empty')}</p>}
   {current?.continuation&&<section className="assistant-linked-history" aria-label={tr('history')}><h3>{tr('history')}</h3>{(current.history_messages||[]).map((message:Data,index:number)=><article className={'assistant-message assistant-message-'+(message.role==='user'?'user':'assistant')} key={'history-'+index}><strong>{tr(message.role==='user'?'you':'assistant')}</strong><div className="assistant-message-content">{String(message.content||'')}</div></article>)}</section>}
   {(current?.messages||[]).map((message:Data,index:number)=>{const proposal=message.proposal,saved=proposal?snapshots.current.get(proposal.form_id+':'+proposal.revision):null,diff=saved?describeProposal(saved,proposal):null,messageKey=message.id||String(index);
    return <article className={'assistant-message assistant-message-'+message.role} key={messageKey}><strong>{tr(message.role==='user'?'you':'assistant')}</strong><div className="assistant-message-content">{String(message.content||'')}</div>
     {proposal&&<section className="assistant-proposal" aria-label={tr('proposal')}><h3>{tr('proposal')}</h3>{proposal.explanation&&<p>{String(proposal.explanation)}</p>}{diff?<dl>{diff.map((change:Data)=><div key={change.id}><dt>{change.label}</dt><dd><span>{tr('before')}: {display(change.before,saved,change.id)}</span><span>→ {tr('after')}: {display(change.after,saved,change.id)}</span></dd></div>)}</dl>:<p>{tr('stale')}</p>}<p className="caption">{tr('draftOnly')}</p><button type="button" disabled={!diff||Boolean(applying)||applied.current.has(message.id)} onClick={()=>saved&&apply(message,saved,proposal)}>{tr('apply')}</button>{applyStatus[message.id]&&<p role="status">{tr(applyStatus[message.id])}</p>}</section>}
    </article>;})}
  </div>
  <div className="assistant-operation-status">{pending&&<><p role="status">{tr(pending.stage==='variant'?(pending.unknown?'variantUnknown':'running'):pending.unknown?'unknown':'running')}</p><p className="caption">{tr('request')}: <code>{pending.requestId}</code></p></>}{error&&<p role="alert" className="error-text">{error}</p>}<button type="button" disabled={preparing} onClick={()=>refresh(true)}>{tr(pending?'reconcile':'refresh')}</button></div>
  <form className="assistant-composer" onSubmit={event=>{event.preventDefault();return mutate('send');}} autoComplete="off">
   <details className="assistant-form-tools"><summary>{language==='en'?'Current form':'当前表单'}</summary>
   <label className="assistant-attach"><input type="checkbox" checked={attach} disabled={busy} onChange={event=>{setAttach(event.target.checked);if(!event.target.checked){setSnapshot(null);setFormStatus('');}}}/>{tr('attach')}</label><button type="button" className="assistant-read-form" onClick={readForm} disabled={busy}>{tr('readForm')}</button>{formStatus&&<p className="caption" role="status">{tr(formStatus,{count:snapshot?.fields.length||0})}{snapshot&&formCaptureInfo(snapshot).omitted>0&&(language==='en'?` ${formCaptureInfo(snapshot).omitted} oversized fields were omitted.`:` 已略过 ${formCaptureInfo(snapshot).omitted} 个超出大小限制的字段。`)}</p>}
   </details>
   <textarea aria-label={tr('message')} placeholder={tr('placeholder')} value={text} onChange={event=>setText(event.target.value)} maxLength={4000} disabled={!current||busy} onKeyDown={event=>{if((event.ctrlKey||event.metaKey)&&event.key==='Enter'){event.preventDefault();void mutate('send');}}}/>
   <div className="assistant-composer-bottom"><ModelReasoningPicker profiles={profileRows} value={current?switchModel:model} effort={effort} pending={modelChanged} defaultId={catalog?.default_profile_id} language={language} label={language==='en'?'Assistant model and thinking depth':'助手模型与思考深度'} disabled={busy||catalogNeedsRefresh.current} onModelChange={chooseModel} onEffortChange={level=>{if(!busy&&choices.some(row=>row.level===level))setEffort(level);}} onApply={!current?()=>mutate('create'):modelChanged?switchConversation:undefined} applyDisabled={!target||busy||catalogNeedsRefresh.current} applyLabel={tr(current?'switch':'newSession')} notice={tr(current?'switchNotice':'charge')}/><button className="primary" type="submit" disabled={!current||!text.trim()||busy||modelChanged}>{tr(busy?'sending':'send')}</button></div>
   {modelChanged&&<p className="assistant-model-pending caption" role="status">{language==='en'?'Apply the model selection before sending.':'先应用模型选择'}</p>}
   <details className="assistant-composer-details"><summary>{language==='en'?'Session and usage':'会话与用量'}</summary><p className="caption">{tr('charge')}</p>{budget&&<p className="caption">{tr('budget',{used:budget.requests_used??budget.used??'—',remaining:budget.requests_remaining??budget.remaining??'—',unknown:budget.requests_reserved_unknown??budget.unknown??'—'})}</p>}<p className="caption">{tr('noPermission')}</p>{!available.length&&<p className="caption">{tr('noModels')}</p>}</details>
  </form>
 </aside>;
}
