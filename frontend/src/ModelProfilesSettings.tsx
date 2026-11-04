import React,{useEffect,useRef,useState} from 'react';
import {t} from './preferences.mjs';
import {modelSettingsCopy} from './model-settings-copy.mjs';
import {parseModelTokenValues} from './model-token-values.mjs';

type Data=Record<string,any>;
type Props={request:(path:string,token?:string,body?:Data)=>Promise<Data>;initialProfiles?:Data;onProfilesChange?:(value:Data)=>void;language?:string};
type Level={level:string;api_value:string|null};
type Draft={id:number;name:string;model:string;context:string;output:string;protocol:string;effort:string;levels:Level[];protocolTouched?:boolean;catalogId?:string;catalogUrl?:string;originUrl?:string;reviewRequired?:boolean;expanded?:boolean;manual?:boolean;source?:Data;baseline?:string;selected?:boolean};
type Editor={mode:'add'|'edit';url:string;key:string;source:Data|null;drafts:Draft[]};
const efforts:Record<string,string[]>={none:['off'],openai:['off','minimal','low','medium','high','xhigh','max'],deepseek:['off','low','high','max']};
const levelNames=['off','low','medium','high','xhigh','max','minimal'];
function defaultLevels(protocol:string):Level[]{return (protocol==='none'?['off']:levelNames).map(level=>({level,
 api_value:level==='off'?(protocol==='openai'?'none':null):protocol==='deepseek'?({minimal:'low',medium:'high',xhigh:'high'} as Data)[level]||level:level}));}
function defaultProtocol(endpoint:string){try{const url=new URL(endpoint.trim());return url.protocol==='https:'&&url.hostname==='api.deepseek.com'&&(!url.port||url.port==='443')&&!url.username&&!url.password&&!url.search&&!url.hash&&['','/','/v1','/v1/'].includes(url.pathname)?'deepseek':'openai';}catch{return 'openai';}}
const safeNumber=(value:any)=>Number.isSafeInteger(value)&&value>0?value:undefined;
function publicProfiles(value:Data):Data{return {revision:value.revision,default_profile_id:value.default_profile_id,max_profiles:value.max_profiles,
 profiles:Array.isArray(value.profiles)?value.profiles.map((p:Data)=>({id:p.id,name:p.name,base_url:p.base_url,model:p.model,key_set:Boolean(p.key_set),legacy:Boolean(p.legacy),archived:Boolean(p.archived),selectable:Boolean(p.selectable),
  context_window_tokens:safeNumber(p.context_window_tokens),max_output_tokens:safeNumber(p.max_output_tokens),limits_explicit:p.limits_explicit===true,
  reasoning_explicit:p.reasoning_explicit===true,reasoning_protocol:p.reasoning_protocol,reasoning_effort:p.reasoning_effort,
  reasoning_levels:Array.isArray(p.reasoning_levels)?p.reasoning_levels.filter((item:Data)=>levelNames.includes(item.level)&&(item.api_value===null||typeof item.api_value==='string')).map((item:Data)=>({level:item.level,api_value:item.api_value})):null})):[]};}

function draftFingerprint(draft:Draft){const lengths=parseModelTokenValues(draft.context,draft.output);return JSON.stringify({
 name:draft.name.trim(),model:draft.model,context:lengths?.context_window_tokens??draft.context.trim(),output:lengths?.max_output_tokens??draft.output.trim(),
 protocol:draft.protocol,...(draft.protocol==='inherit'?{}:{effort:draft.effort,levels:draft.levels})});}
function providerGroups(rows:Data[]){const groups:Data[]=[];for(const profile of rows.filter(row=>!row.archived)){
 let group=groups.find(item=>item.base_url===profile.base_url);if(!group){group={base_url:profile.base_url,representative:profile,profiles:[],models:[]};groups.push(group);}
 group.profiles.push(profile);const previous=group.models.findIndex((item:Data)=>item.model===profile.model);if(previous<0)group.models.push(profile);else group.models[previous]=profile;
 }return groups;}

