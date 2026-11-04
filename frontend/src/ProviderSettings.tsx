import {uiIn} from './ui-language.mjs';
import React,{useEffect,useRef,useState} from 'react';

type Data=Record<string,any>;
type Props={request:(path:string,token?:string,body?:Data)=>Promise<Data>;initial?:Data;initialProfiles?:Data;onProfilesChange?:(value:Data)=>void;language?:string};
// Retain only public fields, even if a malformed response contains credentials.
function publicProfiles(value:Data):Data{return {revision:value.revision,default_profile_id:value.default_profile_id,max_profiles:value.max_profiles,
 profiles:Array.isArray(value.profiles)?value.profiles.map((p:Data)=>({id:p.id,name:p.name,base_url:p.base_url,model:p.model,key_set:Boolean(p.key_set),legacy:Boolean(p.legacy),selectable:Boolean(p.selectable),
 context_window_tokens:Number.isInteger(p.context_window_tokens)?p.context_window_tokens:undefined,max_output_tokens:Number.isInteger(p.max_output_tokens)?p.max_output_tokens:undefined,limits_explicit:p.limits_explicit===true})):[]};}
function failure(e:unknown,fallback:string,secret=''){
 const message=(e as Error)?.message||'';
 return /[\u3400-\u9fff]/.test(message)&&message.length<500&&(!secret||!message.includes(secret))?message:fallback;
}

export {ModelProfilesSettings} from './ModelProfilesSettings';