/** Immutable model versions, grouped by endpoint. Directory reads never run inference. */
export function ModelProfilesSettings({request,initialProfiles,onProfilesChange,language='zh-CN'}:Props){
 const tr=(key:string,variables:Data={})=>modelSettingsCopy(language,key,variables)??t(language,key,variables);
 const [profiles,setProfiles]=useState<Data|null>(()=>initialProfiles?publicProfiles(initialProfiles):null),profilesRef=useRef(profiles);
 const [deleting,setDeleting]=useState<string|null>(null),[showArchived,setShowArchived]=useState(false);
 const [editor,setEditor]=useState<Editor|null>(null),editorRef=useRef<Editor|null>(null);
 const [directory,setDirectory]=useState<Data[]>([]),[directoryBusy,setDirectoryBusy]=useState(false),[directoryMessage,setDirectoryMessage]=useState('');
 const [busy,setBusy]=useState(false),[uncertain,setUncertain]=useState(false),[checked,setChecked]=useState(false),[message,setMessage]=useState(''),[error,setError]=useState(''),[saved,setSaved]=useState<Data[]>([]);
 const alive=useRef(true),sending=useRef(false),blocked=useRef(false),epoch=useRef(0),directoryAttempt=useRef(-1),directoryRunning=useRef(-1),sequence=useRef(0);
 const firstInput=useRef<HTMLInputElement>(null),addButton=useRef<HTMLButtonElement>(null),deleteTarget=useRef<string|null>(null);
 function confirmDelete(id:string|null){deleteTarget.current=id;setDeleting(id);}
 const count=profiles?.profiles.filter((p:Data)=>!p.legacy).length||0,limit=profiles&&count>=profiles.max_profiles;
 function apply(value:Data){const safe=publicProfiles(value);profilesRef.current=safe;setProfiles(safe);onProfilesChange?.(safe);return safe;}
 function patch(values:Partial<Editor>){if(editorRef.current)editorRef.current={...editorRef.current,...values};setEditor(previous=>previous?{...previous,...values}:previous);}
 function changeDraft(id:number,values:Partial<Draft>){
  if(editorRef.current)editorRef.current={...editorRef.current,drafts:editorRef.current.drafts.map(draft=>draft.id===id?{...draft,...values}:draft)};
  setEditor(previous=>previous?{...previous,drafts:previous.drafts.map(draft=>draft.id===id?{...draft,...values}:draft)}:previous);
 }
 function invalidateDirectory(){epoch.current++;directoryRunning.current=-1;setDirectory([]);setDirectoryMessage('');setDirectoryBusy(false);}
 function connection(field:'url'|'key',value:string){
  invalidateDirectory();const protocol=defaultProtocol(value);
  patch(field==='url'?{url:value,key:'',drafts:editorRef.current!.drafts.map(draft=>({...draft,catalogId:undefined,catalogUrl:undefined,...(draft.protocolTouched?{}:{protocol,effort:'off',levels:defaultLevels(protocol)})}))}:{key:value});setError('');
 }
 function blank(profile?:Data,endpoint=editorRef.current?.url||''):Draft{
  const output=safeNumber(profile?.max_output_tokens),context=safeNumber(profile?.context_window_tokens);
  const proposed=profile&&!profile.limits_explicit?Math.max(4096,context||32768,(output||8192)+4096):context;
  const draft:Draft={id:++sequence.current,...(profile?{originUrl:endpoint,source:profile,catalogId:profile.model,catalogUrl:endpoint,selected:true}:{}),name:profile?profile.name:'',model:profile?.model||'',
   context:profile&&safeNumber(proposed)?String(proposed):'',output:profile?String(output||8192):'',
   protocol:profile?(profile.reasoning_explicit&&efforts[profile.reasoning_protocol]?profile.reasoning_protocol:'inherit'):defaultProtocol(endpoint),effort:profile?.reasoning_effort||'off',protocolTouched:Boolean(profile),
   levels:profile?.reasoning_levels?.length?profile.reasoning_levels.map((item:Level)=>({...item})):defaultLevels(profile?.reasoning_protocol||defaultProtocol(endpoint))};
  if(profile)draft.baseline=draftFingerprint(draft);return draft;
 }
 function back(){if(sending.current||blocked.current)return;invalidateDirectory();editorRef.current=null;setEditor(null);setError('');setChecked(false);queueMicrotask(()=>addButton.current?.focus());}
 async function read(){
  if(sending.current)return;sending.current=true;setBusy(true);setError('');
  try{const value=await request('/api/model-profiles');if(alive.current){const safe=apply(value);if(deleteTarget.current)syncSavedModels(safe);else if(editorRef.current)patch({source:remainingSource(editorRef.current,safe)});blocked.current=false;setUncertain(false);setChecked(true);confirmDelete(null);}}
  catch{if(alive.current)setError(tr('models.readError'));}
  finally{sending.current=false;if(alive.current)setBusy(false);}
 }
 useEffect(()=>{alive.current=true;if(!initialProfiles)void read();return()=>{alive.current=false;epoch.current++;editorRef.current=null;};},[]);
 useEffect(()=>{if(editor)firstInput.current?.focus();},[Boolean(editor)]);
 async function fetchDirectory(automatic=false){
  const current=editorRef.current,version=epoch.current;
  if(!current||sending.current||blocked.current||!current.url.trim())return;
  const reuse=current.source?.key_set&&current.url===current.source.base_url&&!current.key;
  if(!reuse&&!current.key)return;
  if(directoryRunning.current===version||(automatic&&directoryAttempt.current===version))return;
  directoryAttempt.current=version;directoryRunning.current=version;setDirectoryBusy(true);setDirectoryMessage('');
  const payload:Data=reuse?{profile_id:current.source!.id}:{base_url:current.url.trim(),api_key:current.key};
  const secret=current.key;
  try{
   const value=await request('/api/provider-models',undefined,payload);
   if(!alive.current||epoch.current!==version||!editorRef.current)return;
   const seen=new Set<string>(),models=(Array.isArray(value.models)?value.models:[]).filter((model:Data)=>
    typeof model.id==='string'&&model.id.length>0&&model.id.length<=256&&!/[\s\x00-\x1f\x7f]/.test(model.id)&&(!secret||!model.id.includes(secret))&&!seen.has(model.id)&&Boolean(seen.add(model.id)))
    .map((model:Data)=>({id:model.id,name:typeof model.name==='string'&&(!secret||!model.name.includes(secret))?model.name:model.id,
     context_window_tokens:safeNumber(model.context_window_tokens),max_output_tokens:safeNumber(model.max_output_tokens),
     reasoning_efforts:Array.isArray(model.reasoning_efforts)?model.reasoning_efforts.filter((item:string)=>efforts.openai.includes(item)):[]}));
   setDirectory(models);const latest=editorRef.current;patch({drafts:latest.drafts.map(draft=>draft.originUrl===latest.url&&models.some((model:Data)=>model.id===draft.model)?{...draft,catalogId:draft.model,catalogUrl:latest.url}:draft)});setDirectoryMessage(tr(models.length?'models.directoryReady':'models.directoryEmpty',{count:models.length}));
  }catch{if(alive.current&&epoch.current===version)setDirectoryMessage(tr('models.directoryError'));}
  finally{if('api_key' in payload)payload.api_key='';if(alive.current&&epoch.current===version){directoryRunning.current=-1;setDirectoryBusy(false);}}
 }
 function open(profile?:Data,service?:Data){
  if(sending.current||blocked.current||(limit&&!profile&&!service))return;invalidateDirectory();confirmDelete(null);setError('');setMessage('');setSaved([]);setChecked(false);
  const endpoint=(profile||service)?.base_url||'',group=providerGroups(profilesRef.current?.profiles||[]).find(item=>item.base_url===endpoint);
  const source=profile||service||null,drafts=group?group.models.map((item:Data)=>({...blank(item,endpoint),expanded:group.models.length===1})):[blank(undefined,endpoint)];
  const next:Editor={mode:group?'edit':'add',url:endpoint,key:'',source,drafts};editorRef.current=next;setEditor(next);if(service?.key_set)void fetchDirectory();
 }
 function remainingSource(current:Editor,value:Data){
  if(current.source&&current.source.base_url!==current.url)return current.source;
  const active=value.profiles.find((profile:Data)=>profile.id===current.source?.id&&!profile.archived);
  return active||providerGroups(value.profiles).find(group=>group.base_url===current.url)?.models.find((profile:Data)=>profile.key_set&&profile.selectable)||null;
 }
 function syncSavedModels(value:Data){const current=editorRef.current;if(!current)return;const group=providerGroups(value.profiles).find(item=>item.base_url===current.url);
  const sources=(group?.models||[]).map((profile:Data)=>current.drafts.find(draft=>draft.source?.id===profile.id)||{...blank(profile,current.url),expanded:false});
  patch({source:remainingSource(current,value),drafts:[...sources,...current.drafts.filter(draft=>!draft.source)]});
 }
 function disabledModels(current:Editor,value:Data){return current.drafts.filter(draft=>draft.source&&draft.selected===false).map(draft=>({draft,
  targets:value.profiles.filter((profile:Data)=>!profile.archived&&profile.base_url===draft.source!.base_url&&profile.model===draft.source!.model)}));}
 function manual(){const current=editorRef.current;if(!current||sending.current||blocked.current)return;const empty=current.drafts.find(draft=>!draft.catalogId&&!draft.model&&!draft.name&&!draft.context&&!draft.output&&!draft.manual);if(empty)changeDraft(empty.id,{manual:true,expanded:true});else patch({drafts:[...current.drafts,{...blank(),manual:true}]});}
 function choose(model:Data,selected:boolean){
  const current=editorRef.current;if(!current||sending.current||blocked.current)return;
  const existing=current.drafts.find(draft=>draft.catalogId===model.id&&draft.catalogUrl===current.url);
  if(existing?.source){changeDraft(existing.id,{selected,expanded:selected?existing.expanded:false});if(!selected)setMessage(tr('models.pendingRemovalHint'));return;}
  if(!selected){patch({drafts:current.drafts.filter(draft=>draft.catalogId!==model.id||draft.catalogUrl!==current.url)});return;}
  if(existing)return;
  const template=current.drafts.find(item=>!item.model&&!item.name&&!item.context&&!item.output),draft={...blank(),
   ...(template?.protocolTouched?{protocol:template.protocol,effort:template.effort,levels:template.levels.map(item=>({...item})),protocolTouched:true}:{}),
   catalogId:model.id,catalogUrl:current.url,name:String(model.name||model.id).slice(0,80),model:model.id,
   context:model.context_window_tokens?String(model.context_window_tokens):'',output:model.max_output_tokens?String(model.max_output_tokens):''};
  patch({drafts:[...current.drafts.filter(item=>item.model||item.name||item.context||item.output||item.manual),draft]});
 }
 async function setDefault(id:string){
  const current=profilesRef.current;if(sending.current||blocked.current||!current?.profiles.some((p:Data)=>p.id===id&&p.selectable))return;
  sending.current=true;setBusy(true);setError('');setMessage('');
  try{const value=await request('/api/model-profiles',undefined,{revision:current.revision,action:'set_default',profile_id:id});if(alive.current){apply(value);setMessage(tr('models.defaultMessage'));}}
  catch(e){if(alive.current){blocked.current=Boolean((e as Data)?.uncertain);setUncertain(blocked.current);setError(tr(blocked.current?'models.unknown':'models.saveError'));}}
  finally{sending.current=false;if(alive.current)setBusy(false);}
 }
 async function archive(profile:Data,restore=false){
  const current=profilesRef.current;
  if(sending.current||blocked.current||!current||(!restore&&deleteTarget.current!==profile.id))return;
  sending.current=true;setBusy(true);setError('');setMessage('');
  try{const value=await request('/api/model-profiles',undefined,{revision:current.revision,action:restore?'restore':'archive',profile_id:profile.id});
   if(alive.current){const safe=apply(value);syncSavedModels(safe);confirmDelete(null);setMessage(tr(restore?'models.restored':'models.removed'));}}
  catch(e){if(alive.current){blocked.current=Boolean((e as Data)?.uncertain);setUncertain(blocked.current);setChecked(false);setError(tr(blocked.current?'models.unknown':'models.archiveError'));}}
  finally{sending.current=false;if(alive.current)setBusy(false);}
 }
 async function save(){
  const current=editorRef.current,initial=profilesRef.current;
  if(sending.current||blocked.current||!current||!initial||!current.drafts.length)return;
  if(current.drafts.some(draft=>draft.reviewRequired)){setError(tr('models.reviewUnknown'));return;}
  const endpoint=current.url.trim(),reuse=current.source?.key_set&&current.url===current.source.base_url;
  const changed=current.drafts.filter(draft=>draft.selected!==false&&(!draft.source||draft.baseline!==draftFingerprint(draft)||endpoint!==draft.source.base_url||Boolean(current.key)));
  const disabled=disabledModels(current,initial);
  if(!changed.length&&!disabled.some(item=>item.targets.length)){back();setMessage(tr('models.unchanged'));return;}
  if(changed.length&&(!endpoint||changed.some(draft=>!current.key&&!(draft.source?draft.source.key_set&&endpoint===draft.source.base_url:reuse)))){setError(tr('models.connectionRequired'));return;}
  if(disabled.some(item=>changed.some(draft=>draft.id!==item.draft.id&&endpoint===item.draft.source!.base_url&&draft.model===item.draft.source!.model))){setError(tr('models.conflictingChoices'));return;}
  const prepared:Data[]=[];
  for(const draft of changed){
   if(!draft.model||/[\s\x00-\x1f\x7f]/.test(draft.model)){setError(tr('models.idError'));return;}
   const lengths=parseModelTokenValues(draft.context,draft.output);
   if(lengths===null){setError(tr('models.lengthError'));return;}
   if(draft.protocol!=='inherit'&&(!efforts[draft.protocol]||!draft.levels.length||draft.levels.length>7||new Set(draft.levels.map(item=>item.level)).size!==draft.levels.length
    ||!draft.levels.some(item=>item.level===draft.effort)||draft.levels.some(item=>!levelNames.includes(item.level)||(item.api_value!==null&&(!item.api_value||item.api_value.length>64||/[\s\x00-\x1f\x7f]/.test(item.api_value)))
     ||(draft.protocol==='none'&&(item.level!=='off'||item.api_value!==null))||(draft.protocol==='deepseek'&&item.level==='off'&&item.api_value!==null)
     ||(draft.protocol==='openai'&&item.level==='off'&&item.api_value!==null&&item.api_value!=='none')))){setError(tr('models.reasoningError'));return;}
   prepared.push({id:draft.id,source:draft.source,name:draft.name.trim()||draft.model.slice(0,80),model:draft.model,...lengths,
    ...(draft.protocol==='inherit'?{}:{reasoning_protocol:draft.protocol,reasoning_effort:draft.effort,reasoning_levels:draft.levels.map(item=>({...item}))})});
  }
  if(prepared.length>initial.max_profiles-initial.profiles.filter((p:Data)=>!p.legacy).length){setError(tr('models.limit',{count:initial.max_profiles}));return;}
  sending.current=true;setBusy(true);setError('');setMessage('');invalidateDirectory();patch({key:''});
  let secret=current.key,newSourceId=reuse&&!current.key?current.source!.id:undefined,completed=0,deactivated=0;
  try{
   for(const item of prepared){
    const {id,source,...fields}=item,ownSourceId=source&&endpoint===source.base_url?source.id:undefined,sourceId=source?ownSourceId:newSourceId;
    const payload:Data={revision:profilesRef.current!.revision,action:'create',base_url:endpoint,api_key:source?secret:(newSourceId?'':secret),...fields,...(sourceId?{source_profile_id:sourceId}:{})};
    try{
     const before=profilesRef.current!,value=await request('/api/model-profiles',undefined,payload);
     if(!alive.current)return;const safe=apply(value),created=safe.profiles.find((profile:Data)=>profile.id===value.created_profile_id);
     if(created&&created.base_url===endpoint&&created.key_set){if(secret||!newSourceId)newSourceId=created.id;patch({source:created});}
     const reused=before.profiles.some((profile:Data)=>profile.id===value.created_profile_id);completed++;
     setSaved(previous=>[...previous,{id:value.created_profile_id,name:fields.name,model:fields.model,reused}]);
     if(editorRef.current)patch({drafts:editorRef.current.drafts.filter(draft=>draft.id!==id)});
    }catch(e){
     if(alive.current){blocked.current=Boolean((e as Data)?.uncertain);setUncertain(blocked.current);setChecked(false);
      if(blocked.current)changeDraft(id,{reviewRequired:true});setError(tr(blocked.current?'models.unknown':'models.saveError'));}
     return;
    }finally{payload.api_key='';}
   }
   // Only explicit unchecked saved models become removals; directory omissions never enter this plan.
   // Creation uses the still-active credential sources first. Every removal then uses the latest receipt revision.
   for(const intent of disabled){
    const targets=profilesRef.current!.profiles.filter((profile:Data)=>!profile.archived&&profile.base_url===intent.draft.source!.base_url&&profile.model===intent.draft.source!.model);
    for(const target of targets){
     try{const value=await request('/api/model-profiles',undefined,{revision:profilesRef.current!.revision,action:'archive',profile_id:target.id});
      if(!alive.current)return;const safe=apply(value);if(editorRef.current)patch({source:remainingSource(editorRef.current,safe)});
     }catch(e){if(alive.current){blocked.current=Boolean((e as Data)?.uncertain);setUncertain(blocked.current);setChecked(false);if(blocked.current)changeDraft(intent.draft.id,{reviewRequired:true});setError(tr(blocked.current?'models.unknown':'models.archiveError'));}return;}
    }
    if(targets.length)deactivated++;
    if(editorRef.current)patch({drafts:editorRef.current.drafts.filter(draft=>draft.id!==intent.draft.id)});
   }
   if(alive.current){editorRef.current=null;setEditor(null);setMessage(tr('models.choicesSaved',{count:completed,disabled:deactivated}));queueMicrotask(()=>addButton.current?.focus());}
  }finally{secret='';sending.current=false;if(alive.current)setBusy(false);}
 }
 const activeProfiles=(profiles?.profiles||[]).filter((profile:Data)=>!profile.archived);
 const archivedProfiles=(profiles?.profiles||[]).filter((profile:Data)=>profile.archived);
 const services=providerGroups(activeProfiles);
 const currentService=editor?services.find(service=>service.base_url===editor.url):null;
 const history=currentService?.profiles.filter((profile:Data)=>!currentService.models.some((model:Data)=>model.id===profile.id))||[];
 const label=(key:string,index:number)=>tr(key)+(editor&&editor.drafts.length>1?' · '+(index+1):'');
 const providerName=(url:string)=>{try{return new URL(url).host;}catch{return tr('models.noUrl');}};
 const serviceLabel=(url:string)=>{try{const host=new URL(url).hostname;return host==='api.deepseek.com'?'DeepSeek':host==='ark.cn-beijing.volces.com'?tr('models.volcengineProvider'):providerName(url);}catch{return tr('models.noUrl');}};
 const renderServices=()=> <ul className="saved-model-list" aria-label={tr('models.saved')}>{services.map(service=><li key={service.base_url}>
  <div className="saved-model-description"><strong>{serviceLabel(service.base_url)}</strong><span className="model-provider-count">{tr('models.modelCount',{count:service.models.length})}</span>{service.profiles.some((profile:Data)=>profile.id===profiles!.default_profile_id)&&<span className="saved-model-badges"><span>{tr('models.compactDefault')}</span></span>}<small>{service.base_url||tr('models.noUrl')}</small></div>
  <div className="saved-model-actions"><button type="button" disabled={busy||uncertain} onClick={()=>open(service.representative)} aria-label={tr('models.edit')+': '+service.base_url}>{tr('models.editShort')}</button><button type="button" disabled={busy||uncertain} onClick={()=>{open(service.representative);setMessage(tr('models.providerDeleteHint'));}} aria-label={tr('models.delete')+': '+service.base_url}>{tr('models.delete')}</button></div>
 </li>)}</ul>;
 const renderDelete=(profile:Data)=>deleting===profile.id&&<div className="model-delete-confirm" role="group" aria-label={tr('models.delete')+': '+profile.name}><p>{tr('models.deleteHint')}</p><button type="button" disabled={busy||uncertain} onClick={()=>archive(profile)}>{tr('models.confirmDelete')}</button><button type="button" disabled={busy||uncertain} onClick={()=>confirmDelete(null)}>{tr('models.cancelDelete')}</button></div>;
 const renderFields=(draft:Draft,index:number)=><fieldset className="model-draft-fields model-profile-fields" disabled={busy||uncertain}>

     <label>{tr('models.modelId')}<input data-form-assist-draft="true" aria-label={label('models.modelId',index)} value={draft.model} onChange={event=>changeDraft(draft.id,{model:event.target.value})} maxLength={256} placeholder={tr('models.modelPlaceholder')} required/></label><p className="caption">{tr('models.idHint')}</p>
     <label>{tr('models.name')}<input data-form-assist-draft="true" aria-label={label('models.name',index)} value={draft.name} onChange={event=>changeDraft(draft.id,{name:event.target.value})} maxLength={80} placeholder={tr('models.namePlaceholder')}/></label>
     <div className="model-length-fields"><label>{tr('models.context')}<input data-form-assist-draft="true" aria-label={label('models.context',index)} value={draft.context} onChange={event=>changeDraft(draft.id,{context:event.target.value})} placeholder="1M"/></label><label>{tr('models.output')}<input data-form-assist-draft="true" aria-label={label('models.output',index)} value={draft.output} onChange={event=>changeDraft(draft.id,{output:event.target.value})} placeholder="128K"/></label></div><p className="caption">{tr('models.explicitLimits')}</p>
     <label>{tr('models.reasoningProtocol')}<select data-form-assist-draft="true" aria-label={label('models.reasoningProtocol',index)} value={draft.protocol} onChange={event=>{const protocol=event.target.value,levels=defaultLevels(protocol);changeDraft(draft.id,{protocol,protocolTouched:true,levels,effort:levels.some(item=>item.level===draft.effort)?draft.effort:'off'});}}>{['inherit','none','openai','deepseek'].map(protocol=><option key={protocol} value={protocol}>{tr('models.protocol.'+protocol)}</option>)}</select></label>
     {draft.protocol!=='inherit'&&<><label>{tr('models.reasoningEffort')}<select data-form-assist-draft="true" aria-label={label('models.reasoningEffort',index)} value={draft.effort} onChange={event=>changeDraft(draft.id,{effort:event.target.value,protocolTouched:true})}>{draft.levels.map(item=><option key={item.level} value={item.level}>{item.level}</option>)}</select></label>
      <fieldset className="notification-options"><legend>{tr('models.levelsTitle')}</legend><p className="caption">{tr('models.levelsHint')}</p>
       {draft.levels.map(item=><div className="model-length-fields" key={item.level}><label>{item.level}<input data-form-assist-draft="true" aria-label={label('models.apiValue',index)+' · '+item.level} value={item.api_value??''} maxLength={64} disabled={draft.protocol!=='openai'&&item.level==='off'} placeholder={tr(item.level==='off'&&draft.protocol==='deepseek'?'models.thinkingDisabled':draft.protocol==='none'?'models.noReasoningParameters':'models.apiDefault')} onChange={event=>{const value=event.target.value,latest=editorRef.current!.drafts.find(row=>row.id===draft.id)!;changeDraft(draft.id,{protocolTouched:true,levels:latest.levels.map(level=>level.level===item.level?{...level,api_value:value||null}:level)});}}/></label><button type="button" disabled={draft.levels.length===1} aria-label={label('models.removeLevel',index)+' · '+item.level} onClick={()=>{const latest=editorRef.current!.drafts.find(row=>row.id===draft.id)!,levels=latest.levels.filter(level=>level.level!==item.level);if(levels.length)changeDraft(draft.id,{protocolTouched:true,levels,effort:levels.some(level=>level.level===latest.effort)?latest.effort:levels[0].level});}}>{tr('models.removeLevel')}</button></div>)}
       {(draft.protocol==='none'?['off']:levelNames).filter(level=>!draft.levels.some(item=>item.level===level)).map(level=><button type="button" key={level} aria-label={label('models.addLevel',index)+' · '+level} onClick={()=>{const latest=editorRef.current!.drafts.find(row=>row.id===draft.id)!;if(!latest.levels.some(item=>item.level===level))changeDraft(draft.id,{protocolTouched:true,levels:[...latest.levels,defaultLevels(draft.protocol).find(item=>item.level===level)!]});}}>＋ {level}</button>)}
      </fieldset></>}<p className="caption">{tr('models.reasoningHint')}</p>
     {draft.reviewRequired&&<p className="inline-notice">{tr('models.reviewUnknown')} <button type="button" disabled={!checked} onClick={()=>changeDraft(draft.id,{reviewRequired:false})}>{tr('models.keepCheckedDraft')}</button></p>}
     {draft.source&&<button type="button" disabled={busy||uncertain||!draft.source.selectable||draft.source.id===profiles?.default_profile_id} aria-label={tr('models.setDefault')+': '+draft.source.name} onClick={()=>setDefault(draft.source!.id)}>{tr('models.setDefault')}</button>}
     {!draft.source&&<button type="button" aria-label={tr('models.removeDraft')+': '+(draft.model||index+1)} onClick={()=>patch({drafts:editorRef.current!.drafts.filter(item=>item.id!==draft.id)})}>{tr('models.removeDraft')}</button>}
 </fieldset>;
 const renderRow=(model:Data|undefined,draft:Draft|undefined,index:number)=> <li className="model-directory-item" key={model?.saved?'saved-'+draft!.source!.id:model?'catalog-'+model.id:'draft-'+draft!.id}>
  <div className="model-directory-heading">
   {model?<label className="model-directory-choice"><input type="checkbox" aria-label={tr(model.saved&&draft?.originUrl!==editor?.url?'models.originalServiceModel':'models.selectModel')+': '+model.id} checked={Boolean(draft)&&draft!.selected!==false} onChange={event=>{if(draft?.source){changeDraft(draft.id,{selected:event.currentTarget.checked,expanded:event.currentTarget.checked?draft.expanded:false});if(!event.currentTarget.checked)setMessage(tr('models.pendingRemovalHint'));}else choose(model,event.currentTarget.checked);}}/><span>{draft?.name||model.name}<small>{model.id}</small>{draft?.source?.id===profiles?.default_profile_id&&<span className="saved-model-badges"><span>{tr('models.compactDefault')}</span></span>}</span></label>:<span className="model-manual-name">{draft!.name||draft!.model||tr('models.manualTitle')}<small>{draft!.model||tr('models.manualIdHint')}</small></span>}
   {draft?.source&&<button type="button" className="model-row-delete" disabled={busy||uncertain} aria-label={tr('models.deleteModel')+': '+draft.source.name} onClick={()=>confirmDelete(draft.source!.id)}>{tr('models.delete')}</button>}
   {draft&&<button type="button" className="model-expand" aria-label={tr('models.configure')+': '+(model?.id||draft.model||tr('models.manualTitle'))} aria-expanded={draft.expanded!==false} onClick={()=>changeDraft(draft.id,{expanded:draft.expanded===false})}>{draft.expanded===false?'⌄':'⌃'}</button>}
  </div>
  {draft?.source&&renderDelete(draft.source)}
  {draft?.selected===false&&draft.reviewRequired&&<p className="inline-notice">{tr('models.reviewDisableUnknown')} <button type="button" disabled={!checked} onClick={()=>changeDraft(draft.id,{reviewRequired:false})}>{tr('models.keepDisabledIntent')}</button></p>}
  {draft&&draft.selected!==false&&draft.expanded!==false&&renderFields(draft,index)}
 </li>;
 return <section className="model-profile-settings" aria-labelledby="saved-models-title" onKeyDown={event=>{if(event.key==='Escape'&&editor){event.preventDefault();event.stopPropagation();back();}}}>
  {editor?<>
   <button className="settings-back" type="button" onClick={back} disabled={busy||uncertain}>← {tr('models.back')}</button><h3 id="saved-models-title">{tr(editor.mode==='edit'?'models.editTitle':'models.addTitle')}</h3>
   <p className="settings-description">{tr(editor.mode==='edit'?'models.immutable':'models.addHint')}</p><p className="caption">{tr('models.apiProtocol')}</p>
   {editor.mode==='edit'&&!editor.source?.limits_explicit&&<p className="inline-notice">{tr('models.proposedLimits')}</p>}
   {editor.source&&<p className="caption">{tr('models.savedKeySources',{name:editor.source.name})}</p>}
   {!editor.source&&services.length>0&&<label className="model-service-reuse">{tr('models.existingService')}<select aria-label={tr('models.existingService')} value="" disabled={busy||uncertain} onChange={event=>{const source=services.find(item=>item.representative.id===event.target.value)?.representative;if(source)open(undefined,source);}}><option value="">{tr('models.newService')}</option>{services.map(service=><option key={service.base_url} value={service.representative.id}>{serviceLabel(service.base_url)} · {service.base_url}</option>)}</select></label>}
   <form autoComplete="off" onSubmit={event=>{event.preventDefault();return save();}}><fieldset className="model-profile-fields" disabled={busy||uncertain}>
    <label>{tr('models.baseUrl')}<input data-form-assist-draft="true" type="url" ref={firstInput} aria-label={tr('models.baseUrl')} value={editor.url} onChange={event=>connection('url',event.target.value)} onBlur={()=>{void fetchDirectory(true);}} maxLength={2048} placeholder="https://api.example.com/v1" required/></label>
    <label>{tr('models.apiKey')}<input type="password" aria-label={tr('models.apiKey')} autoComplete="new-password" spellCheck={false} value={editor.key} onChange={event=>connection('key',event.target.value)} onBlur={()=>{void fetchDirectory(true);}} maxLength={8192} placeholder={tr(editor.source?.key_set&&editor.url===editor.source.base_url?'models.reuseKey':'models.keyPlaceholder')}/></label>
    <p className="caption">{tr('models.directoryHint')}</p><button type="button" disabled={directoryBusy} onClick={()=>fetchDirectory()}>{tr(directoryBusy?'models.directoryLoading':'models.fetchDirectory')}</button>
    {defaultProtocol(editor.url)==='deepseek'&&<p className="caption">{tr('models.deepseekDefaults')}</p>}
    {directoryMessage&&<p role="status">{directoryMessage}</p>}
    <div className="model-directory-title"><h4>{tr('models.chooseModels')} <span className="model-provider-count">{tr('models.selectedCount',{count:editor.drafts.filter(draft=>draft.selected!==false&&(draft.model||draft.source)).length})}</span></h4><button type="button" onClick={manual}>{tr('models.manualModel')}</button></div>
    <p className="caption model-length-units">{tr('models.units')}</p>
    <ul className="model-directory-list" aria-label={tr('models.chooseModels')}>
     {directory.map(model=>{const draft=editor.drafts.find(row=>row.catalogId===model.id&&row.catalogUrl===editor.url);return renderRow(model,draft,draft?editor.drafts.indexOf(draft):0);})}
     {editor.drafts.filter(draft=>!directory.some(model=>model.id===draft.catalogId&&draft.catalogUrl===editor.url)&&(!directory.length||draft.model||draft.name||draft.context||draft.output||draft.manual)).map(draft=>renderRow(draft.source?{id:draft.catalogId||draft.model,name:draft.name,saved:true}:undefined,draft,editor.drafts.indexOf(draft)))}
    </ul>
    {disabledModels(editor,profiles!).some(item=>item.targets.length||item.draft.reviewRequired)&&<section className="model-pending-removal" aria-label={tr('models.pendingRemoval')}><strong>{tr('models.pendingRemovalCount',{count:disabledModels(editor,profiles!).filter(item=>item.targets.length||item.draft.reviewRequired).length})}</strong><p>{tr('models.pendingRemovalHint')}</p><ul>{disabledModels(editor,profiles!).map(item=><li key={item.draft.id}>{item.draft.source!.name} · {item.draft.source!.model} <small>{tr('models.retainedVersionCount',{count:item.targets.length})}</small></li>)}</ul></section>}
    <button className="primary" type="submit" disabled={!editor.drafts.length||editor.drafts.some(draft=>draft.reviewRequired)}>{tr(busy?'models.saving':'models.saveChoices')}</button>
   </fieldset></form>
   {history.length>0&&<details className="model-version-history"><summary>{tr('models.versionHistory',{count:history.length})}</summary><p className="caption">{tr('models.historyHint')}</p><ul>{history.map((profile:Data)=><li key={profile.id}><span>{profile.name}{profile.id===profiles?.default_profile_id&&<span className="saved-model-badges"><span>{tr('models.compactDefault')}</span></span>}<small>{profile.model} · {profile.id.slice(0,8)}</small></span><button type="button" disabled={busy||uncertain} aria-label={tr('models.deleteModel')+': '+profile.name} onClick={()=>confirmDelete(profile.id)}>{tr('models.delete')}</button>{renderDelete(profile)}</li>)}</ul></details>}
  </>:<><div className="settings-section-heading"><div><h3 id="saved-models-title">{tr('models.saved')}</h3><p className="settings-description">{tr('models.description')}</p></div><button ref={addButton} type="button" className="primary" disabled={busy||uncertain||Boolean(limit)||!profiles} onClick={()=>open()}>＋ {tr('models.add')}</button></div>{!profiles&&<p role="status">{tr('models.loading')}</p>}{profiles&&!activeProfiles.length&&<p className="settings-empty">{tr('models.empty')}</p>}{profiles&&renderServices()}</>}
  {!editor&&archivedProfiles.length>0&&<section className="archived-models"><button type="button" aria-expanded={showArchived} onClick={()=>setShowArchived(value=>!value)}>{tr('models.archivedCount',{count:archivedProfiles.length})}</button>{showArchived&&<ul className="saved-model-list">{archivedProfiles.map((profile:Data)=><li key={profile.id}><div className="saved-model-description"><strong>{profile.name}</strong><small>{providerName(profile.base_url)}</small></div><div className="saved-model-actions"><button type="button" disabled={busy||uncertain} onClick={()=>archive(profile,true)} aria-label={tr('models.restore')+': '+profile.name}>{tr('models.restore')}</button></div></li>)}</ul>}</section>}
  {saved.length>0&&<section aria-label={tr('models.savedThisBatch')}><h4>{tr('models.savedThisBatch')}</h4><ul>{saved.map((item,index)=><li key={index}>{item.name} · {item.model}{item.reused?' · '+tr('models.reusedMessage'):''}</li>)}</ul></section>}
  {editor&&editor.drafts.some(draft=>draft.reviewRequired)&&profiles&&<section><h4>{tr('models.checkSavedList')}</h4>{renderServices()}</section>}
  {limit&&<p className="inline-notice">{tr('models.limit',{count:profiles?.max_profiles})} {tr('models.historyCapacity')}</p>}<button type="button" className="settings-refresh" disabled={busy} onClick={read}>{tr(uncertain?'models.reconcile':'models.refresh')}</button>
  {message&&<p role="status">{message}</p>}{error&&<p role="alert" className="error-text">{error}</p>}
 </section>;
}