export function ProviderSettings({request,initial,initialProfiles,onProfilesChange,language='zh-CN'}:Props){
 const copy=(value:string)=>uiIn(language,value);
 const [settings,setSettings]=useState<Data|null>(initial||null),[key,setKey]=useState(''),[url,setUrl]=useState(initial?.base_url||''),[model,setModel]=useState(initial?.model||'');
 const [busy,setBusy]=useState(false),[uncertain,setUncertain]=useState(false),[message,setMessage]=useState(''),[error,setError]=useState('');
 const sending=useRef(false),alive=useRef(true),legacyBlocked=useRef(false);
 const [profiles,setProfiles]=useState<Data|null>(initialProfiles?publicProfiles(initialProfiles):null),[profileName,setProfileName]=useState('');
 const [profileUrl,setProfileUrl]=useState(''),[profileModel,setProfileModel]=useState(''),[profileKey,setProfileKey]=useState('');
 const [profileBusy,setProfileBusy]=useState(false),[profileUncertain,setProfileUncertain]=useState(false),[profileMessage,setProfileMessage]=useState(''),[profileError,setProfileError]=useState('');
 const profileSending=useRef(false),profileBlocked=useRef(false);
 const addedCount=profiles?.profiles.filter((p:Data)=>!p.legacy).length||0;
 function applyProfiles(value:Data){const safe=publicProfiles(value);setProfiles(safe);onProfilesChange?.(safe);}
 async function readProfiles(){
  if(profileSending.current)return;
  profileSending.current=true;setProfileBusy(true);setProfileError('');setProfileMessage('');
  try{const value=await request('/api/model-profiles');if(alive.current){applyProfiles(value);profileBlocked.current=false;setProfileUncertain(false);}}
  catch(e){if(alive.current)setProfileError(failure(e,copy("无法读取模型列表，请稍后刷新核对。")));}
  finally{profileSending.current=false;if(alive.current)setProfileBusy(false);}
 }
 async function changeProfiles(action:'create'|'set_default',profileId?:string){
  if(profileSending.current||profileBlocked.current||!profiles)return;
  if(action==='create'&&(addedCount>=profiles.max_profiles||!profileName.trim()||!profileUrl.trim()||!profileModel.trim()||!profileKey))return;
  if(action==='set_default'&&!profiles.profiles.some((p:Data)=>p.id===profileId&&p.selectable))return;
  profileSending.current=true;setProfileBusy(true);setProfileError('');setProfileMessage('');
  const payload:Data=action==='create'?{revision:profiles.revision,action,name:profileName.trim(),base_url:profileUrl.trim(),model:profileModel.trim(),api_key:profileKey}:
   {revision:profiles.revision,action,profile_id:profileId};
  setProfileKey('');
  try{const value=await request('/api/model-profiles',undefined,payload);if(alive.current){applyProfiles(value);
   if(action==='create'){setProfileName('');setProfileUrl('');setProfileModel('');}
   setProfileMessage(action==='create'?copy("模型已添加到本机，未调用模型；原配置与旧任务保持原样。"):copy("已设置新任务的默认模型；旧任务与原监控继续使用原配置。"));}}
  catch(e){if(alive.current){const unknown=Boolean((e as Data).uncertain);profileBlocked.current=unknown;setProfileUncertain(unknown);
   setProfileError(unknown?copy("保存结果未知，已暂停再次提交。请重新读取模型列表，核对后再操作。"):failure(e,copy("模型设置未保存，请检查填写内容或刷新列表后重试。"),payload.api_key));}}
  finally{if('api_key' in payload)payload.api_key='';profileSending.current=false;if(alive.current)setProfileBusy(false);}
 }
 function apply(value:Data){setSettings(value);setUrl(value.base_url||'');setModel(value.model||'');setKey('');}
 async function read(){
  if(sending.current)return;
  sending.current=true;setBusy(true);setError('');
  try{const value=await request('/api/provider-config');if(alive.current){apply(value);legacyBlocked.current=false;setUncertain(false);}}
  catch(e){if(alive.current)setError(failure(e,copy("无法读取原模型设置，请稍后重试。")));}
  finally{sending.current=false;if(alive.current)setBusy(false);}
 }
 useEffect(()=>{alive.current=true;if(!initial)read();if(!initialProfiles)readProfiles();return()=>{alive.current=false;};},[]);
 async function save(event:React.FormEvent){
  event.preventDefault();if(sending.current||legacyBlocked.current||uncertain||!settings?.editable)return;
  sending.current=true;setBusy(true);setError('');setMessage('');
  const payload={revision:settings.revision,api_key:key,base_url:url.trim(),model:model.trim()};setKey('');
  try{const value=await request('/api/provider-config',undefined,payload);if(alive.current){apply(value);setMessage(copy("已保存到本机。保存仅检查格式，未调用模型。"));if(!profileBlocked.current)void readProfiles();}}
  catch(e){if(alive.current){setError(failure(e,copy("原模型设置未保存，请检查填写内容或刷新设置后重试。"),payload.api_key));legacyBlocked.current=Boolean((e as Data).uncertain);setUncertain(legacyBlocked.current);}}
  finally{payload.api_key='';sending.current=false;if(alive.current)setBusy(false);}
 }
 return <section className="provider-settings" aria-labelledby="provider-settings-title">
  <header><h3 id="provider-settings-title">{copy("模型连接")}</h3><span className="small-tag">{settings?.configured?copy("已配置"):copy("待配置")}</span></header>
  <p className="caption">{copy("配置兼容 OpenAI Chat Completions 的服务。密钥保存在本机服务端文件中，浏览器不持久保存，也不回显。")}</p>
  {settings?.reason&&<p className="inline-notice">{profiles&&settings.editable===false?copy("原配置已绑定或由启动参数指定，保持只读。添加或选择其他模型，请使用下方“多个模型配置”；旧任务保留原绑定。"):settings.reason}</p>}
  {settings?.error&&<p role="alert" className="error-text">{settings.error}</p>}
  <form onSubmit={save} autoComplete="off">
   <fieldset disabled={!settings?.editable||busy||uncertain}>
    <label>Base URL<input type="url" aria-label={copy("模型 Base URL")} placeholder="https://api.example.com/v1" value={url} onChange={e=>setUrl(e.target.value)} maxLength={2048} required/></label>
    <label>Model<input aria-label={copy("模型名称")} placeholder={copy("填写服务商提供的模型 ID")} value={model} onChange={e=>setModel(e.target.value)} maxLength={256} required/></label>
    <label>API Key<input type="password" aria-label={copy("模型 API Key")} autoComplete="new-password" spellCheck={false} placeholder={settings?.key_set?copy("已保存；留空保留，换 URL 时须重填"):copy("填写 API Key")} value={key} onChange={e=>setKey(e.target.value)} maxLength={8192} required={!settings?.key_set}/></label>
    <button className="primary" type="submit">{busy?copy("正在保存…"):copy("保存模型配置")}</button>
   </fieldset>
  </form>
  <button type="button" disabled={busy} onClick={read}>{uncertain?copy("重新读取设置，核对保存结果"):copy("刷新模型设置")}</button>
  {message&&<p role="status">{message}</p>}{error&&<p role="alert" className="error-text">{error}</p>}
  <section aria-labelledby="model-profiles-title">
   <header><h3 id="model-profiles-title">{copy("多个模型配置")}</h3><span className="small-tag">{profiles?`${profiles.profiles.length}${copy(" 个配置")}`:copy("待读取")}</span></header>
   <p className="caption">{copy("添加不同服务或模型后，可在新任务中选择。保存和设置默认值均不调用模型；默认值只用于新任务，旧任务、审批、恢复和原监控继续使用原配置。")}</p>
   {profiles&&<ul aria-label={copy("已保存的模型配置")}>{profiles.profiles.map((p:Data)=><li key={p.id}>
    <strong>{p.name}</strong> · {p.model||copy("未配置模型")}{p.legacy?copy(" · 原配置"):''}{p.id===profiles.default_profile_id?copy(" · 新任务默认"):''}
    <p className="caption">{p.base_url||copy("未配置服务地址")} · {p.key_set?copy("密钥已保存"):copy("尚未设置密钥")}{!p.selectable?copy(" · 当前不可选"):''}</p>
    <button type="button" disabled={profileBusy||profileUncertain||!p.selectable||p.id===profiles.default_profile_id} onClick={()=>changeProfiles('set_default',p.id)} aria-label={`${copy("将 ")}${p.name}${copy(" 设为新任务默认模型")}`}>{copy("设为新任务默认")}</button>
   </li>)}</ul>}
   {profiles&&profiles.profiles.length===0&&<p className="inline-notice">{copy("尚无可用模型配置，请添加模型。")}</p>}
   {profiles&&<form autoComplete="off" onSubmit={event=>{event.preventDefault();return changeProfiles('create');}}>
    <fieldset disabled={profileBusy||profileUncertain||addedCount>=profiles.max_profiles}>
     <legend>{copy("添加模型")}</legend>
     <label>{copy("配置名称")}<input aria-label={copy("新模型配置名称")} value={profileName} onChange={e=>setProfileName(e.target.value)} maxLength={80} placeholder={copy("例如：开发模型")} required/></label>
     <label>Base URL<input type="url" aria-label={copy("新模型 Base URL")} value={profileUrl} onChange={e=>setProfileUrl(e.target.value)} maxLength={2048} placeholder="https://api.example.com/v1" required/></label>
     <label>Model<input aria-label={copy("新模型 ID")} value={profileModel} onChange={e=>setProfileModel(e.target.value)} maxLength={256} placeholder={copy("填写服务商提供的模型 ID")} required/></label>
     <label>API Key<input type="password" aria-label={copy("新模型 API Key")} autoComplete="new-password" spellCheck={false} value={profileKey} onChange={e=>setProfileKey(e.target.value)} maxLength={8192} placeholder={copy("填写新模型的 API Key")} required/></label>
     <button type="submit" className="primary">{profileBusy?copy("正在处理模型设置…"):copy("添加模型")}</button>
    </fieldset>
   </form>}
   {profiles&&addedCount>=profiles.max_profiles&&<p className="inline-notice">{copy("已达到模型配置数量上限（最多添加 ")}{profiles.max_profiles}{copy(" 个，不含原配置）。")}</p>}
   <p className="caption">{copy("配置保存后保持固定。需要更换服务地址、模型或密钥时，请另建配置；同一完整配置重复添加会复用已有配置。")}</p>
   <button type="button" disabled={profileBusy} onClick={readProfiles}>{profileUncertain?copy("重新读取模型列表，核对保存结果"):copy("刷新模型列表")}</button>
   {profileMessage&&<p role="status">{profileMessage}</p>}{profileError&&<p role="alert" className="error-text">{profileError}</p>}
  </section>
 </section>;
}
